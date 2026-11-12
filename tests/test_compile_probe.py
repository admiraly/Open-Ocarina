import argparse
import hashlib
import json
from pathlib import Path
import struct
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from tools.compile_probe import ROOT, driver_identity, parse_environment, private_output, reflect, run


def instruction(opcode, *operands):
    return [((len(operands) + 1) << 16) | opcode, *operands]


def shader(*extra):
    words = [0x07230203, 0x00010600, 0, 256, 0]
    words += instruction(15, 5, 1, 0x6e69616d, 0)
    words += instruction(16, 1, 17, 64, 1, 1)
    for item in extra:
        words += item
    return struct.pack(f"<{len(words)}I", *words)


def uniform(binding=0):
    return [instruction(21, 2, 32, 0), instruction(30, 3, 2), instruction(71, 3, 2),
            instruction(32, 4, 2, 3), instruction(59, 4, 5, 2),
            instruction(71, 5, 33, binding), instruction(71, 5, 34, 0)]


class CompileProbeTests(unittest.TestCase):
    def test_reflection_combines_compatible_descriptor_aliases(self):
        data = shader(*uniform(), instruction(59, 4, 6, 2), instruction(71, 6, 33, 0), instruction(71, 6, 34, 0))
        self.assertEqual(reflect(data)["bindings"], [{"set": 0, "binding": 0, "type": 6, "count": 1}])

    def test_literal_storage_buffer_array(self):
        data = shader(instruction(21, 2, 32, 0), instruction(43, 2, 9, 3),
                      instruction(30, 3, 2), instruction(71, 3, 2), instruction(28, 10, 3, 9),
                      instruction(32, 4, 12, 10), instruction(59, 4, 5, 12),
                      instruction(71, 5, 33, 7), instruction(71, 5, 34, 0))
        self.assertEqual(reflect(data)["bindings"], [{"set": 0, "binding": 7, "type": 7, "count": 3}])

    def test_exact_push_constant_extent(self):
        data = shader(instruction(21, 2, 32, 0), instruction(21, 6, 64, 0),
                      instruction(30, 3, 2, 6), instruction(72, 3, 0, 35, 0),
                      instruction(72, 3, 1, 35, 48), instruction(32, 4, 9, 3), instruction(59, 4, 5, 9))
        self.assertEqual(reflect(data)["push_constant_bytes"], 56)

    def test_missing_push_member_offset_is_rejected(self):
        data = shader(instruction(21, 2, 32, 0), instruction(30, 3, 2),
                      instruction(32, 4, 9, 3), instruction(59, 4, 5, 9))
        with self.assertRaisesRegex(ValueError, "member offsets"):
            reflect(data)

    def test_invalid_instruction_and_header(self):
        for data in (b"bad", shader() + struct.pack("<I", 0), shader() + struct.pack("<I", (8 << 16) | 71)):
            with self.subTest(data_length=len(data)), self.assertRaises(ValueError):
                reflect(data)

    def test_unknown_descriptor_is_not_guessed(self):
        data = shader(instruction(29, 3, 2), instruction(32, 4, 12, 3), instruction(59, 4, 5, 12),
                      instruction(71, 5, 33, 0), instruction(71, 5, 34, 0))
        with self.assertRaisesRegex(ValueError, "decorated block"):
            reflect(data)

    def test_descriptor_storage_must_agree(self):
        data = shader(*uniform(), instruction(59, 4, 6, 12))
        with self.assertRaisesRegex(ValueError, "storage classes"):
            reflect(data)

    def test_incompatible_aliases_are_rejected(self):
        data = shader(*uniform(), instruction(30, 10, 2), instruction(71, 10, 2), instruction(32, 11, 12, 10),
                      instruction(59, 11, 12, 12), instruction(71, 12, 33, 0), instruction(71, 12, 34, 0))
        with self.assertRaisesRegex(ValueError, "incompatible"):
            reflect(data)

    def test_layout_limits_are_bounded(self):
        with self.assertRaisesRegex(ValueError, "layout exceeds"):
            reflect(shader(*uniform(), instruction(71, 5, 34, 8)))

    def test_explicit_numeric_overrides_only(self):
        self.assertEqual(parse_environment(["HS_SPILL_BATCH=1"]), {"HS_SPILL_BATCH": "1"})
        for items in (["LD_PRELOAD=anything"], ["HS_SPILL_BATCH=yes"], ["HS_SPILL_BATCH=1", "HS_SPILL_BATCH=2"]):
            with self.subTest(items=items), self.assertRaises(ValueError):
                parse_environment(items)

    def test_shader_hash_is_checked_before_compiler_or_driver_use(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "input.spv"
            path.write_bytes(shader())
            args = argparse.Namespace(input=path, sha256="0" * 64, output=Path(temporary) / "output")
            with patch("tools.compile_probe.subprocess.run") as child:
                with self.assertRaisesRegex(ValueError, "digest differs"):
                    run(args)
                child.assert_not_called()

    def test_output_paths_remain_private_and_new(self):
        with self.assertRaisesRegex(ValueError, "private"):
            private_output(ROOT / "research" / "compile-results")
        with tempfile.TemporaryDirectory() as temporary:
            with self.assertRaisesRegex(ValueError, "exists"):
                private_output(Path(temporary))

    def test_helper_has_no_execution_entry_points(self):
        source = (ROOT / "tools" / "vulkan_compile_probe.c").read_text()
        for forbidden in ("vkCmdDispatch", "vkQueueSubmit", "vkGetDeviceQueue", "vkCreateCommandPool"):
            self.assertNotIn(forbidden, source)

    def test_helper_keeps_shader_optimization_enabled(self):
        source = (ROOT / "tools" / "vulkan_compile_probe.c").read_text()
        self.assertNotIn("DISABLE_OPTIMIZATION", source)
        self.assertIn(".robustBufferAccess2 = VK_TRUE", source)
        self.assertIn(".subgroupSizeControl = VK_TRUE, .computeFullSubgroups = VK_TRUE", source)

    def test_required16_is_an_explicit_policy_not_a_device_feature_change(self):
        source = (ROOT / "tools" / "vulkan_compile_probe.c").read_text()
        self.assertIn('!strcmp(argv[3], "pinned16") ? 16 : 32', source)
        self.assertIn('.requiredSubgroupSize = required_width', source)
        self.assertIn('.subgroupSizeControl = VK_TRUE, .computeFullSubgroups = VK_TRUE', source)

    def test_driver_identity_pins_readable_library(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            library = root / "fake-driver.so"
            library.write_bytes(b"synthetic driver fixture")
            driver = root / "driver.json"
            driver.write_text('{"ICD":{"library_path":"fake-driver.so"}}')
            result = driver_identity(driver)
            self.assertEqual(result["library_sha256"], hashlib.sha256(library.read_bytes()).hexdigest())
            self.assertNotIn("library_path", result)

    def test_failed_structural_validation_prevents_compiler_or_vulkan_use(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "input.spv"
            source.write_bytes(shader(*uniform()))
            library = root / "driver.so"
            library.write_bytes(b"synthetic library")
            driver = root / "driver.json"
            driver.write_text(json.dumps({"ICD": {"library_path": str(library)}}))
            args = argparse.Namespace(input=source, sha256=hashlib.sha256(source.read_bytes()).hexdigest(),
                driver=driver, mesa_build=None, timeout=30, device=0, push_constant_bytes=88,
                env=[], output=root / "output", validator="test-validator")
            with patch("tools.compile_probe.subprocess.run", return_value=subprocess.CompletedProcess([], 1, "", "rejected")) as child:
                with self.assertRaisesRegex(ValueError, "validation failed"):
                    run(args)
                self.assertEqual(child.call_count, 1)
                self.assertEqual(child.call_args.args[0][0], "test-validator")


if __name__ == "__main__":
    unittest.main()
