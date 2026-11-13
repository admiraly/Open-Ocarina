# SPDX-License-Identifier: GPL-3.0-or-later
"""Descriptor-element accounting for the D8 common-33 compile preparation."""
import hashlib
import importlib
import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
PREP = importlib.import_module("tools.grok_d8_interval_compile")


def synthetic_common():
    rows = []
    for binding in range(31):
        if binding <= 4 or 9 <= binding <= 17:
            kind = 6
        elif 20 <= binding <= 29:
            kind = 1
        else:
            kind = 7
        count = 3 if binding == 8 else 1
        stage = PREP.MESH_STAGE if binding <= 8 or binding == 30 else PREP.FRAGMENT_STAGE
        rows.append({"set": 0, "binding": binding, "type": kind, "count": count, "stage_flags": stage})
    return rows


class LayoutAccountingTests(unittest.TestCase):
    def test_push_limit_is_element_sum_not_binding_count(self):
        rows = synthetic_common()
        self.assertEqual(len(rows), 31)
        self.assertEqual(PREP.descriptor_elements(rows), 33)
        self.assertLessEqual(len(rows), PREP.KNOWN_PRIOR_MAX_PUSH_DESCRIPTORS)
        self.assertFalse(PREP.push_layout_allowed(rows))
        with self.assertRaises(ValueError) as caught:
            PREP.reject_push_layout(rows)
        self.assertIn("33 descriptor elements", str(caught.exception))
        self.assertIn("not the limit", str(caught.exception))

    def test_common_totals_and_mesh_only_record(self):
        rows = synthetic_common()
        totals, per_stage = PREP.element_totals(rows)
        self.assertEqual(totals, PREP.ELEMENT_TOTALS)
        self.assertEqual(per_stage, PREP.STAGE_ELEMENT_TOTALS)
        record = rows[30]
        self.assertEqual(record["stage_flags"], PREP.MESH_STAGE)
        self.assertEqual(record["count"], 1)

    def test_helper_source_has_no_execution_path_and_builds_unrun(self):
        source = PREP.HELPER_SOURCE.read_text()
        for name in PREP.FORBIDDEN_HELPER_APIS:
            self.assertNotIn(name, source)
        self.assertIn("push_mode != 0", source)
        self.assertIn("COMMON_ELEMENTS", source)
        if any(shutil.which(tool) is None for tool in ("pkg-config", "cc", "nm")):
            self.skipTest("optional native build tools are absent")
        available = subprocess.run(["pkg-config", "--exists", "vulkan"],
                                   capture_output=True, timeout=10)
        if available.returncode:
            self.skipTest("optional Vulkan development headers/library are absent")
        with tempfile.TemporaryDirectory() as directory:
            built = PREP.build_helper(Path(directory), "cc")
            self.assertFalse(built["helper_executed"])
            self.assertTrue((Path(directory) / "compile-helper").is_file())

    def test_runner_is_parent_bounded_and_unauthorized(self):
        text = PREP.runner_text("original", "abc")
        self.assertNotIn("ulimit", text)
        self.assertNotIn("rm -rf", text)
        self.assertIn("existing evidence is preserved", text)
        self.assertIn("explicitly reviewed driver ICD", text)
        self.assertIn("MESA_SHADER_CACHE_DIR", text)
        self.assertIn("unset HS_CS32_MAX_INSTS", text)
        self.assertIn("timeout 180", text)
        self.assertNotIn("/home/", text)
        self.assertNotIn("/mnt/", text)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "original.sh"
            path.write_text(text)
            path.chmod(0o755)
            result = subprocess.run([str(path)], capture_output=True, text=True)
        self.assertEqual(result.returncode, 2)
        self.assertIn("not authorized", result.stderr)

    def test_lowering_tool_pin_unchanged(self):
        self.assertEqual(PREP.digest(PREP.LOWERING_TOOL), PREP.PINS["lowering_tool_sha256"])


class PrivateModuleTests(unittest.TestCase):
    def test_prepare_common_layout(self):
        original = ROOT / "local/graphics-targets/Hookshot-nightly-d8e59341cf4f629c-0.spv"
        fragment = ROOT / "local/graphics-targets/Hookshot-nightly-d8e59341cf4f629c-1.spv"
        candidate = ROOT / "local/grok-d8-interval/candidate.spv"
        records = ROOT / "local/grok-d8-interval/records.bin"
        if not all(path.is_file() for path in (original, fragment, candidate, records)):
            self.skipTest("private d8 modules are absent")
        work = tempfile.TemporaryDirectory(prefix="d8-compile-test-", dir=ROOT / "local")
        self.addCleanup(work.cleanup)
        output = Path(work.name) / "prepared"
        plan = PREP.prepare(output, original, candidate, fragment, records)
        self.assertEqual(plan["descriptor_binding_count"], 31)
        self.assertEqual(plan["descriptor_element_count"], 33)
        self.assertTrue(plan["push_layout_rejected"])
        self.assertFalse(plan["binding_30"]["used_by_original_mesh"])
        self.assertTrue(plan["binding_30"]["mesh_only"])
        self.assertEqual(plan["entry_loads"]["load_count"], 21)
        self.assertFalse(plan["entry_loads"]["whole_shader_rewrite"])
        self.assertEqual(plan["fragment_unchanged"], PREP.PINS["fragment_sha256"])
        self.assertFalse(plan["helper_executed"])
        self.assertFalse(plan["execution_admitted"])
        self.assertFalse(plan["live_certificate"])
        checked = PREP.check_plan(output / "plan.json")
        self.assertEqual(checked["plan_sha256"], plan["plan_sha256"])
        layout = (output / "layout.txt").read_text().splitlines()
        self.assertTrue(layout[0].split()[3] == "0")
        self.assertEqual(sum(int(line.split()[3]) for line in layout[1:32]), 33)
        self.assertIn("second-set", plan["future_abi"])


if __name__ == "__main__":
    unittest.main()
