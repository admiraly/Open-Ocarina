# SPDX-License-Identifier: GPL-3.0-or-later
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest import mock

from tools.ghidra_probe import command, private_output, run_probe, validate_limits


class ProbeTests(unittest.TestCase):
    def test_limits_are_enforced(self):
        validate_limits(0x80020080, 64, 20, 120)
        for args in ((3, 64, 20, 120), (0, 0, 20, 120), (0, 4097, 20, 120),
                     (0, 64, 0, 120), (0, 64, 61, 120), (0, 64, 20, 20),
                     (0, 64, 20, 601), (2**63, 64, 20, 120),
                     (0x7FFFFFFFFFFFFFFC, 8, 20, 120)):
            with self.subTest(args=args), self.assertRaises(ValueError):
                validate_limits(*args)

    def test_output_inside_repo_must_be_private(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self.assertEqual(private_output(root / "local" / "probe", root), root / "local" / "probe")
            with self.assertRaisesRegex(ValueError, "must be under"):
                private_output(root / "research" / "probe", root)

    def test_command_disables_global_analysis_and_bounds_one_script(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            launcher = root / "support" / "analyzeHeadless"
            launcher.parent.mkdir()
            launcher.touch()
            args = command(root, root / "input.elf", root / "private", 0x80020080, 64, 20)
            self.assertIn("-noanalysis", args)
            self.assertIn("-deleteProject", args)
            index = args.index("-postScript")
            self.assertEqual(args[index + 1:index + 5], ["BoundedProbe.java", "0x80020080", "64", "20"])
            self.assertNotIn("-recursive", args)

    def test_missing_install_is_explicit(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            with self.assertRaisesRegex(ValueError, "not found"):
                command(root, root / "input.elf", root / "private", 0, 64, 20)

    @staticmethod
    def synthetic_elf(path):
        # Header-only fixture for wrapper input validation, not an executable.
        header = bytearray(64)
        header[:6] = b"\x7fELF\x02\x01"
        header[18:20] = b"\xb7\x00"
        path.write_bytes(header)

    def test_mocked_run_reports_only_metadata(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            elf = root / "synthetic.elf"
            self.synthetic_elf(elf)
            home = root / "ghidra"
            launcher = home / "support" / "analyzeHeadless"
            launcher.parent.mkdir(parents=True)
            launcher.touch()
            output = root / "output"

            def fake_process(*args, **kwargs):
                (output / "probe.json").write_text(json.dumps({"entry": "0x1000", "max_bytes": 64,
                    "decompile_completed": True, "instruction_count": 2, "ghidra_version": "test"}))
                return mock.Mock(wait=mock.Mock(return_value=0))

            with mock.patch("tools.ghidra_probe.subprocess.Popen", side_effect=fake_process):
                result = run_probe(home, elf, output, 0x1000, 64, 20, 120)
            self.assertTrue(result["decompile_completed"])
            self.assertFalse(result["matched_source"])
            self.assertNotIn("decompiled", result)
            self.assertEqual(len(result["input_sha256"]), 64)
            self.assertEqual(json.loads((output / "request.json").read_text())["address"], "0x1000")

    def test_timeout_stops_only_its_own_group(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            elf = root / "synthetic.elf"
            self.synthetic_elf(elf)
            launcher = root / "support" / "analyzeHeadless"
            launcher.parent.mkdir()
            launcher.touch()
            process = mock.Mock(pid=54321)
            process.wait.side_effect = [subprocess.TimeoutExpired("test", 120), 0]
            with mock.patch("tools.ghidra_probe.subprocess.Popen", return_value=process), \
                    mock.patch("tools.ghidra_probe.os.killpg") as kill:
                with self.assertRaisesRegex(ValueError, "wall-time"):
                    run_probe(root, elf, root / "output", 0x1000, 64, 20, 120)
            self.assertEqual(kill.call_args.args[0], 54321)

    def test_wrong_architecture_rejected_before_launch(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            elf = root / "wrong.elf"
            elf.write_bytes(b"not an ELF")
            with mock.patch("tools.ghidra_probe.subprocess.Popen") as launch:
                with self.assertRaisesRegex(ValueError, "AArch64"):
                    run_probe(root, elf, root / "output", 0, 64)
            launch.assert_not_called()

    def test_symlinked_input_is_refused_before_resolution(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            original = root / "synthetic.elf"
            self.synthetic_elf(original)
            link = root / "input.elf"
            link.symlink_to(original)
            with self.assertRaisesRegex(ValueError, "symlink"):
                run_probe(root, link, root / "output", 0, 64)


if __name__ == "__main__":
    unittest.main()
