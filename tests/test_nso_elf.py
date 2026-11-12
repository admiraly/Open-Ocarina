# SPDX-License-Identifier: GPL-3.0-or-later
"""Synthetic NSO/ELF fixtures; no game code or external analysis tools required."""
import hashlib
import json
from pathlib import Path
import struct
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from tools import nso_elf


TEXT = bytes(range(16))
RODATA = b"constant"
DATA = b"variable"


def synthetic_nso(*, flags=0x38, bss=16, compressed_text=None):
    header = bytearray(256)
    header[:4] = b"NSO0"
    struct.pack_into("<I", header, 12, flags)
    header[64:96] = bytes(range(32))
    struct.pack_into("<I", header, 60, bss)
    body = bytearray()
    for index, (payload, memory_offset) in enumerate(zip((TEXT, RODATA, DATA), (0, 0x1000, 0x2000))):
        stored = compressed_text if index == 0 and compressed_text is not None else payload
        struct.pack_into("<III", header, 16 + 16 * index, 256 + len(body), memory_offset, len(payload))
        struct.pack_into("<I", header, 96 + 4 * index, len(stored))
        header[160 + 32 * index:192 + 32 * index] = hashlib.sha256(payload).digest()
        body.extend(stored)
    return header + body


class NsoTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.directory = Path(self.temporary.name)
        self.input = self.directory / "synthetic.nso"

    def write(self, contents=None):
        self.input.write_bytes(synthetic_nso() if contents is None else contents)
        return self.input

    def test_reads_uncompressed_segments_and_declared_hashes(self):
        path = self.write()
        image = nso_elf.read_nso(path)
        self.assertEqual(image.segments[0].data, TEXT)
        self.assertEqual(image.segments[1].data, RODATA)
        self.assertEqual(image.segments[2].data, DATA)
        self.assertEqual(image.segments[0].file_offset, 256)
        self.assertEqual(image.segments[2].memory_offset, 0x2000)
        self.assertEqual(image.bss_size, 16)
        self.assertEqual(image.source_sha256, hashlib.sha256(path.read_bytes()).hexdigest())

    def test_rejects_short_or_wrong_header(self):
        for content in (b"NSO0", b"NOPE" + bytes(252)):
            with self.subTest(content=content[:4]), self.assertRaisesRegex(ValueError, "header"):
                nso_elf.read_nso(self.write(content))

    def test_rejects_unknown_version_reserved_fields_and_flags(self):
        for offset, value in ((4, 1), (8, 1), (12, 0x100)):
            contents = synthetic_nso()
            struct.pack_into("<I", contents, offset, value)
            with self.subTest(offset=offset), self.assertRaises(ValueError):
                nso_elf.read_nso(self.write(contents))

    def test_rejects_zbic_instead_of_treating_as_lz4(self):
        with self.assertRaisesRegex(ValueError, "ZBIC"):
            nso_elf.read_nso(self.write(synthetic_nso(flags=0x80)))

    def test_preserves_bit_six_without_interpreting_it(self):
        image = nso_elf.read_nso(self.write(synthetic_nso(flags=0x78)))
        _, metadata = nso_elf.build_elf(image, 0x10000)
        self.assertEqual(metadata["uninterpreted_source_flags"], 0x40)

    def test_rejects_corrupted_segment_hash(self):
        contents = synthetic_nso()
        contents[256] ^= 1
        with self.assertRaisesRegex(ValueError, "SHA-256 mismatch"):
            nso_elf.read_nso(self.write(contents))

    def test_rejects_truncated_segment(self):
        with self.assertRaisesRegex(ValueError, "Truncated"):
            nso_elf.read_nso(self.write(synthetic_nso()[:-1]))

    def test_rejects_segment_inside_header(self):
        contents = synthetic_nso()
        struct.pack_into("<I", contents, 16, 128)
        with self.assertRaisesRegex(ValueError, "file range"):
            nso_elf.read_nso(self.write(contents))

    def test_rejects_file_segment_overlap(self):
        contents = synthetic_nso()
        struct.pack_into("<I", contents, 32, 260)
        with self.assertRaisesRegex(ValueError, "Overlapping segments"):
            nso_elf.read_nso(self.write(contents))

    def test_rejects_memory_overlap_including_bss(self):
        contents = synthetic_nso()
        struct.pack_into("<I", contents, 52, 0xFF0)
        with self.assertRaisesRegex(ValueError, "Overlapping segments"):
            nso_elf.read_nso(self.write(contents))

    def test_rejects_empty_or_misaligned_text(self):
        for offset, value in ((24, 0), (20, 2), (24, 15)):
            contents = synthetic_nso()
            struct.pack_into("<I", contents, offset, value)
            with self.subTest(offset=offset, value=value), self.assertRaises(ValueError):
                nso_elf.read_nso(self.write(contents))

    def test_limits_sparse_virtual_image_and_bss(self):
        for offset, value in ((52, 0x10000000), (60, 0x10000000)):
            contents = synthetic_nso()
            struct.pack_into("<I", contents, offset, value)
            with self.subTest(offset=offset), self.assertRaisesRegex(ValueError, "limit"):
                nso_elf.read_nso(self.write(contents), max_image_bytes=0x4000)

    def test_rejects_nonpositive_image_limit(self):
        with self.assertRaisesRegex(ValueError, "positive"):
            nso_elf.read_nso(self.write(), max_image_bytes=0)

    def test_compression_dependency_missing_is_actionable(self):
        contents = synthetic_nso(flags=0x39, compressed_text=b"compressed")
        with patch.object(nso_elf.importlib, "import_module", side_effect=ModuleNotFoundError), self.assertRaisesRegex(ValueError, "optional 'lz4'"):
            nso_elf.read_nso(self.write(contents))

    def test_decompression_uses_expected_size_and_checks_hash(self):
        calls = []

        def decompress(data, *, uncompressed_size):
            calls.append((data, uncompressed_size))
            return TEXT

        contents = synthetic_nso(flags=0x39, compressed_text=b"compressed")
        with patch.object(nso_elf.importlib, "import_module", return_value=SimpleNamespace(decompress=decompress)):
            image = nso_elf.read_nso(self.write(contents))
        self.assertEqual(calls, [(b"compressed", len(TEXT))])
        self.assertEqual(image.segments[0].data, TEXT)
        self.assertTrue(image.segments[0].compressed)

    def test_rejects_invalid_or_wrong_sized_decompression(self):
        contents = synthetic_nso(flags=0x39, compressed_text=b"compressed")
        for decoder in (lambda *a, **k: b"short", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("bad block"))):
            with patch.object(nso_elf.importlib, "import_module", return_value=SimpleNamespace(decompress=decoder)):
                with self.assertRaises(ValueError):
                    nso_elf.read_nso(self.write(contents))

    def test_rejects_input_symlink(self):
        self.write()
        alias = self.directory / "alias.nso"
        alias.symlink_to(self.input)
        with self.assertRaisesRegex(ValueError, "non-symlink"):
            nso_elf.read_nso(alias)

    def test_elf_machine_sections_flags_and_bss(self):
        image = nso_elf.read_nso(self.write())
        content, metadata = nso_elf.build_elf(image, 0x10000)
        header = struct.unpack_from("<16sHHIQQQIHHHHHH", content)
        self.assertEqual(header[0][:7], b"\x7fELF\x02\x01\x01")
        self.assertEqual(header[1:4], (2, 183, 1))
        self.assertEqual(header[4], 0x10000)
        self.assertEqual(header[10], 3)
        self.assertEqual(header[12:14], (6, 5))
        for index, permission in enumerate((5, 4, 6)):
            ptype, flags, offset, address, _, filesz, memsz, alignment = struct.unpack_from("<IIQQQQQQ", content, 64 + 56 * index)
            segment = image.segments[index]
            self.assertEqual((ptype, flags), (1, permission))
            self.assertEqual(address, 0x10000 + segment.memory_offset)
            self.assertEqual(address % alignment, offset % alignment)
            self.assertEqual(content[offset:offset + filesz], segment.data)
            self.assertEqual(memsz, filesz + (16 if index == 2 else 0))
        bss = struct.unpack_from("<IIQQQQIIQQ", content, header[6] + 4 * 64)
        self.assertEqual(bss[1], 8)
        self.assertEqual(bss[3:6], (0x12008, metadata["segments"][2]["file_offset"] + 8, 16))
        self.assertTrue(metadata["entry_is_conventional"])

    def test_non_page_aligned_base_keeps_load_congruence(self):
        image = nso_elf.read_nso(self.write())
        content, _ = nso_elf.build_elf(image, 0x10004)
        for index in range(3):
            fields = struct.unpack_from("<IIQQQQQQ", content, 64 + index * 56)
            self.assertEqual(fields[2] % fields[7], fields[3] % fields[7])

    def test_rejects_invalid_base_entry_and_overflow(self):
        image = nso_elf.read_nso(self.write())
        for base in (-4, 3, 1 << 64):
            with self.subTest(base=base), self.assertRaises(ValueError):
                nso_elf.build_elf(image, base)
        for entry in (0x10002, 0x11000, 0x10010):
            with self.subTest(entry=entry), self.assertRaises(ValueError):
                nso_elf.build_elf(image, 0x10000, entry)
        _, metadata = nso_elf.build_elf(image, 0x10000, 0x1000C)
        self.assertFalse(metadata["entry_is_conventional"])

    def test_conversion_is_deterministic_and_preserves_input(self):
        self.write()
        original = self.input.read_bytes()
        first, second = self.directory / "first.elf", self.directory / "second.elf"
        left = nso_elf.convert(self.input, first, 0x10000)
        right = nso_elf.convert(self.input, second, 0x10000)
        self.assertEqual(left, right)
        self.assertEqual(first.read_bytes(), second.read_bytes())
        self.assertEqual(self.input.read_bytes(), original)

    def test_output_cannot_replace_input_or_follow_symlink(self):
        self.write()
        for output in (self.input, self.directory / "alias.elf"):
            if output != self.input:
                output.symlink_to(self.input)
            with self.subTest(output=output.name), self.assertRaises(ValueError):
                nso_elf.convert(self.input, output, 0x10000)
        self.assertEqual(self.input.read_bytes(), synthetic_nso())

    def test_repository_output_must_be_private(self):
        self.write()
        fake_root = self.directory / "repo"
        with patch.object(nso_elf, "ROOT", fake_root):
            with self.assertRaisesRegex(ValueError, "must stay under"):
                nso_elf.convert(self.input, fake_root / "public.elf", 0x10000)
            nso_elf.convert(self.input, fake_root / "local" / "ok.elf", 0x10000)
        self.assertTrue((fake_root / "local" / "ok.elf").is_file())

    def test_cli_alias_outputs_only_metadata(self):
        self.write()
        command = [sys.executable, "-m", "tools.nso_elf", "--input", str(self.input),
                   "--output", str(self.directory / "out.elf"), "--base", "0x10000"]
        result = subprocess.run(command, cwd=nso_elf.ROOT, capture_output=True, text=True, check=False)
        self.assertEqual(result.returncode, 0, result.stderr)
        metadata = json.loads(result.stdout)
        self.assertEqual(metadata["machine"], 183)
        self.assertNotIn("data", metadata["segments"][0])
        self.assertIn("limitations", metadata)


if __name__ == "__main__":
    unittest.main()
