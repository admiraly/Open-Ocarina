// SPDX-License-Identifier: GPL-3.0-or-later
// Mirror-sync interval planner. The copies match a page-by-page walk: a capture
// whose generation is still the page generation stays on that slot, a protected
// page is omitted, and every other dirty page is copied from guest memory.
// A CPU stamp of a whole 2 MB region becomes one live run. Sparse stamps visit
// only pages that stamp_page has noted. Missing notes fall back to the scan.
// Live gaps are not examined for protection or captures. Callers may use them
// only when protection and GPU-page tracking are off, and only for pages whose
// own chunk generation is below the region stamp. Every capture page must have
// a note. Noted pages are classified even if a wrapped chunk is below that stamp.
#pragma once

#include <atomic>
#include <stddef.h>
#include <stdint.h>
#include <vector>

namespace open_ocarina {
namespace grok_memory {

enum GrokCopyKind { kGrokLive = 0, kGrokCapture = 1 };

struct GrokCopy {
    int kind;
    uint32_t buffer;
    uint64_t dst;
    uint64_t src;
    uint64_t size;
};

struct GrokCap {
    int kept;
    uint32_t gen;
    uint32_t slot;
    uint32_t buffer;
};

enum GrokPlan { kGrokEmpty = 0, kGrokPlanned = 1, kGrokFallback = 2 };

inline void grok_append_copy(std::vector<GrokCopy>& out, const GrokCopy& next) {
    if (!next.size) return;
    if (!out.empty()) {
        GrokCopy& last = out.back();
        const int adjacent = last.dst + last.size == next.dst && last.size <= ~uint64_t(0) - next.size;
        const int live = next.kind == kGrokLive && last.kind == kGrokLive;
        const int same_capture = next.kind == kGrokCapture && last.kind == kGrokCapture &&
                                 next.buffer == last.buffer && last.src + last.size == next.src;
        if (adjacent && (live || same_capture)) {
            last.size += next.size;
            return;
        }
    }
    out.push_back(next);
}

inline uint32_t grok_page_generation(uint32_t chunk_gen, uint32_t region_full) {
    return chunk_gen > region_full ? chunk_gen : region_full;
}

inline GrokCopy grok_page_copy(uint64_t pool_lo, uint64_t pool_hi, uint64_t page, uint32_t page_gen,
                               uint32_t synced, int sync_live, int protect, GrokCap cap) {
    GrokCopy c{};
    if (protect || page_gen <= synced) return c;
    const uint64_t start = page << 12;
    const uint64_t ga = start < pool_lo ? pool_lo : start;
    const uint64_t gb = start + 4096 < pool_hi ? start + 4096 : pool_hi;
    if (gb <= ga) return c;
    if (!sync_live && cap.kept && cap.gen && cap.gen == page_gen) {
        c.kind = kGrokCapture;
        c.buffer = cap.buffer;
        c.dst = ga - pool_lo;
        c.src = (uint64_t)cap.slot * 4096u + (ga - start);
        c.size = gb - ga;
        return c;
    }
    c.kind = kGrokLive;
    c.dst = ga - pool_lo;
    c.src = c.dst;
    c.size = gb - ga;
    return c;
}

// Fixed region storage avoids racing lazy ownership/publication. Publish the
// note before the corresponding release-store of region generation; a planner
// first acquires that generation, then snapshots these words.
struct GrokRegionNotes {
    std::atomic<uint64_t> words[8]{};
    void note(uint64_t page) {
        const unsigned local = unsigned(page & 511u);
        words[local >> 6].fetch_or(1ull << (local & 63u), std::memory_order_release);
    }
    void snapshot(uint64_t (&bits)[8]) const {
        for (unsigned i=0;i<8;++i) bits[i]=words[i].load(std::memory_order_acquire);
    }
};

inline uint64_t grok_copy_pages(uint64_t pool_lo, uint64_t dst, uint64_t size) {
    if (!size) return 0;
    const uint64_t begin=pool_lo+dst, last=begin+size-1;
    return (last >> 12)-(begin >> 12)+1;
}

inline void grok_note_page(uint64_t* bits, uint64_t page) {
    const unsigned local = (unsigned)(page & 511u);
    bits[local >> 6] |= 1ull << (local & 63u);
}

// bits is eight words for this region, or null when no page in it was noted.
// A null bitset with full > synced is a pure region stamp. A null bitset with
// only a raised region generation cannot name the dirty pages, so the caller
// scans. chunk_gen(page) is the page's own stamp, not maxed with region_full.
template <class ChunkGen, class CapFn, class ProtectFn>
GrokPlan grok_plan_region(uint64_t pool_lo, uint64_t pool_hi, uint64_t page0, uint64_t page1,
                          uint32_t synced, uint32_t region_gen, uint32_t full, const uint64_t* bits,
                          int sync_live, ChunkGen chunk_gen, CapFn cap_fn, ProtectFn protect_fn,
                          std::vector<GrokCopy>& out) {
    if (page0 >= page1 || region_gen <= synced) return kGrokEmpty;
    if (!(full > synced) && !bits) return kGrokFallback;

    auto emit_page = [&](uint64_t page, uint32_t page_gen) {
        grok_append_copy(out, grok_page_copy(pool_lo, pool_hi, page, page_gen, synced, sync_live,
                                              protect_fn(page) ? 1 : 0, cap_fn(page)));
    };
    auto emit_live_gap = [&](uint64_t from, uint64_t to) {
        if (from >= to) return;
        const uint64_t start = from << 12;
        const uint64_t stop = to << 12;
        const uint64_t ga = start < pool_lo ? pool_lo : start;
        const uint64_t gb = stop < pool_hi ? stop : pool_hi;
        if (gb <= ga) return;
        GrokCopy c{};
        c.kind = kGrokLive;
        c.dst = ga - pool_lo;
        c.src = c.dst;
        c.size = gb - ga;
        grok_append_copy(out, c);
    };

    if (full > synced) {
        uint64_t cursor = page0;
        if (bits) {
            for (unsigned word = 0; word < 8; ++word) {
                uint64_t mask = bits[word];
                while (mask) {
                    const unsigned bit = (unsigned)__builtin_ctzll(mask);
                    mask &= mask - 1;
                    const uint64_t page = (page0 & ~uint64_t(511)) + (uint64_t)word * 64u + bit;
                    if (page < page0 || page >= page1) continue;
                    const uint32_t chunk = chunk_gen(page);
                    // A uint32 generation can wrap. A retained capture may
                    // then equal full despite a smaller chunk. Its old note
                    // remains set, so classify it rather than assuming stale.
                    if (chunk < full) {
                        const GrokCap cap=cap_fn(page);
                        if (sync_live || !cap.kept || !cap.gen || cap.gen!=full) continue;
                    }
                    emit_live_gap(cursor, page);
                    emit_page(page, grok_page_generation(chunk, full));
                    cursor = page + 1;
                }
            }
        }
        emit_live_gap(cursor, page1);
        return kGrokPlanned;
    }

    for (unsigned word = 0; word < 8; ++word) {
        uint64_t mask = bits[word];
        while (mask) {
            const unsigned bit = (unsigned)__builtin_ctzll(mask);
            mask &= mask - 1;
            const uint64_t page = (page0 & ~uint64_t(511)) + (uint64_t)word * 64u + bit;
            if (page < page0 || page >= page1) continue;
            emit_page(page, grok_page_generation(chunk_gen(page), full));
        }
    }
    return kGrokPlanned;
}

} // namespace grok_memory
} // namespace open_ocarina
