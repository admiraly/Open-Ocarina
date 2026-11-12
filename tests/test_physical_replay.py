import copy
import hashlib
import json
from pathlib import Path
import shutil
import struct
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from tools import physical_replay as replay


def framing_shader():
    """Independent framing fixture; intentionally never submitted to Vulkan."""
    words = [0x07230203, 0x00010600, 0, 64, 0]

    def op(code, *args): words.extend([((len(args) + 1) << 16) | code, *args])

    op(15, 5, 20, 0x6e69616d, 0); op(16, 20, 17, 64, 1, 1)
    op(21, 2, 32, 0); op(30, 3, 2); op(71, 3, 2); op(32, 4, 12, 3)
    for identity, binding in ((5, 0), (6, 1)):
        op(59, 4, identity, 12); op(71, identity, 33, binding); op(71, identity, 34, 0)
    op(25, 7, 2, 1, 0, 0, 0, 1, 0); op(27, 8, 7); op(32, 9, 0, 8); op(59, 9, 10, 0)
    op(71, 10, 33, 2); op(71, 10, 34, 0)
    op(21, 11, 64, 0); op(30, 12, 11); op(72, 12, 0, 35, 48); op(71, 12, 2); op(32, 13, 9, 12); op(59, 13, 14, 9)
    return struct.pack(f"<{len(words)}I", *words)


class PhysicalReplayTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.root = Path(self.temp.name)
        self.capture = self.root / "capture"

        def fake_compile(command, **kwargs):
            Path(command[command.index("-o") + 1]).write_bytes(framing_shader())
            return subprocess.CompletedProcess(command, 0, b"", b"")

        with patch.object(replay.subprocess, "run", side_effect=fake_compile):
            replay.make_fixture(self.capture)

    def tearDown(self): self.temp.cleanup()

    def manifest(self): return json.loads((self.capture / "manifest.json").read_text())

    def save(self, value): (self.capture / "manifest.json").write_text(json.dumps(value))

    def evidence(self, value, name, edit):
        artifact = value["evidence"][name]; path = self.capture / artifact["path"]
        content = json.loads(path.read_text()); edit(content)
        data = json.dumps(content).encode(); path.write_bytes(data)
        artifact.update(size=len(data), sha256=hashlib.sha256(data).hexdigest())

    def validate(self): return replay.validate(self.capture, validate_spirv=False)

    def independence_fixture(self):
        return {"objects": [
            {"tracked": True, "lifetime_id": 1, "size": 4096, "edges": [{"memory_lifetime_id": 10, "resource_offset": 0, "size": 4096, "memory_offset": 0}]},
            {"tracked": True, "lifetime_id": 2, "size": 4096, "edges": [{"memory_lifetime_id": 11, "resource_offset": 0, "size": 4096, "memory_offset": 0}]}],
            "memories": [{"lifetime_id": 10, "kind": "dma-buf", "validated": {"kind": 2, "backing_id": 7, "backing_offset": 0, "acquired_generation": 8}},
                         {"lifetime_id": 11, "kind": "dma-buf", "validated": {"kind": 2, "backing_id": 7, "backing_offset": 4096, "acquired_generation": 3362}}]}

    def test_disjoint_original_intervals_allow_owned_linearization(self):
        report = replay.source_independence(self.independence_fixture(), check_ledger=False)
        self.assertEqual(report["canonical_backing_groups"], 1)
        self.assertEqual(report["overlapping_intervals"], 0)

    def test_differing_global_generations_do_not_hide_real_alias_overlap(self):
        value = self.independence_fixture()
        value["memories"][1]["validated"]["backing_offset"] = 2048
        with self.assertRaisesRegex(ValueError, "overlap"):
            replay.source_independence(value, check_ledger=False)

    def test_internal_sparse_alias_cannot_be_flattened(self):
        value = self.independence_fixture()
        value["objects"][0]["size"] = 8192
        value["objects"][0]["edges"].append({"memory_lifetime_id": 10, "resource_offset": 4096, "size": 4096, "memory_offset": 0})
        with self.assertRaisesRegex(ValueError, "overlap"):
            replay.source_independence(value, check_ledger=False)

    def test_true_original_owner_extent_is_required(self):
        value = self.manifest(); value["views"][-1]["buffer_size"] = 4080; self.save(value)
        with self.assertRaisesRegex(ValueError, "View exceeds"):
            self.validate()

    def test_owned_alias_graph_and_allocation_extents(self):
        state = self.validate()
        self.assertEqual(state["views"]["write-view"]["allocation"], state["views"]["alias-view"]["allocation"])
        self.assertEqual(state["allocations"]["output"]["expected_artifact"]["size"], 4096)
        self.assertFalse(state["producer_completeness_independently_proven"])

    def test_prepare_is_cpu_only_and_binds_native_graph_identity(self):
        with patch.object(replay.subprocess, "Popen") as process:
            report = replay.prepare(self.capture, self.root / "prepared", validate_spirv=False)
            process.assert_not_called()
        control = (self.root / "prepared" / "plan.bin").read_bytes()
        width, digest, graph = replay.control_identity(control)
        self.assertEqual(width, 32)
        self.assertEqual(digest, report["source_manifest_sha256"])
        self.assertEqual(graph, report["allocation_graph"])
        self.assertFalse(report["dispatch_executed"])
        self.assertEqual(report["live_snapshot_atomicity"], "not-established")

    def test_no_cpu_lease_claim_is_needed_for_owned_diagnostic(self):
        value = self.manifest()
        self.evidence(value, "quiescence", lambda item: item.update(cpu_writers="unknown in historical source"))
        self.save(value)
        self.validate()

    def test_historical_scope_cannot_be_inferred_from_frozen_diagnostic(self):
        value = self.manifest(); value.update(mode="historical-dispatch", expected_source="captured-after"); self.save(value)
        with self.assertRaisesRegex(ValueError, "Historical-dispatch execution is not implemented"):
            self.validate()

    def test_raw_component_manifest_is_not_a_replay(self):
        self.save({"schema": "open-ocarina.gpu-allocations/v2", "replay_complete": True})
        with self.assertRaises(ValueError): self.validate()

    def test_same_bytes_do_not_merge_unproven_backing(self):
        value = self.manifest(); value["allocations"][1]["backing"] = copy.deepcopy(value["allocations"][0]["backing"]); self.save(value)
        with self.assertRaisesRegex(ValueError, "Overlapping canonical"):
            self.validate()

    def test_bind_gap_is_rejected(self):
        value = self.manifest()
        self.evidence(value, "backing", lambda item: item["buffer_origins"][0]["pieces"][0].update(size=4095)); self.save(value)
        with self.assertRaisesRegex(ValueError, "bind gap"):
            self.validate()

    def test_generation_mismatch_is_not_hidden_by_equal_payloads(self):
        value = self.manifest()
        self.evidence(value, "backing", lambda item: item["buffer_origins"][0]["pieces"][0].update(generation=2)); self.save(value)
        with self.assertRaisesRegex(ValueError, "canonical backing"):
            self.validate()

    def test_every_nonzero_page_requires_one_relocation(self):
        value = self.manifest(); value["physical_table"]["mappings"] = []; self.save(value)
        with self.assertRaisesRegex(ValueError, "Every nonzero table"):
            self.validate()

    def test_duplicate_page_mapping_rejected(self):
        value = self.manifest(); value["physical_table"]["mappings"] *= 2; self.save(value)
        with self.assertRaises(ValueError): self.validate()

    def test_physical_mapping_cannot_point_outside_canonical_allocation(self):
        value = self.manifest(); value["physical_table"]["mappings"][0]["offset"] = 16; self.save(value)
        with self.assertRaisesRegex(ValueError, "Physical page mapping"):
            self.validate()

    def test_root_must_match_full_pushed_bytes(self):
        value = self.manifest(); value["physical_table"]["root_address"] += 8; self.save(value)
        with self.assertRaisesRegex(ValueError, "Captured push root"):
            self.validate()

    def test_expected_output_must_cover_padding_too(self):
        value = self.manifest(); value["allocations"][1]["expected"]["size"] = 256; self.save(value)
        with self.assertRaises(ValueError): self.validate()

    def test_image_view_cannot_expose_missing_layer(self):
        value = self.manifest(); value["images"][0]["array_layers"] = 2; value["image_views"][0].update(type=5, layer_count=2); self.save(value)
        with self.assertRaisesRegex(ValueError, "uncaptured sampled"):
            self.validate()

    def test_sampler_extensions_are_not_silently_ignored(self):
        value = self.manifest(); value["samplers"][0]["pnext"] = [{"type": 123}]; self.save(value)
        with self.assertRaisesRegex(ValueError, "Sampler extensions"):
            self.validate()

    def test_invalid_sampler_float_bits_rejected(self):
        value = self.manifest(); value["samplers"][0]["core_words"][9] = 0x7fc00000; self.save(value)
        with self.assertRaisesRegex(ValueError, "floating-point"):
            self.validate()

    def test_image_payload_digest_rechecked(self):
        (self.capture / "pixels.bin").write_bytes(bytes(256))
        with self.assertRaisesRegex(ValueError, "SHA-256"):
            self.validate()

    def test_missing_descriptor_not_accepted_as_coverage(self):
        value = self.manifest(); value["bindings"].pop(); self.save(value)
        with self.assertRaises(ValueError): self.validate()

    def test_unknown_target_shader_refused(self):
        value = self.manifest(); value["kind"] = "target"; value["expected_source"] = "none"; self.save(value)
        with self.assertRaisesRegex(ValueError, "exactly reviewed"):
            self.validate()

    def test_execution_requires_exact_plan_approval_before_child(self):
        prepared = self.root / "prepared"; replay.prepare(self.capture, prepared, validate_spirv=False)
        with patch.object(replay.subprocess, "Popen") as process:
            with self.assertRaisesRegex(ValueError, "digest authorization"):
                replay.execute(prepared, self.root / "out", self.root / "helper", "0" * 64, self.root / "driver", "0" * 64)
            process.assert_not_called()

    def test_alternative_policy_requires_separate_authorization(self):
        prepared = self.root / "prepared"; summary = replay.prepare(self.capture, prepared, policy=16, validate_spirv=False)
        with self.assertRaisesRegex(ValueError, "additional exact-policy"):
            replay.execute(prepared, self.root / "out", self.root / "helper", "0" * 64, self.root / "driver", summary["plan_sha256"])

    def test_frozen_payload_mutation_prevents_execution(self):
        prepared = self.root / "prepared"; summary = replay.prepare(self.capture, prepared, validate_spirv=False)
        item = next((prepared / "inputs").iterdir()); item.chmod(0o644); item.write_bytes(b"modified")
        with self.assertRaisesRegex(ValueError, "changed after approval"):
            replay.execute(prepared, self.root / "out", self.root / "helper", "0" * 64, self.root / "driver", summary["plan_sha256"])

    def test_frozen_diagnostic_can_establish_baseline_without_after_claim(self):
        value = self.manifest(); value["expected_source"] = "none"
        for allocation in value["allocations"]: allocation["expected"] = None
        self.save(value); summary = replay.prepare(self.capture, self.root / "prepared", validate_spirv=False)
        self.assertEqual(summary["expected_source"], "none")
        self.assertEqual(summary["live_snapshot_atomicity"], "not-established")

    def test_native_cpu_check_path_is_before_vulkan_instance_creation(self):
        source = (replay.ROOT / "tools" / "vulkan_physical_replay.c").read_text()
        self.assertLess(source.index("if (!execute)"), source.index("vkCreateInstance"))
        self.assertEqual(source.count("vkCmdDispatch("), 1)
        self.assertNotIn("DISABLE_OPTIMIZATION", source)
        self.assertIn("samplerAnisotropy = supported.features.samplerAnisotropy", source)

    def results(self, right_data=None):
        directories = [self.root / "left-result", self.root / "right-result"]
        for directory in directories: directory.mkdir()
        for side, directory in enumerate(directories):
            rows = []
            for index, data in enumerate((b"untouched", b"complete padding too")):
                if side and index == 1 and right_data is not None: data = right_data
                path = directory / f"allocation-{index}.bin"; path.write_bytes(data)
                rows.append({"path": path.name, "size": len(data), "sha256": hashlib.sha256(data).hexdigest()})
            result = {"schema": "open-ocarina.physical-replay-result/v1", "dispatch_executed": True,
                "source_manifest_sha256": "0" * 64, "native_binary_sha256": "1" * 64,
                "driver": {"library_sha256": "2" * 64}, "native": {"device_uuid": "3" * 32, "driver_uuid": "4" * 32, "device_vendor": 32902, "device_id": 22176},
                "allocation_graph": [{"id": "input", "size": 9}, {"id": "output", "size": 20}], "allocations": rows, "policy": 32}
            (directory / "result.json").write_text(json.dumps(result))
        return directories

    def test_complete_output_comparison_includes_untouched_padding(self):
        left, right = self.results(b"complete padding toX")
        result = replay.compare(left, right)
        self.assertFalse(result["owned_frozen_outputs_match"])
        self.assertEqual(result["differences"], [{"allocation_index": 1, "first_difference": 19}])

    def test_output_comparison_cannot_drop_an_allocation(self):
        left, right = self.results()
        path = right / "result.json"; value = json.loads(path.read_text()); value["allocations"].pop(); path.write_text(json.dumps(value))
        with self.assertRaisesRegex(ValueError, "complete frozen input graph"):
            replay.compare(left, right)

    def test_output_mutation_is_rehashed(self):
        left, right = self.results(); (right / "allocation-0.bin").write_bytes(b"different")
        with self.assertRaisesRegex(ValueError, "SHA-256"):
            replay.compare(left, right)

    def test_different_gpu_or_native_build_is_not_silently_compared(self):
        left, right = self.results(); path = right / "result.json"; value = json.loads(path.read_text()); value["native"]["device_uuid"] = "9" * 32; path.write_text(json.dumps(value))
        with self.assertRaisesRegex(ValueError, "devices"):
            replay.compare(left, right)

    def test_native_optimized_build_and_cpu_only_control_roundtrip(self):
        if not shutil.which("cc") or not shutil.which("pkg-config"):
            self.skipTest("Native compiler/pkg-config unavailable")
        if subprocess.run(["pkg-config", "--exists", "vulkan"]).returncode:
            self.skipTest("Vulkan development package unavailable")
        prepared = self.root / "native-plan"; replay.prepare(self.capture, prepared, validate_spirv=False)
        helper = self.root / "native-helper"; receipt = replay.build(helper)
        self.assertIn("-O2", receipt["flags"]); self.assertIn("-DNDEBUG", receipt["flags"])
        child = subprocess.run([str(helper), str(prepared / "plan.bin"), str(prepared), "--check-only"], check=True, capture_output=True, text=True, timeout=10)
        self.assertFalse(json.loads(child.stdout)["dispatch_executed"])

    def test_native_control_rejects_wrapped_root_offset_before_vulkan(self):
        if not shutil.which("cc") or not shutil.which("pkg-config"):
            self.skipTest("Native compiler/pkg-config unavailable")
        if subprocess.run(["pkg-config", "--exists", "vulkan"]).returncode:
            self.skipTest("Vulkan development package unavailable")
        prepared = self.root / "native-plan"
        replay.prepare(self.capture, prepared, validate_spirv=False)
        helper = self.root / "native-helper"
        replay.build(helper)
        original = (prepared / "plan.bin").read_bytes()
        offset = 60  # Fixed version-two header, followed by two counted paths.
        for _ in range(2):
            count = struct.unpack_from("<I", original, offset)[0]
            offset += 4 + count
        push_size = struct.unpack_from("<I", original, offset)[0]
        root_field = offset + 4
        self.assertEqual(struct.unpack_from("<I", original, root_field)[0], 48)
        for root in (0xfffffff8, push_size, push_size - 4):
            with self.subTest(root_offset=root):
                mutated = bytearray(original)
                struct.pack_into("<I", mutated, root_field, root)
                control = prepared / "invalid-root.bin"
                control.write_bytes(mutated)
                child = subprocess.run([str(helper), str(control), str(prepared), "--check-only"],
                                       capture_output=True, text=True, timeout=10)
                self.assertEqual(child.returncode, 2, child.stdout + child.stderr)
                self.assertIn("Invalid or unsupported replay", child.stderr)
                self.assertNotIn("native_plan_validated", child.stdout)


if __name__ == "__main__": unittest.main()
