import copy
import hashlib
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest


SCRIPT = Path(__file__).resolve().parents[1] / "patches" / "hookshot" / "check_page_table_patch.py"
SPEC = importlib.util.spec_from_file_location("page_snapshot_check", SCRIPT)
CHECK = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(CHECK)


class PageTableSnapshotTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        binary = self.root / "page-table.bin"
        with binary.open("wb") as target:
            target.truncate(CHECK.TABLE_BYTES)
        with binary.open("rb") as target:
            digest = hashlib.file_digest(target, "sha256").hexdigest()
        self.document = {
            "schema": "open-ocarina.gpu-page-table/v1",
            "producer": {"stage": "page-table-only", "replay_complete": False},
            "pipeline": {"program": "1" * 16, "variant": "2" * 16, "spirv_sha256": "3" * 64},
            "command_position": "immediately before selected compute dispatch, after descriptor and push binding",
            "dispatch": {"kind": "direct", "group_count": [2, 1, 1], "present_observed": 4, "identity_event_id": None},
            "submission": {"recording_part": 3, "logical_value": 4, "recording_sequence": 7, "completed_recording_sequence": 8},
            "synchronization": {"source": "actual GPU guest_pages_buffer", "prior_writes_to_copy_barrier": True,
                "copy_to_host_barrier": True, "mapped_memory_invalidated": True,
                "successful_exact_recording_submission": True, "recording_timeline_completion_checked": True},
            "table": {"artifact": "page-table.bin", "size": CHECK.TABLE_BYTES, "entry_count": CHECK.TABLE_BYTES // 8,
                "entry_bytes": 8, "page_size": 4096, "byte_order": "little", "root_device_address": "0000000000001000",
                "root_push_offset": 48, "sha256": digest, "windows": [
                    {"guest_base": "1000000000", "size": 8589934592, "first_entry": 0},
                    {"guest_base": "7f00000000", "size": 4294967296, "first_entry": 2097152}]},
            "coverage": {"gpu_page_table": True, "referenced_allocation_contents": False,
                "descriptor_contents": False, "gpu_outputs": False, "temporal_state": False}}

    def validate(self, document=None):
        (self.root / "manifest.json").write_text(json.dumps(document or self.document))
        return CHECK.validate_snapshot(self.root)

    def test_integrity_is_not_complete_replay_or_gpu_attestation(self):
        result = self.validate()
        self.assertTrue(result["artifact_integrity_verified"])
        self.assertFalse(result["replay_complete"])
        self.assertFalse(result["independent_gpu_attestation"])

    def test_missing_or_premature_exact_recording_completion_is_refused(self):
        for field, value in (("recording_sequence", 0), ("completed_recording_sequence", 6), ("recording_part", True),
                             ("logical_value", 1 << 64)):
            document = copy.deepcopy(self.document)
            document["submission"][field] = value
            with self.subTest(field=field), self.assertRaises(ValueError):
                self.validate(document)

    def test_missing_command_position_and_overstated_dispatch_correlation_are_refused(self):
        document = copy.deepcopy(self.document)
        del document["command_position"]
        with self.assertRaisesRegex(ValueError, "command position"):
            self.validate(document)
        for field, value in (("identity_event_id", "not-the-identity-observer-event"), ("group_count", [True, 1, 1]),
                             ("present_observed", -1), ("kind", "indirect")):
            document = copy.deepcopy(self.document)
            document["dispatch"][field] = value
            with self.subTest(field=field), self.assertRaises(ValueError):
                self.validate(document)

    def test_stale_cpu_mapping_and_missing_submission_evidence_are_refused(self):
        for field, value in (("source", "CPU guest_pages map"), ("successful_exact_recording_submission", False),
                             ("copy_to_host_barrier", False), ("mapped_memory_invalidated", False)):
            document = copy.deepcopy(self.document)
            document["synchronization"][field] = value
            with self.subTest(field=field), self.assertRaises(ValueError):
                self.validate(document)

    def test_table_truncation_and_tampering_are_refused(self):
        with (self.root / "page-table.bin").open("r+b") as target:
            target.write(b"x")
        with self.assertRaisesRegex(ValueError, "digest"):
            self.validate()
        with (self.root / "page-table.bin").open("r+b") as target:
            target.truncate(8)
        with self.assertRaisesRegex(ValueError, "truncated"):
            self.validate()

    def test_symlink_or_escaping_artifact_is_refused(self):
        document = copy.deepcopy(self.document)
        document["table"]["artifact"] = "../other.bin"
        with self.assertRaises(ValueError):
            self.validate(document)
        (self.root / "page-table.bin").unlink()
        (self.root / "page-table.bin").symlink_to(self.root / "absent")
        with self.assertRaisesRegex(ValueError, "regular"):
            self.validate()

    def test_overstated_coverage_and_wrong_windows_are_refused(self):
        document = copy.deepcopy(self.document)
        document["coverage"]["gpu_outputs"] = True
        with self.assertRaises(ValueError):
            self.validate(document)
        document = copy.deepcopy(self.document)
        document["table"]["windows"][1]["first_entry"] = 0
        with self.assertRaisesRegex(ValueError, "window"):
            self.validate(document)


if __name__ == "__main__":
    unittest.main()
