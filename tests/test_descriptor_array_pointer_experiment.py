# SPDX-License-Identifier: GPL-3.0-or-later
import random
import unittest
from pathlib import Path

from tools.descriptor_array_pointer_experiment import (
    Descriptor, U32, U64, W0, W0_SIZE, W1, WIDTHS, access_location,
    build_partitions, canonical, example_plan, glsl_source, interval_lookup,
    page_location, pointer_assembly, validate_only, walk_reference,
)


class DescriptorArrayPointerTests(unittest.TestCase):
    def assert_differential(self, views, addresses):
        partitions = build_partitions(views)
        for width in WIDTHS:
            for address in addresses:
                self.assertEqual(interval_lookup(partitions, address, width),
                                 walk_reference(views, address, width),
                                 (width, address, views))
        return partitions

    def test_short_first_descriptor_does_not_hide_later_vector_hit(self):
        views = [Descriptor(96, 64, 10, 0, 0), Descriptor(96, 64, 64, 1, 1)]
        partitions = self.assert_differential(views, range(80, 170))
        self.assertEqual(interval_lookup(partitions, 100, 4), (0, 0, 4))
        self.assertEqual(interval_lookup(partitions, 100, 8), (0, 0, 0))
        self.assertEqual(interval_lookup(partitions, 100, 16), (1, 1, 0))
        self.assertEqual(interval_lookup(partitions, 105, 4), (1, 1, 8))

    def test_overlap_first_match_keeps_binding_and_descriptor_element(self):
        views = [Descriptor(96, 32, 32, 1, 0), Descriptor(112, 64, 64, 0, 1)]
        partitions = self.assert_differential(views, range(80, 190))
        self.assertEqual(interval_lookup(partitions, 116, 4), (1, 0, 20))
        self.assertEqual(interval_lookup(partitions, 132, 4), (0, 1, 20))
        # Distinct roots with identical bytes remain distinct logical choices.
        self.assertNotEqual(interval_lookup(partitions, 116, 4)[:2], (0, 1))

    def test_nearest_candidate_priority_and_earliest_tie(self):
        views = [Descriptor(96, 64, 64, 1, 1),
                 Descriptor(96, 0, 64, 0, 0, "candidate"),
                 Descriptor(112, 0, 32, 0, 1, "candidate"),
                 Descriptor(112, 0, 32, 1, 0, "candidate")]
        partitions = self.assert_differential(views, range(80, 180))
        self.assertEqual(interval_lookup(partitions, 116, 4), (0, 1, 4))
        self.assertEqual(interval_lookup(partitions, 148, 4), (0, 0, 52))

    def test_zero_word_single_prefix_and_table_full_bound(self):
        single = Descriptor(101, 0, 32, 0, 0)
        table = Descriptor(160, 0, 32, 1, 1, "table")
        partitions = self.assert_differential([single, table], range(80, 210))
        self.assertEqual(interval_lookup(partitions, 100, 16), (0, 0, 0))
        self.assertIsNone(interval_lookup(partitions, 101, 16))
        self.assertEqual(interval_lookup(partitions, 180, 16), (1, 1, 16))

    def test_u32_array_length_and_byte_shift_wrap_without_allocation(self):
        for width in WIDTHS:
            view = Descriptor(96, 0, (1 << 32) + width, 0, 1, "table")
            partitions = self.assert_differential([view], range(80, 140))
            self.assertEqual(interval_lookup(partitions, 96, width), (0, 1, 0))
            self.assertIsNone(interval_lookup(partitions, 96 + width, width))
            count_wrap = Descriptor(96, 0, ((1 << 32) + 1) * width, 0, 0, "table")
            self.assertEqual(build_partitions([count_wrap])[width][0].high, 96 + width)
            zero_extent = Descriptor(96, 0, 1 << 32, 0, 0, "table")
            self.assertIsNone(interval_lookup(build_partitions([zero_extent]), 96, width))

    def test_u64_bound_and_nvn_end_wrap_match_unsigned_source_comparisons(self):
        views = [Descriptor(U64 - 15, 64, 64, 0, 0),
                 Descriptor(U64 - 47, 64, 16, 1, 1)]
        partitions = self.assert_differential(views, range(U64 - 80, U64 + 1))
        self.assertTrue(all(not rows for rows in partitions.values()))

    def test_unaligned_hit_floors_typed_index_but_physical_page_fit_uses_operand(self):
        views = [Descriptor(W0 + 4092, 64, 64, 0, 0)]
        partitions = self.assert_differential(views, range(W0 + 4070, W0 + 4140))
        for width, offset in ((4, 12), (8, 8), (16, 0)):
            self.assertEqual(interval_lookup(partitions, W0 + 4095, width), (0, 0, offset))
            self.assertIsNone(page_location({0: 0x10000}, W0 + 4095, width))
        # A wider descriptor element can itself cross a guest page with a
        # different valid binding alignment; this model accepts that parameter.
        cross = [Descriptor(W0 + 4092, 32, 32, 0, 1)]
        for width in WIDTHS:
            self.assertEqual(interval_lookup(build_partitions(cross, 4), W0 + 4092, width),
                             walk_reference(cross, W0 + 4092, width, 4))

    def test_snapshot_descriptor_is_not_replaced_by_current_page_mapping(self):
        views = [Descriptor(W0 + 64, 32, 32, 1, 1, device=0x10000)]
        partitions = build_partitions(views)
        table = {0: 0x90000}
        for transformed in (False, True):
            location = access_location(views, partitions, table, W0 + 68, 4, transformed=transformed)
            self.assertEqual(location, ("StorageBuffer", 1, 1, 4))
            self.assertNotEqual(location, ("PhysicalStorageBuffer", 0x90044))
            self.assertEqual(access_location(views, partitions, table, W0 + 128, 4, transformed=transformed),
                             ("PhysicalStorageBuffer", 0x90080))

    def test_root_choice_preserves_shared_backing_alias_store(self):
        backing = bytearray(64)
        roots = {(0, 0): memoryview(backing)[:32], (1, 1): memoryview(backing)[8:40]}
        views = [Descriptor(W0 + 96, 32, 32, 0, 0), Descriptor(W0 + 192, 32, 32, 1, 1)]
        partitions = build_partitions(views)
        for transformed in (False, True):
            selected = access_location(views, partitions, {}, W0 + 104, 4, transformed=transformed)
            _, binding, element, offset = selected
            roots[binding, element][offset:offset + 4] = b"ABCD"
            selected = access_location(views, partitions, {}, W0 + 192, 4, transformed=transformed)
            _, binding, element, offset = selected
            self.assertEqual(bytes(roots[binding, element][offset:offset + 4]), b"ABCD")

    def test_miss_windows_page_null_and_follow_stores(self):
        partitions = build_partitions([])
        table = {0: 0x10000, W0_SIZE // 4096: 0x20000}
        for transformed in (False, True):
            for guest, physical in ((W0 + 64, 0x10040), (W1 + 64, 0x20040)):
                kwargs = {"transformed": transformed, "store": True}
                self.assertIsNone(access_location([], partitions, table, guest, 4, **kwargs))
                self.assertEqual(access_location([], partitions, table, guest, 4, follow_stores=True, **kwargs),
                                 ("PhysicalStorageBuffer", physical))
            self.assertIsNone(access_location([], partitions, table, W0 + 4092, 8, transformed=transformed))
            self.assertIsNone(access_location([], partitions, table, W0 + 4096, 4, transformed=transformed))
            self.assertIsNone(access_location([], partitions, table, 64, 4, transformed=transformed))

    def test_seeded_boundary_differential(self):
        rng = random.Random(0xA770)
        for _ in range(48):
            views = [Descriptor(W0 + rng.randrange(128), rng.choice((0, 1, 7, 32, U32)),
                                rng.choice((0, 1, 10, 32, 80, (1 << 32) + 16)),
                                index // 2, index % 2, rng.choice(("candidate", "single", "table")))
                     for index in range(4)]
            self.assert_differential(views, range(W0 - 2, W0 + 220))

    def test_generated_contract_has_real_arrays_typed_aliases_and_width_records(self):
        text = glsl_source()
        for width in WIDTHS:
            for root in ("a", "b"):
                self.assertIn(f"{root}{width}[2]", text)
            self.assertIn(f"locate(address,{WIDTHS.index(width)}u)", text)
            self.assertIn(f"length{width}", text)
            self.assertIn(f"Physical{width}", text)
        self.assertIn("nonuniformEXT", text)
        self.assertIn("parameters.follow_stores==0u", text)
        self.assertNotIn("restrict", text)
        plan = example_plan()
        self.assertEqual(plan["widths"], [4, 8, 16])
        self.assertEqual(canonical(plan), canonical(example_plan()))
        self.assertNotEqual(plan["counts"][0], plan["counts"][2])

    def test_frozen_synthetic_plan_is_current(self):
        plan = Path(__file__).resolve().parents[1] / "research/descriptor-array-pointer-plan.json"
        self.assertEqual(plan.read_bytes(), canonical(example_plan()))

    def test_fail_closed_on_invalid_inputs_or_nonprivate_output(self):
        with self.assertRaises(ValueError):
            build_partitions([Descriptor(0, 4, 4, 2, 0)])
        with self.assertRaises(ValueError):
            build_partitions([Descriptor(0, 4, 4, 0, 0)], 3)
        with self.assertRaises(ValueError):
            interval_lookup({}, -1, 4)
        with self.assertRaises(ValueError):
            pointer_assembly("not the independently authored synthetic shader")
        with self.assertRaises(ValueError):
            validate_only("research/never-write")
        with self.assertRaises(ValueError):
            validate_only("local/../research/never-write")


if __name__ == "__main__":
    unittest.main()
