# Closing backing identity and CPU-writer uncertainty

Source and capture review, followed by a bounded host-observer implementation, 2026-11-13. No renderer edits, shared-checkout builds or GPU work were performed in this lane. The inspected source is Hookshot revision `4cf785a0b143bb34026506c9a3fe4f4bb93c797d` plus the project's capture patch chain. The selected occurrence-64 capture has independently checked GPU-table and buffer-descriptor **range/payload closure**, recorded in [the sanitized observation](gpu-buffer-closure-observation.json). That is not underlying backing identity, an atomic CPU/GPU snapshot, or complete replay. The later sampled-image, dispatch-parameter and binding-provenance captures are separate datasets; their results do not retroactively close this older capture.

## Findings grounded in the current producer

The allocation catalog retains buffer bounds, BDA, primary `VkDeviceMemory`, VMA offset where available, and sparse memory handles. It does not retain the sparse binding intervals or the provenance of each external import. Its unresolved-alias and non-quiesced-writer flags are therefore justified.

The occurrence-64 catalog's imported-buffer guest intervals do not overlap each other. Five catalog rows use more than one memory piece. Several imported payloads change between their GPU copies, as does one mirror. These are observations about this capture, not evidence that guest intervals are physical identities or that the selected shader caused all changes. The lack of overlap does not fill the missing import/piece provenance.

The source already knows the information needed at creation time:

- `gpu/vk/vk_memory.cpp::dma_buf_memory` creates an export from the original guest memfd using an explicit backing-file offset and size, then imports that export into a new Vulkan memory object.
- `import_dma_buf_pieces` builds the actual `VkSparseMemoryBind` list with each buffer resource offset, size, imported memory and memory offset. It waits for successful sparse binding before publishing the pool. The published `Pool` keeps memory handles but drops those offsets and sizes.
- `import_dma_buf` binds an ordinary imported buffer at a known memory offset. `import_pool`'s fallback imports `host->guest_base()` plus the guest offset through `VkImportMemoryHostPointerInfoEXT`, then binds the buffer.
- `gpu/vk/vk_vma.cpp::create_buffer` and `create_buffer_cached` have the exact requested buffer size and `VmaAllocationInfo`. `vmaGetAllocationInfo` supplies the backing Vulkan memory and suballocation offset. Those are distinct from the requested buffer extent and from allocation padding.
- Raw imports can be created on the background importer before they enter `imported`; successful imports can also be redundant, retired, evicted or queued for destruction. A registry restricted to published live pools cannot describe their entire lifetimes.

## Record binding provenance, not an inferred alias table

Add a capture-only backing/binding ledger at the successful resource-creation and bind boundaries. Keep it independently retained until the selected recording completes. A handle must receive a monotonic lifetime serial: Vulkan handles and file-descriptor integers can be reused.

The minimum event model is:

| Event | Evidence to retain |
|---|---|
| Host backing creation | Opaque backing-object lifetime ID, mapping kind and extent; original file-object identity and mapping-file offset when file-backed. |
| Import allocation attempt/commit | Vulkan memory lifetime ID, external import kind, original backing ID/generation, backing byte offset, imported extent, success/rollback and pinning lifetime. |
| Ordinary buffer bind commit | Buffer lifetime ID and true creation size, Vulkan memory lifetime ID and binding offset, successful bind result. |
| Sparse buffer bind commit | Every original `VkSparseMemoryBind` resource offset, size, memory lifetime ID, memory offset and flags; successful queue/fence sequence. Preserve all pieces, not just the first memory. |
| VMA buffer creation/bind | True buffer size, VMA allocation lifetime, backing Vulkan memory lifetime and suballocation offset. |
| Backing discard/remap/destruction | Affected backing intervals and generation transition, result, and overlap with any active import/pin. |
| GPU-address mapping change | Original logical GPU-to-guest or virtual-pool mapping event and generation. This is a lookup relation, not proof of physical aliasing. |

Record committed bind edges only after success; failed partial imports must roll back their ledger entries as well as Vulkan resources. Sparse binds need their completion/order evidence. Keep original memory offsets even where this revision uses zero: otherwise a later implementation change silently changes the interpretation of saved captures.

For file-backed guest memory, the canonical object is the **original guest memfd backing**, not the dma-buf export inode, its temporary descriptor, the Vulkan memory handle or a guest address. Separate exports of overlapping offsets of one stable original backing can alias despite different export and Vulkan handles. A process-local opaque ID tied to original-file creation/identity and a backing generation can express that relation without publishing raw addresses or descriptors.

Host-pointer imports need a host mapping/backing provenance API. A virtual pointer is not independently sufficient if mappings can be replaced or if aliases are introduced elsewhere. The current single reservation is a useful source invariant, but the producer must record its lifetime and any changes rather than infer it from a constant base. Unknown external backings remain unresolved.

## Canonical interval normalization

For a requested byte interval of one buffer, intersect its true buffer extent with its committed bind edges. Each covered subrange maps through the edge's memory offset into the imported original backing offset, or into a VMA Vulkan-memory suballocation offset. Split at sparse-piece boundaries. Reject gaps, inconsistent overlapping binds, unknown generations and arithmetic overflow. Driver allocation padding is not extra accessible buffer data.

Two views alias only where their **canonical backing-object lifetime/generation intervals** overlap. Two VMA buffers sharing a Vulkan memory object but using disjoint offsets are not aliases. A VRAM mirror or writeback shadow remains a different backing from a host import even if both describe the same guest interval or currently contain equal bytes. Mirror synchronization is a copy/coherence event, not an alias relation.

Require the overlapping portions of separately captured aliases to agree at each claimed snapshot epoch. Disagreement cannot be fixed by choosing one copy or declaring the buffers independent; it may expose CPU interleaving or an incorrect provenance edge. Normalize snapshots onto one canonical backing graph before replay and compare every written canonical region. Preserve the independent mirror/shadow copies and their own initial contents.

## The backing-generation trap

`host/hos_main.c::reserve_guest` creates one shared, file-backed guest window where supported. Its seals prevent size changes, not content changes. `hos_posix_guest_discard` can discard shared backing pages. In `host/hos_mem.c`, freed ranges overlapping imports are generally zero-filled instead of discarded so the GPU is not left pinned to old pages.

That protection is a source invariant to audit, not a complete provenance certificate. `gpu/vk/vk_memory.cpp::memory_guest_imported` explicitly checks published live and retired pools. The source review does not establish equivalent coverage for every prepublication/background, evicted or deferred raw import. If a discard/replacement crosses an existing pin, two imports with the same original file offset may refer to different page generations. Recording only the original memfd and offset would then incorrectly merge them.

The next ledger must span raw allocation attempts, publication, rollback, retirement and final destruction. Coordinate backing-discard/remap operations against that registry, or reject captures crossing an unresolved overlapping change. Creation and discard need a shared ordering boundary; recording them independently after the fact leaves a race. This note identifies the missing proof, not a reproduced discard bug in the current capture.

## Existing generations do not freeze CPU writers

`host/nvdrv.c`'s `maps_generation` changes logical GPU mappings and pool tracking. It is not a CPU content-write generation. `nvn_vmap_generation` similarly describes virtual-pool mapping changes.

The generated 64-bit runtime in `nx2/rtgen.py` marks dirty pages after guest stores. `host/hos_mem.c::hos_mark_written` also marks host writes, and `host/rx_backend.c::collect_in` consumes and clears marks. `gpu/vk/vk_memory.cpp::memory_collect_written` assigns later renderer generations to collected pages. Those mechanisms support submission capture and cache invalidation; they do not provide exclusive ownership or an atomic snapshot epoch. A dirty bit collapses repeated writes, another consumer may clear it, and a post-store marker does not prevent the store from overlapping a GPU copy.

`memory_read_as_submitted` can substitute kept submission-capture pages but falls back to guest memory when a page is not retained. Its view/page stamps cannot turn a later CPU read into an exact selected-dispatch GPU snapshot.

The existing guest-write watch is also diagnostic: its fault handler makes the written page writable, and a timer rearms watches later. It does not block all writes for a capture epoch. Virtual-core checkpoints schedule guest threads; they are not a stop-the-writers barrier. Linux `SuspendThread` is explicitly unsupported in the compatibility layer. Arbitrarily suspending threads could also park one while it holds a lock the renderer needs.

## Implementable next producer boundary

Implement backing/bind provenance first. It is narrowly located at host backing creation/discard and Vulkan import/bind success, and it closes a specific information loss without changing shader execution.

For CPU atomicity, introduce a separate opt-in **capture epoch** that begins before any selected GPU-visible BEFORE copies and remains active through the selected dispatch and AFTER copies. It needs acknowledgments from every guest execution thread and native writer capable of modifying a reachable host backing, with new writer admission gated and pending writers drained. Include file/IO completion writers, host zero-fill/free paths, control writes and renderer-owned mapped buffers where applicable. Do not count blocked threads as safe unless their completion paths cannot write before the epoch ends. Do not hold renderer/pool/queue locks while waiting for acknowledgments.

The existing lifted-loop poll and `hos_vcore_checkpoint` are possible guest-thread rendezvous points, not a ready-made implementation. HLE entry/exit and native asynchronous writer boundaries also need accounting. Preserve an explicit registry of acknowledged threads/writers, active operations and missing acknowledgments. Timeout aborts the capture and releases the epoch; it never silently labels partial quiescence complete. The renderer and GPU must retain enough progress capability to complete the selected command and its fence.

An alternative is a separately audited write gate on every CPU mapping of all reachable host backings, held throughout that epoch. It must handle alternate mappings, signal/fault handlers and kernel IO writes; a first-fault log or periodic reprotection is insufficient. Either approach is a diagnostic path, not suitable for FPS measurement. A monotonic write-event detector can instead **reject** observed races, but it needs begin/end instrumentation covering all writers; existing post-write dirty marks and unchanged bytes are not that detector.

## What isolated baseline replay can establish now

Once resource/image identity is sufficient, copy the captured BEFORE state into owned replay allocations, preserving the proven alias graph and relocating all pointers through a verified scheme. Repeated baseline replay agreement establishes deterministic behavior for **that frozen input graph**. Agreement with captured AFTER outputs strengthens evidence that those inputs reproduce the observed selected-dispatch result. A candidate that agrees with that replay passes a bounded shader-output check, not whole-game correctness.

It does **not** prove that live CPU writers were absent, that an overlapping copy was atomic, or that no temporary write-and-restore occurred during the original dispatch. Equal BEFORE/AFTER bytes do not prove a read-only interval; nor does a repeated successful replay exclude an original interleaving that happened to yield the same observed outputs. Nonmatching replay is diagnostic of missing state, incorrect alias/address reconstruction, nondeterminism or temporal interference; it is not permission to widen tolerances.

Keep separate fields for range/payload closure, backing provenance, writer-epoch coverage, frozen-state baseline reproducibility and live-observation agreement. The current capture should not have `cpu_writers_quiesced` or `complete_physical_replay` relabeled true merely because range closure or replay agreement succeeds. Capture-induced freezing can also change the live game's scheduling; later full-workload correctness tests remain necessary.

## Strongest objections and pass tests

- A provenance ledger that starts after imports exist cannot recover discarded bind offsets or pin generations. Start it before the first relevant allocation, or mark earlier objects unresolved.
- Recording metadata while background imports/discards continue can produce a self-consistent but wrong epoch. Tie committed events and retained lifetimes to the exact selected recording and audit the host/backing ordering.
- Guest-thread acknowledgment alone misses native/kernel writers. Demonstrate complete writer coverage, abort on unknown participants, and exercise write-and-restore tests.
- The reference shader may itself contain scheduling-sensitive accesses. Baseline repetitions must precede claims of a stable oracle; matching a frozen replay cannot establish that the unmodified live path was race-free.
- Coalescing mirrors with imports by guest interval destroys intentionally independent versions. Synthetic tests must preserve their separate contents while resolving true overlapping external imports and sparse slices.

Useful producer tests include failed import rollback, Vulkan handle reuse, overlapping exports of one backing, nonzero ordinary bind offsets, multi-piece sparse binds including short tails, discard during import publication, alias snapshot disagreement, CPU write-and-restore during the epoch, a native writer completing after a guest-thread barrier, timeout recovery and late allocations. The host observation helper below implements only the first provenance boundary. No renderer import/bind integration, writer quiescence or complete replay is claimed by this lane.

## Implemented first boundary: optional host observer

`patches/hookshot/backing-lease.patch` adds `host/capture_backing.h`, included by the host startup translation unit. It records successful original guest-window creation and wraps the existing guest discard syscall with observation-only bookkeeping. `HOS_OO_BACKING_LEASE=1` opts in; the default and explicitly disabled paths keep the original discard operation. Non-Linux and zero-based-memory builds expose disabled stubs.

The original memfd is duplicated and retained by the registry so file-descriptor/inode reuse cannot merge distinct live objects. Mapping and lease IDs are monotonic lifetime tokens. Registered aliases of the same original file backing share a backing token and preserve their individual mapping/file offsets. Observed full replacements retire the old mapping identity. Partial or unsupported changes, capacity/history loss and uncertain failed operations make diagnostic provenance unknown instead of guessing a stable state.

The optional C ABI uses `HOS_CAPTURE_BACKING_ABI=1` and exact output-structure sizes:

- `hos_capture_backing_acquire(abi, guest, size, out, out_size)` registers an import-intent lease and returns backing/mapping IDs, original backing offset, extent, acquired/current backing generation and event tokens.
- `hos_capture_backing_validate(abi, lease_id, out, out_size)` returns success only for a retained, noninvalidated diagnostic lease. Successful overlapping discards invalidate known aliases of that backing interval. A disjoint discard can advance the object's global generation while a lease remains valid; consumers must use validation status, not generation equality alone.
- `hos_capture_backing_release(abi, lease_id)` retires a diagnostic lease after import rollback or actual Vulkan memory destruction. A released slot receives a new token when reused; stale IDs never resolve to a later lease.
- `hos_capture_backing_events(abi, after, out, capacity, event_size, next_event)` supplies bounded committed mapping/discard event metadata. Missing event history fails provenance closed.

Positive status one means success, zero means disabled, and negative values distinguish invalid arguments, unknown provenance, capacity rejection and stale leases. All snapshots and any raw backing details remain private.

The observer serializes its metadata with the actual discard operation under a host-only mutex. It never calls Vulkan or acquires renderer pool locks. It **does not defer, reject, zero, remap or abort a game operation**: the original syscall executes and its result/errno are returned unchanged. A successful discard records a generation transition and invalidates overlapping diagnostic leases. A failed operation can have partial effects, so diagnostics become unknown rather than assuming rollback. This preserves game behavior while refusing a false capture certificate.

The helper is not a general syscall interposer. Production hooks cover the inspected guest-window creation and guest-discard entry point; future alias/remap creation paths must commit their real mapping events to the registry. An unobserved external mapping operation is outside this proof. Renderer import acquisition, committed sparse-bind edges, retained token validation at GPU completion and release at every raw free/rollback are a separate composing patch.

### Tests and frozen handoff

The checker extracts and compiles the **exact added production helper** from the patch with `-O2 -DNDEBUG`, C11 and strict warnings. It does not compile an independent behavioral model. Thirteen fresh-process scenarios use real Linux memfd mappings, aliases and discard operations: enabled/default/disabled behavior, ABI/range validation, acquire/release rollback, original-backing alias identity, overlapping/disjoint discard, failure/errno preservation, mapping replacement, uncertain partial change, lease-slot reuse, event/serial loss, cross-thread invalidation and file-descriptor reuse. Four additional tests cover checker framing and the required export recipe; one verifies actual dynamic symbols in the optimized executable. All eighteen dedicated tests passed.

The Linux standalone linker normally exports only the existing `hos_posix_*` prefix. The public backing patch therefore also adds `--export-dynamic-symbol=hos_capture_backing_*` to `nx2/build.py`'s actual Linux standalone link recipe. The checker confirms that branch through the applied Python AST, links the helper with the extracted public export flag and section garbage collection, then checks all four dynamic API names with Binutils `nm`. The symbols alone do not activate observation; the opt-in environment setting is still required. Linux, a C11 compiler and `nm` are needed for these production-helper checks.

```sh
python3 patches/hookshot/check_backing_lease.py --checkout local/hookshot-source
python3 -m unittest discover -s tests -p 'test_backing_lease.py' -v
```

The optional checkout check applies the patch to a temporary index at the pinned revision and verifies the extracted helper. It leaves the shared source and real index unchanged. The frozen host patch SHA-256 is `26c2881cd64267f6ca7e4705e48e9067eafd87bf9532701e8fde9492fba91bad`. The earlier host-only patch lacked public linker wiring and is privately archived; its fingerprint must not label the revised recipe. The combined input-isolation/backing host subsequently built with the public export recipe. Its four dynamic ABI exports and default-disabled behavior passed an actual-header test. A fresh composed renderer/host run is recorded in [the binding-provenance observation](gpu-binding-provenance-observation.json); that evidence is separate from this host-only handoff. A later patch edit requires a new fingerprint and rerun.

## Source references

Function references above were inspected in the pinned source and current capture patches. Upstream source: [memory imports and sparse binds](https://github.com/NotRexed/Hookshot/blob/4cf785a0b143bb34026506c9a3fe4f4bb93c797d/gpu/vk/vk_memory.cpp), [host window creation](https://github.com/NotRexed/Hookshot/blob/4cf785a0b143bb34026506c9a3fe4f4bb93c797d/host/hos_main.c), [host memory/free/write marks](https://github.com/NotRexed/Hookshot/blob/4cf785a0b143bb34026506c9a3fe4f4bb93c797d/host/hos_mem.c), [host write-watch collection](https://github.com/NotRexed/Hookshot/blob/4cf785a0b143bb34026506c9a3fe4f4bb93c797d/host/rx_backend.c), [GPU mapping generations](https://github.com/NotRexed/Hookshot/blob/4cf785a0b143bb34026506c9a3fe4f4bb93c797d/host/nvdrv.c), [native pool mappings and write watches](https://github.com/NotRexed/Hookshot/blob/4cf785a0b143bb34026506c9a3fe4f4bb93c797d/host/nvn_native.c), [generated 64-bit dirty marks/polls](https://github.com/NotRexed/Hookshot/blob/4cf785a0b143bb34026506c9a3fe4f4bb93c797d/nx2/rtgen.py), [thread checkpoints](https://github.com/NotRexed/Hookshot/blob/4cf785a0b143bb34026506c9a3fe4f4bb93c797d/host/hos_thread.c). The allocation patch adds VMA owner retention and capture rows; see `patches/hookshot/allocation-capture.patch` for those project-specific changes.
