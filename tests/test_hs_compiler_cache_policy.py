# SPDX-License-Identifier: MIT
"""Game-free native tests of the exact Mesa patch's cache-input encoder."""
import importlib.util
from pathlib import Path
import shutil
import tempfile
import unittest

SCRIPT = Path(__file__).resolve().parents[1] / "patches/mesa/check_hs_compiler_cache_policy.py"
SPEC = importlib.util.spec_from_file_location("hs_compiler_cache_policy", SCRIPT)
CHECK = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(CHECK)


class HSCompilerCachePolicyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not shutil.which("cc"):
            raise unittest.SkipTest("C compiler required for production-function tests")
        cls.temporary = tempfile.TemporaryDirectory()
        cls.helper = CHECK.build_helper(cls.temporary.name)

    @classmethod
    def tearDownClass(cls):
        cls.temporary.cleanup()

    def test_exact_production_encoder_has_complete_framed_inventory(self):
        value = CHECK.decode(CHECK.encode(self.helper))
        self.assertEqual(value["scope"], 32)
        self.assertEqual(tuple(value["options"]), CHECK.KNOBS)
        self.assertTrue(all(not row["present"] and row["bytes"] == b""
                            for row in value["options"].values()))

    def test_absent_empty_literal_none_are_distinct_for_each_scope(self):
        for label, scope in CHECK.SCOPES.items():
            frames = [CHECK.encode(self.helper, options, scope=scope) for options in
                      ({}, {"HS_FORCE_SIMD8": ""}, {"HS_FORCE_SIMD8": "none"})]
            with self.subTest(scope=label):
                self.assertEqual(len(set(frames)), 3)

    def test_every_codegen_knob_changes_each_compute_task_mesh_and_device_scope(self):
        for label, scope in CHECK.SCOPES.items():
            baseline = CHECK.encode(self.helper, scope=scope)
            for knob in CHECK.KNOBS:
                with self.subTest(scope=label, knob=knob):
                    frame = CHECK.encode(self.helper, {knob: "1"}, scope=scope)
                    self.assertNotEqual(frame, baseline)
                    self.assertEqual(CHECK.decode(frame)["options"][knob],
                                     {"present": True, "bytes": b"1"})

    def test_empty_max_is_distinct_from_disabled_fallback(self):
        for label, scope in CHECK.SCOPES.items():
            with self.subTest(scope=label):
                self.assertNotEqual(CHECK.encode(self.helper, scope=scope),
                                    CHECK.encode(self.helper, {"HS_CS32_MAX_INSTS": ""}, scope=scope))

    def test_adjacent_option_concatenation_collision_is_closed(self):
        for label, scope in CHECK.SCOPES.items():
            with self.subTest(scope=label):
                self.assertNotEqual(CHECK.encode(self.helper,
                    {"HS_FORCE_SIMD8": "cs", "HS_CS32_MAX_INSTS": "1"}, scope=scope),
                    CHECK.encode(self.helper, {"HS_FORCE_SIMD8": "cs1"}, scope=scope))

    def test_raw_numeric_and_presence_spellings_remain_conservatively_distinct(self):
        for knob in CHECK.KNOBS:
            with self.subTest(knob=knob):
                frames = [CHECK.encode(self.helper, {knob: value}) for value in ("", "0", "1", "01", "-1")]
                self.assertEqual(len(set(frames)), len(frames))

    def test_length_counts_bytes_and_preserves_long_option_values(self):
        value = "μ" * 1024
        frame = CHECK.encode(self.helper, {"HS_FORCE_SIMD8": value})
        decoded = CHECK.decode(frame)
        self.assertEqual(decoded["options"]["HS_FORCE_SIMD8"]["bytes"], value.encode())

    def test_effective_drirc_spilling_rate_is_part_of_policy_even_without_environment(self):
        for label, scope in CHECK.SCOPES.items():
            with self.subTest(scope=label):
                frames = [CHECK.encode(self.helper, scope=scope, spilling_rate=rate) for rate in (0, 1, 2, -1)]
                self.assertEqual(len(set(frames)), 4)
                self.assertEqual(CHECK.decode(frames[-1])["spilling_rate_u32"], 0xffffffff)

    def test_effective_debug_simd_configuration_is_part_of_policy(self):
        baseline = CHECK.encode(self.helper)
        for config in (1, 1 << 32, 1 << 63):
            with self.subTest(config=config):
                frame = CHECK.encode(self.helper, compiler_config=config)
                self.assertNotEqual(frame, baseline)
                self.assertEqual(CHECK.decode(frame)["compiler_config"], config)

    def test_each_stage_scope_is_explicit_and_distinct(self):
        frames = [CHECK.encode(self.helper, scope=scope) for scope in CHECK.SCOPES.values()]
        self.assertEqual(len(set(frames)), len(CHECK.SCOPES))

    def test_logging_and_runtime_only_controls_do_not_change_compiled_policy(self):
        for label, scope in CHECK.SCOPES.items():
            baseline = CHECK.encode(self.helper, scope=scope)
            for knob in CHECK.EXCLUDED:
                with self.subTest(scope=label, knob=knob):
                    self.assertEqual(CHECK.encode(self.helper, {knob: "1"}, scope=scope), baseline)

    def test_public_checker_runs_optimized_native_matrix_without_gpu(self):
        result = CHECK.check_helper()
        self.assertTrue(result["exact_native_encoder_tested"])
        self.assertEqual(result["presence_value_comparisons"], 112)
        self.assertFalse(result["gpu_execution"])

    def test_nonmatching_mesa_base_fails_before_applying_patch(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            for name in CHECK.BASE_SHA256:
                path = root / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text("changed source\n")
            with self.assertRaisesRegex(ValueError, "baseline SHA-256 differs"):
                CHECK.check_source(root)
            self.assertFalse((root / CHECK.HEADER).exists())

    def test_truncated_or_trailing_policy_transcripts_are_rejected(self):
        frame = CHECK.encode(self.helper)
        for corrupt in (frame[:-1], frame + b"x", frame[1:]):
            with self.subTest(length=len(corrupt)), self.assertRaises(ValueError):
                CHECK.decode(corrupt)


if __name__ == "__main__":
    unittest.main()
