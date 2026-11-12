# SPDX-License-Identifier: GPL-3.0-or-later
import importlib.util
import json
from pathlib import Path
import struct
import sys
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
SPEC = importlib.util.spec_from_file_location("warp32_probe", ROOT / "tools" / "warp32_probe.py")
probe = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(probe)


class Warp32ProbeTests(unittest.TestCase):
    def test_fixed_input_and_golden_contract(self):
        self.assertEqual(len(probe.packed_input()), 1280)
        words = probe.golden_words()
        self.assertEqual(len(words), 1600)
        masks = [0, 0xffffffff, 0x55555555, 0x80000000]
        for case, mask in enumerate(masks):
            for lane in range(32):
                row = words[(case * 32 + lane) * 10:(case * 32 + lane + 1) * 10]
                self.assertEqual(row[0], lane)
                self.assertEqual(row[3:7], [mask, 0, 0, 0])
                self.assertEqual(row[7:10], [int(mask != 0), int(mask == 0xffffffff), 32])
        self.assertEqual(probe.check_output(probe.pack(words)), probe.hashlib.sha256(probe.pack(words)).hexdigest())

    def test_every_output_word_checked(self):
        original = probe.golden_words()
        for index in range(len(original)):
            changed = original.copy()
            changed[index] ^= 1
            with self.assertRaisesRegex(ValueError, "Mismatch"):
                probe.check_output(probe.pack(changed))
        with self.assertRaisesRegex(ValueError, "length"):
            probe.check_output(b"")

    def test_independent_shared_memory_cpu_model(self):
        # Model workgroup scratch/reduction rather than the golden mask expression.
        outputs = []
        packed = struct.unpack("<320I", probe.packed_input())
        for case in range(5):
            values = list(packed[case * 64:case * 64 + 32])
            predicates = list(packed[case * 64 + 32:case * 64 + 64])
            bits = [(1 << lane) if predicates[lane] else 0 for lane in range(32)]
            mask = 0
            for bit in bits:
                mask |= bit
            for lane in range(32):
                outputs.extend([lane, values[7], values[lane ^ 1], mask, 0, 0, 0,
                                int(mask != 0), int(mask == 0xffffffff), 32])
        self.assertEqual(outputs, probe.golden_words())

    def test_generated_modules_have_fixed_uniform_loop(self):
        software = probe.shader_source(True)
        native = probe.shader_source(False)
        self.assertIn("local_size_x=32", software)
        self.assertIn("c < 5u", software)
        self.assertNotIn("return", software)
        self.assertNotIn("subgroup", software)
        self.assertEqual(software.count("barrier();"), 3)
        self.assertIn("subgroupBallot(pred)", native)
        self.assertIn("subgroupShuffleXor(value, 1u)", native)
        self.assertIn("gl_SubgroupInvocationID", native)

    def test_actual_width_is_required(self):
        metadata = {"synthetic_only": True, "dispatch_executed": True, "vendor_id": 0x8086,
                    "device": "Intel Arc A770", "requested_width": 16, "actual_width": 16,
                    "width_source": "pipeline_executable_properties"}
        probe.validate_result(metadata, 16)
        for change in ({"actual_width": 0}, {"actual_width": 8}, {"width_source": "requested"},
                       {"vendor_id": 0}, {"dispatch_executed": False}):
            with self.assertRaises(ValueError):
                probe.validate_result(metadata | change, 16)

    def test_competing_game_aborts_without_signalling(self):
        completed = probe.subprocess.CompletedProcess([], 0, "1\n", "")
        with patch.object(probe.subprocess, "run", return_value=completed), patch.object(probe.os, "killpg") as kill:
            with self.assertRaisesRegex(ValueError, "competing game"):
                probe.ensure_no_games()
            kill.assert_not_called()


if __name__ == "__main__":
    unittest.main()
