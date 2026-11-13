# SPDX-License-Identifier: GPL-3.0-or-later
"""CPU checks for the D8 interval owned-output replay profile."""
import json
import shutil
import struct
import subprocess
import tempfile
import unittest
from pathlib import Path

from tools import grok_d8_interval_replay as replay
from tools.mesh_mapping_probe import BUILD_FLAGS

ROOT = Path(__file__).resolve().parents[1]


def instruction(opcode, *operands):
    words = [((1 + len(operands)) << 16) | opcode, *operands]
    return words


def module(functions, entry_interfaces):
    entry = instruction(15, 5365, 851, 0x6e69616d, 0, *entry_interfaces)
    body = []
    for ident, ops in functions:
        body.extend(ops)
    words = [0x07230203, 0x00010600, 0, 1000, 0, *entry, *body]
    return struct.pack(f"<{len(words)}I", *words)


def function(ident, prefix):
    ops = []
    ops.extend(instruction(54, 1, ident, 0, 2))
    for word in prefix:
        ops.extend(instruction(43, 1, word, word))
    ops.extend(instruction(248, 7))
    ops.extend(instruction(254, 8))
    ops.extend(instruction(56))
    # Pad so the shared suffix is the last five instructions, represented as words
    # by appending two no-op-like constants before the shared tail inside the caller.
    return ops


class IntervalReplayPolicyTests(unittest.TestCase):
    def test_element_count_fits_mesh_only_union(self):
        self.assertEqual(replay.element_count(replay.expected_bindings(False)), 13)
        owned = replay.expected_bindings(True)
        self.assertEqual(replay.element_count(owned), 14)
        self.assertEqual(sum(row["count"] for row in owned if row["set"] == 0), 12)
        self.assertLessEqual(14, 32)
        self.assertGreater(33, 32)

    def test_helper_change_keeps_entry_and_miss_suffix(self):
        # Five shared trailing words: OpReturnValue plus OpFunctionEnd is not enough, so the
        # tail is one 2-word constant, one 2-word return, and a 1-word function end.
        tail = [*instruction(248, 7), *instruction(254, 8), *instruction(56)]

        def fn(ident, marker):
            return [*instruction(54, 1, ident, 0, 2), *instruction(43, 1, marker, marker), *tail]

        original = module([(851, fn(851, 1)), (149, fn(149, 2)), (620, fn(620, 3))], [])
        candidate_body_149 = [*instruction(54, 1, 149, 0, 2), *instruction(43, 1, 99, 99), *tail]
        candidate_body_620 = [*instruction(54, 1, 620, 0, 2), *instruction(43, 1, 98, 98), *tail]
        decorations = [*instruction(71, 77, 24), *instruction(71, 77, 33, 30), *instruction(71, 77, 34, 0)]
        candidate = module([(851, fn(851, 1)), (149, candidate_body_149), (620, candidate_body_620)], [77])
        raw = struct.unpack(f"<{len(candidate)//4}I", candidate)
        # module() places the entry first; splice decorations after it.
        entry_len = raw[5] >> 16
        words = list(raw[:5 + entry_len]) + decorations + list(raw[5 + entry_len:])
        candidate = struct.pack(f"<{len(words)}I", *words)
        proof = replay.prove_candidate(original, candidate)
        self.assertEqual(proof["changed_functions"], [149, 620])
        self.assertTrue(proof["entry_function_words_identical"])
        self.assertEqual(proof["binding30_id"], 77)

    def test_record_guards_and_validate_refuse_device(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            payload = bytes(range(256)) * 1 + bytes(144)
            self.assertEqual(len(payload), 400)
            guarded = replay.guarded_record(payload)
            self.assertEqual(guarded[64:464], payload)
            self.assertEqual(struct.unpack("<16I", guarded[:64]), (replay.RECORD_GUARD,) * 16)
            inputs = root / "inputs"
            inputs.mkdir()
            bindings = []
            for binding in range(9):
                elements = 3 if binding == 8 else 1
                for element in range(elements):
                    if binding == 8 and element in (1, 2):
                        bindings.append({"binding": binding, "element": element, "type": 7, "null_descriptor": True})
                        continue
                    path = inputs / f"{binding}-{element}.bin"
                    data = bytes([binding, element]) * 32
                    path.write_bytes(data)
                    bindings.append({"binding": binding, "element": element, "type": 6 if binding < 5 else 7,
                                     "range_bytes": len(data), "path": path,
                                     "snapshot": {"sha256": "unused", "size": len(data)}})
            records = root / "records.bin"
            records.write_bytes(payload)
            state = {"bindings": bindings, "groups": [1, 1, 1], "origin": [0, 0, 0], "grid": [1, 1, 1]}
            info = replay.write_plan(root / "inputs.bin", state, records)
            self.assertEqual(info["owned_elements"], 14)
            self.assertEqual(info["set0_bindings"], 10)
            identities = {(row["binding"], row["element"]): row for row in info["identities"]}
            self.assertEqual(identities[(30, 0)]["offset"], 64)
            self.assertEqual(identities[(30, 0)]["range"], 400)
            self.assertTrue(identities[(8, 1)]["null"])
            self.assertEqual(identities[(5, 0)]["offset"], 0)
            required = ("pkg-config", "cc", "spirv-as", "spirv-dis", "spirv-val")
            if any(shutil.which(tool) is None for tool in required):
                self.skipTest("Optional native validation needs a C compiler, Vulkan development files and SPIR-V Tools")
            if subprocess.run(["pkg-config", "--exists", "vulkan"], capture_output=True).returncode:
                self.skipTest("Optional native validation needs Vulkan development files")
            flags = subprocess.check_output(["pkg-config", "--cflags", "--libs", "vulkan"], text=True).split()
            helper = root / "helper"
            subprocess.run(["cc", *BUILD_FLAGS, str(ROOT / "tools" / "vulkan_d8_interval_replay.c"), *flags, "-o", str(helper)],
                           check=True)
            from tools import mesh_output_instrument as instrument
            instrument.prepare_fixture(root / "fixture")
            shader = root / "fixture" / "instrumented.spv"
            validated = json.loads(subprocess.check_output([str(helper), str(shader), str(root / "inputs.bin"),
                                                            str(root / "out.bin"), "32", "0", "--validate"], text=True))
            self.assertFalse(validated["gpu_executed"])
            self.assertFalse(validated["queue_retrieved"])
            self.assertEqual(validated["owned_elements"], 14)
            self.assertFalse(validated["timing_admitted"])
            refused = subprocess.run([str(helper), str(shader), str(root / "inputs.bin"), str(root / "out.bin"), "32", "0"],
                                     capture_output=True, text=True)
            self.assertEqual(refused.returncode, 4)
            self.assertIn("not admitted", refused.stderr)


if __name__ == "__main__":
    unittest.main()
