# SPDX-License-Identifier: GPL-3.0-or-later
import importlib.util
from pathlib import Path
import shutil
import unittest

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("oo_graphics_identity_checker", ROOT / "patches/hookshot/check_graphics_identity.py")
CHECK = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(CHECK)


class GraphicsIdentityPatchTests(unittest.TestCase):
    def test_header_is_independently_extractable(self):
        header = CHECK.header_from_patch()
        self.assertIn("namespace mkwvk::oo_graphics_identity", header)
        self.assertIn("replay_complete", header)

    def test_missing_header_is_refused(self):
        with self.assertRaisesRegex(ValueError, "header missing"):
            CHECK.header_from_patch("diff --git a/unrelated b/unrelated\n+no header\n")

    def test_wrong_overlay_header_is_not_accepted(self):
        with self.assertRaises(ValueError):
            CHECK.header_from_patch("diff --git a/gpu/vk/other.h b/gpu/vk/other.h\n+namespace mkwvk::oo_graphics_identity {}\n")

    def test_checks_are_not_python_assertions(self):
        # A verifier must still reject bad evidence under Python -O.
        with self.assertRaisesRegex(ValueError, "sentinel"):
            CHECK.require(False, "sentinel")

    @unittest.skipUnless(shutil.which("clang++") or shutil.which("g++"), "C++20 compiler unavailable")
    def test_optimized_helper_handles_actual_metadata_boundaries(self):
        report = CHECK.check_helper()
        self.assertTrue(report["optimized_helper_compile"])
        self.assertTrue(report["stage_identity_and_missing_digest_checks"])
        self.assertTrue(report["direct_indirect_and_count_metadata_checked"])
        self.assertTrue(report["logical_recording_distinguished_from_completion"])
        self.assertTrue(report["invalid_bounds_refused"])
        self.assertTrue(report["disabled_and_exact_filters_checked"])


if __name__ == "__main__":
    unittest.main()
