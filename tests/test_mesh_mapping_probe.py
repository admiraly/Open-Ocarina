# SPDX-License-Identifier: GPL-3.0-or-later
import hashlib
import json
from pathlib import Path
import struct
import tempfile
import unittest
from unittest.mock import patch

from tools import mesh_mapping_probe as probe


class MeshMappingProbeTests(unittest.TestCase):
    def test_golden_mapping_and_all_output_positions(self):
        data = probe.packed_golden()
        self.assertEqual(len(data), 896)
        self.assertEqual(probe.check_output(data), hashlib.sha256(data).hexdigest())
        for index in range(224):
            words = probe.golden_words()
            words[index] ^= 1
            with self.assertRaisesRegex(ValueError, "Mapping differs"):
                probe.check_output(struct.pack("<224I", *words))
        for incomplete in (b"", data[:-4], bytes([0xcd]) * 896):
            with self.assertRaises(ValueError):
                probe.check_output(incomplete)

    def test_shader_is_independent_fixed_mesh_zero_geometry(self):
        source = probe.shader_source()
        self.assertIn("local_size_x=32", source)
        self.assertIn("SetMeshOutputsEXT(0u, 0u)", source)
        self.assertNotIn("while", source)
        self.assertNotIn("for (", source)
        self.assertNotIn("return", source)
        for builtin in ("gl_LocalInvocationID", "gl_LocalInvocationIndex", "gl_SubgroupInvocationID",
                        "gl_SubgroupSize", "gl_SubgroupID"):
            self.assertIn(builtin, source)

    def test_build_never_invokes_execution_helper_or_driver(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "prepared"
            commands = []

            def cpu_tool(command, env, destination, stem, timeout):
                commands.append(command)
                if stem.startswith("version-"):
                    return "Synthetic test tool version\n"
                if stem == "compile-shader":
                    (destination / "mapping.spv").write_bytes(struct.pack("<5I", 0x07230203, 0x00010600, 0, 1, 0))
                if stem == "build-helper":
                    (destination / "mesh-execution-helper").write_bytes(b"synthetic helper fixture")
                return ""

            with patch.object(probe, "run_process", side_effect=cpu_tool), patch.object(probe, "driver_identity") as driver:
                report = probe.build(output)
            driver.assert_not_called()
            self.assertTrue(report["build_only"])
            self.assertFalse(report["gpu_executed"])
            self.assertTrue(all(command[0] in ("cc", "glslangValidator", "spirv-val", "pkg-config") for command in commands))
            manifest = output / "build-report.json"
            reviewed = probe.digest(manifest)
            probe.prepared(output, reviewed)
            (output / "output.bin").write_bytes(b"synthetic prior execution marker")
            with self.assertRaisesRegex(ValueError, "already been executed"):
                probe.prepared(output, reviewed)
            (output / "output.bin").unlink()
            with self.assertRaisesRegex(ValueError, "reviewed"):
                probe.prepared(output, "0" * 64)
            (output / "mapping.spv").write_bytes(b"tampered synthetic module")
            with self.assertRaisesRegex(ValueError, "hash mismatch"):
                probe.prepared(output, reviewed)

    def test_prepared_manifest_edit_fails(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory)
            manifest = output / "build-report.json"
            manifest.write_text(json.dumps({"schema": 1, "build_only": False, "gpu_executed": True}))
            with self.assertRaisesRegex(ValueError, "Unexpected"):
                probe.prepared(output, probe.digest(manifest))
            manifest.write_text("{}")
            with self.assertRaisesRegex(ValueError, "reviewed"):
                probe.prepared(output, "0" * 64)

    def test_execution_metadata_requires_actual_mesh_width(self):
        good = {"synthetic_only": True, "mesh_draw_executed": True, "vendor_id": 0x8086,
                "device": "Intel Arc A770", "actual_width": 32, "requested_width": 32,
                "executable_stage": 128, "width_source": "pipeline_executable_properties",
                "workgroup": [32, 1, 1], "draw_mesh_tasks": [1, 1, 1]}
        probe.validate_metadata(good)
        for wrong in ({"actual_width": 16}, {"executable_stage": 32}, {"width_source": "requested"},
                      {"draw_mesh_tasks": [2, 1, 1]}, {"vendor_id": 0}, {"mesh_draw_executed": False}):
            with self.assertRaises(ValueError):
                probe.validate_metadata(good | wrong)

    def test_environment_excludes_driver_overrides_and_injection(self):
        with patch.dict(probe.os.environ, {"HS_FORCE_SIMD8": "all", "LD_PRELOAD": "bad", "INTEL_DEBUG": "all",
                                          "VK_INSTANCE_LAYERS": "bad", "PATH": "/test/bin"}, clear=True):
            self.assertEqual(probe.environment(), {"PATH": "/test/bin"})

    def test_competing_game_aborts_without_signalling(self):
        process = probe.subprocess.CompletedProcess([], 0, "1\n", "")
        with patch.object(probe.subprocess, "run", return_value=process) as run, patch.object(probe.os, "killpg") as kill:
            with self.assertRaisesRegex(ValueError, "Competing"):
                probe.ensure_no_games()
            self.assertIn("-i", run.call_args.args[0])
            kill.assert_not_called()

    def test_helper_is_separate_single_draw_and_bounded_wait(self):
        source = (probe.ROOT / "tools" / "vulkan_mesh_execution_probe.c").read_text()
        self.assertEqual(source.count("draw(command, 1, 1, 1);"), 1)
        self.assertIn("UINT64_C(2000000000)", source)
        self.assertIn("_Exit(3)", source)
        self.assertNotIn("vkQueueWaitIdle", source)
        self.assertNotIn("vkDeviceWaitIdle", source)
        self.assertNotIn("vkCmdDispatch", source)


if __name__ == "__main__":
    unittest.main()
