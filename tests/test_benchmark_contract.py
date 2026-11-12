import copy
import hashlib
import json
from pathlib import Path
import tempfile
import unittest

from tools.benchmark_contract import artifact, compare, validate_run


def sha(value):
    return hashlib.sha256(value.encode()).hexdigest()


class BenchmarkContractTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.trusted = {sha("synthetic producer")}

    def tearDown(self):
        self.temporary.cleanup()

    def make(self, label, revision="a", camera="camera", identity_changes=None):
        root = self.root / label
        root.mkdir()
        identity = {"game_modules_sha256": {name: sha(name) for name in ("main", "sdk", "subsdk0", "rtld")},
                    "renderer_revision": revision * 40, "device_uuid": "a" * 32,
                    **{name: sha(name) for name in ("driver_sha256", "shader_policy_sha256", "save_sha256",
                                                   "settings_sha256", "input_sha256")}}
        identity.update(identity_changes or {})
        identity_digest = hashlib.sha256(json.dumps(identity, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
        manifest = {"schema": 1, "purpose": "timing", "instrumentation": [], "identity": identity,
                    "producer": {"contract": "open-ocarina-benchmark-v1", "sha256": next(iter(self.trusted))},
                    "native_frames_only": True, "frame_generation": False, "cache_warm": True,
                    "warmup_native_frames": 300, "samples": []}
        for index in range(3):
            run_id = f"{label}-{index}"
            evidence = {"schema": 1, "producer_sha256": next(iter(self.trusted)), "run_id": run_id,
                        "identity_sha256": identity_digest, "native_frames": 3, "elapsed_ns": 50000000,
                        "warmup_native_frames": 300, "cache_warm": True, "instrumentation": [],
                        "live_input_isolated": True, "competing_gpu_processes": [], "generated_frames": 0,
                        "frames": [{"sample_frame": frame, "camera_sha256": sha(camera + str(frame)),
                                    "scene_state_sha256": sha("state" + str(frame)),
                                    "logical_workload_sha256": sha("work" + str(frame)),
                                    "pending_shader_compiles": 0, "skipped_draws": 0, "skipped_dispatches": 0,
                                    "correctness_checked": True} for frame in range(3)]}
            data = json.dumps(evidence).encode()
            path = root / f"{index}.json"
            path.write_bytes(data)
            manifest["samples"].append({"run_id": run_id, "native_frames": 3, "elapsed_ns": 50000000,
                                        "evidence": {"path": path.name, "sha256": hashlib.sha256(data).hexdigest()}})
        return manifest, root

    def rewrite_evidence(self, manifest, root, change, index=0):
        ref = manifest["samples"][index]["evidence"]
        path = root / ref["path"]
        evidence = json.loads(path.read_text())
        change(evidence)
        data = json.dumps(evidence).encode()
        path.write_bytes(data)
        ref["sha256"] = hashlib.sha256(data).hexdigest()

    def pair(self, **candidate_kwargs):
        left, left_root = self.make("baseline")
        right, right_root = self.make("candidate", revision="b", **candidate_kwargs)
        return left, right, left_root, right_root

    def test_comparison_accepts_reviewed_evidence_without_claiming_speedup(self):
        result = compare(*self.pair(), "renderer_revision", self.trusted)
        self.assertTrue(result["contract_compatible"])
        self.assertFalse(result["speedup_inferred"])
        self.assertEqual(result["changed_identity_fields"], ["renderer_revision"])

    def test_no_producer_is_trusted_by_default(self):
        with self.assertRaisesRegex(ValueError, "not externally reviewed"):
            compare(*self.pair(), "renderer_revision")

    def test_same_save_different_camera_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "Same save is insufficient"):
            compare(*self.pair(camera="different framing"), "renderer_revision", self.trusted)

    def test_uncontrolled_settings_change_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "Uncontrolled identity"):
            compare(*self.pair(identity_changes={"settings_sha256": sha("other settings")}),
                    "renderer_revision", self.trusted)

    def test_unpinned_module_or_revision_is_rejected(self):
        manifest, root = self.make("baseline")
        del manifest["identity"]["game_modules_sha256"]["rtld"]
        with self.assertRaisesRegex(ValueError, "all four"):
            validate_run(manifest, root, self.trusted)
        manifest["identity"]["game_modules_sha256"]["rtld"] = sha("rtld")
        manifest["identity"]["renderer_revision"] = "short"
        with self.assertRaisesRegex(ValueError, "complete renderer"):
            validate_run(manifest, root, self.trusted)

    def test_changed_identity_needs_matching_producer_evidence(self):
        manifest, root = self.make("baseline")
        manifest["identity"]["settings_sha256"] = sha("fabricated settings")
        with self.assertRaisesRegex(ValueError, "not backed"):
            validate_run(manifest, root, self.trusted)

    def test_attribution_and_instrumented_samples_are_rejected(self):
        manifest, root = self.make("baseline")
        manifest["purpose"] = "attribution"
        with self.assertRaisesRegex(ValueError, "Attribution"):
            validate_run(manifest, root, self.trusted)
        manifest["purpose"] = "timing"
        manifest["instrumentation"] = ["gpu_pass_timing"]
        with self.assertRaisesRegex(ValueError, "instrumentation"):
            validate_run(manifest, root, self.trusted)

    def test_warmup_and_native_frames_are_required(self):
        manifest, root = self.make("baseline")
        for field, bad in (("cache_warm", False), ("warmup_native_frames", 0),
                           ("frame_generation", True), ("native_frames_only", False)):
            changed = copy.deepcopy(manifest)
            changed[field] = bad
            with self.assertRaises(ValueError):
                validate_run(changed, root, self.trusted)

    def test_positive_integer_elapsed_time_is_required(self):
        manifest, root = self.make("baseline")
        for elapsed in (0, -1, True, float("nan"), float("inf"), "60 FPS"):
            changed = copy.deepcopy(manifest)
            changed["samples"][0]["elapsed_ns"] = elapsed
            with self.assertRaisesRegex(ValueError, "positive integer"):
                validate_run(changed, root, self.trusted)

    def test_repetitions_need_unique_run_ids_and_three_samples(self):
        manifest, root = self.make("baseline")
        changed = copy.deepcopy(manifest)
        changed["samples"].pop()
        with self.assertRaisesRegex(ValueError, "three independent"):
            validate_run(changed, root, self.trusted)
        manifest["samples"][1]["run_id"] = manifest["samples"][0]["run_id"]
        with self.assertRaisesRegex(ValueError, "unique"):
            validate_run(manifest, root, self.trusted)

    def test_camera_trace_must_cover_every_frame(self):
        manifest, root = self.make("baseline")
        self.rewrite_evidence(manifest, root, lambda record: record["frames"].pop())
        with self.assertRaisesRegex(ValueError, "Missing per-native-frame"):
            validate_run(manifest, root, self.trusted)

    def test_repeated_camera_or_workload_disagreement_is_rejected(self):
        manifest, root = self.make("baseline")
        self.rewrite_evidence(manifest, root,
                              lambda record: record["frames"][0].update(camera_sha256=sha("drift")), 2)
        with self.assertRaisesRegex(ValueError, "Baseline repetitions disagree"):
            validate_run(manifest, root, self.trusted)

    def test_missing_camera_identity_is_rejected(self):
        manifest, root = self.make("baseline")
        self.rewrite_evidence(manifest, root, lambda record: record["frames"][0].pop("camera_sha256"))
        with self.assertRaisesRegex(ValueError, "Missing observed frame identity"):
            validate_run(manifest, root, self.trusted)

    def test_incomplete_work_or_unchecked_rendering_is_rejected(self):
        for field, bad in (("pending_shader_compiles", 1), ("skipped_draws", 1),
                           ("skipped_dispatches", 1), ("correctness_checked", False)):
            manifest, root = self.make(field)
            self.rewrite_evidence(manifest, root, lambda record: record["frames"][0].update({field: bad}))
            with self.assertRaises(ValueError):
                validate_run(manifest, root, self.trusted)

    def test_live_input_competing_processes_and_generated_frames_are_rejected(self):
        for field, bad in (("live_input_isolated", False), ("competing_gpu_processes", ["other game"]),
                           ("generated_frames", 1)):
            manifest, root = self.make(field)
            self.rewrite_evidence(manifest, root, lambda record: record.update({field: bad}))
            with self.assertRaises(ValueError):
                validate_run(manifest, root, self.trusted)

    def test_tampered_artifact_is_rejected(self):
        manifest, root = self.make("baseline")
        with (root / "0.json").open("a") as output:
            output.write(" ")
        with self.assertRaisesRegex(ValueError, "SHA-256 mismatch"):
            validate_run(manifest, root, self.trusted)

    def test_path_escape_and_symlink_are_rejected(self):
        manifest, root = self.make("baseline")
        ref = manifest["samples"][0]["evidence"]
        for name in ("../0.json", str(root / "0.json"), "dir\\0.json"):
            with self.assertRaises(ValueError):
                artifact(root, {"path": name, "sha256": ref["sha256"]})
        (root / "link.json").symlink_to(root / "0.json")
        with self.assertRaisesRegex(ValueError, "symlinks"):
            artifact(root, {"path": "link.json", "sha256": ref["sha256"]})

    def test_reusing_the_same_samples_across_conditions_is_rejected(self):
        left, root = self.make("same")
        with self.assertRaisesRegex(ValueError, "reuse"):
            compare(left, copy.deepcopy(left), root, root, "renderer_revision", self.trusted)

    def test_schema_boolean_is_not_an_integer_version(self):
        manifest, root = self.make("baseline")
        manifest["schema"] = True
        with self.assertRaisesRegex(ValueError, "schema"):
            validate_run(manifest, root, self.trusted)

    def test_evidence_schema_and_timing_booleans_are_rejected(self):
        for field in ("schema", "native_frames", "elapsed_ns", "warmup_native_frames"):
            manifest, root = self.make("boolean-" + field)
            self.rewrite_evidence(manifest, root, lambda record: record.update({field: True}))
            with self.assertRaises(ValueError):
                validate_run(manifest, root, self.trusted)


if __name__ == "__main__":
    unittest.main()
