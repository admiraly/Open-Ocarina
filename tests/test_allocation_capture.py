# SPDX-License-Identifier: GPL-3.0-or-later
import copy
import hashlib
import importlib.util
import json
from pathlib import Path
import struct
import unittest

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("allocation_checker", ROOT / "patches/hookshot/check_allocation_capture.py")
CHECK = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(CHECK)
FIXTURE_SPEC = importlib.util.spec_from_file_location("page_fixture", Path(__file__).with_name("test_page_table_snapshot.py"))
FIXTURE = importlib.util.module_from_spec(FIXTURE_SPEC)
FIXTURE_SPEC.loader.exec_module(FIXTURE)


class AllocationCaptureTests(unittest.TestCase):
    def setUp(self):
        fixture = FIXTURE.PageTableSnapshotTests()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        self.directory = fixture.root
        self.page_document = fixture.document
        self.write_table([0x1003])
        payload = b"p" * 4096
        digest = hashlib.sha256(payload).hexdigest()
        row = {"id": 0, "buffer": "0000000000000042", "address": "0000000000001003", "size": 4096,
               "guest_lo": "0000001000000000", "guest_hi": "0000001000001000", "memory": "0000000000000050",
               "memory_offset": 256, "memory_piece_count": 1, "memory_piece_handles": ["0000000000000050"], "usage": 3, "role": "pool-mirror", "copy_supported": True}
        for phase in ("before", "after"):
            name = f"buffer-0-{phase}.bin"
            (self.directory / name).write_bytes(payload)
            row[phase] = {"artifact": name, "sha256": digest}
        self.document = {"schema": "open-ocarina.gpu-allocations/v1", "replay_complete": False,
            "recording_part": 3, "recording_sequence": 7, "completed_recording_sequence": 8,
            "program": "1" * 16, "variant": "2" * 16, "spirv_sha256": "3" * 64,
            "candidate_count": 1, "candidate_bytes": 4096, "required_staging_bytes": 8192, "budget_bytes": 32 << 20,
            "table_manifest": "manifest.json", "table_exported": True,
            "before_position": "immediately before selected dispatch", "after_position": "immediately after selected dispatch",
            "after_recorded": True, "mapped_memory_invalidated": True, "coverage": copy.deepcopy(CHECK.COVERAGE),
            "table_resolution": {"mapped": 1, "unresolved": 0, "ambiguous": 0, "partial": 0, "missing_payload": 0},
            "failures": ["images_not_captured", "temporal_state_not_captured", "underlying_import_aliases_unresolved",
                         "cpu_writers_not_quiesced", "late_allocations_require_completed_table_resolution"],
            "buffers": [row], "descriptors": [{"binding": 6, "element": 0, "type": 7, "buffer": row["buffer"], "offset": 0, "range": 4096}]}

    def write_table(self, entries):
        binary = self.directory / "page-table.bin"
        with binary.open("r+b") as output:
            output.write(struct.pack("<" + "Q" * len(entries), *entries))
        with binary.open("rb") as source:
            self.page_document["table"]["sha256"] = hashlib.file_digest(source, "sha256").hexdigest()
        (self.directory / "manifest.json").write_text(json.dumps(self.page_document))

    def validate(self, document=None):
        (self.directory / "allocations.json").write_text(json.dumps(self.document if document is None else document))
        return CHECK.validate_snapshot(self.directory)

    def test_payload_ranges_are_distinct_from_full_replay_and_attestation(self):
        result = self.validate()
        self.assertTrue(result["table_payload_ranges_complete"])
        self.assertFalse(result["replay_complete"])
        self.assertFalse(result["independent_gpu_attestation"])
        self.assertFalse(result["coverage"]["underlying_import_aliases_resolved"])

    def test_pipeline_command_and_timeline_must_match_completed_table(self):
        for field, value in (("recording_part", 4), ("recording_sequence", 9), ("completed_recording_sequence", 6),
                             ("program", "f" * 16), ("spirv_sha256", "f" * 64), ("recording_part", True)):
            document = copy.deepcopy(self.document)
            document[field] = value
            with self.subTest(field=field), self.assertRaises(ValueError):
                self.validate(document)

    def test_every_actual_table_entry_is_checked_including_late_mapping(self):
        self.write_table([0x1003, 0xA007])
        with self.assertRaisesRegex(ValueError, "every copied GPU entry"):
            self.validate()
        self.document["table_resolution"].update(mapped=2, unresolved=1)
        self.document["failures"].append("gpu_table_has_unresolved_pages")
        result = self.validate()
        self.assertFalse(result["table_payload_ranges_complete"])
        self.assertEqual(result["unresolved_page_samples"][0]["device_address"], "000000000000a007")

    def test_unaligned_base_and_partial_final_page_are_never_rounded(self):
        row = copy.deepcopy(self.document["buffers"][0])
        row.update(size=4100, guest_hi="0000001000001004")
        result, _ = CHECK.resolve_table([0x1003, 0x2003, 0x2007], [row])
        self.assertEqual(result, {"mapped": 3, "unresolved": 1, "ambiguous": 0, "partial": 1, "missing_payload": 0})

    def test_overlapping_bda_owner_is_ambiguous_even_with_identical_payloads(self):
        row = self.document["buffers"][0]
        second = dict(row, id=1, buffer="0000000000000043")
        result, _ = CHECK.resolve_table([0x1003], [row, second])
        self.assertEqual(result["ambiguous"], 1)
        second = dict(row, id=1, guest_lo="0000001000001000", guest_hi="0000001000002000")
        result, _ = CHECK.resolve_table([0x1003], [second])
        self.assertEqual(result["unresolved"], 1)

    def test_descriptor_whole_size_and_unknown_owner_are_explicit(self):
        self.document["descriptors"][0]["range"] = CHECK.U64 - 1
        self.assertEqual(self.validate()["unresolved_descriptor_count"], 0)
        self.document["descriptors"][0]["buffer"] = "0000000000000099"
        with self.assertRaisesRegex(ValueError, "Unresolved descriptor"):
            self.validate()
        self.document["failures"].append("descriptor_owner_unknown")
        self.assertEqual(self.validate()["unresolved_descriptor_count"], 1)

    def test_budget_failure_can_be_catalog_only_but_never_payload_complete(self):
        row = self.document["buffers"][0]
        row.update(size=32 << 20, guest_hi=f"{0x1000000000 + (32 << 20):016x}", before=None, after=None)
        self.document.update(candidate_bytes=32 << 20, required_staging_bytes=64 << 20, mapped_memory_invalidated=False)
        self.document["table_resolution"]["missing_payload"] = 1
        self.document["failures"] += ["staging_budget_or_catalog_limit_exceeded", "payloads_not_all_invalidated", "gpu_table_has_missing_payloads"]
        result = self.validate()
        self.assertEqual(result["payload_bytes"], 0)
        self.assertFalse(result["table_payload_ranges_complete"])
        self.document["failures"].remove("staging_budget_or_catalog_limit_exceeded")
        with self.assertRaisesRegex(ValueError, "budget"):
            self.validate()

    def test_payload_tampering_truncation_escape_and_symlink_are_refused(self):
        artifact = self.directory / "buffer-0-before.bin"
        artifact.write_bytes(b"x" * 4096)
        with self.assertRaisesRegex(ValueError, "digest"):
            self.validate()
        artifact.write_bytes(b"x")
        with self.assertRaisesRegex(ValueError, "Truncated"):
            self.validate()
        artifact.unlink()
        artifact.symlink_to(self.directory / "buffer-0-after.bin")
        with self.assertRaisesRegex(ValueError, "symlinks"):
            self.validate()
        self.document["buffers"][0]["before"]["artifact"] = "../escape.bin"
        with self.assertRaisesRegex(ValueError, "path"):
            self.validate()

    def test_missing_usage_sync_accounting_and_overstated_coverage_are_refused(self):
        for mutate in (lambda d: d["buffers"][0].update(usage=2), lambda d: d.update(mapped_memory_invalidated=False),
                       lambda d: d.update(required_staging_bytes=1), lambda d: d["coverage"].update(images=True),
                       lambda d: d.update(table_resolution=dict(d["table_resolution"], mapped=True)),
                       lambda d: d.update(replay_complete=True)):
            document = copy.deepcopy(self.document)
            mutate(document)
            with self.assertRaises(ValueError):
                self.validate(document)


if __name__ == "__main__":
    unittest.main()
