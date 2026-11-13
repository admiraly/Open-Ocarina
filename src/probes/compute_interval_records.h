// SPDX-License-Identifier: GPL-3.0-or-later
// Independently authored, CPU-only C++17 core. No Vulkan or renderer dependency.
// Consistency checks on supplied evidence do not authenticate its producer.
#ifndef OPEN_OCARINA_COMPUTE_INTERVAL_RECORDS_H
#define OPEN_OCARINA_COMPUTE_INTERVAL_RECORDS_H

#include <algorithm>
#include <array>
#include <cstddef>
#include <cstdint>
#include <limits>
#include <stdexcept>
#include <string>
#include <vector>

namespace open_ocarina {
namespace compute_intervals {

constexpr const char* kModuleSha256 =
    "b9af52c0ba07f64eb8b0ca2905c432d094999fdf669aa95182ff7754b510e97b";
constexpr std::size_t kC0Bytes = 2224;
constexpr std::uint32_t kRecordBinding = 12;
constexpr std::uint32_t kMaximumRowsPerWidth = 64;
constexpr std::size_t kMaximumOwners = 256, kMaximumAdditionalWrites = 1024;

// Live extraction and command-access inventory production remain unimplemented.
enum class SourceMode { Unspecified, OwnedFrozenGraph, LiveAsSubmitted };
enum class WriteKind { Storage, Physical, Transfer, Host };

struct Key {
    std::uint64_t id = 0, generation = 0;
};
inline bool operator==(const Key& a, const Key& b) {
    return a.id == b.id && a.generation == b.generation;
}

struct Owner {
    Key allocation, backing;
    std::uint64_t backing_offset = 0, size = 0;
    // All recording values must be in the producer's single monotonic domain.
    std::uint64_t first_recording = 0, retained_through_recording = 0;
    // Completion prerequisite for reusing this allocation, not its last writer.
    std::uint64_t reuse_after_completion = 0;
};

struct OwnedView {
    Key allocation;
    std::uint64_t offset = 0, range = 0, view_generation = 0;
    std::uint64_t recording = 0, descriptor_generation = 0;
    // A null view has zero owner, offset and range; generation still identifies
    // the pending descriptor writer that supplied this null value.
};

struct Root {
    std::uint32_t binding = 0, element = 0;
    OwnedView view;
};

struct WriteSpan {
    WriteKind kind = WriteKind::Storage;
    Key allocation;
    std::uint64_t offset = 0, size = 0;
    std::uint64_t first_order = 0, last_order = 0;
};

struct Input {
    SourceMode mode = SourceMode::Unspecified;
    std::string module_sha256;
    std::uint64_t recording = 0, descriptor_generation = 0;
    std::uint64_t completed_value = 0;
    std::uint64_t selectors_ready_order = 0, dispatch_order = 0, lease_end_order = 0;
    std::uint32_t storage_alignment = 16;
    std::vector<Owner> owners;
    OwnedView c0;
    std::array<std::uint8_t, kC0Bytes> c0_bytes{};
    // Source walk order: five singles, then the three literal table elements.
    std::array<Root, 8> roots{};
    // Concrete additional possible writes in the lease, not a completeness flag.
    // Binding 6's entire range is independently treated as writable below.
    std::vector<WriteSpan> additional_writes;
};

struct Row {
    std::uint64_t low = 0, high = 0, base = 0;
    std::uint32_t binding = 0, element = 0;
};

struct Output {
    std::array<std::vector<Row>, 2> rows;  // width 4, then width 16
    std::vector<std::uint8_t> bytes;     // explicit LE, no host struct packing
};

namespace detail {
inline void require(bool condition, const char* message) {
    if (!condition) throw std::invalid_argument(message);
}
inline std::uint64_t sum(std::uint64_t a, std::uint64_t b) {
    require(b <= std::numeric_limits<std::uint64_t>::max() - a,
            "Non-modular owner/view range overflows");
    return a + b;
}
inline bool overlap(std::uint64_t a, std::uint64_t n,
                    std::uint64_t b, std::uint64_t m) {
    return n && m && a < sum(b, m) && b < sum(a, n);
}
inline const Owner& owner(const Input& input, Key key) {
    const auto it = std::find_if(input.owners.begin(), input.owners.end(),
                                [key](const Owner& o) { return o.allocation == key; });
    require(it != input.owners.end(), "Missing exact owner generation");
    return *it;
}
inline bool is_null(const OwnedView& v) { return v.allocation.id == 0; }
inline void validate_view(const Input& input, const OwnedView& v, bool nullable) {
    require(v.view_generation && v.recording == input.recording &&
            v.descriptor_generation == input.descriptor_generation,
            "Missing or inconsistent view/descriptor/recording generation");
    if (is_null(v)) {
        require(nullable && !v.allocation.generation && !v.offset && !v.range,
                "Unsupported null view encoding");
    } else {
        const auto& o = owner(input, v.allocation);
        require(v.range && v.offset <= o.size && v.range <= o.size - v.offset,
                "Descriptor view exceeds true owner extent");
    }
}
inline void validate_owners(const Input& input) {
    require(!input.owners.empty(), "Owned allocation inventory is missing");
    require(input.owners.size() <= kMaximumOwners, "Owner inventory exceeds reviewed capacity");
    for (std::size_t i = 0; i < input.owners.size(); ++i) {
        const auto& o = input.owners[i];
        require(o.allocation.id && o.allocation.generation && o.backing.id &&
                o.backing.generation && o.size, "Missing owned backing identity or extent");
        sum(o.backing_offset, o.size);
        require(o.first_recording && o.first_recording <= input.recording &&
                input.recording <= o.retained_through_recording,
                "Owner does not cover the recording lifetime");
        require(o.reuse_after_completion <= input.completed_value,
                "Owner reused before recorded completion prerequisite");
        for (std::size_t j = 0; j < i; ++j) {
            const auto& previous = input.owners[j];
            require(previous.allocation.id != o.allocation.id,
                    "Duplicate or competing allocation generations");
            require(previous.backing.id != o.backing.id ||
                    previous.backing.generation == o.backing.generation,
                    "Competing backing generations in one snapshot");
        }
    }
}
inline std::uint32_t read32(const std::uint8_t* p) {
    return std::uint32_t(p[0]) | (std::uint32_t(p[1]) << 8) |
           (std::uint32_t(p[2]) << 16) | (std::uint32_t(p[3]) << 24);
}
inline std::uint64_t read64(const std::uint8_t* p) {
    return std::uint64_t(read32(p)) | (std::uint64_t(read32(p + 4)) << 32);
}
constexpr std::array<std::uint32_t, 8> kSelectors{{192,208,256,272,352,2176,2192,2208}};

inline void validate_write(const Input& input, const WriteSpan& write) {
    require(write.kind == WriteKind::Storage || write.kind == WriteKind::Physical ||
            write.kind == WriteKind::Transfer || write.kind == WriteKind::Host,
            "Unknown writer class");
    require(write.size && write.first_order <= write.last_order &&
            write.last_order <= input.lease_end_order, "Invalid writer range/order inventory");
    const auto& writer = owner(input, write.allocation);
    require(write.offset <= writer.size && write.size <= writer.size - write.offset,
            "Possible writer exceeds true owner extent");
    if (write.last_order < input.selectors_ready_order) return;
    const auto& c0_owner = owner(input, input.c0.allocation);
    if (!(writer.backing == c0_owner.backing)) return;
    const auto writer_at = sum(writer.backing_offset, write.offset);
    const auto c0_at = sum(c0_owner.backing_offset, input.c0.offset);
    for (const auto selector : kSelectors)
        require(!overlap(writer_at, write.size, sum(c0_at, selector), 16),
                "Possible write aliases a leased selector record");
}

struct Claim {
    std::uint64_t base, end;
    std::uint32_t binding, element;
};
inline std::array<Claim, 8> claims(const Input& input, std::uint32_t width) {
    std::array<Claim, 8> result{};
    for (std::size_t i = 0; i < result.size(); ++i) {
        const auto* p = input.c0_bytes.data() + kSelectors[i];
        const auto guest = read64(p);
        const auto size_word = read32(p + 8);
        const auto base = guest & ~std::uint64_t(15);
        const auto bound = is_null(input.roots[i].view) ? 0 : input.roots[i].view.range;
        // OpArrayLength is U32; its shift happens BEFORE U64 conversion.
        const auto count32 = static_cast<std::uint32_t>(bound / width);
        const auto bytes32 = static_cast<std::uint32_t>(count32 << (width == 4 ? 2 : 4));
        // These two endpoint additions intentionally wrap in U64.
        const std::uint64_t typed_end = base + std::uint64_t(bytes32);
        const std::uint64_t nvn_end = guest + std::uint64_t(size_word);
        const auto end = i >= 5 && !size_word ? typed_end : std::min(typed_end, nvn_end);
        result[i] = {base, end, input.roots[i].binding, input.roots[i].element};
    }
    return result;
}
inline std::vector<Row> partition(const std::array<Claim, 8>& candidates) {
    std::vector<std::uint64_t> endpoints;
    for (const auto& c : candidates)
        if (c.base < c.end) {
            endpoints.push_back(c.base);
            endpoints.push_back(c.end);
        }
    std::sort(endpoints.begin(), endpoints.end());
    endpoints.erase(std::unique(endpoints.begin(), endpoints.end()), endpoints.end());
    std::vector<Row> rows;
    for (std::size_t i = 1; i < endpoints.size(); ++i) {
        const auto low = endpoints[i - 1], high = endpoints[i];
        for (const auto& c : candidates) {
            if (c.base > low || low >= c.end) continue;
            if (!rows.empty() && rows.back().high == low && rows.back().base == c.base &&
                rows.back().binding == c.binding && rows.back().element == c.element)
                rows.back().high = high;
            else
                rows.push_back({low, high, c.base, c.binding, c.element});
            break; // Exact original first-match order; no nearest phase.
        }
    }
    require(rows.size() <= kMaximumRowsPerWidth, "Interval count exceeds reviewed capacity");
    return rows;
}
inline void put32(std::vector<std::uint8_t>& bytes, std::uint32_t value) {
    for (unsigned i = 0; i < 4; ++i) bytes.push_back(static_cast<std::uint8_t>(value >> (i * 8)));
}
inline void put64(std::vector<std::uint8_t>& bytes, std::uint64_t value) {
    for (unsigned i = 0; i < 8; ++i) bytes.push_back(static_cast<std::uint8_t>(value >> (i * 8)));
}
} // namespace detail

inline Output build(const Input& input) {
    using namespace detail;
    require(input.mode == SourceMode::OwnedFrozenGraph,
            "Live routing-input producer is not implemented or admitted");
    require(input.module_sha256 == kModuleSha256 && input.storage_alignment == 16,
            "Unsupported module or storage alignment");
    require(input.recording && input.descriptor_generation && input.selectors_ready_order &&
            input.selectors_ready_order <= input.dispatch_order &&
            input.dispatch_order <= input.lease_end_order,
            "Missing or invalid recording/generation/lease identity");
    validate_owners(input);
    require(input.additional_writes.size() <= kMaximumAdditionalWrites,
            "Writer inventory exceeds reviewed capacity");
    validate_view(input, input.c0, false);
    require(input.c0.range == kC0Bytes, "Target C0 descriptor-visible range must be exactly 2224 bytes");
    for (std::size_t i = 0; i < input.roots.size(); ++i) {
        const auto& root = input.roots[i];
        require(root.binding == (i < 5 ? i + 2 : 7) && root.element == (i < 5 ? 0 : i - 5),
                "Unsupported, duplicate or reordered typed root");
        validate_view(input, root.view, true);
    }
    // Module-specific write inventory: the original direct StorageBuffer root 6.
    const auto& writable = input.roots[4].view;
    if (!is_null(writable))
        validate_write(input, {WriteKind::Storage, writable.allocation, writable.offset,
                              writable.range, input.dispatch_order, input.lease_end_order});
    for (const auto& write : input.additional_writes) validate_write(input, write);

    Output output;
    output.rows[0] = partition(claims(input, 4));
    output.rows[1] = partition(claims(input, 16));
    const auto scalar_count = static_cast<std::uint32_t>(output.rows[0].size());
    put32(output.bytes, scalar_count);
    put32(output.bytes, static_cast<std::uint32_t>(output.rows[1].size()));
    put32(output.bytes, 0);
    put32(output.bytes, scalar_count);
    for (std::size_t w = 0; w < 2; ++w) {
        for (const auto& row : output.rows[w]) {
            put64(output.bytes, row.low); put64(output.bytes, row.high); put64(output.bytes, row.base);
            put32(output.bytes, row.binding); put32(output.bytes, row.element);
            put32(output.bytes, w == 0 ? 4 : 16);
            put32(output.bytes, row.binding < 7 ? row.binding - 2 : 5 + row.element);
            put32(output.bytes, 0); put32(output.bytes, 0);
        }
    }
    return output;
}

} // namespace compute_intervals
} // namespace open_ocarina
#endif
