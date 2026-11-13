# SPDX-License-Identifier: GPL-3.0-or-later
import hashlib
import struct
import tempfile
from pathlib import Path
import unittest
from unittest.mock import patch

from tools import ndve_stage_export as export


def synthetic_stage():
    """Independent framing fixture only; never a game stage or executable input."""
    control = bytearray(216)
    control[:4] = b"\x34\x12\x76\x98"
    struct.pack_into("<I", control, 56, 22400)
    struct.pack_into("<I", control, 68, 22528)
    struct.pack_into("<H", control, 86, 5)
    struct.pack_into("<4I", control, 88, 32, 1, 1, 3360)
    code = bytearray(22528)
    for index in range(72):
        struct.pack_into("<I", code, index * 16, 0x23c | (7 << 12))
    return bytes(control), bytes(code)


class NDVEStageExportTests(unittest.TestCase):
    def test_exact_digest_key_shape_and_hmma_count_are_jointly_required(self):
        control, code = synthetic_stage()
        digest = hashlib.sha256(control + code).hexdigest()
        key = f"{export.fnv(code, export.fnv(control)):016x}"
        with patch.object(export, "NATIVE_SHA", digest), patch.object(export, "PROGRAM", key):
            result = export.validate_native(control, code)
            self.assertEqual(result["encoded_HMMA16816F16_nonfalse_predicate_sites"], 72)
            for offset, value in ((56, 16), (68, 16), (88, 64), (100, 0)):
                changed = bytearray(control)
                struct.pack_into("<I", changed, offset, value)
                with self.subTest(offset=offset), self.assertRaises(ValueError):
                    export.validate_native(bytes(changed), code)
            with self.assertRaises(ValueError):
                export.validate_native(control, code[:-1])
        with self.assertRaisesRegex(ValueError, "digest or runtime key"):
            export.validate_native(control, code)

    def test_complete_seed_validation_rejects_trailing_data_and_bad_indices(self):
        control, code = synthetic_stage()
        digest = hashlib.sha256(control + code).hexdigest()
        key = f"{export.fnv(code, export.fnv(control)):016x}"
        seed = struct.pack("<8sIIQIII", b"HKSEED01", 1, 1, 0, 5, 216, len(code)) + control + code + struct.pack("<II", 1, 0)
        with tempfile.TemporaryDirectory() as directory, patch.object(export, "NATIVE_SHA", digest), patch.object(export, "PROGRAM", key):
            path = Path(directory) / "seed.bin"
            path.write_bytes(seed)
            singleton, identity = export.singleton_seed(path)
            self.assertEqual(singleton, seed)
            self.assertEqual(identity["seed_stage_index"], 0)
            for data in (seed + b"trailing", seed[:-1], seed[:-4] + struct.pack("<I", 1)):
                path.write_bytes(data)
                with self.assertRaises(ValueError):
                    export.singleton_seed(path)

    def test_restricted_helper_removes_device_query_and_rejects_changed_source(self):
        sample = ('#include <dlfcn.h>\nvoid body(){\n                auto tr = translate(env, false);\n}\n'
                  '    u32 ReadCbufValue(u32 index, u32 offset) override {\n}\n'
                  '    Shader::TextureType ReadTextureType(u32 handle) override {\n}\n'
                  '    bool IsTexturePixelFormatInteger(u32 handle) override {\n}\n'
                  '    bool IsCbufStorageBufferPointer(u32 index, u32 offset) override {\n}\n'
                  '    u32 TextureBoundBuffer() const override {\n}\n'
                  'Shader::TexturePixelFormat ReadTexturePixelFormat(u32) override { return Shader::TexturePixelFormat{}; }\n'
                  '// ---- device query (--target query):\nvoid query(){vkCreateInstance();dlopen();}\n')
        with patch.object(export, "BAKE_SHA", hashlib.sha256(sample.encode()).hexdigest()):
            result = export.restricted_driver(sample)
            self.assertNotIn("vkCreateInstance(", result)
            self.assertNotIn("dlopen(", result)
            self.assertNotIn("dlfcn.h", result)
            self.assertIn("MatrixMulAddF16", result)
            self.assertIn('target_preset("tensor-software")', result)
            self.assertIn("0xe409733c45530d75ULL", result)
            self.assertIn("options.variant_cap = 1", result)
            self.assertIn("options.bank = false", result)
            self.assertIn("offline_query cbuf ", result)
            self.assertIn("offline_query texture_pixel_format ", result)
        with self.assertRaisesRegex(ValueError, "source changed"):
            export.restricted_driver(sample)

    def test_cpu_limits_and_ambient_shader_options_are_not_inherited(self):
        with patch.dict("os.environ", {"RX_VK_NO_F16": "1", "EDEN_NX2_TENSOR": "stub", "INTEL_DEBUG": "1"}):
            env = export.clean_environment()
        self.assertNotIn("RX_VK_NO_F16", env)
        self.assertNotIn("EDEN_NX2_TENSOR", env)
        self.assertNotIn("INTEL_DEBUG", env)
        for jobs in (0, 5, True):
            with self.subTest(jobs=jobs), self.assertRaisesRegex(ValueError, "CPU jobs"):
                export.export(Path("unused"), Path("unused"), Path("unused"), jobs)

    def test_environment_queries_retain_each_unknown_answer_and_duplicates(self):
        queries = export.environment_queries("offline_query cbuf bank=0 offset=88 answer=0 resolved=0\n"
                                             "offline_query cbuf bank=0 offset=88 answer=0 resolved=0\n"
                                             "offline_query cbuf bank=1 offset=4 answer=7 resolved=1\n"
                                             "offline_query texture_type handle=0 answer=2 resolved=0\n")
        self.assertEqual(len(queries), 3)
        self.assertEqual(sum(row["calls"] for row in queries), 4)
        self.assertEqual(sum(row["resolved"] == 0 for row in queries), 2)
        with self.assertRaises(ValueError):
            export.environment_queries("no audit\n")


if __name__ == "__main__":
    unittest.main()
