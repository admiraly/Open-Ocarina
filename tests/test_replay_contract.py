# SPDX-License-Identifier: GPL-3.0-or-later
import copy
import hashlib
import json
from pathlib import Path
import struct
import tempfile
import unittest

from tools.replay_contract import SCHEMA, compare_outputs, load_capture, validate_capture


def instruction(opcode, *operands):
    return [((len(operands) + 1) << 16) | opcode, *operands]


def synthetic_spirv(extra=(), generator=0):
    # Independently authored framing fixture; never submitted to Vulkan.
    words = [0x07230203, 0x00010300, generator, 32, 0]
    words += instruction(17, 1)
    words += instruction(14, 0, 1)
    words += instruction(15, 5, 1, *struct.unpack("<2I", b"main\0\0\0\0"))
    words += instruction(16, 1, 17, 1, 1, 1)
    words += list(extra)
    words += instruction(19, 2)
    words += instruction(33, 3, 2)
    words += instruction(54, 2, 1, 0, 3)
    words += instruction(248, 4)
    words += instruction(253)
    words += instruction(56)
    return struct.pack(f"<{len(words)}I", *words)


def snapshot(directory, name, data):
    path = directory / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return {"path": name, "size": len(data), "sha256": hashlib.sha256(data).hexdigest()}


def write_manifest(directory, document):
    (directory / "manifest.json").write_text(json.dumps(document))


def update_coverage(directory, document):
    evidence = {"schema": "open-ocarina.buffer-coverage/v1",
        "spirv_sha256": document["pipeline"]["spirv"]["sha256"],
        "method": "Synthetic test producer enumerates its entire declared buffer interface; no game input",
        "descriptor_slots": [[item["set"], item["binding"], item["element"]] for item in document["bindings"]],
        "resource_ids": [item["id"] for item in document["resources"]], "unresolved_accesses": []}
    document["producer"]["coverage_evidence"] = snapshot(directory, "coverage.json", json.dumps(evidence).encode())


def make_capture(directory):
    directory.mkdir(parents=True, exist_ok=True)
    shader = snapshot(directory, "shader.spv", synthetic_spirv())
    initial = snapshot(directory, "memory/initial.bin", b"abcdefgh")
    expected = snapshot(directory, "memory/expected.bin", b"ABCDxxxx")
    push = snapshot(directory, "push.bin", b"")
    document = {
        "schema": SCHEMA,
        "producer": {"name": "synthetic-test", "revision": "a" * 40, "stage": "complete-buffer-capture", "coverage_evidence": {}},
        "pipeline": {"program": "0123456789abcdef", "variant": "1111111111111111", "spirv": shader,
            "entry_point": "main", "local_size": [1, 1, 1], "specialization": [],
            "subgroup": {"required_size": 32, "require_full_subgroups": True},
            "layout": [{"set": 0, "binding": 0, "descriptor_type": "storage_buffer", "count": 1}],
            "translator_stamp": "2222222222222222", "cache_stamp": "3333333333333333",
            "device": {"vendor_id": 0x8086, "device_id": 0, "driver_version": 1, "api_version": 1,
                "driver_uuid": "1" * 32, "device_uuid": "2" * 32,
                "enabled_features": [], "enabled_extensions": []},
            "float_controls": {"signed_zero_inf_nan_preserve_float32": True}},
        "dispatch": {"kind": "direct", "group_count": [1, 1, 1], "present": 1, "submission": 2, "ordinal": 3, "event_id": "dispatch-3"},
        "allocations": [{"id": "allocation-0", "size": 8, "initial": initial, "expected": expected}],
        "resources": [{"id": "storage-0", "kind": "buffer", "allocation": "allocation-0", "offset": 0, "size": 4,
            "access": "read_write", "role": "counter", "comparison": "byte_exact"}],
        "bindings": [{"set": 0, "binding": 0, "element": 0, "resource": "storage-0", "offset": 0, "size": 4}],
        "aliases": [], "push_constants": push,
        "address_model": {"kind": "logical-buffers", "evidence": "Fixture has no physical pointer type/conversion; all claimed inputs explicitly declared"},
        "temporal": {"model": "self-contained-dispatch", "external_dependencies": [], "evidence": "Synthetic fixture has no outside producer; only declared initial bytes are relevant"},
        "synchronization": {
            phase: {"dispatch_event": "dispatch-3", "method": "gpu-visible-command-position", "evidence": "Synthetic fixture; no device execution claimed"}
            for phase in ("before_snapshot", "after_snapshot")}}
    update_coverage(directory, document)
    write_manifest(directory, document)
    return document


class CaptureTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.directory = Path(self.temporary.name) / "capture"
        self.document = make_capture(self.directory)

    def fails(self, pattern):
        write_manifest(self.directory, self.document)
        with self.assertRaisesRegex(ValueError, pattern):
            validate_capture(self.directory)

    def test_contract_integrity_does_not_claim_semantic_completeness_or_replay(self):
        result = validate_capture(self.directory)
        self.assertTrue(result["manifest_consistent"])
        self.assertFalse(result["producer_completeness_independently_proven"])
        self.assertFalse(result["replay_implemented"])
        self.assertEqual(result["written_allocations"], 1)

    def test_identity_only_runtime_event_is_not_a_complete_capture(self):
        self.document["producer"]["stage"] = "identity-only"
        self.fails("incomplete")

    def test_unknown_schema_or_fields_fail_closed(self):
        self.document["schema"] = "future"
        self.fails("Unsupported capture schema")
        self.document["schema"] = SCHEMA
        self.document["ignore_unknown_resource"] = True
        self.fails("unknown")

    def test_wrong_hash_and_missing_or_truncated_snapshots_are_refused(self):
        (self.directory / "memory/initial.bin").write_bytes(b"12345678")
        self.fails("SHA-256 mismatch")
        (self.directory / "memory/initial.bin").write_bytes(b"abc")
        self.fails("length")
        (self.directory / "memory/initial.bin").unlink()
        self.fails("missing")

    def test_snapshot_cannot_cover_only_written_view_instead_of_whole_allocation(self):
        self.document["allocations"][0]["expected"] = snapshot(self.directory, "short.bin", b"ABCD")
        self.fails("complete")

    def test_every_written_allocation_requires_full_expected_contents(self):
        self.document["allocations"][0].pop("expected")
        self.fails("Every written allocation")

    def test_empty_output_oracle_cannot_vacuously_pass(self):
        self.document["resources"][0]["access"] = "read"
        self.fails("writable allocation")

    def test_path_traversal_absolute_paths_and_symlinks_are_refused(self):
        original = self.document["push_constants"]["path"]
        for name in ("../push.bin", "/push.bin", "memory/../push.bin", "memory//push.bin", "memory\\push.bin"):
            with self.subTest(name=name):
                self.document["push_constants"]["path"] = name
                self.fails("path")
        self.document["push_constants"]["path"] = original
        (self.directory / "linked.bin").symlink_to(self.directory / "push.bin")
        self.document["push_constants"]["path"] = "linked.bin"
        self.fails("Symlink")

    def test_symlinked_parent_directory_is_refused(self):
        (self.directory / "linked").symlink_to(self.directory / "memory", target_is_directory=True)
        self.document["allocations"][0]["initial"]["path"] = "linked/initial.bin"
        self.fails("Symlink")

    def test_missing_descriptor_array_element_is_refused(self):
        self.document["pipeline"]["layout"][0]["count"] = 2
        self.fails("Missing descriptor")

    def test_unresolved_or_wrong_shader_coverage_evidence_is_refused(self):
        evidence = json.loads((self.directory / "coverage.json").read_text())
        evidence["unresolved_accesses"] = ["unknown device address"]
        self.document["producer"]["coverage_evidence"] = snapshot(self.directory, "coverage.json", json.dumps(evidence).encode())
        self.fails("Unresolved")
        evidence["unresolved_accesses"] = []
        evidence["spirv_sha256"] = "f" * 64
        self.document["producer"]["coverage_evidence"] = snapshot(self.directory, "coverage.json", json.dumps(evidence).encode())
        self.fails("another shader")

    def test_alias_relationships_must_exactly_match_allocation_overlap(self):
        alias = copy.deepcopy(self.document["resources"][0])
        alias.update(id="alias-1", offset=2)
        self.document["resources"].append(alias)
        binding = copy.deepcopy(self.document["bindings"][0])
        binding.update(element=1, resource="alias-1")
        self.document["bindings"].append(binding)
        self.document["pipeline"]["layout"][0]["count"] = 2
        update_coverage(self.directory, self.document)
        self.fails("aliases")
        self.document["aliases"] = [["storage-0", "alias-1"]]
        write_manifest(self.directory, self.document)
        self.assertTrue(validate_capture(self.directory)["manifest_consistent"])

    def test_false_aliases_and_out_of_bounds_views_are_refused(self):
        self.document["aliases"] = [["storage-0", "storage-0"]]
        self.fails("alias")
        self.document["aliases"] = []
        self.document["resources"][0]["offset"] = 6
        self.fails("beyond")

    def test_physical_address_and_temporal_state_are_explicitly_unsupported(self):
        self.document["address_model"]["kind"] = "physical-addresses"
        self.fails("Physical-address")
        self.document["address_model"]["kind"] = "logical-buffers"
        self.document["temporal"]["external_dependencies"] = ["prior-frame-counter"]
        self.fails("temporal")

    def test_boolean_claim_is_not_address_or_temporal_evidence(self):
        for field in ("address_model", "temporal"):
            old = self.document[field]["evidence"]
            self.document[field]["evidence"] = True
            self.fails("evidence")
            self.document[field]["evidence"] = old

    def test_physical_pointer_opcode_is_detected_despite_logical_manifest_claim(self):
        self.document["pipeline"]["spirv"] = snapshot(self.directory, "shader.spv", synthetic_spirv(instruction(120, 8, 9, 10)))
        update_coverage(self.directory, self.document)
        self.fails("Physical-storage")

    def test_physical_pointer_type_is_conservatively_rejected(self):
        self.document["pipeline"]["spirv"] = snapshot(self.directory, "shader.spv", synthetic_spirv(instruction(32, 8, 5349, 9)))
        update_coverage(self.directory, self.document)
        self.fails("Physical-storage")

    def test_capability_alone_is_not_mistaken_for_actual_physical_pointer_use(self):
        self.document["pipeline"]["spirv"] = snapshot(self.directory, "shader.spv", synthetic_spirv(instruction(17, 5347)))
        update_coverage(self.directory, self.document)
        write_manifest(self.directory, self.document)
        self.assertTrue(validate_capture(self.directory)["manifest_consistent"])

    def test_spirv_layout_and_local_size_must_agree(self):
        self.document["pipeline"]["local_size"] = [64, 1, 1]
        self.fails("local size")
        self.document["pipeline"]["local_size"] = [1, 1, 1]
        extra = instruction(71, 20, 34, 0) + instruction(71, 20, 33, 3)
        self.document["pipeline"]["spirv"] = snapshot(self.directory, "shader.spv", synthetic_spirv(extra))
        update_coverage(self.directory, self.document)
        self.fails("descriptor absent")

    def test_malformed_spirv_instruction_framing_is_refused(self):
        for extra in ([0], [((10 << 16) | 19)]):
            with self.subTest(extra=extra):
                self.document["pipeline"]["spirv"] = snapshot(self.directory, "shader.spv", synthetic_spirv(extra))
                update_coverage(self.directory, self.document)
                self.fails("SPIR-V instruction")

    def test_layout_capacity_and_specialization_duplicates_are_refused(self):
        self.document["pipeline"]["layout"].append({"set": 0, "binding": 1, "count": 65536, "descriptor_type": "storage_buffer"})
        self.fails("bounded contract limit")
        self.document["pipeline"]["layout"].pop()
        self.document["pipeline"]["specialization"] = [{"id": 1, "value_hex": "00"}] * 2
        self.fails("Duplicate")

    def test_device_feature_identity_is_explicit(self):
        self.document["pipeline"]["device"]["enabled_features"] = True
        self.fails("features/extensions")
        self.document["pipeline"]["device"]["enabled_features"] = []
        self.document["pipeline"]["device"]["driver_uuid"] = "unknown"
        self.fails("UUID")

    def test_indirect_dispatch_images_and_float_tolerance_are_not_silently_supported(self):
        self.document["dispatch"]["kind"] = "indirect"
        self.fails("Indirect")
        self.document["dispatch"]["kind"] = "direct"
        self.document["resources"][0]["kind"] = "image"
        self.fails("Image")
        self.document["resources"][0]["kind"] = "buffer"
        self.document["resources"][0]["comparison"] = "float_close"
        self.fails("byte-exact")

    def test_numeric_bool_duplicate_json_and_nonfinite_values_are_refused(self):
        self.document["allocations"][0]["size"] = True
        self.fails("allocation size")
        (self.directory / "manifest.json").write_text('{"schema":1,"schema":2}')
        with self.assertRaisesRegex(ValueError, "Duplicate"):
            load_capture(self.directory)
        (self.directory / "manifest.json").write_text('{"schema":NaN}')
        with self.assertRaisesRegex(ValueError, "Non-finite"):
            load_capture(self.directory)


class ComparisonTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.left, self.right = self.root / "reference", self.root / "candidate"
        self.reference, self.candidate = make_capture(self.left), make_capture(self.right)

    def test_same_declared_outputs_compare_without_semantic_or_performance_claim(self):
        result = compare_outputs(self.left, self.right)
        self.assertTrue(result["outputs_byte_equal"])
        self.assertFalse(result["producer_completeness_independently_proven"])
        self.assertEqual(result["compared_bytes"], 8)

    def test_full_allocation_detects_changes_outside_named_written_view(self):
        self.candidate["allocations"][0]["expected"] = snapshot(self.right, "memory/expected.bin", b"ABCDxxx!")
        write_manifest(self.right, self.candidate)
        result = compare_outputs(self.left, self.right)
        self.assertFalse(result["outputs_byte_equal"])
        self.assertEqual(result["differences"], [{"allocation": "allocation-0", "first_difference": 7}])

    def test_counter_difference_remains_byte_exact(self):
        self.candidate["allocations"][0]["expected"] = snapshot(self.right, "memory/expected.bin", b"BBCDxxxx")
        write_manifest(self.right, self.candidate)
        self.assertFalse(compare_outputs(self.left, self.right)["outputs_byte_equal"])

    def test_different_initial_memory_push_constants_or_dispatch_cannot_compare(self):
        self.candidate["allocations"][0]["initial"] = snapshot(self.right, "memory/initial.bin", b"12345678")
        write_manifest(self.right, self.candidate)
        with self.assertRaisesRegex(ValueError, "contracts differ"):
            compare_outputs(self.left, self.right)
        self.candidate = make_capture(self.right)
        self.candidate["dispatch"]["group_count"] = [2, 1, 1]
        write_manifest(self.right, self.candidate)
        with self.assertRaisesRegex(ValueError, "contracts differ"):
            compare_outputs(self.left, self.right)

    def test_shader_and_subgroup_changes_are_explicitly_reported_not_assumed_equivalent(self):
        self.candidate["pipeline"]["spirv"] = snapshot(self.right, "shader.spv", synthetic_spirv(generator=1))
        self.candidate["pipeline"]["subgroup"] = {"required_size": None, "require_full_subgroups": False}
        update_coverage(self.right, self.candidate)
        write_manifest(self.right, self.candidate)
        result = compare_outputs(self.left, self.right)
        self.assertTrue(result["outputs_byte_equal"])
        self.assertNotEqual(result["reference_spirv_sha256"], result["candidate_spirv_sha256"])
        self.assertFalse(result["producer_completeness_independently_proven"])


if __name__ == "__main__":
    unittest.main()
