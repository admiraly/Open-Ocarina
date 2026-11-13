# SPDX-License-Identifier: GPL-3.0-or-later
import hashlib
import json
from pathlib import Path
import shutil
import struct
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from tools import matrix_restore_native as native


class FixtureTests(unittest.TestCase):
    def test_all_fixture_headers_extents_and_known_rounding_references(self):
        for kind in native.KINDS:
            for width in (8, 16, 32):
                for case in native.FIXTURES:
                    data = native.fixture(kind, width, case)
                    magic, shape, requested, fixture, groups, na, nb, nc, nd = struct.unpack_from("<8s8I", data)
                    self.assertEqual((magic, shape, requested, fixture, groups),
                                     (b"OOMAT001", native.KINDS[kind], width, native.FIXTURES[case], 32 // width))
                    self.assertEqual(len(data), 40 + (na + nb) * 2 + (nc + nd) * 4)
                    self.assertEqual(nc, nd)
                    if case == "rounding":
                        words = struct.unpack(f"<{nd}I", data[-nd * 4:])
                        expected = (0, 0x80000000, 0x3F800000, 0x33800000)
                        self.assertEqual(words, tuple(expected[row % 4] for row in range(nd // 8) for _ in range(8)))

    def test_safe_groups_and_m_tiles_have_distinct_identity_outputs(self):
        data = native.fixture("checkpoint", 8, "identity")
        values = struct.unpack("<512I", data[-512 * 4:])
        self.assertNotEqual(values[:128], values[128:256])
        self.assertNotEqual(values[:64], values[64:128])

    def test_unknown_shapes_widths_and_fixtures_are_refused(self):
        for arguments in (("M16", 32, "identity"), ("base", 4, "identity"), ("base", 32, "game")):
            with self.assertRaises(ValueError):
                native.fixture(*arguments)


class NativeCPUValidationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not shutil.which("cc"):
            raise unittest.SkipTest("Optional CPU-only native validation requires a C compiler/Vulkan headers")
        check = subprocess.run(["cc", "-x", "c", "-E", "-"], input="#include <vulkan/vulkan.h>\n",
                               capture_output=True, text=True, timeout=10)
        if check.returncode:
            raise unittest.SkipTest("Optional CPU-only native validation requires Vulkan headers")
        cls.temporary = tempfile.TemporaryDirectory()
        cls.directory = Path(cls.temporary.name)
        cls.helper = cls.directory / "helper"
        # Linker trap replaces vkCreateInstance. Even an accidental regression
        # into the runtime branch cannot call the Vulkan loader or create an instance.
        trap = cls.directory / "trap.c"
        trap.write_text("#include <vulkan/vulkan.h>\n#include <stdlib.h>\n"
                        "VkResult __wrap_vkCreateInstance(const VkInstanceCreateInfo *i, const VkAllocationCallbacks *a, VkInstance *o) "
                        "{(void)i;(void)a;(void)o;_Exit(91);}\n")
        subprocess.run(["cc", "-std=c11", "-O2", "-DNDEBUG", "-Wall", "-Wextra", "-Werror",
                        str(native.ROOT / "src/probes/matrix_restore_native.c"), str(trap),
                        "-Wl,--wrap=vkCreateInstance", "-o", str(cls.helper), "-lvulkan"],
                       check=True, capture_output=True, timeout=30)

    @classmethod
    def tearDownClass(cls):
        cls.temporary.cleanup()

    def run_fixture(self, data):
        bundle = self.directory / "fixture.bin"
        bundle.write_bytes(data)
        return subprocess.run([str(self.helper), "cpu", str(bundle)], capture_output=True, text=True, timeout=5)

    def test_all_eighteen_cpu_paths_return_before_the_vulkan_trap(self):
        for kind in native.KINDS:
            for width in (8, 16, 32):
                for case in native.FIXTURES:
                    result = self.run_fixture(native.fixture(kind, width, case))
                    self.assertEqual(result.returncode, 0, result.stderr)
                    self.assertEqual(json.loads(result.stdout), {"cpu_validated": True, "vulkan_instance_created": False})

    def test_trap_really_intercepts_runtime_entry_without_loading_a_driver(self):
        fixture = self.directory / "fixture.bin"
        fixture.write_bytes(native.fixture("base", 32, "identity"))
        module = self.directory / "dummy.spv"
        module.write_bytes(struct.pack("<5I", 0x07230203, 0x00010600, 0, 1, 0))
        result = subprocess.run([str(self.helper), "properties", str(fixture), str(module), str(self.directory / "unused")],
                                capture_output=True, timeout=5)
        self.assertEqual(result.returncode, 91)

    def test_truncated_corrupt_inputs_and_reference_words_are_refused_before_vulkan(self):
        original = native.fixture("checkpoint", 16, "layout")
        for offset in (0, 8, 12, 24, 40, len(original) - 1):
            data = bytearray(original)
            data[offset] ^= 1
            self.assertNotEqual(self.run_fixture(data).returncode, 0)
        self.assertNotEqual(self.run_fixture(original[:-4]).returncode, 0)


class FrozenAdmissionTests(unittest.TestCase):
    def test_wrong_plan_hash_is_rejected_without_loading_vulkan(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)
            (path / "plan.json").write_text("{}")
            with self.assertRaisesRegex(ValueError, "plan hash"):
                native.reviewed(path, "0" * 64)

    def test_changed_frozen_artifact_and_missing_dependency_inventory_are_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)
            (path / "helper").write_text("changed")
            plan = {"schema": "open-ocarina.matrix-restoration-prepared/v1", "driver_profile": native.E5,
                    "dependencies": {name: native.digest(native.ROOT / name) for name in native.DEPENDENCIES},
                    "artifacts": {"helper": "0" * 64}}
            encoded = json.dumps(plan).encode()
            (path / "plan.json").write_bytes(encoded)
            with self.assertRaisesRegex(ValueError, "artifact"):
                native.reviewed(path, hashlib.sha256(encoded).hexdigest())
            plan["dependencies"] = {}
            encoded = json.dumps(plan).encode()
            (path / "plan.json").write_bytes(encoded)
            with self.assertRaisesRegex(ValueError, "inventory"):
                native.reviewed(path, hashlib.sha256(encoded).hexdigest())

    def test_unreviewed_case_never_reaches_driver_identity_or_process_creation(self):
        with patch.object(native, "reviewed", return_value=(Path("unused"), {"cases": []})), \
                patch.object(native, "driver_identity") as identity, patch.object(native, "run_process") as process:
            with self.assertRaisesRegex(ValueError, "one frozen case"):
                native.execute(Path("unused"), "0" * 64, Path("unused"), "game", "dispatch")
            identity.assert_not_called()
            process.assert_not_called()

    def test_unapproved_driver_never_starts_native_process(self):
        case = {"name": "base-identity-sg32", "module": "module", "bundle": "fixture"}
        plan = {"cases": [case], "artifacts": {"module": "x", "fixture": "x"}}
        with patch.object(native, "reviewed", return_value=(Path("unused"), plan)), \
                patch.object(native, "driver_identity", return_value={"library_sha256": "0" * 64, "icd_sha256": "0" * 64}), \
                patch.object(native, "run_process") as process:
            with self.assertRaisesRegex(ValueError, "E5"):
                native.execute(Path("unused"), "0" * 64, Path("unused"), case["name"], "compile")
            process.assert_not_called()


if __name__ == "__main__":
    unittest.main()
