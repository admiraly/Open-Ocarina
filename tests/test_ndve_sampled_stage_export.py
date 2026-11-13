# SPDX-License-Identifier: GPL-3.0-or-later
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

from tools import ndve_sampled_stage_export as sampled
from tools.ndve_stage_export import NATIVE_SHA, PROGRAM


ROOT = Path(__file__).resolve().parents[1]
HOST = """
struct HostTranslateInfo {
    u64 min_ssbo_alignment{};
    u32 max_per_stage_descriptor_sampled_images{};
    bool support_float64{};
    void ApplyDescriptorLimitPolicy() noexcept {}
};
"""
PROFILE = """
struct Profile {
    u32 supported_spirv{0x00010000};
    bool unified_descriptor_binding{};
};
"""
INFO = """
enum class TextureType : u32 {
    Color1D,
    ColorArray1D,
    Color2D,
    Color3D,
};
"""


def field(object_name, name, value):
    return {"event": "field", "object": object_name, "field": name, "value": value}


def base_rows():
    rows = [{"event": "context_begin", "context": index} for index in range(1, 5)]
    rows += [{"event": "context_end", "context": index, "complete": True} for index in range(1, 5)]
    rows.append({"event": "native_digest_verified", "sha256": NATIVE_SHA, "key": PROGRAM})
    for name, value in (("local_x", 32), ("shared_bytes", 3360), ("lowered_ok", 1)):
        rows.append(field("native", name, value))
    for name in ("hardware_native_mma", "Ampere_native_matrix_multiply", "SPIRV_mma_software"):
        rows.append(field("native_settings", name, 1))
    for name, value in (("c0_byte88", sampled.HANDLE), ("texture_integer_response", 0), ("texture_type_response", 2)):
        rows.append(field("bound_state_sample", name, value))
    for name, value in (("known", 1), ("has_view", 0), ("generation_stable_during_sample", 1)):
        rows.append(field("texture_registration", name, value))
    host = {"min_ssbo_alignment": 16, "max_per_stage_descriptor_sampled_images": 0, "support_float64": 1}
    for name, value in host.items():
        rows.append(field("host_info_used", name, value))
        rows.append(field("sampled_host_info_not_use_proof", name, value))
    profile = {"supported_spirv": 67072, "unified_descriptor_binding": 1}
    for name, value in profile.items():
        rows.append(field("sampled_pinned_profile_not_use_proof", name, value))
        rows.append(field("sampled_base_profile_not_use_proof", name, value))
    rows.append({"event": "compiler_query", "ordinal": 1, "phase": "nonforced_translation", "key": PROGRAM,
                 "forced": False, "questions_replay": False, "questions_recording": True,
                 "kind": "cbuf", "first": 0, "second": 88, "answer": sampled.HANDLE})
    return rows


class SampledStageExportTests(unittest.TestCase):
    def fields(self):
        return sampled.struct_fields(HOST, "HostTranslateInfo"), sampled.struct_fields(PROFILE, "Profile")

    def test_source_field_order_and_conditional_type_are_required(self):
        host, profile = self.fields()
        config = sampled.configuration_from_rows(base_rows(), host, profile, 2)
        self.assertEqual([item["name"] for item in host], list(config["host"]))
        self.assertFalse(config["conditional_texture_type"]["actual_compiler_query_observed"])
        generated = sampled.assignment_source(host, profile, config)
        self.assertIn("h.min_ssbo_alignment = 16ull;", generated)
        self.assertIn("p.supported_spirv = 67072u;", generated)
        self.assertIn("conditional_sampled", generated)
        self.assertNotIn("return 0", generated)

    def test_duplicate_profile_field_and_unknown_shape_are_rejected(self):
        host, profile = self.fields()
        duplicate = base_rows() + [field("sampled_pinned_profile_not_use_proof", "supported_spirv", 67072)]
        with self.assertRaisesRegex(ValueError, "Duplicate"):
            sampled.configuration_from_rows(duplicate, host, profile, 2)
        mismatched = base_rows()
        mismatched.append(field("host_info_used", "support_float16", 1))
        with self.assertRaisesRegex(ValueError, "Host fields"):
            sampled.configuration_from_rows(mismatched, host, profile, 2)
        with self.assertRaisesRegex(ValueError, "Color2D"):
            sampled.configuration_from_rows(base_rows(), host, profile, 0)

    def test_frozen_public_export_pins_match(self):
        self.assertEqual(sampled.frozen_export_pins(), sampled.FROZEN_EXPORT_PINS)
        for relative in sampled.FROZEN_EXPORT_PINS:
            self.assertTrue((ROOT / relative).is_file())

    def test_driver_rewrite_removes_synthetic_answers(self):
        path = ROOT / "local/hookshot-source/nx2/bake_ds/bake_ds.cpp"
        if not path.is_file():
            self.skipTest("optional private frozen driver is unavailable")
        original = path.read_text()
        host, profile = self.fields()
        # The frozen driver is hashed; the rewrite is checked against that source.
        if hashlib.sha256(original.encode()).hexdigest() != sampled.BAKE_SHA:
            self.skipTest("frozen driver is not the pinned bake_ds")
        rewritten = sampled.sampled_driver(original, sampled.assignment_source(host, profile, sampled.configuration_from_rows(base_rows(), host, profile, 2)))
        self.assertNotIn("dlopen(", rewritten)
        self.assertNotIn("vkCreateInstance(", rewritten)
        self.assertNotIn("return Shader::TextureType::Color2D", rewritten)
        self.assertIn("assign_observed_profile", rewritten)
        self.assertIn("whatif_finish", rewritten)
        self.assertIn("0xe409733c45530d75ULL", rewritten)

    @unittest.skipUnless(shutil.which("c++"), "optional CPU query-gate compiler")
    def test_future_known_query_cannot_be_used_as_an_observed_repeat(self):
        host, profile = self.fields()
        config = sampled.configuration_from_rows(base_rows(), host, profile, 2)
        config["queries"] = [
            {"kind": "local_memory_size", "first": 0, "second": 0, "answer": 7},
            {"kind": "cbuf", "first": 0, "second": 88, "answer": sampled.HANDLE},
        ]
        source = r'''
#include <cstdio>
#include <cstdint>
#include <cstring>
#include <iterator>
#include <stdexcept>
using u32 = uint32_t; using u64 = uint64_t;
namespace Shader {
using RuntimeError = std::runtime_error;
''' + HOST + PROFILE + "}\n" + sampled.assignment_source(host, profile, config) + r'''
int main() {
    bool denied = false;
    try { (void)whatif_answer("cbuf", 0, 88); }
    catch (const Shader::RuntimeError&) { denied = true; }
    if (!denied || g_whatif_at != 0) return 1;
    if (whatif_answer("local_memory_size", 0, 0) != 7 || g_whatif_at != 1) return 2;
    if (whatif_answer("local_memory_size", 0, 0) != 7 || g_whatif_at != 1) return 3;
    if (whatif_answer("cbuf", 0, 88) != 281020472 || g_whatif_at != 2) return 4;
    whatif_finish();
    return 0;
}
'''
        with tempfile.TemporaryDirectory() as directory:
            cpp = Path(directory) / "gate.cpp"
            binary = Path(directory) / "gate"
            cpp.write_text(source)
            subprocess.run(["c++", "-std=c++17", "-O2", "-DNDEBUG", str(cpp), "-o", str(binary)],
                           check=True, capture_output=True, text=True, timeout=30)
            result = subprocess.run([str(binary)], check=True, capture_output=True, text=True, timeout=5)
        self.assertIn("whatif_deny kind=cbuf first=0 second=88", result.stderr)
        self.assertIn("observed_consumed=2", result.stderr)


if __name__ == "__main__":
    unittest.main()
