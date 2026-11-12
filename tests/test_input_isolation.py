import importlib.util
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("input_check", ROOT / "patches/hookshot/check_input_isolation.py")
CHECK = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(CHECK)


class InputIsolationTests(unittest.TestCase):
    def test_patch_is_host_only_and_keeps_explicit_opt_in(self):
        patch = CHECK.PATCH.read_text()
        self.assertNotIn("diff --git a/gpu/", patch)
        self.assertIn('env_on("HOS_INPUT_EXCLUSIVE_PERSIST")', patch)
        self.assertIn("hos_input_player_exclusive()", patch)
        self.assertIn("hos_control_input_exclusive(void)", patch)

    def test_policies_are_extracted_from_actual_patch(self):
        header = CHECK.header_from_patch()
        self.assertIn("if (!enabled || presses <= 0", header)
        self.assertIn("if (persistent) return 1;", header)
        self.assertIn("frame - start < frames", header)

    def test_missing_policy_header_is_refused(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "bad.patch"
            path.write_text("unrelated content")
            with mock.patch.object(CHECK, "PATCH", path):
                with self.assertRaises(ValueError):
                    CHECK.header_from_patch()

    def test_function_extraction_keeps_nested_query_body(self):
        source = "int query(void) { if (1) { return 1; } return 0; }\nint other(void) { return 2; }"
        result = CHECK.function(source, "query")
        self.assertIn("return 0;", result)
        self.assertNotIn("other", result)

    def test_unterminated_query_is_refused(self):
        with self.assertRaises(ValueError):
            CHECK.function("int query(void) { if (1) { return 1; }", "query")

    def test_optimized_policy_cases_without_private_inputs(self):
        compiler = shutil.which("cc") or shutil.which("clang")
        if compiler is None:
            self.skipTest("optional C compiler unavailable")
        result = CHECK.check(compiler=compiler)
        self.assertTrue(result["optimized_policy_check"])
        self.assertFalse(result["production_mouse_drain_and_release_check"])
        self.assertFalse(result["read_only_control_lease_check"])
        self.assertEqual(result["production_script_ownership_cases"], 0)


if __name__ == "__main__":
    unittest.main()
