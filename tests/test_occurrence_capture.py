# SPDX-License-Identifier: GPL-3.0-or-later
import copy
import importlib.util
import json
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("occurrence_check", ROOT / "patches/hookshot/check_occurrence_capture.py")
CHECK = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(CHECK)
FIXTURE_SPEC = importlib.util.spec_from_file_location("allocation_fixture", Path(__file__).with_name("test_allocation_capture.py"))
FIXTURE = importlib.util.module_from_spec(FIXTURE_SPEC)
FIXTURE_SPEC.loader.exec_module(FIXTURE)


class OccurrenceCaptureTests(unittest.TestCase):
    def setUp(self):
        fixture = FIXTURE.AllocationCaptureTests()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        self.directory = fixture.directory
        self.document = copy.deepcopy(fixture.document)
        self.document.update(schema="open-ocarina.gpu-allocations/v2", selected_occurrence=2,
                             occurrence_filter="exact program, variant and emitted SPIR-V SHA-256", null_descriptor_enabled=True)
        row = self.document["buffers"][0]
        row["guest_range"] = True
        stream = copy.deepcopy(row)
        stream.update(id=1, buffer="0000000000000043", address="0000000000000000", guest_range=False,
                      guest_lo="0000000000000000", guest_hi="0000000000000000", role="renderer-buffer")
        for phase in ("before", "after"):
            stream[phase]["artifact"] = f"buffer-1-{phase}.bin"
            (self.directory / stream[phase]["artifact"]).write_bytes((self.directory / row[phase]["artifact"]).read_bytes())
        self.document["buffers"].append(stream)
        self.document.update(candidate_count=2, candidate_bytes=8192, required_staging_bytes=16384)
        self.document["descriptors"] += [
            {"binding": 0, "element": 0, "type": 6, "buffer": stream["buffer"], "offset": 16, "range": 128},
            {"binding": 7, "element": 1, "type": 7, "buffer": "0" * 16, "offset": 0, "range": CHECK.U64 - 1},
            {"binding": 7, "element": 2, "type": 7, "buffer": "0" * 16, "offset": 0, "range": CHECK.U64 - 1}]

    def validate(self, document=None):
        (self.directory / "allocations.json").write_text(json.dumps(self.document if document is None else document))
        return CHECK.validate_snapshot(self.directory)

    def test_nonguest_stream_and_enabled_nulls_resolve_all_descriptor_inputs(self):
        result = self.validate()
        self.assertEqual(result["selected_occurrence"], 2)
        self.assertEqual(result["defined_null_descriptor_count"], 2)
        self.assertEqual(result["unresolved_descriptor_count"], 0)
        self.assertTrue(result["descriptor_payload_ranges_complete"])
        self.assertTrue(result["table_payload_ranges_complete"])
        self.assertFalse(result["replay_complete"])

    def test_feature_state_must_be_present_and_enabled_for_null_semantics(self):
        document = copy.deepcopy(self.document)
        del document["null_descriptor_enabled"]
        with self.assertRaisesRegex(ValueError, "feature state"):
            self.validate(document)
        self.document["null_descriptor_enabled"] = False
        with self.assertRaisesRegex(ValueError, "must remain unresolved"):
            self.validate()
        self.document["failures"].append("null_descriptor_feature_not_enabled")
        result = self.validate()
        self.assertEqual(result["unresolved_descriptor_count"], 2)
        self.assertEqual(result["defined_null_descriptor_count"], 0)
        self.assertFalse(result["descriptor_payload_ranges_complete"])

    def test_occurrence_is_bounded_and_exact_filter_declaration_required(self):
        for value in (0, 65537, True, "2"):
            document = copy.deepcopy(self.document)
            document["selected_occurrence"] = value
            with self.subTest(value=value), self.assertRaises(ValueError):
                self.validate(document)
        self.document["occurrence_filter"] = "program only"
        with self.assertRaisesRegex(ValueError, "filter identity"):
            self.validate()

    def test_renderer_owned_buffers_must_not_invent_guest_ranges(self):
        for update in ({"guest_lo": "0000001000000000"}, {"guest_range": True}, {"role": "pool-mirror"}):
            document = copy.deepcopy(self.document)
            document["buffers"][1].update(update)
            with self.subTest(update=update), self.assertRaises(ValueError):
                self.validate(document)

    def test_stream_bounds_and_payloads_remain_required(self):
        self.document["descriptors"][1]["range"] = 8192
        with self.assertRaisesRegex(ValueError, "Unresolved descriptor"):
            self.validate()
        self.document["failures"].append("descriptor_range_unresolved")
        self.assertEqual(self.validate()["unresolved_descriptor_count"], 1)
        self.document["descriptors"][1]["range"] = 128
        self.document["buffers"][1]["before"] = None
        result = self.validate()
        self.assertFalse(result["descriptor_payload_ranges_complete"])
        self.assertTrue(result["table_payload_ranges_complete"])

    def test_null_framing_is_not_silently_reinterpreted(self):
        for field, value in (("offset", 4), ("range", 8), ("type", 3)):
            document = copy.deepcopy(self.document)
            document["descriptors"][2][field] = value
            with self.subTest(field=field), self.assertRaisesRegex(ValueError, "null descriptor framing"):
                self.validate(document)

    def test_guest_and_renderer_roles_do_not_merge_physical_ownership(self):
        row = self.document["buffers"][1]
        resolution, _ = CHECK.resolve_table([0x1003], [row])
        self.assertEqual(resolution["unresolved"], 1)
        self.assertFalse(self.document["coverage"]["underlying_import_aliases_resolved"])


if __name__ == "__main__":
    unittest.main()
