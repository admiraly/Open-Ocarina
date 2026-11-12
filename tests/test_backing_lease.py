# SPDX-License-Identifier: GPL-3.0-or-later
import importlib.util
from pathlib import Path
import shutil
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("check_backing_lease", ROOT / "patches/hookshot/check_backing_lease.py")
checker = importlib.util.module_from_spec(spec)
spec.loader.exec_module(checker)


@unittest.skipUnless(sys.platform.startswith("linux") and shutil.which("cc") and shutil.which("nm"), "Linux, a C11 compiler and nm are required")
class ProductionBackingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory(prefix="oo-backing-unit-")
        cls.binary = checker.compile_helper(Path(cls.temporary.name))

    @classmethod
    def tearDownClass(cls):
        cls.temporary.cleanup()

    def run_case(self, case):
        self.assertTrue(checker.run_case(self.binary, case)["passed"])

    def test_default_behavior_has_no_lease_or_discard_change(self):
        self.run_case("default")

    def test_explicitly_disabled_behavior_matches_original_discard(self):
        self.run_case("disabled")

    def test_acquire_validate_rollback_release_and_abi_bounds(self):
        self.run_case("basic")

    def test_distinct_mappings_of_original_backing_share_canonical_identity(self):
        self.run_case("alias")

    def test_successful_discard_invalidates_overlapping_aliases_but_not_disjoint_lease(self):
        self.run_case("discard")

    def test_failed_discard_preserves_result_and_errno_but_refuses_a_false_rollback_proof(self):
        self.run_case("failure")

    def test_observed_mapping_replacement_retires_original_mapping_identity(self):
        self.run_case("replacement")

    def test_unrepresentable_partial_mapping_change_invalidates_diagnostics(self):
        self.run_case("partial")

    def test_capacity_rejection_and_slot_reuse_cannot_reuse_lease_identity(self):
        self.run_case("capacity")

    def test_lost_event_history_fails_provenance_closed(self):
        self.run_case("events")

    def test_serial_wrap_cannot_create_a_false_matching_identity(self):
        self.run_case("serial")

    def test_cross_thread_discard_invalidation_is_serialized(self):
        self.run_case("thread")

    def test_retained_backing_identity_is_not_original_fd_integer(self):
        self.run_case("fd-reuse")

    def test_all_optional_apis_are_dynamic_exports_under_optimized_gc_linking(self):
        self.assertEqual(set(checker.exported_apis(self.binary)), checker.EXPORTS)


class PatchFramingTests(unittest.TestCase):
    def test_helper_is_extracted_exactly_from_the_added_production_file(self):
        source = checker.helper_from_patch()
        self.assertIn("int hos_capture_backing_acquire", source)
        self.assertIn("const int result=madvise(pointer,size,advice),saved=errno", source)
        for forbidden in ("vkQueue", "vkDevice", "pools_mu", "oo_buffer_owners"):
            self.assertNotIn(forbidden, source)

    def test_truncated_new_helper_hunk_is_refused(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "short.patch"
            path.write_text("+++ b/host/capture_backing.h\n@@ -0,0 +1,2 @@\n+one\n")
            with self.assertRaisesRegex(ValueError, "Truncated"):
                checker.helper_from_patch(path)

    def test_unknown_hunk_format_cannot_silently_compile_a_different_helper(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "wrong.patch"
            path.write_text("+++ b/host/capture_backing.h\n@@ -1,1 +1,1 @@\n+one\n")
            with self.assertRaisesRegex(ValueError, "framing"):
                checker.helper_from_patch(path)

    def test_missing_production_link_rule_is_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "no-exports.patch"
            path.write_text(checker.PATCH.read_text().replace(checker.EXPORT_FLAG, "removed-export"))
            with self.assertRaisesRegex(ValueError, "dynamic export"):
                checker.export_flags_from_patch(path)


if __name__ == "__main__":
    unittest.main()
