# SPDX-License-Identifier: GPL-3.0-or-later
"""Identity and preservation checks for the d8 interval lowering.

The private mesh binary is optional. Synthetic cases cover wrap, first-match,
table-zero and binding selection without embedding shader bytes.
"""
import hashlib
import importlib.util
import sys
import struct
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("grok_d8_interval", ROOT / "tools" / "grok-d8-interval.py")
LOWERING = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = LOWERING
SPEC.loader.exec_module(LOWERING)
U32 = (1 << 32) - 1
U64 = (1 << 64) - 1
TARGET = "835aa59799c6ecb621034d946e2fa4eadb1aa2407d293321ae3e3052882bc9b3"


def independent_end(guest, size_word, bound_bytes, kind, width):
    shift = width.bit_length() - 1
    base = guest & ~15
    elements = (bound_bytes // width) & U32
    typed_bytes = (elements << shift) & U32
    typed_end = (base + typed_bytes) & U64
    nvn_end = (guest + size_word) & U64
    if kind == "table" and size_word == 0:
        return base, typed_end
    return base, typed_end if typed_end < nvn_end else nvn_end


def independent_hit(views, address, width):
    for view in views:
        base, end = independent_end(view.guest, view.size_word, view.bound_bytes, view.kind, width)
        if base <= address < end:
            index = ((address - base) & U32) >> (width.bit_length() - 1)
            return view.binding, view.element, base, index
    return None


def find_original():
    root = ROOT / "local" / "native-mesh-replay"
    if not root.is_dir():
        return None
    for path in root.glob("*/original/source.spv"):
        if hashlib.sha256(path.read_bytes()).hexdigest() == TARGET:
            return path
    return None


class IntervalModelTests(unittest.TestCase):
    def test_wrap_first_match_and_table_zero(self):
        views = [
            LOWERING.View(U64 - 8, 32, 64, 5, 0, "single"),
            LOWERING.View(16, 0, 32, 6, 0, "single"),
            LOWERING.View(0, 0, 64, 8, 0, "table"),
            LOWERING.View(0, 8, 64, 8, 1, "table"),
        ]
        for width in (4, 16):
            rows = LOWERING.build_rows(views, width)
            endpoints = {0, 1 << 64}
            for view in views:
                endpoints.update(independent_end(view.guest, view.size_word, view.bound_bytes, view.kind, width))
            for left, right in zip(sorted(endpoints), sorted(endpoints)[1:]):
                for address in (left, right - 1):
                    selected, _steps = LOWERING.binary_identity(rows, address)
                    direct = independent_hit(views, address, width)
                    if direct is None:
                        self.assertIsNone(selected)
                    else:
                        self.assertEqual(selected, direct[:3])
                        base = selected[2]
                        shift = width.bit_length() - 1
                        self.assertEqual(((address - base) & U32) >> shift, direct[3])

    def test_reflected_binding_skips_occupied_pipeline_slots(self):
        module = LOWERING.Module(struct.pack("<5I", 0x07230203, 0x10600, 0, 1, 0))
        self.assertEqual(LOWERING.free_binding(module, list(range(30))), 30)


class RealModuleTests(unittest.TestCase):
    def test_real_module_candidate(self):
        spirv = find_original()
        capture = ROOT / "local" / "runtime-mesh-input-02" / "capture" / "mesh-inputs.json"
        if spirv is None or not capture.is_file():
            self.skipTest("private d8 module or capture is absent")
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "out"
            report = LOWERING.build(spirv, capture, output)
            candidate = (output / "candidate.spv").read_bytes()
            self.assertEqual(hashlib.sha256(candidate).hexdigest(), report["candidate_sha256"])
            self._assert_preserved_words(spirv.read_bytes(), candidate, report)
        self.assertEqual(report["original_sha256"], TARGET)
        self.assertEqual(report["added_binding"]["binding"], 30)
        self.assertEqual(report["widths"], [4, 16])
        self.assertEqual([helper["function"] for helper in report["helpers"]], [149, 620])
        self.assertEqual([helper["calls"] for helper in report["helpers"]], [11, 61])
        rejected = {row["function"]: row for row in report["rejected"]}
        self.assertIn("no call", rejected[390]["reason"])
        self.assertFalse(rejected[390]["rewritten"])
        self.assertEqual(report["preservation"]["modified_functions"], [149, 620])
        self.assertEqual(len(report["preservation"]["untouched_functions"]), 5)
        views = [LOWERING.View(**row) for row in report["views"]]
        for width in (4, 16):
            rows = LOWERING.build_rows(views, width)
            self.assertEqual(len(rows), report["selection"][str(width)]["rows"])
            endpoints = {0, 1 << 64}
            for view in views:
                endpoints.update(independent_end(view.guest, view.size_word, view.bound_bytes, view.kind, width))
            for left, right in zip(sorted(endpoints), sorted(endpoints)[1:]):
                direct = independent_hit(views, left, width)
                selected, _steps = LOWERING.binary_identity(rows, left)
                if direct is None:
                    self.assertIsNone(selected)
                else:
                    self.assertEqual(selected, direct[:3])
        self.assertFalse(report["execution_admitted"])
        self.assertFalse(report["instruction_timing_measured"])

    def _assert_preserved_words(self, original, candidate, report):
        old = LOWERING.function_bodies(original)
        new = LOWERING.function_bodies(candidate)
        modified = set(report["preservation"]["modified_functions"])
        self.assertEqual(set(old), set(new))
        for function, body in old.items():
            if function not in modified:
                self.assertEqual(body, new[function])
        words = struct.unpack(f"<{len(original) // 4}I", original)
        nulls = set()
        at = 5
        while at < len(words):
            count = words[at] >> 16
            if (words[at] & 0xFFFF) == 46:
                nulls.add(words[at + 2])
            at += count
        for function in modified:
            label, returned, end = new[function][-3:]
            self.assertEqual(old[function][-3:], (label, returned, end))
            self.assertEqual((label[0] & 0xFFFF, returned[0] & 0xFFFF, end[0] & 0xFFFF), (248, 254, 56))
            self.assertIn(returned[1], nulls)


if __name__ == "__main__":
    unittest.main()
