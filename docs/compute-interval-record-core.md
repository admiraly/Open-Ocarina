# CPU record core and proposed renderer boundary

The independently authored C++17 core in [`compute_interval_records.h`](../src/probes/compute_interval_records.h) builds the exact readonly binding-12 record consumed by the [frozen compute prototype](../research/compute-interval-lowering.md). It has no Vulkan, Hookshot, shader compiler or third-party dependency. Its only current mode is an explicitly selected owned frozen graph. Unspecified mode and `LiveAsSubmitted` throw before construction. No renderer extraction, writer mutation, binding change or production feature is implemented.

## Input contract

`open_ocarina::compute_intervals::build(const Input&)` consumes:

- The reviewed original module digest and storage alignment 16. The surrounding caller must verify the actual SPIR-V identity; a string supplied to this API is not a digest computation or authentication of the module.
- An exact 2224-byte copy of the C0 descriptor-visible range, plus its owned view. The view includes allocation ID/generation, offset, range, view generation, recording ID and descriptor-writer generation.
- Eight final normalized descriptor views in the original walk order: bindings 2–6 element 0, followed by binding 7 elements 0–2. Each has a concrete owned buffer range or the canonical null encoding. A real zero-buffer fallback is a nonnull owned 16-byte view, not a null view.
- Owned allocation and backing IDs/generations, backing offsets, allocation extents, first/retained recording range and a completion prerequisite for any allocation reuse. Distinct buffer handles may share a backing, so alias checks use backing identity and offset, not handle inequality.
- Selector-ready, dispatch and lease-end positions in one explicit recording order domain, plus concrete additional possible-write spans. Each span names its owner generation, range, writer class and order interval. The known target's entire writable binding-6 range is independently checked as a possible writer; a caller cannot disable that check.

The core validates exact generations, true owner bounds, recording coverage, reuse/completion consistency, fixed root identities and all supplied writer overlaps. It rejects duplicate/competing owner generations, competing backing generations, overflow in non-modular owner/view arithmetic, unsupported/null encodings, missing metadata and malformed command intervals. Work is capped at 256 owners and 1024 additional write spans. A rejected or unsupported input produces no partial output.

Each selector's full 16-byte record is guarded, including the fourth word the builder does not use. A write to any protected record after selector-ready and within the lease fails. This is deliberately conservative. Pre-upload writes are allowed. A transfer confined to C0 grid bytes 0–11 remains disjoint from these selector records; the synthetic tests cover that indirect-grid case without enabling indirect execution.

**Producer truth gap:** these are consistency checks on concrete supplied facts. Integer generations and order values do not establish actual liveness, command synchronization, ownership or completeness of a write inventory. There is no `validated=true` or `no_alias=true` input that certifies them. The caller still has to produce truthful ownership and access records from the renderer. No such live producer exists in this change, and relabeling live inputs as an owned diagnostic is unsupported.

## Selection and output semantics

The exact module has no emitted nearest-candidate phase. Five ordinary roots precede three literal table elements. The builder reads selector address/size words at C0 bytes 192, 208, 256, 272, 352, 2176, 2192 and 2208. It independently partitions widths 4 and 16:

1. Align the guest base down to 16 bytes.
2. Compute `uint32(range / width)`, then perform the byte-count shift in uint32 before converting to uint64.
3. Compute the typed and NVN ends using wrapping uint64 arithmetic. Ordinary singles use the smaller end even when their size word is zero. Table elements alone use the typed end when their size word is zero.
4. Partition every nonempty interval endpoint and choose the first matching original root. Merge adjacent rows only when binding, array element and aligned guest base are identical.

No application buffer payload is read. Selection identity is compared, not equal payload bytes. A zero-size unaligned ordinary root can still claim its alignment prefix; a short typed view can lose to a later root at width 16 while winning at width 4. These source behaviors remain intact. The shader's original `uint32(address - base) >> log2(width)` typed index is unchanged by this CPU builder.

`Output::bytes` is explicit little endian and does not depend on C++ struct padding or host endianness. It retains the existing 16-byte header and 48-byte row ABI, binding 12 and route IDs. It adds no push bytes and does not inspect or rewrite either GPU or CPU physical page tables. Physical MISS instructions, follow-store policy, typed StorageBuffer roots and the original workload are outside this core and remain unchanged in the frozen candidate.

## Concrete proposed extraction interface

The source audit in [live-selector-inputs.md](../research/live-selector-inputs.md) establishes a practical extraction site, not an implemented certificate. A reviewed renderer integration would need a narrow adapter with these responsibilities:

1. **Identify the exact pending writer.** After `build_descriptors` has finished and before that thread-local writer is reset or replaced, copy the final binding-0 and eight storage `VkDescriptorBufferInfo` entries. Give each writer reset/build a monotonic generation. Bind that generation to program/stage identity, recording slot, recording part and stream generation. A logical submission value alone can cover multiple parts and is insufficient.
2. **Resolve the selected C0 owner.** Find its VkBuffer among all retained streams of that recording, including a stream already outgrown. Resolve that stream's allocation and mapped base, then copy `[descriptor.offset, descriptor.offset + 2224)`. Do not use the current stream's mapped pointer for an older stream descriptor. Do not call `driver_bank_fill`, `driver_bank_word`, `pointer_table`, `ub_read` or a later guest read to reconstruct the upload.
3. **Normalize actual storage views.** Resolve final buffer handles, offset/range and null/zero-buffer fallback to owned allocations and backing ranges. Preserve actual shrink/wide results. Resolve aliases through the allocation registry, not guest address equality. The producer must reject unknown, destroyed, recycled or unresolved owners; a generation mismatch cannot silently fall back to a guessed range.
4. **Establish the selector lease.** Retain the selected upload owner through the dispatch's completion, with the recording slot's actual reuse prerequisite. Enumerate any possible host, transfer, physical or storage writes during the lease using real command/allocation records. Check the exact module's known writable binding and any additional possible writers. The current target has no image-write instruction, but a different module or unrecognized writer class must fail closed rather than inherit this target's write inventory. The source's stream usage includes storage usage; uniform-buffer usage is not an alias proof. VMA block allocations must retain true shared-backing offsets.
5. **Build and retain the record.** After a separately reviewed live producer and mode become available, invoke the same typed partition rules, allocate a distinct record owner, copy/flush its bytes and retain it with the recording. Verify it cannot alias an existing source allocation. The present API deliberately rejects live mode, so this step cannot yet be enabled by an environment switch or a caller boolean.
6. **Bind matching identities.** Add one readonly binding-12 descriptor and its pipeline/set-layout entry. Match module, record ABI, writer generation and exact record bytes when choosing the candidate pipeline. Reject source changes or stale cache entries before emitting any candidate bind. Keep the existing root push operation and physical MISS path. On unsupported extraction or allocation failure, preserve the original shader and full workload.

An adapter should return an owned immutable input bundle and a lease tied to its recording, not naked pointers into `dw().bufs`, a mutable state object or a transient `cur->mapped` view. Any later writer mutation must invalidate the bundle. The proposed new generation counters, owner resolution, writer inventory and binding mutation require separate source review; this note does not authorize or implement them.

## Verification and frozen artifacts

Fifteen public synthetic tests compile the actual header with GCC as C++17, warnings treated as errors. They check complete uint64 selection cells against an independently authored direct source-order identity walk and compare 160 deterministic mixed cases with the frozen Python builder. Cases cover overlap/priority, null versus real zero-buffer fallback, unaligned zero-size singles, width-specific typed floor, uint32 ArrayLength/shift wrap, uint64 endpoint wrap, malformed metadata, backing aliases, selector writes and work caps. The same 15 tests passed using a Clang UBSan build with no diagnostics.

On the existing hash-pinned owned graph, both GCC and Clang UBSan produce exactly the original **592 bytes**, SHA-256 `49da4bdc0c4542b4fef7b5dcbb631d41fb030d80803cde3bb37185722daf9770`. All 13 complete selection-domain cells agree for each width. That check normalizes captured owner/backing identities and ranges, but uses explicitly fresh **diagnostic lease positions**, not invented live completion or retention evidence. It validates the record calculation for that graph; it does not close historical or live liveness.

Frozen source identities:

- Header: `eec1960f1c3a8df33d0c9ccbb70f8a01037713c912317bb705e87b8aae7bc88b`.
- Tests: `d7e49bbb1b05aa0575f8614d715bfcd9fa45d99f17b88ed6beeb3189acdef058`.
- GCC test transport binary: `ad8b48d7325c788a794461dbc922dc901a1f03dde3553220b0217f8065684dde`.
- Clang UBSan test transport binary: `3270a535a05e8c7cfe9d35f519087f52fe817d2a2beefcd7559d2e48755c9b41`.

Compiler versions were GCC 16.2.1 and Clang 22.1.8. They are optional external dependencies for the synthetic compile tests; no GPU is needed. The full repository suite passed 663 tests with five optional skips. Private inputs, binaries and reports remain under ignored `local/cpp-compute-interval-records-01/`. The [observation metadata](../research/compute-interval-record-core-observation.json) records the exact scope. No GPU/Vulkan call, candidate pipeline compilation, renderer mutation, feature enablement or execution admission occurred.
