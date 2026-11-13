# SPDX-License-Identifier: GPL-3.0-or-later
"""Mirror-sync planner matches a page walk, including captures and holes."""
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
HEADER = ROOT / "src" / "probes" / "grok_memory_mirror_plan.h"
CXX = shutil.which("c++")
PAGE = 4096
REGION = 512

HARNESS = r"""
#include "grok_memory_mirror_plan.h"
#include <iostream>
#include <sstream>
#include <string>
#include <vector>
using open_ocarina::grok_memory::GrokCap;
using open_ocarina::grok_memory::GrokCopy;
using open_ocarina::grok_memory::GrokPlan;
using open_ocarina::grok_memory::grok_plan_region;
struct Page {
    uint32_t chunk = 0;
    int protect = 0;
    GrokCap cap{};
};
int main() {
    uint64_t pool_lo, pool_hi;
    uint32_t synced, sync_live, nreg;
    if (!(std::cin >> pool_lo >> pool_hi >> synced >> sync_live >> nreg)) return 2;
    struct Reg { uint32_t gen, full; int has; uint64_t bits[8]; };
    std::vector<Reg> regs(nreg);
    for (uint32_t i = 0; i < nreg; ++i) {
        std::cin >> regs[i].gen >> regs[i].full >> regs[i].has;
        for (int b = 0; b < 8; ++b) std::cin >> regs[i].bits[b];
    }
    uint32_t np;
    std::cin >> np;
    std::vector<Page> pages(np);
    for (uint32_t i = 0; i < np; ++i) {
        uint32_t kept;
        std::cin >> pages[i].chunk >> kept >> pages[i].cap.gen >> pages[i].cap.slot
                 >> pages[i].cap.buffer >> pages[i].protect;
        pages[i].cap.kept = kept ? 1 : 0;
    }
    const uint64_t base = pool_lo >> 12;
    std::vector<GrokCopy> out;
    bool fallback = false;
    for (uint32_t r = 0; r < nreg && !fallback; ++r) {
        const uint64_t p0 = std::max(base, uint64_t(r) * 512u);
        const uint64_t p1 = std::min(base + np, uint64_t(r + 1) * 512u);
        if (p0 >= p1) continue;
        auto chunk = [&](uint64_t page) { return pages[page - base].chunk; };
        auto cap = [&](uint64_t page) { return pages[page - base].cap; };
        auto protect = [&](uint64_t page) { return pages[page - base].protect; };
        const GrokPlan plan = grok_plan_region(pool_lo, pool_hi, p0, p1, synced, regs[r].gen, regs[r].full,
                                               regs[r].has ? regs[r].bits : nullptr, sync_live ? 1 : 0,
                                               chunk, cap, protect, out);
        if (plan == open_ocarina::grok_memory::kGrokFallback) fallback = true;
    }
    if (fallback) { std::cout << "fallback\n"; return 0; }
    std::cout << out.size() << "\n";
    for (uint32_t i = 0; i < out.size(); ++i)
        std::cout << out[i].kind << " " << out[i].buffer << " " << out[i].dst << " " << out[i].src
                  << " " << out[i].size << "\n";
    return 0;
}
"""


def merge(copies):
    out = []
    for kind, buf, dst, src, size in copies:
        if not size:
            continue
        if out:
            last = out[-1]
            adjacent = last[2] + last[4] == dst and last[4] + size >= last[4]
            live = kind == 0 and last[0] == 0
            capture = kind == 1 and last[0] == 1 and buf == last[1] and last[3] + last[4] == src
            if adjacent and (live or capture):
                last[4] += size
                continue
        out.append([kind, buf, dst, src, size])
    return out


def reference(pool_lo, pool_hi, synced, sync_live, regions, pages):
    """Independent page walk of the frozen mirror_sync decisions."""
    base = pool_lo >> 12
    copies = []
    page = base
    end = base + len(pages)
    while page < end:
        region = page // REGION
        gen, full, _bits = regions[region]
        if gen <= synced:
            page = min(end, (region + 1) * REGION)
            continue
        chunk = pages[page - base]["chunk"]
        page_gen = max(chunk, full)
        protect = pages[page - base]["protect"]
        cap = pages[page - base]["cap"]
        if protect or page_gen <= synced:
            page += 1
            continue
        start = page * PAGE
        ga = max(start, pool_lo)
        gb = min(start + PAGE, pool_hi)
        if not sync_live and cap[0] and cap[1] and cap[1] == page_gen:
            copies.append((1, cap[3], ga - pool_lo, cap[2] * PAGE + (ga - start), gb - ga))
        else:
            copies.append((0, 0, ga - pool_lo, ga - pool_lo, gb - ga))
        page += 1
    return merge(copies)


def noted_bits(region_index, pages, base):
    bits = [0] * 8
    for i, page in enumerate(pages):
        if not page["noted"]:
            continue
        absolute = base + i
        if absolute // REGION != region_index:
            continue
        local = absolute & 511
        bits[local >> 6] |= 1 << (local & 63)
    return bits


def plan_input(pool_lo, pool_hi, synced, sync_live, regions, pages):
    """Select the fast path the patch uses. A raised region with no notes falls back."""
    base = pool_lo >> 12
    lines = [f"{pool_lo} {pool_hi} {synced} {int(sync_live)} {len(regions)}"]
    fallback = False
    for index, (gen, full, _ignored) in enumerate(regions):
        bits = noted_bits(index, pages, base)
        has = 1 if any(bits) else 0
        if gen > synced and not (full > synced) and not has:
            fallback = True
        lines.append(" ".join([str(gen), str(full), str(has), *map(str, bits)]))
    lines.append(str(len(pages)))
    for page in pages:
        cap = page["cap"]
        lines.append(
            f"{page['chunk']} {int(cap[0])} {cap[1]} {cap[2]} {cap[3]} {int(page['protect'])}"
        )
    return fallback, "\n".join(lines) + "\n"


def compile_planner(work):
    work = Path(work)
    source = work / "plan.cc"
    source.write_text(HARNESS)
    binary = work / "plan"
    subprocess.check_call(
        [CXX, "-std=c++20", "-O2", "-DNDEBUG", "-Wall", "-Wextra", "-Werror", f"-I{HEADER.parent}", str(source), "-o", str(binary)],
        cwd=work, timeout=30,
    )
    return binary


def run_planner(binary, text):
    result = subprocess.run([str(binary)], input=text, text=True, capture_output=True, check=True, timeout=5)
    lines = result.stdout.splitlines()
    if lines == ["fallback"]:
        return "fallback"
    count = int(lines[0])
    copies = []
    for line in lines[1:]:
        kind, buf, dst, src, size = map(int, line.split())
        copies.append((kind, buf, dst, src, size))
    if len(copies) != count:
        raise AssertionError("planner count does not match rows")
    return copies


def blank_page():
    return {"chunk": 0, "protect": 0, "noted": 0, "cap": (0, 0, 0, 0)}


def scenario(regions, mutate, pool_lo=0, pool_hi=None, synced=1, sync_live=False):
    count = len(regions) * REGION
    pages = [blank_page() for _ in range(count)]
    mutate(pages)
    if pool_hi is None:
        pool_hi = count * PAGE
    return pool_lo, pool_hi, synced, sync_live, regions, pages


@unittest.skipUnless(CXX, "C++ compiler unavailable")
class MirrorPlanTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.work = tempfile.TemporaryDirectory(prefix="grok-memory-")
        cls.addClassCleanup(cls.work.cleanup)
        cls.binary = compile_planner(cls.work.name)

    def assert_matches(self, built):
        pool_lo, pool_hi, synced, sync_live, regions, pages = built
        expected = reference(pool_lo, pool_hi, synced, sync_live, regions, pages)
        fallback, text = plan_input(pool_lo, pool_hi, synced, sync_live, regions, pages)
        got = run_planner(self.binary, text)
        if fallback:
            self.assertEqual(got, "fallback")
            return
        self.assertEqual(got, [tuple(copy) for copy in expected])

    def test_sparse_notes_skip_clean_pages_and_keep_capture_slots(self):
        def mutate(pages):
            pages[3]["chunk"] = 4
            pages[3]["noted"] = 1
            pages[3]["cap"] = (1, 4, 9, 2)
            pages[4]["chunk"] = 4
            pages[4]["noted"] = 1
            pages[4]["cap"] = (1, 4, 10, 2)
            pages[80]["chunk"] = 5
            pages[80]["noted"] = 1
            pages[80]["cap"] = (1, 4, 1, 7)  # stale capture generation: live copy

        regions = [(5, 0, None)] + [(0, 0, None)] * 0
        # one region is enough; region_gen 5, full 0
        self.assert_matches(scenario([(5, 0, None)], mutate, synced=2))

    def test_region_stamp_is_one_live_run_and_newer_capture_is_cut_out(self):
        def mutate(pages):
            pages[10]["chunk"] = 9
            pages[10]["noted"] = 1
            pages[10]["cap"] = (1, 9, 3, 4)
            pages[11]["chunk"] = 9
            pages[11]["noted"] = 1
            pages[200]["protect"] = 1
            pages[200]["chunk"] = 9
            pages[200]["noted"] = 1

        # full generation 8 dirties the region. Pages at chunk 9 are not part of that run.
        self.assert_matches(scenario([(9, 8, None)], mutate, synced=3))

    def test_protected_noted_page_is_a_hole_between_live_copies(self):
        def mutate(pages):
            for index in (39, 40, 41):
                pages[index]["chunk"] = 4
                pages[index]["noted"] = 1
            pages[40]["protect"] = 1

        built = scenario([(4, 0, None)], mutate, synced=1)
        self.assert_matches(built)
        copies = reference(0, REGION * PAGE, 1, False, built[4], built[5])
        self.assertEqual(len(copies), 2)
        self.assertEqual(copies[0][2] + copies[0][4], 40 * PAGE)
        self.assertEqual(copies[1][2], 41 * PAGE)

    def test_capture_equal_to_region_full_is_not_swallowed(self):
        def mutate(pages):
            # Legal shape: the capture generation was written into the page too.
            pages[15]["chunk"] = 6
            pages[15]["noted"] = 1
            pages[15]["cap"] = (1, 6, 1, 8)

        self.assert_matches(scenario([(6, 6, None)], mutate, synced=2))

    def test_illegal_capture_without_page_stamp_is_a_different_plan(self):
        def mutate(pages):
            pages[15]["cap"] = (1, 6, 1, 8)  # no chunk stamp and no note

        pool_lo, pool_hi, synced, sync_live, regions, pages = scenario([(6, 6, None)], mutate, synced=2)
        expected = reference(pool_lo, pool_hi, synced, sync_live, regions, pages)
        _fallback, text = plan_input(pool_lo, pool_hi, synced, sync_live, regions, pages)
        got = run_planner(self.binary, text)
        self.assertNotEqual(got, expected)
        self.assertTrue(any(copy[0] == 1 for copy in expected))
        self.assertTrue(all(copy[0] == 0 for copy in got))

    def test_missing_note_on_a_sparse_page_falls_back(self):
        def mutate(pages):
            pages[2]["chunk"] = 3  # dirty, but stamp_page's note was lost

        built = scenario([(3, 0, None)], mutate, synced=1)
        fallback, text = plan_input(*built)
        self.assertTrue(fallback)
        self.assertEqual(run_planner(self.binary, text), "fallback")

    def test_sync_live_ignores_a_matching_capture(self):
        def mutate(pages):
            pages[1]["chunk"] = 3
            pages[1]["noted"] = 1
            pages[1]["cap"] = (1, 3, 0, 1)

        self.assert_matches(scenario([(3, 0, None)], mutate, synced=1, sync_live=True))

    def test_pool_edges_clip_the_first_and_last_page(self):
        def mutate(pages):
            pages[0]["chunk"] = 2
            pages[0]["noted"] = 1
            pages[REGION]["chunk"] = 2
            pages[REGION]["noted"] = 1

        pool_lo = 100
        pool_hi = REGION * PAGE + 50
        self.assert_matches(
            scenario([(2, 0, None), (2, 0, None)], mutate, pool_lo=pool_lo, pool_hi=pool_hi, synced=1)
        )

    def test_noncontiguous_capture_slots_stay_split(self):
        def mutate(pages):
            pages[5]["chunk"] = 2
            pages[5]["noted"] = 1
            pages[5]["cap"] = (1, 2, 1, 3)
            pages[6]["chunk"] = 2
            pages[6]["noted"] = 1
            pages[6]["cap"] = (1, 2, 4, 3)

        built = scenario([(2, 0, None)], mutate, synced=1)
        self.assert_matches(built)
        copies = reference(0, REGION * PAGE, 1, False, built[4], built[5])
        self.assertEqual(len(copies), 2)

    def test_clean_region_emits_nothing(self):
        def mutate(pages):
            pages[8]["chunk"] = 2
            pages[8]["noted"] = 1

        self.assert_matches(scenario([(2, 0, None)], mutate, synced=2))

    def test_wrapped_generation_keeps_a_noted_matching_capture(self):
        def mutate(pages):
            pages[7]["chunk"] = 1
            pages[7]["noted"] = 1  # retained since the original capture stamp
            pages[7]["cap"] = (1, 3, 19, 8)
        built=scenario([(3,3,None)],mutate,synced=2)
        self.assert_matches(built)
        copies=reference(0,REGION*PAGE,2,False,built[4],built[5])
        self.assertTrue(any(c[0]==1 and c[1]==8 for c in copies))

    def test_atomic_notes_and_clipped_page_counter(self):
        # Execute the actual added helper rather than a Python mirror. The
        # synchronized region generation is the source publication protocol.
        source=r"""
#include "grok_memory_mirror_plan.h"
#include <thread>
#include <cstdlib>
using namespace open_ocarina::grok_memory;
int main() {
 GrokRegionNotes notes;
 std::thread workers[8];
 for (unsigned t=0;t<8;++t) workers[t]=std::thread([&,t]{
  for(unsigned n=0;n<10000;++n)for(unsigned p=t;p<512;p+=8)notes.note(p);
 });
 for(auto& t:workers)t.join();
 uint64_t bits[8];notes.snapshot(bits);
 for(auto b:bits)if(b!=~uint64_t(0))return 1;
 GrokRegionNotes published;
 std::atomic<unsigned> generation{0};
 std::thread producer([&]{published.note(511);generation.store(7,std::memory_order_release);});
 while(generation.load(std::memory_order_acquire)!=7)std::this_thread::yield();
 published.snapshot(bits);producer.join();if(!(bits[7]&(1ull<<63)))return 2;
 if(grok_copy_pages(3996,0,150)!=2 || grok_copy_pages(0,4096,4096)!=1 ||
    grok_copy_pages(3996,0,0)!=0)return 3;
 return 0;
}
"""
        with tempfile.TemporaryDirectory(prefix="oo-mirror-atomic-") as tmp:
            directory=Path(tmp);cpp=directory/"notes.cpp";exe=directory/"notes"
            cpp.write_text(source)
            subprocess.run([CXX,"-std=c++20","-O2","-DNDEBUG","-Wall","-Wextra",
                            "-Werror","-pthread","-I",str(HEADER.parent),str(cpp),
                            "-o",str(exe)],check=True,capture_output=True,text=True,timeout=30)
            subprocess.run([str(exe)],check=True,timeout=15)

    def test_opt_in_publication_precedes_chunk_and_region_stores(self):
        patch=(ROOT/"patches/hookshot/grok-memory-mirror-plan.patch").read_text()
        self.assertIn('static const bool enabled=env_on("RX_VK_GROK_MIRROR_PLAN")',patch)
        self.assertIn('grok_mirror_plan_on() && !protect_bare_on() && !gpu_pages_on()',patch)
        self.assertIn('if (grok_mirror_plan_on()) grok_region_notes()[p / kRegionPages].note(p);\n'
                      '     store_u32(c[p % kChunkPages], g);\n',patch)
        self.assertIn('const uint32_t generation = load_u32(region_gen[region]);',patch)
        self.assertIn('grok_region_notes()[region].snapshot(bits);',patch)

    def test_header_is_the_patched_payload(self):
        patch = (ROOT / "patches" / "hookshot" / "grok-memory-mirror-plan.patch").read_text()
        lines = []
        copying = False
        for line in patch.splitlines():
            if line.startswith("+++ b/gpu/vk/grok_memory_mirror_plan.h"):
                copying = True
                continue
            if copying and line.startswith("diff --git"):
                break
            if copying and line.startswith("+") and not line.startswith("+++"):
                lines.append(line[1:])
        self.assertEqual("\n".join(lines) + "\n", HEADER.read_text())


if __name__ == "__main__":
    unittest.main()
