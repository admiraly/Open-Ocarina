# SPDX-License-Identifier: GPL-3.0-or-later
"""CPU checks for the 705-group D8 interval grid plans and serial supervisor."""
import hashlib
import json
import struct
import tempfile
import unittest
from pathlib import Path

from tools import grok_d8_interval_grid as grid
from tools import mesh_replay


def valid_capture(groups):
    """Initialized guards plus the lane counts and writtenness check_capture requires."""
    data = bytearray(mesh_replay.initialized_capture(groups))
    count = groups[0] * groups[1] * groups[2]
    words = list(struct.unpack(f"<{len(data) // 4}I", data))
    stride, marker = 11232, 22848 // 4
    vertex_components = 128 * 4 * 10
    for group in range(count):
        base = 16 + group * stride
        for lane in range(32):
            words[base + 16 + lane * 2] = 128
            words[base + 17 + lane * 2] = 0
        start = base + marker
        words[start:start + vertex_components] = [1] * vertex_components
    return struct.pack(f"<{len(words)}I", *words)


def _plan(origin, groups, payload=b"abcd"):
    capture = mesh_replay.initialized_capture([groups, 1, 1])
    header = [1, 10, 12, 0, len(capture), 64, len(capture) - 128, groups, 1, 1, origin, 0, 0, 705, 1, 1]
    middle = b""
    for index in range(10):
        blob = payload + struct.pack("<I", index)
        middle += struct.pack("<I", len(blob)) + blob
    for row in range(12):
        middle += struct.pack("<7I", 0, row % 9, 0, 7, 0, 5, 0)
    return struct.pack("<8s16I", grid.MAGIC, *header) + middle + capture


class PartitionTests(unittest.TestCase):
    def test_exact_cover(self):
        windows = grid.partition()
        self.assertEqual(len(windows), 23)
        self.assertEqual(windows[0]["origin"], [0, 0, 0])
        self.assertEqual(windows[0]["groups"], [32, 1, 1])
        self.assertEqual(windows[4]["origin"], [128, 0, 0])
        self.assertEqual(windows[-1]["origin"], [704, 0, 0])
        self.assertEqual(windows[-1]["groups"], [1, 1, 1])
        covered = []
        for window in windows:
            covered.extend(range(window["origin"][0], window["origin"][0] + window["groups"][0]))
        self.assertEqual(covered, list(range(705)))

    def test_rejects_gap_overlap_and_bad_tail(self):
        windows = grid.partition()
        gapped = json.loads(json.dumps(windows))
        gapped[1]["origin"] = [33, 0, 0]
        with self.assertRaises(ValueError):
            grid.assert_partition(gapped)
        overlapped = json.loads(json.dumps(windows))
        overlapped[1]["origin"] = [31, 0, 0]
        with self.assertRaises(ValueError):
            grid.assert_partition(overlapped)
        tail = json.loads(json.dumps(windows))
        tail[-1]["groups"] = [2, 1, 1]
        with self.assertRaises(ValueError):
            grid.assert_partition(tail)

    def test_retarget_keeps_middle_and_replaces_extent(self):
        source = _plan(128, 2)
        rewritten = grid.retarget(source, [704, 0, 0], [1, 1, 1])
        self.assertEqual(grid.parse_plan(source)["middle"], grid.parse_plan(rewritten)["middle"])
        self.assertEqual(grid.parse_plan(rewritten)["capture_size"], 44928 + 128)
        self.assertNotEqual(len(source), len(rewritten))
        with self.assertRaises(ValueError):
            grid.retarget(source, [704, 0, 0], [2, 1, 1])
        broken = bytearray(source)
        broken[8:12] = b"\x02\x00\x00\x00"
        with self.assertRaises(ValueError):
            grid.parse_plan(bytes(broken))


class SupervisorTests(unittest.TestCase):
    def _materialize(self, directory):
        """Three legal windows inside a rewritten 705-grid are too heavy.

        execute_grid calls assert_partition, which requires the real 705 cover.
        The mock chain therefore builds the real window list and tiny plans
        whose headers match, using the public retarget of one synthetic source.
        """
        source = _plan(0, 32)
        plans = []
        for window in grid.partition():
            blob = grid.retarget(source, window["origin"], window["groups"])
            path = directory / f"{window['name']}.bin"
            path.write_bytes(blob)
            plans.append({**window, "plan": str(path), "sha256": hashlib.sha256(blob).hexdigest()})
        return {"grant": grid.GRANT, "helper": str(directory / "helper"),
                "modules": {"original": str(directory / "original.spv"),
                            "candidate": str(directory / "candidate.spv")},
                "windows": plans}

    def _supervisor(self, calls, captures):
        def run(command, env, directory, seconds, cap, inspect):
            self.assertEqual(seconds, grid.PARENT_SECONDS)
            self.assertEqual(cap, grid.PARENT_CAP)
            self.assertNotIn("--validate", command)
            self.assertEqual(command[-2:], ["32", "0"])
            calls.append((Path(directory).parent.name, Path(directory).name))
            parsed = grid.parse_plan(Path(command[2]).read_bytes())
            document = {"output_profile": "d8-interval-mesh-owned/v1", "gpu_executed": True,
                        "mesh_draw_calls": 1, "requested_width": 32, "actual_width": 32,
                        "width_source": "pipeline_executable_properties", "executable_stage": 128,
                        "draw_mesh_tasks": parsed["groups"], "origin": parsed["origin"],
                        "fence_timeout_ns": grid.FENCE_NS, "input_bytes_unchanged": True,
                        "control_bytes_unchanged": True, "robustBufferAccess2": True,
                        "nullDescriptor": True, "capture_bytes": parsed["capture_size"],
                        "rasterizer_discard": True, "attachments": 0,
                        "pipeline_optimization_disabled": False, "vendor_id": 0x8086,
                        "device_id": 0x56A0}
            Path(directory, "stdout.log").write_text(json.dumps(document) + "\n")
            name = Path(directory).name
            blob = captures.get(name, valid_capture(parsed["groups"]))
            Path(command[3]).write_bytes(blob)
            return {"reaped": True, "returncode": 0, "failure": None}
        return run

    def test_three_case_chain_stops_on_mismatch(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            spec = self._materialize(root)
            calls = []
            bad = bytearray(valid_capture([32, 1, 1]))
            bad[400] ^= 0x01
            report = grid.execute_grid(spec, root / "out", grid.GRANT,
                                        self._supervisor(calls, {"candidate32": bytes(bad)}), {})
            self.assertFalse(report["accepted"])
            self.assertEqual(calls, [("origin-000-groups-32", case) for case in grid.CASES])
            self.assertEqual(report["windows_completed"], [])
            self.assertEqual(sorted(path.name for path in (root / "out").glob("origin-*")),
                             ["origin-000-groups-32"])
            lease = json.loads((root / "out" / "lease.json").read_text())
            self.assertTrue(lease["released"])
            self.assertEqual(lease["state"], "released")

    def test_bad_grant_and_stale_output_launch_nothing(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            spec = self._materialize(root)
            calls = []
            with self.assertRaises(ValueError):
                grid.execute_grid(spec, root / "out", "wrong-grant", self._supervisor(calls, {}), {})
            self.assertEqual(calls, [])
            self.assertFalse((root / "out").exists())
            (root / "stale").mkdir()
            with self.assertRaises(FileExistsError):
                grid.execute_grid(spec, root / "stale", grid.GRANT, self._supervisor(calls, {}), {})
            self.assertEqual(calls, [])

    def test_supervisor_failure_reaps_before_release(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            spec = self._materialize(root)

            def fail(command, env, directory, seconds, cap, inspect):
                return {"reaped": True, "returncode": 3, "failure": "parent process timeout"}

            report = grid.execute_grid(spec, root / "out", grid.GRANT, fail, {})
            self.assertFalse(report["accepted"])
            self.assertIn("timeout", report["stopped"])
            self.assertTrue(json.loads((root / "out" / "lease.json").read_text())["released"])
            self.assertEqual(sorted(path.name for path in (root / "out").glob("origin-*")),
                             ["origin-000-groups-32"])
            self.assertEqual(sorted(path.name for path in (root / "out" / "origin-000-groups-32").iterdir()),
                             ["original32-a"])

    def test_unreaped_or_raised_supervisor_does_not_release(self):
        for raises in (False, True):
            with self.subTest(raises=raises), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                spec = self._materialize(root)

                def unproven(*args):
                    if raises:
                        raise RuntimeError("cleanup not established")
                    return {"reaped": False, "returncode": None, "failure": "still running"}

                report = grid.execute_grid(spec, root / "out", grid.GRANT, unproven, {})
                self.assertFalse(report["accepted"])
                self.assertEqual(report["windows_completed"], [])
                lease = json.loads((root / "out" / "lease.json").read_text())
                self.assertFalse(lease["released"])
                self.assertEqual(lease["state"], "cleanup-unproven")


if __name__ == "__main__":
    unittest.main()
