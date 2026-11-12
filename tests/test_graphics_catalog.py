# SPDX-License-Identifier: GPL-3.0-or-later
import copy
import hashlib
import struct
import tempfile
import unittest
from pathlib import Path

from tools.graphics_catalog import stage_catalog


PROFILE = {"schema": 1, "cache_stamp": "000000000000007b", "info_bytes": 16,
           "descriptor_bytes": [4, 4, 4, 4, 4, 4], "attribute_bytes": 8,
           "provenance": {"kind": "Synthetic layout for parser regression tests only"}}
SPV = struct.pack("<IIIII", 0x07230203, 0x00010600, 0, 1, 0)


def stage(nvn, vk):
    # Deliberately pointer-like raw Info bytes must be skipped, never followed.
    info = b"\xff" * 16
    descriptors = struct.pack("<QI", 1, 123) + struct.pack("<Q", 0) * 5
    mapping = struct.pack("<QQQ", 1, 3, 4)
    handles = struct.pack("<QQII", 1, 2, 17, 19)
    return struct.pack("<iIQ", nvn, vk, 5) + SPV + struct.pack("<Q", 0) + info + descriptors + mapping + handles


class GraphicsCatalogTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.path = Path(temp.name) / "cache"
        self.payload = (struct.pack("<IQQ", 2, 0, 0)
                        + struct.pack("<IIIIIIIIIQ", 1, 1, 1, 0, 3, 64, 16, 128, 32, 3)
                        + stage(6, 64) + stage(7, 128) + stage(1, 16))

    def write(self, payload=None, magic=b"HSP1", stored_flags=2):
        payload = self.payload if payload is None else payload
        self.path.write_bytes(struct.pack("<4sIQQQ", magic, 0, 123, 456, len(payload))
                              + (struct.pack("<I", stored_flags) if magic == b"HSP2" else b"") + payload)

    def test_reads_every_stage_and_ends_exactly(self):
        self.write()
        row = stage_catalog(self.path, PROFILE, 456)["records"][0]
        self.assertEqual([s["vk_stage_name"] for s in row["stages"]], ["task_ext", "mesh_ext", "fragment"])
        self.assertEqual(row["stages"][2]["spirv_sha256"], hashlib.sha256(SPV).hexdigest())
        self.assertEqual(row["stages"][1]["descriptor_record_counts"]["constant_buffer"], 1)
        self.assertEqual(row["stages"][0]["legacy_mapping_count"], 1)
        self.assertEqual(row["stages"][0]["handle_program_count"], 1)
        self.assertEqual(row["fallback_metadata"]["payload_words"], 128)
        self.assertNotIn("first_stage", row)
        self.assertNotIn("spirv", row["stages"][0])

    def test_wrong_abi_and_trailing_data_rejected(self):
        self.write(self.payload + b"extra")
        with self.assertRaisesRegex(ValueError, "unconsumed"):
            stage_catalog(self.path, PROFILE)
        self.write()
        wrong = copy.deepcopy(PROFILE)
        wrong["info_bytes"] = 8
        with self.assertRaises(ValueError):
            stage_catalog(self.path, wrong)

    def test_stamp_and_profile_validation(self):
        self.write()
        for field, value in [("cache_stamp", "wrong"), ("info_bytes", -1),
                             ("descriptor_bytes", [4]), ("provenance", {})]:
            wrong = copy.deepcopy(PROFILE)
            wrong[field] = value
            with self.assertRaises(ValueError):
                stage_catalog(self.path, wrong)

    def test_later_stage_invalid_magic_rejected(self):
        self.write(self.payload[:-len(stage(1, 16))] + stage(1, 16).replace(SPV, b"bad!" + SPV[4:]))
        with self.assertRaisesRegex(ValueError, "magic"):
            stage_catalog(self.path, PROFILE)

    def test_count_overflow_bounded_by_record(self):
        tail = stage(1, 16)
        # Last stage's first descriptor vector, after two IDs, SPV extent, c1 extent, raw Info.
        at = 16 + len(SPV) + 8 + 16
        tail = tail[:at] + struct.pack("<Q", 1 << 63) + tail[at + 8:]
        self.write(self.payload[:-len(stage(1, 16))] + tail)
        with self.assertRaisesRegex(ValueError, "record"):
            stage_catalog(self.path, PROFILE)

    def test_hsp2_uncompressed_supported_compressed_rejected(self):
        self.write(magic=b"HSP2")
        self.assertEqual(len(stage_catalog(self.path, PROFILE)["records"][0]["stages"]), 3)
        self.write(b"synthetic opaque compressed record", magic=b"HSP2", stored_flags=18)
        with self.assertRaisesRegex(ValueError, "Compressed"):
            stage_catalog(self.path, PROFILE)


if __name__ == "__main__":
    unittest.main()
