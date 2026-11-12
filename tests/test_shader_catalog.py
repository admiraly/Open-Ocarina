# SPDX-License-Identifier: GPL-3.0-or-later
import hashlib
import struct
import tempfile
import unittest
from pathlib import Path

from tools.shader_catalog import cache_catalog, fnv_update, seed_catalog


def payload():
    # Entirely synthetic metadata and a tiny stand-in word vector, not game code.
    return (struct.pack("<IQ", 1, 0) + struct.pack("<Q", 0)
            + struct.pack("<IIIIIIIIIQ", 32, 1, 1, 128, 0, 0, 0, 0, 0, 1)
            + struct.pack("<iIQIIQ", 5, 32, 2, 0x07230203, 0, 0))


class ShaderCatalogTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "cache"

    def cache(self, data=None, magic=b"HSP1", flags=1):
        data = payload() if data is None else data
        self.path.write_bytes(struct.pack("<4sIQQQ", magic, 0, 123, 456, len(data))
                              + (struct.pack("<I", flags) if magic == b"HSP2" else b"") + data)

    def test_reads_compute_metadata_without_exporting_words(self):
        self.cache()
        result = cache_catalog(self.path, 456)
        row = result["records"][0]
        self.assertEqual(row["workgroup"], [32, 1, 1])
        self.assertEqual(row["first_stage"]["nvn_stage"], 5)
        self.assertEqual(row["first_stage"]["spirv_sha256"], hashlib.sha256(struct.pack("<II", 0x07230203, 0)).hexdigest())
        self.assertNotIn("spirv", row["first_stage"])

    def test_second_format_uncompressed(self):
        self.cache(magic=b"HSP2")
        self.assertEqual(cache_catalog(self.path)["records"][0]["stage_count"], 1)

    def test_compressed_record_is_explicitly_opaque(self):
        self.cache(data=b"synthetic compressed payload", magic=b"HSP2", flags=17)
        row = cache_catalog(self.path)["records"][0]
        self.assertIn("not decoded", row["metadata_status"])
        self.assertNotIn("first_stage", row)

    def test_partial_record_rejected(self):
        self.cache()
        self.path.write_bytes(self.path.read_bytes()[:-1])
        with self.assertRaises(ValueError):
            cache_catalog(self.path)

    def test_excessive_vector_rejected(self):
        self.cache(data=struct.pack("<IQ", 1, 1 << 63))
        with self.assertRaises(ValueError):
            cache_catalog(self.path)

    def test_missing_program_rejected(self):
        self.cache()
        with self.assertRaisesRegex(ValueError, "absent"):
            cache_catalog(self.path, 999)

    def test_unknown_stored_flags_rejected(self):
        self.cache(magic=b"HSP2", flags=128)
        with self.assertRaises(ValueError):
            cache_catalog(self.path)

    def test_seed_runtime_identity_differs_from_stored_seed_key(self):
        control = bytearray(216)
        control[:4] = b"\x34\x12\x76\x98"
        struct.pack_into("<I", control, 56, 1)
        struct.pack_into("<I", control, 68, 16)
        struct.pack_into("<H", control, 86, 5)
        code = b"synthetic-stage!"
        self.assertEqual(len(code), 16)
        target = fnv_update(fnv_update(0xcbf29ce484222325, control), code)
        data = (struct.pack("<8sIIQIII", b"HKSEED01", 1, 1, 12345, 5, len(control), len(code))
                + control + code + struct.pack("<II", 1, 0))
        self.path.write_bytes(data)
        row = seed_catalog(self.path, target)["matches"][0]
        self.assertNotEqual(row["program"], row["stored_seed_key"])
        self.assertEqual(row["native_code_bytes"], 16)
        self.path.write_bytes(data[:-4] + struct.pack("<I", 1))
        with self.assertRaisesRegex(ValueError, "nonexistent"):
            seed_catalog(self.path, target)


if __name__ == "__main__":
    unittest.main()
