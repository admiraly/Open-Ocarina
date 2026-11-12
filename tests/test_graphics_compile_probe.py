# SPDX-License-Identifier: GPL-3.0-or-later
import argparse
import copy
import hashlib
import json
from pathlib import Path
import struct
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from tools.graphics_compile_probe import configuration, graphics_driver_identity, layout_text, reflect_stage, run, ROOT
from tests.test_compile_probe import instruction, uniform


def module(kind, *extra):
    model = {"task": 5364, "mesh": 5365, "fragment": 4}[kind]
    words = [0x07230203, 0x00010600, 0, 256, 0]
    words += instruction(15, model, 1, 0x6e69616d, 0)
    if kind != "fragment":
        words += instruction(16, 1, 17, 32, 1, 1)
    for item in extra:
        words += item
    return struct.pack(f"<{len(words)}I", *words)


STATE = {"color_format": 37, "depth_format": 126, "samples": 1, "extent": [1536, 864],
         "depth_test": True, "depth_write": True, "depth_compare_op": 3, "cull_mode": 0,
         "front_face": 0, "alpha_to_coverage": False, "color_write_mask": 15}


class GraphicsCompileProbeTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.manifest = {"schema": 1, "push_constant_bytes": 88, "descriptor_mode": "push",
                         "diagnostic_state": copy.deepcopy(STATE), "stages": []}
        for kind in ("mesh", "fragment"):
            data = module(kind, *uniform())
            path = self.root / (kind + ".spv")
            path.write_bytes(data)
            self.manifest["stages"].append({"stage": kind, "path": path.name,
                "sha256": hashlib.sha256(data).hexdigest(), "required_subgroup_size": 0 if kind == "fragment" else 32})

    def test_cross_stage_binding_flags_are_unioned(self):
        config, snapshots = configuration(self.manifest, self.root)
        self.assertEqual(config["bindings"], [{"set": 0, "binding": 0, "type": 6, "count": 1, "stage_flags": 144}])
        self.assertEqual(config["stages"][0]["local_size"], [32, 1, 1])
        self.assertIsNone(config["stages"][1]["local_size"])
        self.assertEqual(snapshots[0], (self.root / "mesh.spv").read_bytes())
        self.assertIn("0 0 6 1 144\n", layout_text(config))

    def test_task_mesh_fragment_and_ordinary_sets(self):
        data = module("task", *uniform())
        (self.root / "task.spv").write_bytes(data)
        self.manifest["stages"].insert(0, {"stage": "task", "path": "task.spv", "sha256": hashlib.sha256(data).hexdigest(), "required_subgroup_size": 16})
        self.manifest["descriptor_mode"] = "sets"
        config, _ = configuration(self.manifest, self.root)
        self.assertEqual(config["bindings"][0]["stage_flags"], 208)
        self.assertEqual(len(config["stages"]), 3)

    def test_mismatched_shader_stage_rejected(self):
        with self.assertRaisesRegex(ValueError, "Entry model"):
            reflect_stage(module("fragment"), "mesh")

    def test_incompatible_cross_stage_binding_rejected(self):
        data = module("fragment", instruction(21, 2, 32, 0), instruction(30, 3, 2),
                      instruction(71, 3, 2), instruction(32, 4, 12, 3), instruction(59, 4, 5, 12),
                      instruction(71, 5, 33, 0), instruction(71, 5, 34, 0))
        (self.root / "fragment.spv").write_bytes(data)
        self.manifest["stages"][1]["sha256"] = hashlib.sha256(data).hexdigest()
        with self.assertRaisesRegex(ValueError, "incompatible"):
            configuration(self.manifest, self.root)

    def test_explicit_state_subgroup_mode_and_digest_required(self):
        mutations = [lambda m: m["diagnostic_state"].pop("samples"),
                     lambda m: m["stages"][0].pop("required_subgroup_size"),
                     lambda m: m.pop("descriptor_mode"),
                     lambda m: m["stages"][0].update(sha256="0" * 64),
                     lambda m: m["stages"].reverse(),
                     lambda m: m["diagnostic_state"].update(depth_format=0),
                     lambda m: m["stages"].insert(1, "invalid stage")]
        for mutate in mutations:
            manifest = copy.deepcopy(self.manifest)
            mutate(manifest)
            with self.subTest(manifest=manifest), self.assertRaises(ValueError):
                configuration(manifest, self.root)

    def test_push_range_covers_every_stage(self):
        data = module("fragment", instruction(21, 2, 32, 0), instruction(30, 3, 2),
                      instruction(72, 3, 0, 35, 64), instruction(32, 4, 9, 3), instruction(59, 4, 5, 9))
        (self.root / "fragment.spv").write_bytes(data)
        self.manifest["stages"][1]["sha256"] = hashlib.sha256(data).hexdigest()
        self.manifest["push_constant_bytes"] = 64
        with self.assertRaisesRegex(ValueError, "Push range"):
            configuration(self.manifest, self.root)

    def test_prepare_only_builds_without_running_helper_or_opening_driver(self):
        manifest = self.root / "manifest.json"
        manifest.write_text(json.dumps(self.manifest))
        output = self.root / "output"
        args = argparse.Namespace(manifest=manifest, output=output, prepare_only=True, driver=None,
                                  mesa_build=None, timeout=30, device=0, cc="cc", validator="spirv-val")
        calls = []
        def child(command, **kwargs):
            calls.append(command)
            if command[0] == "cc":
                Path(command[command.index("-o") + 1]).write_bytes(b"synthetic compiled helper")
            return subprocess.CompletedProcess(command, 0, "validator version\n" if "--version" in command else "-lvulkan\n" if command[0] == "pkg-config" else "", "")
        with patch("tools.graphics_compile_probe.subprocess.run", side_effect=child), \
             patch("tools.graphics_compile_probe.run_child") as helper, \
             patch("tools.graphics_compile_probe.graphics_driver_identity") as driver:
            result = run(args)
            helper.assert_not_called()
            driver.assert_not_called()
        self.assertFalse(result["device_opened"])
        self.assertFalse(result["render_executed"])
        self.assertIn("-O2", next(c for c in calls if c[0] == "cc"))

    def test_graphics_driver_audit_requires_optimized_mesh_and_fragment(self):
        rows = [{"file": "/source/" + name, "arguments": ["c++", "-O2", "-DNDEBUG"]}
                for name in ("brw_compile_mesh.cpp", "brw_compile_fs.cpp")]
        (self.root / "compile_commands.json").write_text(json.dumps(rows))
        with patch("tools.graphics_compile_probe.driver_identity", return_value={}):
            report = graphics_driver_identity(Path("driver.json"), self.root)
            self.assertEqual(len(report["graphics_compiler_flags"]), 2)
            rows[0]["arguments"] = ["c++", "-O0", "-DNDEBUG"]
            (self.root / "compile_commands.json").write_text(json.dumps(rows))
            with self.assertRaisesRegex(ValueError, "optimized"):
                graphics_driver_identity(Path("driver.json"), self.root)

    def test_native_helper_has_no_execution_calls(self):
        source = (ROOT / "tools" / "vulkan_graphics_compile_probe.c").read_text()
        for forbidden in ("vkCmd", "vkQueueSubmit", "vkGetDeviceQueue", "vkCreateCommandPool", "vkCreateImage", "vkCreateBuffer"):
            self.assertNotIn(forbidden, source)
        self.assertIn("props[i].stages", source)
        self.assertNotIn("DISABLE_OPTIMIZATION", source)


if __name__ == "__main__":
    unittest.main()
