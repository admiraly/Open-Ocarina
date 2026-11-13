# SPDX-License-Identifier: GPL-3.0-or-later
import ctypes
import ctypes.util
import json
from pathlib import Path
import random
import struct
import tempfile
import unittest

from tools import matrix_restore_probe as probe


class RoundingTests(unittest.TestCase):
    def test_all_half_encodings_round_trip_except_declared_nan_canonicalization(self):
        for word in range(65536):
            actual = probe.float32_to_half(probe.half_to_float32(word))
            expected = ((word & 0x8000) | 0x7E00) if word & 0x7C00 == 0x7C00 and word & 1023 else word
            self.assertEqual(actual, expected, hex(word))

    def test_half_ties_subnormals_and_overflow(self):
        cases = {0x33000000: 0, 0x33000001: 1, 0xB3000000: 0x8000,
                 0x33800000: 1, 0x38800000: 0x0400,
                 0x3F801000: 0x3C00, 0x3F801001: 0x3C01, 0x3F803000: 0x3C02,
                 0x477FEFFF: 0x7BFF, 0x477FF000: 0x7C00,
                 0x7F800000: 0x7C00, 0xFF800000: 0xFC00}
        for original, expected in cases.items():
            self.assertEqual(probe.float32_to_half(original), expected, hex(original))

    def test_float32_half_conversion_matches_independent_pack_for_finite_samples(self):
        rng = random.Random(20261113)
        for _ in range(3000):
            word = rng.getrandbits(32)
            value = struct.unpack("!f", word.to_bytes(4, "big"))[0]
            if word & 0x7F800000 == 0x7F800000:
                continue
            expected = probe.half(value)
            self.assertEqual(probe.float32_to_half(word), expected, hex(word))

    def test_fused_operation_and_nonfinite_classification(self):
        f = lambda value: int.from_bytes(struct.pack("!f", value), "big")
        self.assertEqual(probe.fma32(f(1.0), f(2.0), f(3.0)), f(5.0))
        self.assertEqual(probe.fma32(0x7F800000, 0, 0), probe.QNaN32)
        self.assertEqual(probe.fma32(0x7F800000, f(1.0), 0xFF800000), probe.QNaN32)
        self.assertEqual(probe.fma32(f(1.0), f(1.0), 0xFF800000), 0xFF800000)
        self.assertEqual(probe.fma32(0x80000000, f(1.0), 0x80000000), 0x80000000)
        self.assertEqual(probe.fma32(0x80000000, f(1.0), 0), 0)
        self.assertEqual(probe.fma32(f(1.0), f(-1.0), f(1.0)), 0)

    def test_integer_fma_against_available_host_fmaf_for_finite_samples(self):
        library = ctypes.util.find_library("m")
        if not library:
            self.skipTest("Independent optional host fmaf reference unavailable")
        lib = ctypes.CDLL(library)
        if not hasattr(lib, "fmaf"):
            self.skipTest("Independent optional host fmaf reference unavailable")
        if hasattr(lib, "fegetround") and lib.fegetround() != 0:
            self.skipTest("Host reference is not in nearest-even rounding mode")
        lib.fmaf.argtypes = [ctypes.c_float, ctypes.c_float, ctypes.c_float]
        lib.fmaf.restype = ctypes.c_float
        rng = random.Random(13579)
        for _ in range(1000):
            words = [rng.getrandbits(32) for _ in range(3)]
            if any(word & 0x7F800000 == 0x7F800000 for word in words):
                continue
            values = [struct.unpack("!f", word.to_bytes(4, "big"))[0] for word in words]
            expected = int.from_bytes(struct.pack("!f", lib.fmaf(*values)), "big")
            self.assertEqual(probe.fma32(*words), expected, [hex(word) for word in words])

    def test_invalid_encodings_are_refused(self):
        for value in (-1, 65536, 0.5):
            with self.assertRaises(ValueError):
                probe.half_to_float32(value)
        with self.assertRaises(ValueError):
            probe.fma32(0, 0, 1 << 32)


class MatrixContractTests(unittest.TestCase):
    def matrices(self):
        return ([[row * 16 + k for k in range(16)] for row in range(16)],
                [[1000 + k * 8 + col for col in range(8)] for k in range(16)],
                [[2000 + row * 8 + col for col in range(8)] for row in range(16)])

    def test_warp32_fragment_round_trip_and_independent_corner_layout(self):
        a, b, c = self.matrices()
        packed = probe.pack_fragments(a, b, c)
        self.assertEqual(probe.unpack_fragments(packed), (a, b, c))
        pair = lambda x, y: x | y << 16
        self.assertEqual(packed["A"][0], [pair(0, 1), pair(128, 129), pair(8, 9), pair(136, 137)])
        self.assertEqual(packed["A"][31], [pair(118, 119), pair(246, 247), pair(126, 127), pair(254, 255)])
        self.assertEqual(packed["B"][0], [pair(1000, 1008), pair(1064, 1072)])
        self.assertEqual(packed["C"][31], [pair(2062, 2063), pair(2126, 2127)])

    def test_two_m8_tiles_cover_m16_and_two_k8_chunks_without_input_aliases(self):
        a, b, c = self.matrices()
        tiled = probe.tile_inputs(a, b, c)
        self.assertEqual(len(tiled["A_rows"]), 32)
        self.assertEqual(tiled["A_rows"][0], a[0][:8] + [0] * 8)
        self.assertEqual(tiled["A_rows"][8], a[0][8:] + [0] * 8)
        self.assertEqual(tiled["A_rows"][16], a[8][:8] + [0] * 8)
        self.assertEqual(tiled["A_rows"][24], a[8][8:] + [0] * 8)
        self.assertEqual(tiled["B_rows"][0], b[0])
        self.assertEqual(tiled["B_rows"][16], b[8])
        self.assertEqual(tiled["B_rows"][8:16], [[0] * 8 for _ in range(8)])
        tiled["B_rows"][0][0] = 999
        tiled["C_rows"][0][0] = 999
        self.assertEqual(b[0][0], 1000)
        self.assertEqual(c[0][0], 2000)

    def test_checkpoint_padding_and_operation_order_counterexamples_remain_visible(self):
        rows = probe.fixture_report()["counterexamples"]
        self.assertEqual(rows["final_half_cast_is_not_checkpoint_equivalent"],
                         {"checkpoint_half": 0, "final_only_half": probe.half(2 ** -11)})
        self.assertEqual(rows["positive_zero_padding_changes_negative_zero"],
                         {"checkpoint_half": 0x8000, "padded_sequential_half": 0})
        self.assertEqual(rows["native_reduction_order_cannot_be_assumed"],
                         {"ordered_half": 0, "reordered_half": probe.half(1.0)})

    def test_identity_matrix_preserves_logical_product(self):
        a = [[probe.half(1.0 if row == k else 0.0) for k in range(16)] for row in range(16)]
        b = [[probe.half((k + col) / 16) for col in range(8)] for k in range(16)]
        c = [[0] * 8 for _ in range(16)]
        self.assertEqual(probe.logical_hmma(a, b, c), b)

    def test_invalid_shapes_and_fragment_words_are_refused(self):
        a, b, c = self.matrices()
        with self.assertRaises(ValueError):
            probe.pack_fragments(a[:-1], b, c)
        bad = probe.pack_fragments(a, b, c)
        bad["B"][0][0] = -1
        with self.assertRaises(ValueError):
            probe.unpack_fragments(bad)
        with self.assertRaises(ValueError):
            probe.dot16([0] * 16, [0] * 16, order=[0] * 16)


class PreparationTests(unittest.TestCase):
    def test_cpu_only_preparation_never_implies_pipeline_or_shader_execution(self):
        with tempfile.TemporaryDirectory() as folder:
            output = Path(folder) / "new"
            result = probe.prepare(output)
            self.assertFalse(result["shader_prepared"])
            self.assertFalse(result["gpu_pipeline_compiled"])
            self.assertFalse(result["gpu_executed"])
            self.assertIn("oracle", result["limitations"][0])
            self.assertTrue((output / "report.json").is_file())
            self.assertEqual(json.loads((output / "report.json").read_text()), result)
            with self.assertRaises(ValueError):
                probe.prepare(output)

    def test_public_output_location_is_refused(self):
        with self.assertRaises(ValueError):
            probe.private_output(probe.ROOT / "research" / "not-private-output")

    def test_shader_is_advertised_shape_with_isolated_subgroup_outputs(self):
        self.assertIn("8, 16, gl_MatrixUseA", probe.GLSL)
        self.assertIn("16, 8, gl_MatrixUseB", probe.GLSL)
        self.assertIn("8, 8, gl_MatrixUseAccumulator", probe.GLSL)
        self.assertIn("gl_WorkGroupID.x * gl_NumSubgroups + gl_SubgroupID", probe.GLSL)
        self.assertNotIn("vkCreate", probe.GLSL)

    def test_checkpoint_shader_has_explicit_rounding_at_each_of_two_k_steps(self):
        self.assertIn("k < 2u", probe.CHECKPOINT_GLSL)
        self.assertIn("m < 2u", probe.CHECKPOINT_GLSL)
        self.assertIn("mc[element] = half_checkpoint(mc[element])", probe.CHECKPOINT_GLSL)
        self.assertIn("uint remainder", probe.CHECKPOINT_GLSL)
        self.assertIn("unpackHalf2x16(result).x", probe.CHECKPOINT_GLSL)
        self.assertNotIn("float16_t(mc[element])", probe.CHECKPOINT_GLSL)


if __name__ == "__main__":
    unittest.main()
