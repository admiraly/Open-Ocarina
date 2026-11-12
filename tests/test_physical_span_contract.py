# SPDX-License-Identifier: GPL-3.0-or-later
import struct
import unittest

from tools.physical_span_contract import (
    PAGE, U64, acyclic, edge_dominates, inspect_module, validate_page_owners, validate_windows,
)


class PhysicalSpanContractTests(unittest.TestCase):
    def test_true_edge_dominates_load(self):
        graph = {1: [2, 3], 2: [4], 3: [5], 4: [5], 5: []}
        self.assertTrue(edge_dominates(graph, 1, 1, 2, 4))

    def test_false_branch_bypass_rejected(self):
        graph = {1: [2, 3], 2: [4], 3: [4], 4: []}
        self.assertFalse(edge_dominates(graph, 1, 1, 2, 4))

    def test_loop_does_not_manufacture_dominance(self):
        graph = {1: [2, 3], 2: [4], 3: [4], 4: [1, 5], 5: []}
        self.assertFalse(edge_dominates(graph, 1, 1, 2, 5))

    def test_unreachable_site_is_not_certified(self):
        self.assertFalse(edge_dominates({1: [2], 2: [], 3: []}, 1, 1, 2, 3))

    def test_acyclic_analysis_rejects_hidden_cycles(self):
        self.assertTrue(acyclic({1: [2, 2, 3], 2: [3], 3: []}))
        self.assertFalse(acyclic({1: [2], 2: [3], 3: [2]}))
        self.assertFalse(acyclic({1: [], 2: [2]}))

    def test_window_indexes_end_at_exact_table_extent(self):
        self.assertEqual(validate_windows([(0x1000000000, 1 << 33, 0),
                                          (0x7f00000000, 1 << 32, 1 << 21)]), 3 << 20)

    def test_window_overlap_and_index_holes_rejected(self):
        for windows in ([(4096, 8192, 0), (8192, 4096, 2)],
                        [(4096, 4096, 0), (16384, 4096, 2)],
                        [(4096, 4096, 0), (16384, 4096, 0)]):
            with self.subTest(windows=windows), self.assertRaises(ValueError):
                validate_windows(windows)

    def test_window_overflow_and_partial_page_rejected(self):
        for first in ((U64 - PAGE, PAGE * 2, 0), (0, PAGE + 1, 0)):
            with self.subTest(first=first), self.assertRaises(ValueError):
                validate_windows([first, (1 << 32, PAGE, 2)])

    def test_last_complete_page_is_safe(self):
        owners = [{"address": 0x10000, "size": PAGE * 2}]
        result = validate_page_owners(struct.pack("<QQ", 0, 0x10000 + PAGE), owners, 16)
        self.assertEqual(result["minimum_owner_tail_bytes"], PAGE)
        self.assertEqual(result["nonzero_entries"], 1)

    def test_start_coverage_does_not_prove_wide_tail(self):
        # A 16-byte access at the final page offset can exceed this real owner,
        # even though the page-table entry itself resolves to a captured byte.
        owners = [{"address": 0x10000, "size": PAGE - 1}]
        with self.assertRaisesRegex(ValueError, "partial-page"):
            validate_page_owners(struct.pack("<Q", 0x10000), owners, 8)

    def test_full_owner_cannot_hide_short_alias_owner(self):
        owners = [{"address": 0x10000, "size": PAGE},
                  {"address": 0x10000, "size": PAGE - 1}]
        with self.assertRaisesRegex(ValueError, "partial-page"):
            validate_page_owners(struct.pack("<Q", 0x10000), owners, 8)

    def test_page_start_at_owner_end_rejected(self):
        with self.assertRaisesRegex(ValueError, "no original"):
            validate_page_owners(struct.pack("<Q", 0x11000), [{"address": 0x10000, "size": PAGE}], 8)

    def test_owner_address_overflow_rejected(self):
        with self.assertRaisesRegex(ValueError, "owner interval"):
            validate_page_owners(struct.pack("<Q", U64 - PAGE), [{"address": U64 - PAGE, "size": 2 * PAGE}], 8)

    def test_missing_table_bytes_and_unaligned_mapping_rejected(self):
        for table, size in ((b"", 8), (struct.pack("<Q", 0x10001), 8)):
            with self.subTest(table=table), self.assertRaises(ValueError):
                validate_page_owners(table, [{"address": 0x10000, "size": PAGE * 2}], size)

    def test_producer_boolean_is_not_coverage(self):
        with self.assertRaises(ValueError):
            validate_page_owners(struct.pack("<Q", 0x10000), [{"coverage_complete": True}], 8)

    def test_unreviewed_shader_cannot_borrow_certificate(self):
        with self.assertRaisesRegex(ValueError, "Unreviewed shader digest"):
            inspect_module(b"independently authored non-target bytes")


if __name__ == "__main__":
    unittest.main()
