# SPDX-License-Identifier: GPL-3.0-or-later
import hashlib
import importlib.util
import json
from pathlib import Path
import shutil
import struct
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("dispatch_parameters_checker", ROOT / "patches/hookshot/check_dispatch_parameters.py")
CHECK = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(CHECK)


class DispatchParametersTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.directory = Path(temporary.name)
        self.pipeline = {"policy_complete": True, "layout_complete": True, "program": "0123456789abcdef", "variant": "fedcba9876543210",
                         "spirv_sha256": "b" * 64, "cache_stamp": "0" * 16, "layout_handle": "0" * 15 + "1",
                         "entry_point": "main", "specialization": [], "local_size": [64, 1, 1],
                         "subgroup": {"required_size": 32, "require_full_subgroups": True},
                         "push_range": {"offset": 0, "size": 88, "stage_flags": 32},
                         "descriptor_layout": [{"set": 0, "binding": 0, "type": 6, "count": 1, "stage_flags": 32}]}
        payload = bytearray(range(88));struct.pack_into("<Q", payload, 40, 0x1122334455667788)
        (self.directory / "push-constants.bin").write_bytes(payload)
        self.table = {"schema": "open-ocarina.gpu-page-table/v1", "pipeline": {k: self.pipeline[k] for k in ("program", "variant", "spirv_sha256")},
                      "submission": {"recording_part": 7, "logical_value": 9, "recording_sequence": 15, "completed_recording_sequence": 15},
                      "dispatch": {"kind": "direct", "group_count": [3, 4, 5]},
                      "table": {"root_push_offset": 40, "root_device_address": "1122334455667788"}}
        self.others = {}
        for name, schema in (("allocations.json", "open-ocarina.gpu-allocations/v2"), ("images.json", "open-ocarina.sampled-images/v1")):
            self.others[name] = {"schema": schema, "replay_complete": False, "recording_part": 7, "recording_sequence": 15,
                                 "completed_recording_sequence": 15, "selected_occurrence": 64, **self.table["pipeline"]}
        self.document = {"schema": "open-ocarina.dispatch-parameters/v1", "replay_complete": False, "parameters_complete": True,
                         **self.table["submission"], "selected_occurrence": 64, "pipeline": self.pipeline,
                         "dispatch": {"kind": "direct", "group_count": [3, 4, 5]},
                         "push_constants": {"artifact": "push-constants.bin", "size": 88, "offset": 0, "stage_flags": 32,
                                            "root_push_offset": 40, "sha256": hashlib.sha256(payload).hexdigest()},
                         "features": {"enabled_feature_chain_complete": False, "float_controls_captured": False},
                         "remaining_gates": ["full_enabled_device_features", "float_controls", "underlying_import_aliases", "cpu_writers_quiesced", "temporal_state", "independent_output_comparison"]}
        self.save()

    def save(self):
        associations = []
        for name, document in {"manifest.json": self.table, **self.others}.items():
            data = json.dumps(document).encode()
            (self.directory / name).write_bytes(data)
            associations.append({"artifact": name, "sha256": hashlib.sha256(data).hexdigest()})
        self.document["associations"] = associations
        (self.directory / "dispatch-parameters.json").write_text(json.dumps(self.document))

    def validate(self):
        return CHECK.validate_capture(self.directory)

    def test_exact_direct_parameters_are_still_not_full_replay(self):
        report = self.validate()
        self.assertTrue(report["parameters_complete"])
        self.assertFalse(report["replay_complete"])
        self.assertEqual(report["push_bytes"], 88)

    def test_payload_corruption_is_refused(self):
        (self.directory / "push-constants.bin").write_bytes(b"x" * 88)
        with self.assertRaisesRegex(ValueError, "integrity"):
            self.validate()

    def test_rehashed_wrong_page_root_is_refused(self):
        payload = bytearray((self.directory / "push-constants.bin").read_bytes());payload[40] ^= 1
        (self.directory / "push-constants.bin").write_bytes(payload)
        self.document["push_constants"]["sha256"] = hashlib.sha256(payload).hexdigest();self.save()
        with self.assertRaisesRegex(ValueError, "page root"):
            self.validate()

    def test_partial_range_cannot_claim_complete_push(self):
        self.pipeline["push_range"]["size"] = 80;self.save()
        with self.assertRaisesRegex(ValueError, "complete compute range"):
            self.validate()

    def test_gpu_indirect_grid_is_not_accepted_as_direct(self):
        self.document["dispatch"] = {"kind": "indirect", "group_count": None};self.save()
        with self.assertRaisesRegex(ValueError, "direct grids"):
            self.validate()

    def test_missing_push_and_stale_layout_are_refused(self):
        for field in ("layout_complete", "policy_complete"):
            self.pipeline[field] = False;self.save()
            with self.assertRaisesRegex(ValueError, "layout/policy"):
                self.validate()
            self.pipeline[field] = True
        self.document["parameters_complete"] = False;self.save()
        with self.assertRaisesRegex(ValueError, "incomplete"):
            self.validate()

    def test_logical_value_cannot_replace_actual_recording_sequence(self):
        self.document["recording_sequence"] = 9;self.save()
        with self.assertRaisesRegex(ValueError, "recording/completion"):
            self.validate()

    def test_same_pipeline_other_occurrence_is_refused(self):
        self.others["images.json"]["selected_occurrence"] = 2;self.save()
        with self.assertRaisesRegex(ValueError, "recording/occurrence"):
            self.validate()

    def test_association_payload_hash_is_checked(self):
        (self.directory / "images.json").write_text("{}")
        with self.assertRaisesRegex(ValueError, "association hash"):
            self.validate()

    def test_symlink_blob_is_refused(self):
        data = (self.directory / "push-constants.bin").read_bytes()
        (self.directory / "push-constants.bin").unlink();(self.directory / "other.bin").write_bytes(data)
        (self.directory / "push-constants.bin").symlink_to("other.bin")
        with self.assertRaisesRegex(ValueError, "nonregular"):
            self.validate()

    def test_bool_cannot_masquerade_as_descriptor_count(self):
        self.pipeline["descriptor_layout"][0]["count"] = True;self.save()
        with self.assertRaisesRegex(ValueError, "descriptor count"):
            self.validate()

    def test_full_replay_claim_and_hidden_feature_gates_are_refused(self):
        self.document["replay_complete"] = True;self.save()
        with self.assertRaisesRegex(ValueError, "overstated"):
            self.validate()
        self.document["replay_complete"] = False;self.document["features"]["float_controls_captured"] = True;self.save()
        with self.assertRaisesRegex(ValueError, "gates concealed"):
            self.validate()

    def test_missing_header_and_optimized_assertions_fail_closed(self):
        with self.assertRaisesRegex(ValueError, "sentinel"):
            CHECK.require(False, "sentinel")

    def test_intervening_push_fails_source_audit(self):
        call = "vkCmdPushConstants(cmd, p.layout, p.push_stages, 0, kPushSize, data);"
        pin = "void pin_subgroup() {}"
        before = "void push_constants() {" + call + "}" + pin
        after = "void push_constants() {oo_dp::invalidate_push();if (!p.uses_push)return;" + call + "oo_dp::note_push(data, Shader::Backend::SPIRV::GUEST_PAGES_PUSH_OFFSET);}" + pin
        after += "void ensure_layout() {for (const auto& binding : bindings){} oo_dp::note_layout();}"
        after += "void dispatch() {push_constants(cmd, p, nullptr);oo_compute_identity(cmd, p, s, x, y, z, 0);vkCmdDispatch(cmd, x, y, z);}"
        after += "void oo_compute_identity() {oo_dp::note_policy();memory_pt_snapshot_record();}"
        # The unrelated original dispatch must be present in both command streams.
        before += "void dispatch() {vkCmdDispatch(cmd, x, y, z);}"
        memory = "void memory_pt_snapshot_record() {if(!selected)return;oo_dp::select();}"
        memory += "void memory_pt_snapshot_collect() {if(completed_sequence<pending.request.submitted_sequence)return;oo_allocation_collect();oo_dp::write_completed();}"
        self.assertTrue(CHECK.source_audit(before, after, memory)["no_intervening_direct_push"])
        # Keep the Vulkan command stream equal, but move an extra push into the critical interval.
        changed = after.replace("oo_compute_identity(cmd", call + "oo_compute_identity(cmd")
        with self.assertRaisesRegex(ValueError, "recording commands|intervening push"):
            CHECK.source_audit(before + call, changed, memory)

    @unittest.skipUnless(shutil.which("clang++") or shutil.which("g++"), "C++20 compiler unavailable")
    def test_optimized_actual_header_capture_boundaries(self):
        report = CHECK.check_helper()
        self.assertTrue(report["fresh_single_use_shadow"])
        self.assertTrue(report["command_layout_recording_and_root_mismatch_refused"])
        self.assertTrue(report["same_recording_completion_checked"])


if __name__ == "__main__":
    unittest.main()
