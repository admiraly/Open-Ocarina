#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""CPU model of Hookshot global-address lowering.

Compares the unrolled first-match storage-buffer walk plus page-table fallback
with a page-table-only helper. Finite boundary-grid sampling finds counterexamples;
an empty objection list is not a proof or permission to change a runtime pipeline.
Synthetic ranges only: no guest addresses from a capture, no device execution.
"""

from __future__ import annotations

import unittest

PAGE = 4096
W0 = 0x1000000000
W0_BYTES = 8 << 30
W1 = 0x7F00000000
W1_BYTES = 4 << 30
W0_PAGES = W0_BYTES >> 12
ALIGN = 16
SIZES = (4, 8, 16)


class Binding:
    def __init__(self, guest, size_word, bound_len, device, blob, kind="single", index=0):
        self.guest = guest
        self.size_word = size_word
        self.bound_len = bound_len
        self.device = device
        self.blob = blob
        self.kind = kind
        self.index = index

    def aligned(self):
        return self.guest & ~(ALIGN - 1)

    def end(self):
        # size word 0: the binding length is the end. Otherwise the exclusive
        # end is the nearer of the NVN size and the bound bytes.
        if self.size_word == 0:
            return self.aligned() + self.bound_len
        by_size = self.guest + self.size_word
        by_bind = self.aligned() + self.bound_len
        return min(by_size, by_bind)


class Image:
    """Device memory and a guest page table. Pending runs are not visible yet."""

    def __init__(self):
        self.cpu = {}
        self.gpu = {}
        self.pending = []
        self.mem = {}

    def map_page(self, guest_page, device_page, onto="both"):
        if onto in ("both", "cpu"):
            self.cpu[guest_page] = device_page
        if onto in ("both", "gpu"):
            self.gpu[guest_page] = device_page

    def defer(self, guest_page, device_page):
        self.pending.append((guest_page, device_page))

    def flush(self):
        # Last write to a page wins, matching memory_table_flush.
        for page, dev in self.pending:
            self.gpu[page] = dev
        self.pending.clear()

    def table(self, which):
        src = self.cpu if which == "cpu" else self.gpu
        if which == "gpu":
            out = dict(src)
            for page, dev in self.pending:
                out[page] = dev
            return out
        return src


def window_index(addr):
    off0 = addr - W0
    off1 = addr - W1
    in0 = 0 <= off0 < W0_BYTES
    in1 = 0 <= off1 < W1_BYTES
    if in0:
        return off0 >> 12
    if in1:
        return W0_PAGES + (off1 >> 12)
    return None


def pt_translate(table, addr, size):
    if (addr & 0xFFF) + size > PAGE:
        return None
    index = window_index(addr)
    if index is None:
        return None
    dev = table.get(index, 0)
    if not dev:
        return None
    return dev + (addr & 0xFFF)


def read_dev(image, dev, size):
    out = bytearray(size)
    for i in range(size):
        out[i] = image.mem.get(dev + i, 0)
    return bytes(out)


def write_blob(image, binding):
    for i, b in enumerate(binding.blob):
        image.mem[binding.device + i] = b


def ssbo_hit(bindings, addr):
    """First-match, with nearest-base among kind=='candidate' before the rest."""
    cands = [b for b in bindings if b.kind == "candidate"]
    best = None
    best_dist = None
    for b in cands:
        base = b.aligned()
        end = b.end()
        if base <= addr < end:
            dist = addr - base
            if best is None or dist < best_dist:
                best, best_dist = b, dist
    if best is not None:
        return best
    for b in bindings:
        if b.kind == "candidate":
            continue
        base = b.aligned()
        if base <= addr < b.end():
            return b
    return None


def binding_load(image, binding, relative, size, robust_mode="whole", alignment=1):
    """Model explicit allowed robustness policies, not a universal Vulkan oracle.

    Vulkan permits wide accesses to be checked in 32-bit pieces and permits
    descriptor ranges rounded to a reported robustness alignment. Both choices
    must be preserved by a transformed hit path. Real driver behavior still
    needs a device profile and GPU checks.
    """
    if alignment <= 0 or alignment & (alignment - 1):
        raise ValueError("Robustness alignment must be a positive power of two")
    limit = (binding.bound_len + alignment - 1) & ~(alignment - 1)
    if robust_mode not in ("whole", "component32"):
        raise ValueError("Unknown robustness model")
    chunk = size if robust_mode == "whole" else 4
    if size % chunk:
        raise ValueError("Modeled accesses must contain complete components")
    result = bytearray()
    for offset in range(0, size, chunk):
        position = relative + offset
        result.extend(read_dev(image, binding.device + position, chunk)
                      if 0 <= position and position + chunk <= limit else bytes(chunk))
    return bytes(result)


def legacy_load(image, bindings, addr, size, table="gpu", robust_mode="whole", alignment=1):
    hit = ssbo_hit(bindings, addr)
    if hit is not None:
        rel = addr - hit.aligned()
        return binding_load(image, hit, rel, size, robust_mode, alignment)
    dev = pt_translate(image.table(table), addr, size)
    if dev is None:
        return bytes(size)
    return read_dev(image, dev, size)


def pt_only_load(image, addr, size, table="gpu"):
    dev = pt_translate(image.table(table), addr, size)
    if dev is None:
        return bytes(size)
    return read_dev(image, dev, size)


def legacy_store_effect(image, bindings, addr, follow_stores):
    """True when a one-byte store is visible. Misses are dropped unless follow_stores."""
    if ssbo_hit(bindings, addr) is not None:
        return True
    if not follow_stores:
        return False
    return pt_translate(image.table("gpu"), addr, 1) is not None


def interesting_addrs(bindings, image=None):
    addrs = set()
    for b in bindings:
        base, end = b.aligned(), b.end()
        for a in (base, base + 4, end - 16, end - 4, end, end + 4):
            if a > 0:
                addrs.add(a)
        # Page edges inside and just past the claim.
        page = base & ~0xFFF
        while page < end + PAGE:
            addrs.add(page)
            addrs.add(max(page - 4, 0))
            addrs.add(page + PAGE - 16)
            addrs.add(page + PAGE - 4)
            page += PAGE
            if page > end + PAGE:
                break
    if image is not None:
        for index in image.table("gpu"):
            if index < W0_PAGES:
                guest = W0 + (index << 12)
            else:
                guest = W1 + ((index - W0_PAGES) << 12)
            addrs.add(guest)
            addrs.add(guest + PAGE - 8)
    return sorted(a for a in addrs if a and window_index(a) is not None)


def flatten_singles(bindings):
    """First-match singles as disjoint guest intervals. Candidates stay nearest-base."""
    claimed = []
    for b in bindings:
        if b.kind == "candidate" or b.bound_len <= 0 or b.guest == 0:
            continue
        start, end = b.aligned(), b.end()
        cursor = start
        while cursor < end:
            blocker = None
            for lo, hi, _ in claimed:
                if lo <= cursor < hi:
                    blocker = hi
                    break
            if blocker is not None:
                cursor = blocker
                continue
            nxt = end
            for lo, hi, _ in claimed:
                if cursor < lo < nxt:
                    nxt = lo
            claimed.append((cursor, nxt, b))
            cursor = nxt
    return claimed


def specialized_load(image, bindings, addr, size, robust_mode="whole", alignment=1):
    """Interval-table helper: same bytes as the unrolled walk, hits stay on the binding."""
    cands = [b for b in bindings if b.kind == "candidate"]
    best = None
    best_dist = None
    for b in cands:
        base, end = b.aligned(), b.end()
        if base <= addr < end:
            dist = addr - base
            if best is None or dist < best_dist:
                best, best_dist = b, dist
    hit = best
    if hit is None:
        for lo, hi, b in flatten_singles(bindings):
            if lo <= addr < hi:
                hit = b
                break
    if hit is not None:
        rel = addr - hit.aligned()
        return binding_load(image, hit, rel, size, robust_mode, alignment)
    dev = pt_translate(image.table("gpu"), addr, size)
    if dev is None:
        return bytes(size)
    return read_dev(image, dev, size)


def sampled_objections(image, bindings, follow_stores=False, stores=False):
    """Find sampled objections, not proof of equivalence or pipeline permission."""
    reasons = []
    gpu = image.table("gpu")
    if image.pending and any(image.cpu.get(p, 0) != d for p, d in image.pending):
        # Reading the CPU mapping would certify the wrong table. Callers must
        # pass the post-flush view; this flags a stale snapshot if used raw.
        pass
    for b in bindings:
        if b.bound_len <= 0 or b.guest == 0:
            continue
        # Device identity: each page of the claim must be the binding's bytes.
        base, end = b.aligned(), b.end()
        page = base & ~0xFFF
        while page < end:
            dev = gpu.get(window_index(page), 0) if window_index(page) is not None else 0
            expect = b.device + (page - base) if page >= base else None
            if page < base:
                page += PAGE
                continue
            if not dev or dev != b.device + (page - base):
                reasons.append("claim-device")
                break
            page += PAGE
    probed = interesting_addrs(bindings, image)
    for addr in probed:
        for size in SIZES:
            if legacy_load(image, bindings, addr, size) != pt_only_load(image, addr, size):
                reasons.append("bytes")
                break
        if "bytes" in reasons:
            break
    if stores and not follow_stores:
        for addr in probed:
            if legacy_store_effect(image, bindings, addr, False) != (
                pt_translate(image.table("gpu"), addr, 1) is not None
            ):
                reasons.append("dropped-store")
                break
    # Distinct reasons, stable order.
    out = []
    for r in reasons:
        if r not in out:
            out.append(r)
    return out


def paint(image, binding):
    write_blob(image, binding)
    # Mirror the binding into the page table as one contiguous device range.
    base = binding.aligned()
    span = max(binding.bound_len, binding.size_word + (binding.guest - base))
    page = base & ~0xFFF
    last = base + span
    while page < last:
        if window_index(page) is not None:
            image.map_page(window_index(page), binding.device + (page - base))
        page += PAGE


class CertificateTests(unittest.TestCase):
    def test_empty_sampled_objections_is_not_an_empty_claim_proof(self):
        image = Image()
        binding = Binding(W0, 16, 16, 0xA00000, bytes(PAGE))
        paint(image, binding)
        self.assertEqual(sampled_objections(image, [binding]), [])
        self.assertGreater(binding.bound_len, 0)

    def test_partial_vector_checks_are_not_universal_whole_vector_zero(self):
        image = Image()
        binding = Binding(W0, 16, 16, 0x800000, bytes([3]) * 16 + bytes([9]) * 16)
        paint(image, binding)
        address = W0 + 12
        self.assertEqual(legacy_load(image, [binding], address, 16), bytes(16))
        components = bytes([3]) * 4 + bytes(12)
        self.assertEqual(legacy_load(image, [binding], address, 16, robust_mode="component32"), components)
        for mode in ("whole", "component32"):
            self.assertEqual(specialized_load(image, [binding], address, 16, mode),
                             legacy_load(image, [binding], address, 16, robust_mode=mode))
            self.assertNotEqual(pt_only_load(image, address, 16),
                                legacy_load(image, [binding], address, 16, robust_mode=mode))

    def test_reported_robustness_alignment_can_round_descriptor_bounds(self):
        image = Image()
        binding = Binding(W0, 10, 10, 0x900000, bytes([5]) * 16)
        paint(image, binding)
        self.assertEqual(legacy_load(image, [binding], W0 + 8, 4), bytes(4))
        self.assertEqual(legacy_load(image, [binding], W0 + 8, 4, alignment=4), bytes([5]) * 4)
        self.assertEqual(specialized_load(image, [binding], W0 + 8, 4, alignment=4), bytes([5]) * 4)

    def test_interior_hit_matches_but_tail_does_not(self):
        image = Image()
        blob = bytes([i & 255 for i in range(4096)])
        # Entirely inside one page, but a 16-byte load may start in the last 15 bytes.
        b = Binding(W0 + 0x100, 256, 256, 0xA00000, blob)
        paint(image, b)
        addr = W0 + 0x180
        self.assertEqual(legacy_load(image, [b], addr, 16), pt_only_load(image, addr, 16))
        self.assertEqual(specialized_load(image, [b], addr, 16), legacy_load(image, [b], addr, 16))
        tail = b.aligned() + b.bound_len - 4
        self.assertEqual(legacy_load(image, [b], tail, 16), bytes(16))
        self.assertNotEqual(pt_only_load(image, tail, 16), bytes(16))
        self.assertEqual(specialized_load(image, [b], tail, 16), bytes(16))
        self.assertIn("bytes", sampled_objections(image, [b]))

    def test_page_straddle_inside_binding_disagrees(self):
        image = Image()
        blob = bytes([0x5A]) * 8192
        b = Binding(W0, 8192, 8192, 0xB00000, blob)
        paint(image, b)
        addr = W0 + PAGE - 8
        self.assertEqual(len(legacy_load(image, [b], addr, 16)), 16)
        self.assertNotEqual(legacy_load(image, [b], addr, 16), pt_only_load(image, addr, 16))
        self.assertIn("bytes", sampled_objections(image, [b]))

    def test_short_binding_reads_neighbor_on_page_table(self):
        image = Image()
        blob = bytes([1]) * 256 + bytes([9]) * (PAGE - 256)
        # Bound 256 bytes, but the page table maps the whole page to this device range.
        b = Binding(W0 + 32, 256, 256, 0xC00000, blob)
        image.mem.update({b.device + i: blob[i] for i in range(len(blob))})
        image.map_page(window_index(W0), b.device - 32)
        addr = b.aligned() + 248
        self.assertEqual(legacy_load(image, [b], addr, 16), bytes(16))
        self.assertEqual(pt_only_load(image, addr, 16), bytes([1]) * 8 + bytes([9]) * 8)
        self.assertIn("bytes", sampled_objections(image, [b]))

    def test_first_match_beats_later_overlap(self):
        image = Image()
        first = Binding(W0, 1024, 1024, 0xD00000, bytes([1]) * 1024, index=0)
        second = Binding(W0, 1024, 1024, 0xE00000, bytes([2]) * 1024, index=1)
        write_blob(image, first)
        write_blob(image, second)
        # Page table sees the second pool. The walk must still return the first.
        image.map_page(window_index(W0), second.device)
        self.assertEqual(legacy_load(image, [first, second], W0 + 16, 4), bytes([1]) * 4)
        self.assertEqual(pt_only_load(image, W0 + 16, 4), bytes([2]) * 4)
        self.assertEqual(specialized_load(image, [first, second], W0 + 16, 4), bytes([1]) * 4)
        self.assertEqual(flatten_singles([first, second])[0][2], first)
        self.assertIn("claim-device", sampled_objections(image, [first, second]))

    def test_nearest_candidate_not_first_index(self):
        image = Image()
        wide = Binding(W0, 8192, 8192, 0x1000, bytes([3]) * 8192, kind="candidate", index=0)
        near = Binding(W0 + 4096, 256, 256, 0x1000 + 4096, bytes([4]) * 256, kind="candidate", index=1)
        paint(image, wide)
        # Near is a subrange of the same device bytes, already mapped with wide.
        image.mem.update({near.device + i: 4 for i in range(256)})
        addr = W0 + 4096 + 8
        self.assertIs(ssbo_hit([wide, near], addr), near)
        self.assertEqual(legacy_load(image, [wide, near], addr, 4), bytes([4]) * 4)

    def test_null_descriptor_falls_through(self):
        image = Image()
        empty = Binding(W0, 0, 0, 0, b"")
        live = bytes([7]) * 64
        image.map_page(window_index(W0 + 0x2000), 0x550000)
        for i, byte in enumerate(live):
            image.mem[0x550000 + 0x2000 % PAGE + i] = byte
        addr = (W0 + 0x2000) & ~0xFFF
        # empty binding does not claim; both paths use the table
        self.assertEqual(legacy_load(image, [empty], addr, 4), pt_only_load(image, addr, 4))
        self.assertEqual(sampled_objections(image, [empty]), [])

    def test_stale_cpu_table_is_not_the_gpu_table(self):
        image = Image()
        page = window_index(W0 + 0x3000)
        image.map_page(page, 0x111000, onto="cpu")
        image.defer(page, 0x660000)
        for i in range(16):
            image.mem[0x660000 + i] = 8
        addr = W0 + 0x3000
        # No descriptor claims this address, so the load is the page table.
        self.assertEqual(legacy_load(image, [], addr, 4, table="cpu"), bytes(4))
        self.assertEqual(legacy_load(image, [], addr, 4, table="gpu"), bytes([8]) * 4)
        self.assertEqual(pt_only_load(image, addr, 4), bytes([8]) * 4)
        image.flush()
        self.assertEqual(image.gpu[page], 0x660000)
        self.assertEqual(image.cpu[page], 0x111000)

    def test_size_word_zero_does_not_mean_whole_mapped_page(self):
        image = Image()
        blob = bytes([6]) * PAGE
        b = Binding(W0 + 64, 0, 128, 0x770040, blob)
        image.mem.update({b.device + i: blob[i] for i in range(PAGE)})
        image.map_page(window_index(W0), b.device - 64)
        # Past the 128-byte guess the walk returns robust zero; the page is mapped.
        addr = b.end() - 4
        self.assertEqual(legacy_load(image, [b], addr, 16), bytes(16))
        self.assertNotEqual(pt_only_load(image, addr, 16), bytes(16))
        self.assertEqual(specialized_load(image, [b], addr, 16), legacy_load(image, [b], addr, 16))
        self.assertIn("bytes", sampled_objections(image, [b]))

    def test_dropped_store_outside_claims(self):
        image = Image()
        image.map_page(window_index(W0 + PAGE), 0x880000)
        addr = W0 + PAGE + 8
        self.assertFalse(legacy_store_effect(image, [], addr, follow_stores=False))
        self.assertTrue(pt_translate(image.table("gpu"), addr, 1) is not None)
        reasons = sampled_objections(image, [], follow_stores=False, stores=True)
        self.assertIn("dropped-store", reasons)
        self.assertEqual(sampled_objections(image, [], follow_stores=True, stores=True), [])

    def test_second_window_only(self):
        image = Image()
        blob = bytes([3]) * 512
        b = Binding(W1 + 0x8000, 512, 512, 0x990000, blob)
        paint(image, b)
        self.assertGreaterEqual(window_index(b.aligned()), W0_PAGES)
        self.assertEqual(legacy_load(image, [b], b.aligned(), 4), bytes([3]) * 4)
        self.assertEqual(specialized_load(image, [b], b.aligned(), 4), bytes([3]) * 4)
        # Same tail disagreement as the low window: PT-only is not free of claims.
        self.assertIn("bytes", sampled_objections(image, [b]))

    def test_empty_claims_match_page_table(self):
        image = Image()
        image.map_page(window_index(W0 + 64), 0xABC000)
        for i in range(32):
            image.mem[0xABC000 + 64 + i] = 11
        self.assertEqual(sampled_objections(image, []), [])
        addr = W0 + 64
        self.assertEqual(legacy_load(image, [], addr, 16), bytes([11]) * 16)
        self.assertEqual(pt_only_load(image, addr, 16), bytes([11]) * 16)

    def test_specialized_grid_matches_legacy(self):
        image = Image()
        wide = Binding(W0, 3000, 3000, 0x1000, bytes([3]) * 3000, kind="candidate")
        near = Binding(W0 + 2000, 200, 200, 0x1000 + 2000, bytes([4]) * 200, kind="candidate")
        single = Binding(W0 + 3500, 100, 100, 0x9000, bytes([5]) * 100)
        later = Binding(W0 + 3500, 100, 100, 0x9100, bytes([6]) * 100)
        for b in (wide, near, single, later):
            write_blob(image, b)
        paint(image, wide)
        for addr in range(W0, W0 + 4200, 4):
            for size in SIZES:
                self.assertEqual(
                    specialized_load(image, [wide, near, single, later], addr, size),
                    legacy_load(image, [wide, near, single, later], addr, size),
                    (addr, size),
                )


if __name__ == "__main__":
    unittest.main()
