# SPDX-License-Identifier: GPL-3.0-or-later
"""Compile the independent C++ core; no private inputs or Vulkan are needed."""
import bisect
import copy
from pathlib import Path
import random
import shutil
import struct
import subprocess
import tempfile
import unittest


TARGET = "b9af52c0ba07f64eb8b0ca2905c432d094999fdf669aa95182ff7754b510e97b"
SELECTORS = (192, 208, 256, 272, 352, 2176, 2192, 2208)
U32, U64 = (1 << 32) - 1, (1 << 64) - 1
ROOT = Path(__file__).resolve().parents[1]

# Tiny independently authored input transport, compiled only in the test's
# temporary directory. It is not a renderer integration or evidence producer.
HARNESS = r'''
#include "src/probes/compute_interval_records.h"
#include <iostream>
#include <string>
using namespace open_ocarina::compute_intervals;
static std::uint64_t number(unsigned count) {
    std::uint64_t value = 0;
    for (unsigned i = 0; i < count; ++i) {
        const int c = std::cin.get();
        if (c == std::char_traits<char>::eof()) throw std::invalid_argument("Truncated fixture");
        value |= std::uint64_t(static_cast<unsigned char>(c)) << (i * 8);
    }
    return value;
}
static std::uint32_t count() {
    const auto n = static_cast<std::uint32_t>(number(4));
    if (n > 2048) throw std::invalid_argument("Fixture count exceeds test transport bound");
    return n;
}
static Key key() { return {number(8), number(8)}; }
static OwnedView view() {
    OwnedView v;
    v.allocation = key(); v.offset = number(8); v.range = number(8);
    v.view_generation = number(8); v.recording = number(8); v.descriptor_generation = number(8);
    return v;
}
int main() {
    try {
        Input input;
        input.module_sha256.resize(64); std::cin.read(&input.module_sha256[0], 64);
        input.mode = static_cast<SourceMode>(number(4));
        input.storage_alignment = static_cast<std::uint32_t>(number(4));
        input.recording = number(8); input.descriptor_generation = number(8);
        input.completed_value = number(8); input.selectors_ready_order = number(8);
        input.dispatch_order = number(8); input.lease_end_order = number(8);
        const auto owners = count();
        for (std::uint32_t i = 0; i < owners; ++i) {
            Owner o; o.allocation = key(); o.backing = key(); o.backing_offset = number(8);
            o.size = number(8); o.first_recording = number(8);
            o.retained_through_recording = number(8); o.reuse_after_completion = number(8);
            input.owners.push_back(o);
        }
        input.c0 = view();
        for (auto& byte : input.c0_bytes) byte = static_cast<std::uint8_t>(number(1));
        for (auto& root : input.roots) {
            root.binding = static_cast<std::uint32_t>(number(4));
            root.element = static_cast<std::uint32_t>(number(4)); root.view = view();
        }
        const auto writers = count();
        for (std::uint32_t i = 0; i < writers; ++i) {
            WriteSpan w; w.kind = static_cast<WriteKind>(number(4)); w.allocation = key();
            w.offset = number(8); w.size = number(8); w.first_order = number(8); w.last_order = number(8);
            input.additional_writes.push_back(w);
        }
        if (std::cin.get() != std::char_traits<char>::eof()) throw std::invalid_argument("Trailing fixture data");
        const auto output = build(input);
        std::cout.write(reinterpret_cast<const char*>(output.bytes.data()),
                        static_cast<std::streamsize>(output.bytes.size()));
        return 0;
    } catch (const std::exception& e) { std::cerr << e.what() << '\n'; return 2; }
}
'''


def view(owner=2, offset=0, size=64, generation=1):
    return [owner, 1 if owner else 0, offset, size, generation, 7, 3]


def scenario(specs=None):
    specs = list(specs or [(0, 0, 0)] * 8)
    if len(specs) != 8:
        raise ValueError("Eight exact target roots are required")
    c0 = bytearray(2224)
    roots = []
    for i, (guest, size, bound) in enumerate(specs):
        struct.pack_into("<QI", c0, SELECTORS[i], guest, size)
        roots.append([i + 2 if i < 5 else 7, 0 if i < 5 else i - 5,
                      view(size=bound, generation=i + 2) if bound else view(0, size=0, generation=i + 2)])
    return {"sha": TARGET, "mode": 1, "alignment": 16, "epoch": [7, 3, 100, 10, 11, 20],
            "owners": [[1, 1, 1001, 1, 0, 4096, 1, 7, 0],
                       [2, 1, 1002, 1, 0, max(64, *(s[2] for s in specs)), 1, 7, 0]],
            "c0_view": view(1, offset=64, size=2224), "c0": c0,
            "roots": roots, "writes": []}


def encode(s):
    out = bytearray(s["sha"].encode("ascii"))
    out.extend(struct.pack("<II6Q", s["mode"], s["alignment"], *s["epoch"]))
    out.extend(struct.pack("<I", len(s["owners"])))
    for owner in s["owners"]:
        out.extend(struct.pack("<9Q", *owner))
    out.extend(struct.pack("<7Q", *s["c0_view"]))
    out.extend(s["c0"])
    for binding, element, owned_view in s["roots"]:
        out.extend(struct.pack("<II7Q", binding, element, *owned_view))
    out.extend(struct.pack("<I", len(s["writes"])))
    for write in s["writes"]:
        out.extend(struct.pack("<I6Q", *write))
    return bytes(out)


def source_bounds(spec, width):
    # Independent specification walk; not the C++ algorithm or Python builder.
    guest, size, bound = spec
    aligned = (guest // 16) * 16
    elements = (bound // width) % (1 << 32)
    typed_end = (aligned + ((elements * width) % (1 << 32))) % (1 << 64)
    nvn_end = (guest + size) % (1 << 64)
    return aligned, typed_end, nvn_end


def direct_identity(specs, address, width):
    for i, spec in enumerate(specs):
        base, typed, nvn = source_bounds(spec, width)
        limit = typed if i >= 5 and spec[1] == 0 else min(typed, nvn)
        if base <= address < limit:
            return i + 2 if i < 5 else 7, 0 if i < 5 else i - 5, base
    return None


def decode(blob):
    counts = struct.unpack_from("<4I", blob)
    if counts[2:] != (0, counts[0]) or len(blob) != 16 + 48 * sum(counts[:2]):
        raise AssertionError("Incorrect record header/extent")
    rows = {4: [], 16: []}
    at = 16
    for width, count in zip((4, 16), counts):
        for _ in range(count):
            low, high, base, binding, element, actual_width, route, zero0, zero1 = struct.unpack_from("<QQQ6I", blob, at)
            at += 48
            if actual_width != width or route != (binding - 2 if binding < 7 else 5 + element) or zero0 or zero1:
                raise AssertionError("Incorrect record row ABI")
            rows[width].append((low, high, binding, element, base))
    return rows


def certify(specs, blob):
    proof = {}
    for width, rows in decode(blob).items():
        endpoints = {0, 1 << 64}
        for spec in specs:
            endpoints.update(source_bounds(spec, width))
        for index, row in enumerate(rows):
            low, high, binding, element, base = row
            assert base <= low < high <= U64
            assert index == 0 or rows[index - 1][1] <= low
            endpoints.update((low, high))
        endpoints = sorted(endpoints)
        starts = [row[0] for row in rows]
        for first, last in zip(endpoints, endpoints[1:]):
            # All membership predicates are constant in this complete-domain cell.
            for address in (first, last - 1):
                index = bisect.bisect_right(starts, address) - 1
                selected = None if index < 0 or rows[index][1] <= address else rows[index][2:]
                expected = direct_identity(specs, address, width)
                assert selected == expected, (width, address, selected, expected)
                if selected:
                    # Equal root/base preserves the source's U32 truncation and
                    # typed floor, including unaligned start addresses.
                    assert (((address - selected[2]) & U32) // width ==
                            ((address - expected[2]) % (1 << 32)) // width)
        proof[width] = len(endpoints) - 1
    return proof


@unittest.skipUnless(shutil.which("c++"), "Optional C++17 compiler check")
class ComputeIntervalRecordsCppTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory()
        directory = Path(cls.temporary.name)
        cls.executable = directory / "records"
        source = directory / "records.cpp"
        source.write_text(HARNESS)
        result = subprocess.run(["c++", "-std=c++17", "-O2", "-Wall", "-Wextra", "-Werror", "-pedantic",
                                 "-I", str(ROOT), str(source), "-o", str(cls.executable)],
                                capture_output=True, text=True, timeout=45)
        if result.returncode:
            cls.temporary.cleanup()
            raise AssertionError(result.stderr)

    @classmethod
    def tearDownClass(cls):
        cls.temporary.cleanup()

    def run_core(self, s, error=None):
        result = subprocess.run([str(self.executable)], input=encode(s), capture_output=True, timeout=5)
        if error is not None:
            self.assertEqual(result.returncode, 2)
            self.assertEqual(result.stdout, b"")
            self.assertIn(error, result.stderr.decode())
        else:
            self.assertEqual(result.returncode, 0, result.stderr.decode())
            return result.stdout

    def test_empty_complete_domain(self):
        specs = [(0, 0, 0)] * 8
        blob = self.run_core(scenario(specs))
        self.assertEqual(blob, b"\0" * 16)
        self.assertEqual(certify(specs, blob), {4: 1, 16: 1})

    def test_overlap_identity_is_source_order_not_equal_data_or_nearest(self):
        specs = [(0x1000, 64, 64), (0x1000, 64, 64), (0x1010, 64, 64)] + [(0, 0, 0)] * 5
        blob = self.run_core(scenario(specs)); certify(specs, blob)
        self.assertEqual(decode(blob)[4], [(0x1000, 0x1040, 2, 0, 0x1000),
                                          (0x1040, 0x1050, 4, 0, 0x1010)])

    def test_typed_floor_and_width_specific_first_match(self):
        specs = [(0x1000, 64, 10), (0x1000, 64, 64)] + [(0, 0, 0)] * 6
        blob = self.run_core(scenario(specs)); certify(specs, blob)
        self.assertEqual(decode(blob)[4][0], (0x1000, 0x1008, 2, 0, 0x1000))
        self.assertEqual(decode(blob)[16][0], (0x1000, 0x1040, 3, 0, 0x1000))
        self.assertEqual(direct_identity(specs, 0x100f, 16), (3, 0, 0x1000))
        self.assertEqual(((0x100f - 0x1000) & U32) >> 4, 0)

    def test_unaligned_zero_ordinary_and_table_are_different(self):
        specs = [(0x1003, 0, 64)] + [(0, 0, 0)] * 4 + [(0x1000, 0, 64), (0, 0, 0), (0, 0, 0)]
        blob = self.run_core(scenario(specs)); certify(specs, blob)
        for rows in decode(blob).values():
            self.assertEqual(rows, [(0x1000, 0x1003, 2, 0, 0x1000),
                                    (0x1003, 0x1040, 7, 0, 0x1000)])

    def test_null_stale_words_do_not_claim_but_real_zero_buffer_can(self):
        specs = [(0, 0, 0)] * 5 + [(0x2000, 64, 0), (0x2000, 64, 16), (0, 0, 0)]
        blob = self.run_core(scenario(specs)); certify(specs, blob)
        for rows in decode(blob).values():
            self.assertEqual(rows, [(0x2000, 0x2010, 7, 1, 0x2000)])

    def test_u32_array_length_shift_and_u64_endpoint_wrap(self):
        cases = [
            [(0, 0, 0)] * 5 + [(0x1000, 0, (1 << 32) + 16), (0, 0, 0), (0, 0, 0)],
            [(0, 0, 0)] * 5 + [(0x1000, 0, 1 << 34), (0, 0, 0), (0, 0, 0)],
            [(U64 - 15, 32, 64)] + [(0, 0, 0)] * 7,
            [(0, 0, 0)] * 5 + [(U64 - 15, 0, 64), (0, 0, 0), (0, 0, 0)],
        ]
        for specs in cases:
            with self.subTest(specs=specs):
                blob = self.run_core(scenario(specs)); certify(specs, blob)
        self.assertEqual(decode(self.run_core(scenario(cases[0])))[4], [(0x1000, 0x1010, 7, 0, 0x1000)])
        for specs in cases[1:]:
            self.assertEqual(self.run_core(scenario(specs)), b"\0" * 16)

    def test_deterministic_complete_domain_cases_match_frozen_python_builder(self):
        from tools.compute_interval_lowering import View, build_partitions, pack_records
        rng = random.Random(0xcca770)
        for case in range(160):
            specs = [(rng.choice((rng.randrange(0, 256), 0x1000 + rng.randrange(64), U64 - rng.randrange(64))),
                      rng.choice((0, 1, 3, 17, 64, U32)),
                      rng.choice((0, 4, 10, 16, 64, (1 << 32) + 16, 1 << 34))) for _ in range(8)]
            with self.subTest(case=case):
                blob = self.run_core(scenario(specs)); certify(specs, blob)
                views = [View(guest, size, bound, i + 2 if i < 5 else 7,
                              0 if i < 5 else i - 5, "single" if i < 5 else "table")
                         for i, (guest, size, bound) in enumerate(specs)]
                self.assertEqual(blob, pack_records(build_partitions(views)))

    def test_unsupported_target_live_mode_and_alignment_fail_closed(self):
        for field, value, error in (("sha", "0" * 64, "Unsupported module"),
                                     ("mode", 0, "Live routing-input producer"),
                                     ("mode", 2, "Live routing-input producer"),
                                     ("mode", 99, "Live routing-input producer"),
                                     ("alignment", 32, "Unsupported module")):
            s = scenario(); s[field] = value
            with self.subTest(field=field, value=value): self.run_core(s, error)

    def test_generation_missing_inventory_and_lifetime_rejections(self):
        cases = []
        s = scenario(); s["owners"] = []; cases.append((s, "inventory is missing"))
        s = scenario(); s["owners"][0][1] = 2; cases.append((s, "Missing exact owner generation"))
        s = scenario(); s["owners"][0][3] = 0; cases.append((s, "backing identity"))
        s = scenario(); s["owners"][0][6] = 8; cases.append((s, "recording lifetime"))
        s = scenario(); s["owners"][0][7] = 6; cases.append((s, "recording lifetime"))
        s = scenario(); s["owners"][0][8] = 101; cases.append((s, "completion prerequisite"))
        s = scenario(); s["c0_view"][4] = 0; cases.append((s, "view/descriptor/recording"))
        s = scenario(); s["c0_view"][6] = 4; cases.append((s, "view/descriptor/recording"))
        s = scenario(); s["roots"][0][2][5] = 6; cases.append((s, "view/descriptor/recording"))
        s = scenario(); s["owners"].append(copy.deepcopy(s["owners"][0])); cases.append((s, "allocation generations"))
        s = scenario(); s["owners"][1][2:4] = [1001, 2]; cases.append((s, "backing generations"))
        for s, error in cases:
            with self.subTest(error=error): self.run_core(s, error)

    def test_descriptor_ranges_nulls_and_root_order_fail_closed(self):
        cases = []
        s = scenario(); s["c0_view"][3] = 2223; cases.append((s, "exactly 2224"))
        s = scenario(); s["c0_view"][2] = 4000; cases.append((s, "true owner extent"))
        s = scenario(); s["roots"][0][2] = view(2, offset=63, size=2); cases.append((s, "true owner extent"))
        s = scenario(); s["roots"][0][2][3] = 16; cases.append((s, "null view"))
        s = scenario(); s["roots"][0][0] = 7; cases.append((s, "typed root"))
        s = scenario(); s["roots"][5][1] = 2; cases.append((s, "typed root"))
        s = scenario(); s["owners"][0][4] = U64; cases.append((s, "range overflows"))
        for s, error in cases:
            with self.subTest(error=error): self.run_core(s, error)

    def test_writable_binding_alias_is_derived_not_a_caller_flag(self):
        specs = [(0, 0, 0)] * 4 + [(0x2000, 16, 16)] + [(0, 0, 0)] * 3
        s = scenario(specs)
        s["owners"][1][2] = 1001
        s["owners"][1][5] = 4096
        s["roots"][4][2][2] = 64 + 192
        self.run_core(s, "Possible write aliases")

    def test_explicit_writer_aliases_all_supported_classes_and_backing_offsets(self):
        for kind in range(4):
            s = scenario()
            # Distinct VkBuffer owner, same backing bytes as the last selector.
            s["owners"].append([3, 1, 1001, 1, 64 + 2208, 16, 1, 7, 0])
            s["writes"] = [[kind, 3, 1, 0, 16, 11, 12]]
            with self.subTest(kind=kind): self.run_core(s, "Possible write aliases")

    def test_pre_upload_writes_and_disjoint_indirect_grid_copy_are_allowed(self):
        s = scenario(); baseline = self.run_core(s)
        s["writes"] = [[3, 1, 1, 64 + 192, 16, 0, 9], [2, 1, 1, 64, 12, 11, 11]]
        self.assertEqual(self.run_core(s), baseline)
        s["writes"][0][-1] = 10
        self.run_core(s, "Possible write aliases")

    def test_writer_metadata_and_command_lease_fail_closed(self):
        cases = []
        for write, error in (([99, 1, 1, 0, 16, 11, 12], "Unknown writer"),
                             ([2, 1, 2, 0, 16, 11, 12], "Missing exact owner"),
                             ([2, 1, 1, 4090, 16, 11, 12], "true owner extent"),
                             ([2, 1, 1, 0, 0, 11, 12], "writer range/order"),
                             ([2, 1, 1, 0, 16, 12, 11], "writer range/order"),
                             ([2, 1, 1, 0, 16, 11, 21], "writer range/order")):
            s = scenario(); s["writes"] = [write]; cases.append((s, error))
        for epoch in ([0, 3, 100, 10, 11, 20], [7, 0, 100, 10, 11, 20],
                      [7, 3, 100, 12, 11, 20], [7, 3, 100, 10, 21, 20]):
            s = scenario(); s["epoch"] = epoch; cases.append((s, "recording/generation/lease"))
        for s, error in cases:
            with self.subTest(error=error): self.run_core(s, error)

    def test_inventory_work_is_bounded(self):
        s = scenario()
        for i in range(3, 258):
            s["owners"].append([i, 1, 2000 + i, 1, 0, 64, 1, 7, 0])
        self.run_core(s, "Owner inventory exceeds")
        s = scenario(); s["writes"] = [[3, 1, 1, 0, 1, 0, 0]] * 1025
        self.run_core(s, "Writer inventory exceeds")
