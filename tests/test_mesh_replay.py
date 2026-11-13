# SPDX-License-Identifier: GPL-3.0-or-later
import copy
import hashlib
import json
from pathlib import Path
import struct
import tempfile
import unittest
from unittest.mock import patch

from tools import mesh_replay as replay


def artifact(root, name, data):
    (root / name).write_bytes(data)
    return {"path": name, "size": len(data), "sha256": hashlib.sha256(data).hexdigest()}


def chosen_capture(root, certificate=True):
    """Synthetic metadata/bytes only; never a real snapshot or execution input."""
    ubo = artifact(root, "uniform.bin", bytes(64))
    ssbo = artifact(root, "storage.bin", bytes(4))
    launch = artifact(root, "launch.bin", struct.pack("<3I", 256, 4, 2))
    event = {"program": "d8e59341cf4f629c", "variant": "0c8f08626a9d98a4", "spirv_sha256": replay.ORIGINAL,
             "actual_site_confirmed": True, "selected_occurrence": 1, "recording_sequence": 7,
             "completed_recording_sequence": 7, "recording_part": 0, "logical_value": 11}
    bindings = []
    for _, binding, element, kind in replay.EXPECTED_BINDINGS:
        snapshot = copy.deepcopy(ubo if kind == 6 else ssbo)
        bindings.append({"set": 0, "binding": binding, "element": element, "type": kind,
                         "range_bytes": snapshot["size"], "snapshot": snapshot, "original_buffer_offset": 0,
                         "backing_generation": None})
    document = {"schema": replay.CAPTURE_SCHEMA, "event": event, "subgroup": {"required_size": 32, "require_full_subgroups": False},
                "launch": {"command": "vkCmdDrawMeshTasksIndirectEXT", "draw_count": 1, "stride": 12, "snapshot": launch},
                "bindings": bindings, "raw_coverage": {"cpu_writers": False, "import_aliases": False}}
    if certificate:
        proofs = [artifact(root, f"proof{i}.json", json.dumps({"synthetic_test_fixture": True, "index": i}).encode()) for i in range(2)]
        views = [{"binding": row["binding"], "element": row["element"],
                  "buffer_lifetime": f"fixture-buffer-{row['binding']}-{row['element']}",
                  "pieces": [{"view_offset": 0, "size": row["range_bytes"], "backing_id": "fixture-backing",
                              "generation": 0, "backing_offset": 0}]} for row in bindings]
        evidence = {"schema": replay.COVERAGE_SCHEMA,
                    "event_sha256": hashlib.sha256(json.dumps(event, sort_keys=True, separators=(",", ":")).encode()).hexdigest(),
                    "ownership_complete": True, "cpu_writers_quiescent": True, "import_aliases_resolved": True,
                    "ordered_gpu_before_snapshot": True, "unresolved": [], "driver_policy": "runtime",
                    "launch_sha256": launch["sha256"], "source_revision": "synthetic-test",
                    "source_binary_sha256": "1" * 64, "proof_sources": proofs, "views": views,
                    "slots": sorted([row["set"], row["binding"], row["element"], row["type"], row["range_bytes"], row["snapshot"]["sha256"]]
                                    for row in bindings)}
        document["coverage_evidence"] = artifact(root, "coverage.json", json.dumps(evidence).encode())
    path = root / "capture.json"
    path.write_text(json.dumps(document))
    return path, document


class MeshReplayTests(unittest.TestCase):
    def test_fixture_full_counts_empty_primitives_and_origin(self):
        origin, groups = [128, 0, 0], [2, 1, 1]
        initial = replay.initialized_capture(groups)
        body = replay.instrumenter.fixture_expected_capture(origin, groups)
        result = initial[:64] + body + initial[-64:]
        checked = replay.check_capture(result, groups)
        self.assertEqual(checked["counts"], [{"vertices": 128, "primitives": 128}, {"vertices": 128, "primitives": 0}])
        with self.assertRaisesRegex(ValueError, "counts"):
            replay.check_capture(initial, groups)
        other_body = replay.instrumenter.fixture_expected_capture([127, 0, 0], groups)
        with self.assertRaisesRegex(ValueError, "differs"):
            replay.compare_bytes(result, initial[:64] + other_body + initial[-64:])

    def test_guards_counts_writtenness_padding_and_unwritten_values(self):
        initial = replay.initialized_capture([1, 1, 1])
        data = initial[:64] + replay.instrumenter.fixture_expected_capture([1, 0, 0], [1, 1, 1]) + initial[-64:]
        words = list(struct.unpack(f"<{len(data)//4}I", data))
        # Outer guard, inner guards, lane counts, vertex marker, primitive marker,
        # padding and the first non-emitted primitive's data must all be checked.
        positions = [0, len(words) - 1, 16, 16 + 11216, 16 + 16, 16 + 79,
                     16 + 5712, 16 + 5712 + 5119, 16 + 5712 + 5120,
                     16 + 5712 + 5123, 16 + 5203, 16 + 5204]
        for index in positions:
            changed = words.copy()
            changed[index] ^= 1
            with self.subTest(index=index), self.assertRaises(ValueError):
                replay.check_capture(struct.pack(f"<{len(changed)}I", *changed), [1, 1, 1])
        changed = bytearray(data)
        changed[64 + 320] ^= 1
        replay.check_capture(bytes(changed), [1, 1, 1])  # Defined data needs differential comparison.
        with self.assertRaisesRegex(ValueError, "differs"):
            replay.compare_bytes(data, bytes(changed))

    def test_window_limits_zero_grid_and_unsigned_overflow(self):
        replay.window([256, 4, 2], [128, 0, 0], [2, 1, 1])
        for grid, origin, groups in (([0, 1, 1], [0, 0, 0], [1, 1, 1]),
                                     ([256, 1, 1], [0, 0, 0], [33, 1, 1]),
                                     ([256, 1, 1], [255, 0, 0], [2, 1, 1]),
                                     ([0xffffffff, 1, 1], [0xffffffff, 0, 0], [1, 1, 1])):
            with self.assertRaises(ValueError):
                replay.window(grid, origin, groups)

    def test_chosen_readonly_graph_preserves_unknown_history(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path, _ = chosen_capture(root, certificate=False)
            state = replay.capture_inputs(path, "runtime", "frozen-logical-readonly")
            self.assertFalse(state["historical_draw_reproduced"])
            self.assertEqual(state["historical_atomicity"], "not-established")
            self.assertEqual(len(state["bindings"]), 11)
            with self.assertRaises(ValueError):
                replay.capture_inputs(path, "runtime", "historical-draw")

    def test_historical_gates_and_alias_consistency(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path, document = chosen_capture(root)
            state = replay.capture_inputs(path, "runtime", "historical-draw")
            self.assertEqual(state["grid"], [256, 4, 2])
            coverage = json.loads((root / "coverage.json").read_text())
            for field in ("ownership_complete", "cpu_writers_quiescent", "import_aliases_resolved", "ordered_gpu_before_snapshot"):
                broken = coverage | {field: False}
                document["coverage_evidence"] = artifact(root, "coverage.json", json.dumps(broken).encode())
                path.write_text(json.dumps(document))
                with self.subTest(field=field), self.assertRaises(ValueError):
                    replay.capture_inputs(path, "runtime", "historical-draw")
            # A changed overlapping backing slice cannot be called a coherent historical snapshot.
            document["bindings"][-1]["snapshot"] = artifact(root, "different.bin", b"\xff" * 4)
            coverage["slots"] = sorted([row["set"], row["binding"], row["element"], row["type"], row["range_bytes"], row["snapshot"]["sha256"]]
                                       for row in document["bindings"])
            document["coverage_evidence"] = artifact(root, "coverage.json", json.dumps(coverage).encode())
            path.write_text(json.dumps(document))
            with self.assertRaisesRegex(ValueError, "Aliased"):
                replay.capture_inputs(path, "runtime", "historical-draw")
            # The explicitly chosen readonly graph makes no historical alias claim.
            replay.capture_inputs(path, "runtime", "frozen-logical-readonly")

    def test_ranges_slots_tampering_and_missing_inputs_fail(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path, document = chosen_capture(root, certificate=False)
            original = copy.deepcopy(document)
            changes = [("missing", lambda value: value["bindings"].pop()),
                       ("wrong slot", lambda value: value["bindings"][0].update(binding=8)),
                       ("truncated view", lambda value: value["bindings"][0].update(range_bytes=32)),
                       ("offset overflow", lambda value: value["bindings"][0].update(original_buffer_offset=(1 << 64) - 1))]
            for name, change in changes:
                broken = copy.deepcopy(original)
                change(broken)
                path.write_text(json.dumps(broken))
                with self.subTest(name=name), self.assertRaises(ValueError):
                    replay.capture_inputs(path, "runtime", "frozen-logical-readonly")
            path.write_text(json.dumps(original))
            (root / "uniform.bin").write_bytes(bytes([1]) * 64)
            with self.assertRaisesRegex(ValueError, "SHA-256"):
                replay.capture_inputs(path, "runtime", "frozen-logical-readonly")

    def test_native_plan_deduplicates_only_equal_immutable_views(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path, _ = chosen_capture(root, certificate=False)
            state = replay.capture_inputs(path, "runtime", "frozen-logical-readonly")
            info = replay.write_native_plan(root / "native.bin", state, [128, 0, 0], [2, 1, 1])
            data = (root / "native.bin").read_bytes()
            header = struct.unpack_from("<8s16I", data)
            self.assertEqual(header[0], replay.MAGIC)
            self.assertEqual(header[1:8], (1, 2, 11, 0, 89984, 64, 89856))
            self.assertEqual(header[8:11], (2, 1, 1))
            self.assertEqual(header[11:14], (128, 0, 0))
            self.assertEqual(info["unique_input_buffers"], 2)
            self.assertEqual(data[-89984:], replay.initialized_capture([2, 1, 1]))

    def test_real_null_descriptors_have_no_synthetic_bytes_or_ownership(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path, document = chosen_capture(root, certificate=False)
            document["features"] = {"nullDescriptor": True}
            for row in document["bindings"]:
                if row["binding"] == 8 and row["element"] in (1, 2):
                    row.update(null_descriptor=True, snapshot=None, range_bytes=None,
                               buffer_handle="0000000000000000", original_buffer_offset=0,
                               descriptor_range=(1 << 64) - 1, allocation_row=None,
                               buffer_lifetime=None, backing_generation=None)
            path.write_text(json.dumps(document))
            state = replay.capture_inputs(path, "runtime", "frozen-logical-readonly")
            info = replay.write_native_plan(root / "native.bin", state, [0, 0, 0], [1, 1, 1])
            self.assertEqual(info["descriptor_elements"], 11)
            self.assertEqual(info["null_descriptor_elements"], 2)
            self.assertEqual(info["input_layout"][-2:], [[0, 8, 1, 7, 0xffffffff, 0], [0, 8, 2, 7, 0xffffffff, 0]])
            original = copy.deepcopy(document)
            for field, value in (("range_bytes", 0), ("snapshot", {"fake": "bytes"}), ("original_buffer_offset", 4),
                                 ("backing_generation", 0)):
                document = copy.deepcopy(original)
                document["bindings"][-1][field] = value
                path.write_text(json.dumps(document))
                with self.subTest(field=field), self.assertRaises(ValueError):
                    replay.capture_inputs(path, "runtime", "frozen-logical-readonly")
            original["features"]["nullDescriptor"] = False
            path.write_text(json.dumps(original))
            with self.assertRaisesRegex(ValueError, "feature profile"):
                replay.capture_inputs(path, "runtime", "frozen-logical-readonly")

    def test_unknown_modules_fail_before_instrumentation_or_native_build(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path, _ = chosen_capture(root, certificate=False)
            wrong = root / "wrong.spv"
            wrong.write_bytes(b"independent invalid module fixture")
            with patch.object(replay.instrumenter, "prepare") as instrument, patch.object(replay, "build_helper") as build:
                with self.assertRaisesRegex(ValueError, "exact reviewed"):
                    replay.prepare(root / "output", capture=path, original=wrong, conditional=wrong,
                                   mode="frozen-logical-readonly", driver_profile="legacy-5ea", driver_policy="runtime")
                instrument.assert_not_called()
                build.assert_not_called()

    def test_prepare_only_boundary_keeps_helper_executable_and_pins_artifacts(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "prepared"

            def fixture(destination):
                destination.mkdir()
                source = b"mock source; not a shader"
                module = b"mock instrumented source; not a shader"
                (destination / "source.spv").write_bytes(source)
                (destination / "instrumented.spv").write_bytes(module)
                report = {"source_module_sha256": hashlib.sha256(source).hexdigest()}
                (destination / "report.json").write_text(json.dumps(report))
                return report

            def helper(destination):
                path = destination / "mesh-replay-helper"
                path.write_bytes(b"mock native validator; never executed")
                return path

            def cpu_check(command, env, destination, stem, timeout):
                self.assertEqual(stem, "validate-native-plan")
                self.assertEqual(command[-1], "--validate")
                return json.dumps({"output_profile": replay.D8_PROFILE, "gpu_executed": False, "native_plan_validated": True})

            with patch.object(replay.instrumenter, "prepare_fixture", side_effect=fixture), \
                    patch.object(replay, "validate_instrumentation"), patch.object(replay, "build_helper", side_effect=helper), \
                    patch.object(replay, "run_process", side_effect=cpu_check), patch.object(replay, "driver_identity") as driver:
                plan = replay.prepare(output, fixture=True, origin=[128, 0, 0], groups=[2, 1, 1],
                                      driver_profile="legacy-5ea", driver_policy="runtime")
                self.assertTrue(plan["prepare_only"])
                driver.assert_not_called()
                self.assertEqual((output / "mesh-replay-helper").stat().st_mode & 0o777, 0o555)
                reviewed = replay.digest(output / "prepared-plan.json")
                replay.checked_prepared(output, reviewed)
                self.assertEqual(set(plan["sources"]), set(replay.DEPENDENCY_SOURCES))
                manifest = output / "prepared-plan.json"
                for mutation in (lambda value: value["sources"].pop("tools/spirv_contract.py"),
                                 lambda value: value["driver_profile"].update(library_sha256="0" * 64),
                                 lambda value: value.update(driver_policy={"name": "arbitrary"}),
                                 lambda value: value.update(output_profile="arbitrary"),
                                 lambda value: value.update(schema="open-ocarina.prepared-native-mesh-replay/v1")):
                    broken = copy.deepcopy(plan)
                    mutation(broken)
                    manifest.chmod(0o644)
                    manifest.write_text(json.dumps(broken))
                    with self.assertRaises(ValueError):
                        replay.checked_prepared(output, replay.digest(manifest))
                for field, value in (("process_seconds", 120), ("process_seconds", 31), ("fence_seconds", 120),
                                     ("draws_per_helper", 2), ("groups_per_draw", 64), ("max_total_input_bytes", 1)):
                    broken = copy.deepcopy(plan)
                    broken["limits"][field] = value
                    manifest.write_text(json.dumps(broken))
                    with self.subTest(limit=field, value=value), self.assertRaisesRegex(ValueError, "exact mesh profile budget"):
                        replay.checked_prepared(output, replay.digest(manifest))
                manifest.write_text(json.dumps(plan, indent=2) + "\n")
                manifest.chmod(0o444)
                self.assertEqual(replay.digest(manifest), reviewed)
                for dependency in replay.DEPENDENCY_SOURCES:
                    original_digest = replay.digest

                    def changed_import(path, changed=dependency):
                        return "0" * 64 if path == replay.ROOT / changed else original_digest(path)

                    with self.subTest(dependency=dependency), patch.object(replay, "digest", side_effect=changed_import):
                        with self.assertRaises(ValueError):
                            replay.checked_prepared(output, reviewed)
                (output / "execution-started.json").write_text("{}")
                with self.assertRaisesRegex(ValueError, "already been used"):
                    replay.checked_prepared(output, reviewed)
                (output / "execution-started.json").unlink()
                (output / "inputs.bin").chmod(0o644)
                (output / "inputs.bin").write_bytes(b"tampered fixture inputs")
                with self.assertRaises(ValueError):
                    replay.checked_prepared(output, reviewed)

    def test_native_helper_has_separate_cpu_validation_and_single_bounded_draw(self):
        source = (replay.ROOT / "tools" / "vulkan_mesh_replay.c").read_text()
        self.assertLess(source.index("if (argc == 7)"), source.index("vkCreateInstance("))
        self.assertEqual(source.count("draw(command, plan.groups[0], plan.groups[1], plan.groups[2]);"), 1)
        self.assertIn("UINT64_C(2000000000)", source)
        self.assertIn("_Exit(3)", source)
        self.assertNotIn("vkQueueWaitIdle", source)
        self.assertNotIn("vkDeviceWaitIdle", source)
        self.assertNotIn("vkCmdDispatch", source)

    def test_explicit_driver_profile_and_safe_policy_matrix(self):
        selected = replay.validate_driver_profile("cache-safe-e5", "native-safe")
        self.assertEqual(selected["library_sha256"], "e5a1305ea3b423a18fccbb945aaedd32f8add303926fcc39276ee01032cd456f")
        self.assertEqual(selected["icd_sha256"], "14f4eda1b6fd6cbf07d61edeeffdaaede166f4c09d165fec9f661e9f90556ad3")
        for name, policy in ((None, "native-safe"), ("arbitrary", "native-safe"), ("cache-safe-e5", "runtime"),
                             ("cache-safe-e5", "diagnostic"), ("cache-safe-e5", "arbitrary"), ("legacy-5ea", "native-safe")):
            with self.subTest(name=name, policy=policy), self.assertRaises(ValueError):
                replay.validate_driver_profile(name, policy)
        with patch.object(replay.instrumenter, "prepare_fixture") as instrument:
            with self.assertRaisesRegex(ValueError, "driver profile"):
                replay.prepare(Path("unused-output"), fixture=True)
            instrument.assert_not_called()
        with patch.object(replay, "controlled_driver_environment", return_value=({}, {"controlled_options": {
                "HS_SPILL_BATCH": "1", "HS_PUSH_SHADOW": "1", "shader_spilling_rate": "1", "HS_FORCE_SIMD8": "1"}})):
            with self.assertRaisesRegex(ValueError, "exact named"):
                replay.validate_driver_profile("cache-safe-e5", "native-safe")

    def test_wrong_driver_or_icd_cannot_start_helper(self):
        selected = replay.validate_driver_profile("cache-safe-e5", "native-safe")
        plan = {"output_profile": replay.D8_PROFILE, "driver_profile": selected}
        for identity in ({"library_sha256": "0" * 64, "icd_sha256": selected["icd_sha256"]},
                         {"library_sha256": selected["library_sha256"], "icd_sha256": "0" * 64}):
            with patch.object(replay, "checked_prepared", return_value=(Path("unused"), plan)), \
                    patch.object(replay, "driver_identity", return_value=identity), patch.object(replay, "run_process") as run:
                with self.assertRaisesRegex(ValueError, "driver differs"):
                    replay.execute(Path("unused"), "1" * 64, Path("unused-driver.json"))
                run.assert_not_called()

    def test_profile96_full_empty_tail_and_separate_layout(self):
        profile = replay.PROFILE96
        groups = [2, 1, 1]
        initial = replay.initialized_capture(groups, profile)
        body = replay.instrumenter96.fixture_expected_capture([96, 0, 0], groups)
        data = initial[:64] + body + initial[-64:]
        self.assertEqual(len(data), 73856)
        self.assertEqual(replay.check_capture(data, groups, profile)["counts"],
                         [{"vertices": 96, "primitives": 96}, {"vertices": 96, "primitives": 0}])
        with self.assertRaises(ValueError):
            replay.check_capture(data, groups, replay.D8_PROFILE)
        words = list(struct.unpack(f"<{len(data)//4}I", data))
        for index in (16 + 4688, 16 + 8911, 16 + 8912, 16 + 9199, 16 + 4307, 16 + 9200):
            changed = words.copy()
            changed[index] ^= 1
            with self.subTest(index=index), self.assertRaises(ValueError):
                replay.check_capture(struct.pack(f"<{len(changed)}I", *changed), groups, profile)

    def test_profile96_capture_schema_slots_and_native_magic_are_distinct(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path, document = chosen_capture(root, certificate=False)
            profile = replay.output_profile(replay.PROFILE96)
            document.update(schema=profile["capture_schema"], profile=replay.PROFILE96,
                            runtime_execution_authorized=False, equivalence_proven=False, replay_complete=False)
            document["event"].update(program=profile["program"], variant=profile["variant"], spirv_sha256=profile["modules"]["original"][0])
            row = document["bindings"][4]
            row.update(type=7, range_bytes=4, snapshot=artifact(root, "profile96-storage.bin", bytes(4)))
            path.write_text(json.dumps(document))
            state = replay.capture_inputs(path, "native-safe", "frozen-logical-readonly", replay.PROFILE96)
            replay.write_native_plan(root / "native.bin", state, [96, 0, 0], [2, 1, 1], replay.PROFILE96)
            header = struct.unpack_from("<8s16I", (root / "native.bin").read_bytes())
            self.assertEqual(header[0], b"OOMESH96")
            self.assertEqual(header[5:8], (73856, 64, 73728))
            with self.assertRaisesRegex(ValueError, "schema"):
                replay.capture_inputs(path, "native-safe", "frozen-logical-readonly", replay.D8_PROFILE)
            for field in ("runtime_execution_authorized", "equivalence_proven", "replay_complete"):
                document[field] = True
                path.write_text(json.dumps(document))
                with self.subTest(field=field), self.assertRaisesRegex(ValueError, "producer flags"):
                    replay.capture_inputs(path, "native-safe", "frozen-logical-readonly", replay.PROFILE96)
                document[field] = False
            document.pop("profile")
            path.write_text(json.dumps(document))
            with self.assertRaisesRegex(ValueError, "profile96"):
                replay.capture_inputs(path, "native-safe", "frozen-logical-readonly", replay.PROFILE96)

    def test_old_v1_prepared_plan_cannot_be_relabelled_implicitly(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "prepared-plan.json").write_text(json.dumps({"schema": "open-ocarina.prepared-native-mesh-replay/v1",
                                                               "prepare_only": True, "gpu_executed": False}))
            with self.assertRaises(ValueError):
                replay.checked_prepared(root, replay.digest(root / "prepared-plan.json"))

    def test_exact_profile_process_budgets_preserve_native_bounds(self):
        for profile, seconds in ((replay.D8_PROFILE, 30), (replay.PROFILE96, 120)):
            limits = replay.execution_limits(profile)
            self.assertEqual(limits, {"groups_per_draw": 32, "draws_per_helper": 1, "fence_seconds": 2,
                                      "process_seconds": seconds, "max_total_input_bytes": 1024**3})
            self.assertEqual(replay.output_profile(profile)["process_seconds"], seconds)
        with self.assertRaises(ValueError):
            replay.execution_limits("arbitrary")

    def test_tampered_profile_budgets_rejected_before_artifact_or_device_checks(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            manifest = root / "prepared-plan.json"
            for profile, wrong_process in ((replay.D8_PROFILE, 120), (replay.PROFILE96, 30)):
                approved = replay.execution_limits(profile)
                for limits in (None, {}, approved | {"process_seconds": wrong_process},
                               approved | {"process_seconds": float(approved["process_seconds"])},
                               approved | {"fence_seconds": 120}, approved | {"draws_per_helper": True},
                               approved | {"arbitrary_override": 120}):
                    plan = {"schema": replay.output_profile(profile)["schema"], "output_profile": profile,
                            "prepare_only": True, "gpu_executed": False, "limits": limits}
                    manifest.write_text(json.dumps(plan))
                    with self.subTest(profile=profile, limits=limits), patch.object(replay, "driver_identity") as device:
                        with self.assertRaisesRegex(ValueError, "exact mesh profile budget"):
                            replay.checked_prepared(root, replay.digest(manifest))
                        device.assert_not_called()


if __name__ == "__main__":
    unittest.main()
