# SPDX-License-Identifier: GPL-3.0-or-later
import struct
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from tools.matching_probe import _leb, function_extents, read_memory, run


def synthetic_image(size=8):
    text = bytearray(256)
    rodata = bytearray(256)
    struct.pack_into("<I", text, 4, 0x20)
    text[0x20:0x24] = b"MOD0"
    struct.pack_into("<i", text, 0x30, 0x100 - 0x20)
    rodata[0:4] = b"\x01\x1b\x03\x3b"
    struct.pack_into("<I", rodata, 8, 1)
    struct.pack_into("<ii", rodata, 12, 0x80 - 0x100, 0x60)
    struct.pack_into("<II", rodata, 0x20, 16, 0)
    rodata[0x28:0x34] = b"\x01zR\x00\x01\x7c\x1e\x01\x1b\x0c\x1f\x00"
    struct.pack_into("<IIiiI", rodata, 0x60, 16, 0x164 - 0x120,
                     0x80 - 0x168, size, 0)
    return SimpleNamespace(segments=(
        SimpleNamespace(name=".text", memory_offset=0, data=text),
        SimpleNamespace(name=".rodata", memory_offset=0x100, data=rodata)))


class UnwindProofTests(unittest.TestCase):
    def test_complete_original_extent(self):
        found = function_extents(synthetic_image(), [0x80])[0x80]
        self.assertEqual(found["size"], 8)
        self.assertEqual(found["fde_memory_offset"], 0x160)
        self.assertEqual(found["cie_memory_offset"], 0x120)

    def test_missing_function_is_not_guessed_from_adjacent_start(self):
        with self.assertRaisesRegex(ValueError, "No original unwind"):
            function_extents(synthetic_image(), [0x84])

    def test_disagreeing_fde_start_is_rejected(self):
        image = synthetic_image()
        struct.pack_into("<i", image.segments[1].data, 0x68, 0x84 - 0x168)
        with self.assertRaisesRegex(ValueError, "disagree"):
            function_extents(image, [0x80])

    def test_unaligned_or_empty_function_is_rejected(self):
        for size in (0, -4, 6):
            with self.subTest(size=size), self.assertRaises(ValueError):
                function_extents(synthetic_image(size), [0x80])

    def test_extent_cannot_cross_text_end(self):
        with self.assertRaisesRegex(ValueError, "outside"):
            function_extents(synthetic_image(0x100), [0x80])

    def test_unsupported_header_encoding_is_not_assumed(self):
        image = synthetic_image()
        image.segments[1].data[3] = 0
        with self.assertRaisesRegex(ValueError, "encoding"):
            function_extents(image, [0x80])

    def test_unsupported_cie_encoding_is_not_assumed(self):
        image = synthetic_image()
        image.segments[1].data[0x30] = 0
        with self.assertRaisesRegex(ValueError, "pcrel"):
            function_extents(image, [0x80])

    def test_duplicate_function_entry_is_rejected(self):
        image = synthetic_image()
        rodata = image.segments[1].data
        struct.pack_into("<I", rodata, 8, 2)
        rodata[20:28] = rodata[12:20]
        with self.assertRaisesRegex(ValueError, "Duplicate"):
            function_extents(image, [0x80])

    def test_truncated_table_is_rejected_without_allocating_claimed_size(self):
        image = synthetic_image()
        struct.pack_into("<I", image.segments[1].data, 8, 0xFFFFFFFF)
        with self.assertRaisesRegex(ValueError, "Unmapped"):
            function_extents(image, [0x80])

    def test_read_rejects_unmapped_and_negative_ranges(self):
        for address, size in ((-1, 1), (0, -1), (0x200, 1)):
            with self.subTest(address=address, size=size), self.assertRaises(ValueError):
                read_memory(synthetic_image(), address, size)

    def test_leb_rejects_truncated_or_oversized(self):
        for content in (b"\x80", b"\x80" * 12):
            with self.assertRaises(ValueError):
                _leb(content, 0)


class ProbeSafetyTests(unittest.TestCase):
    def test_public_repository_output_is_rejected(self):
        from tools.matching_probe import ROOT
        with self.assertRaisesRegex(ValueError, "ignored"):
            run(Path("unused"), ROOT / "research" / "objects")

    def test_hash_mismatch_stops_before_compilation(self):
        with tempfile.TemporaryDirectory() as directory, \
                patch("tools.matching_probe.shutil.which", return_value="synthetic-tool"), \
                patch("tools.matching_probe.read_nso", return_value=SimpleNamespace(source_sha256="a" * 64)), \
                patch("tools.matching_probe.subprocess.run") as compiler:
            with self.assertRaisesRegex(ValueError, "revision hash mismatch"):
                run(Path("unused"), Path(directory), expected_sha256="b" * 64)
            compiler.assert_not_called()


if __name__ == "__main__":
    unittest.main()
