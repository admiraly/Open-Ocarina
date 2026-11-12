# GPU buffer allocation evidence

This diagnostic patch adds real GPU buffer payload copies to the exact-dispatch
page-table snapshot. It is an intermediate capture stage, **not complete replay**.
The implementation has optimized build, synthetic validation and a bounded
isolated runtime observation. [Sanitized evidence](../research/gpu-allocation-observation.json)
records the exact tested renderer and patches; raw payloads remain private.
No FPS or rendering improvement is claimed.

Apply `compute-identity.patch`, `page-table-snapshot.patch`, then
`allocation-capture.patch` to the pinned Hookshot revision in
[page-table-snapshot.md](page-table-snapshot.md). The third patch changes only
Vulkan renderer files and adds a standalone allocation helper. Host input changes
are a separate patch lane.

## Opt-in and limits

Use an existing empty directory under ignored private storage for all artifacts.
Keep the page-table and identity observer configuration described in
[page-table-snapshot.md](page-table-snapshot.md), and additionally set:

```sh
export RX_VK_OO_ALLOC_CAPTURE=1
export RX_VK_OO_ALLOC_BUDGET_MIB=2048
```

The default aggregate staging budget is **2 GiB**, covering both BEFORE and AFTER
copies of every candidate buffer. An explicit decimal override accepts 32 through
8192 MiB; an invalid override disables allocation capture. The page table adds
24 MiB outside that payload budget. Catalogs are bounded to 16,384 distinct
buffers. The diagnostic logs candidate count, source bytes, required staging bytes
and configured budget **before allocating payload staging**. The completed
`allocations.json` repeats these totals.

If all candidates do not fit, no payload staging/copies are made. The completed
catalog and GPU table still describe the failure, with missing payload counts;
the tool never silently chooses a subset and calls its coverage complete. A new
run with a fresh directory and an explicit larger budget can follow measured
catalog totals. Staging allocation failure likewise abandons all payload copies.
The budget controls capture staging, not total process memory or source buffers.
Captures can stall the renderer and consume several GiB of disk space; these runs
are unsuitable for FPS comparisons.

## What is copied and when

At the existing exact program, variant and emitted-SPIR-V digest filter, after
descriptor/push binding and before the actual compute dispatch, the renderer
catalogs the current live, retired and evicted pool imports, mirrors, writeback
shadows, and deferred destruction queues. Each row includes the actual `VkBuffer`
handle, queried buffer device address, exact buffer creation size, guest interval,
usage flags, primary `VkDeviceMemory`, VMA binding offset where available, memory
piece handles, and ownership role. Buffer size comes from the pool interval,
**never VMA allocation padding**. Shadow creation adds `TRANSFER_SRC` only under
this opt-in. Normal pool buffers already have the necessary source usage.

All supported catalog candidates are copied in full to private host-visible
staging immediately before the dispatch. A second full copy follows the actual
`vkCmdDispatch` or `vkCmdDispatchIndirect`, before later dispatches can change its
outputs. This deliberately captures a superset of possible writable buffers;
it does not infer physical write ranges from the shader. Global memory barriers
order prior GPU writes before transfer reads, and order those reads before later
GPU accesses; destination barriers make transfer writes available to the host.
Each published staging allocation is invalidated after completion.

Captured source handles stay retained through the exact command recording's
successful submission and actual recording-timeline completion. Deferred mirror,
import, shadow and eviction destruction queues respect those retained handles.
Submission failure or canceled unsubmitted recording reset releases staging
without publishing payload artifacts. Export uses the existing page-table
recording part and sequence, not a loose logical submission watermark. No export
runs while the selected recording is pending.

`buffer-N-before.bin` and `buffer-N-after.bin` contain bytes copied by the GPU.
They are not reconstructed from CPU guest memory. Every artifact is size checked
and SHA-256 hashed; its JSON is committed last. A binary without its committed
manifest is incomplete evidence. All captures, addresses, raw logs and generated
payloads remain private and must never be published in the source repository.

## Coverage checks and remaining gaps

After GPU completion, both the producer and independent checker examine **every
nonzero entry of the copied GPU table**. They resolve actual BDA intervals against
catalog buffer bounds and guest offsets, reporting unresolved addresses,
ambiguous ownership, partial final pages, and resolved pages missing BEFORE or
AFTER payloads. They do not consult the renderer's current CPU `guest_pages[]`.
Unaligned GPU bases and short last pages are handled without rounding or reading
past the buffer. Up to 32 unresolved addresses are returned privately by the
checker for diagnosis.

The catalog is assembled at recording time. Deferred table updates and later
allocations can produce GPU table entries absent from that catalog; these remain
unresolved. Imports being created or waiting on the background importer are not
cataloged as dispatch resources and are flagged when present. Retained buffers
that were already queued for destruction are included with their original true
sizes and guest bounds. There is no claim that allocation events later in the
recording were exhaustively captured.

Resolved Vulkan buffer descriptors are recorded by binding, array element,
descriptor type, buffer handle, offset and range. `VK_WHOLE_SIZE` resolves only
against a known owner. Stream UBOs, descriptor snapshots or other renderer-owned
buffers outside pool ownership are reported as unresolved; their size, flags and
lifetime are not guessed. Images, sampler state, complete push constants and
other temporal state still need later capture. Indirect dispatch counts retain
the page-table stage's explicitly unknown status.

`VkBuffer` identity and VMA allocation identity do not establish underlying
physical identity for host imports: separate imports may alias the same host
pages even with different `VkDeviceMemory` handles. Guest intervals and memory
piece handles preserve evidence but do not prove or normalize that aliasing.
Sparse piece binding offsets are explicitly unresolved. CPU writers to host
imports are not quiesced; a sequence of GPU copies is not an atomic snapshot of
all GPU/CPU state. These limitations remain false in coverage even if every
copied table page resolves to complete payloads. The checker reports
`table_payload_ranges_complete` separately from `replay_complete`, which always
remains false at this stage.

## Validation

The frozen renderer exported full BEFORE/AFTER pairs for 89 cataloged buffers.
The independent checker verified all payload sizes and hashes and recomputed
coverage against every actual GPU-table entry. Resolved pages had complete
payload pairs, but 24,576 pages remained unresolved: a later 96 MiB import was
created after the catalog while its deferred table update preceded the dispatch
on the GPU. Increasing the staging budget does not fix that ownership gap.

The descriptor catalog also contains one stream UBO outside pool ownership and
two null storage-array placeholders. The current checker conservatively counts
all three as unknown owners; future capture must distinguish enabled null-buffer
semantics from a missing live allocation. Images and other stated gaps remain.

Two allocations changed between copies. All 2,001 changed bytes in the binding-6
owner fell within that descriptor's range. Another imported allocation changed
124 bytes outside that binding; CPU writers were not quiesced, so those changes
are not attributed to the selected shader. These observations do not establish
deterministic shader output or complete replay. The test process stopped cleanly.

```sh
python3 patches/hookshot/check_allocation_capture.py --checkout local/hookshot-source
python3 -m unittest discover -s tests -p test_allocation_capture.py -v
python3 patches/hookshot/check_allocation_capture.py --validate-snapshot local/private-capture
```

The checker verifies the three-patch chain with an alternate Git index, compiles
an optimized independent C++20 helper, writes and validates a synthetic capture,
refuses premature completion and short writes, and tests range/limit logic without
proprietary inputs or GPU execution. Regression tests cover late GPU mappings,
partial tails, unaligned bases, overlapping owners, descriptor range resolution,
budget failure, payload tampering, symlinks/path escapes, exact timeline and
pipeline matching, and overstated coverage. Artifact integrity and producer
declarations are not independent GPU attestation.
