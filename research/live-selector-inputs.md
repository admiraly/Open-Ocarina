# Live selector input ownership: actual compute target

This is a CPU-only source audit following the [frozen interval prototype](compute-interval-lowering.md). It changes no renderer, executor, shader or admission rule. The target remains compute `cc62764061c44f6a`, variant `095931037888515a`, SPIR-V `b9af52c0ba07f64eb8b0ca2905c432d094999fdf669aa95182ff7754b510e97b`.

**Finding:** the five ordinary selectors are copied from CPU replay binding state into an owned driver-bank upload. The three extra table selectors are CPU-generated from guest storage-buffer prefix reads. They are therefore not exclusively driver constants. Nevertheless, a future record builder can avoid rereading those guest inputs entirely: consume the actual descriptor-visible C0 bytes already selected by `uniform_range`, together with the final storage descriptor infos in the same `build_descriptors` generation. Matching these routing inputs does not require an atomic snapshot of every application data buffer.

That narrower boundary is supported by source and the owned capture. It is not implemented or admitted for live execution here. In particular, a live builder must establish the actual upload owner, lifetime and absence of writes/aliases rather than accept a producer boolean.

## Exact selector origin

The seed stage at index 31129 was reread without exporting instructions. Its control-plus-code digest independently matches `d25230e04da68341232d28cee40127b6ad03fed45badc68cfcb510ea5587a8be`. The control metadata says zero push words, 13 uniform slots, nine storage slots, 26 texture slots and zero image slots. `CsLayout` therefore puts the uniform table at byte 16, storage table at byte 224, texture table at byte 368, and layout end at byte 576. This establishes the following sources; they were not inferred from the selector values:

| Original storage binding | Selector byte in C0 | CPU source |
|---|---:|---|
| 2 | 192 | `s.ubo[5][11]` |
| 3 | 208 | `s.ubo[5][12]` |
| 4 | 256 | `s.ssbo[5][2]` |
| 5 | 272 | `s.ssbo[5][3]` |
| 6 | 352 | `s.ssbo[5][8]` |
| 7, elements 0–2 | 2176, 2192, 2208 | `pointer_table(s, 5)`, three CPU-generated pool regions |

Each selector supplies address low/high uint32 and low uint32 size; the fourth word is zeroed by the bank fill. These are C0 driver-bank entries even when their values originate in a bound NVN uniform buffer descriptor. They are not reads of that uniform buffer's application payload.

`vk_bank.cpp:55–101` derives the layout from native program control metadata, cached by program serial. `driver_bank_fill` at lines 317–360 zeroes its destination and writes the compute grid, push area, binding-state tables and extra pointer table. `driver_bank_word` at lines 377–420 implements the corresponding individual-word path. `stage_cbuf_word` in `vk_shader.cpp:692–709` dispatches banks 0/2 directly to that function; only banks 3 and above use guest uniform reads through `ub_read` (or the optional live-read mode).

The ordinary replay binding values originate in recorded bind payloads. `vk_replay.cpp:1105–1171` handles single and multiple buffer binds, updates `s.ubo`/`s.ssbo`, and bumps `bind_gen` when values change. The optional deferred-late path explicitly rereads a descriptor array from guest memory; do not describe all configurations as exclusively submission-copied NVN state. Whichever path was used, consuming the already uploaded C0 avoids reconstructing it under a different configuration or time.

## The extra pointer table reads guest memory

`pointer_table`, `vk_bank.cpp:255–313`, caches three entries in thread-local storage by state address, stage and `s.bind_gen`. On a cache miss it scans each bound storage buffer whose size is nonzero and at most 256 bytes, reading up to its first 32 uint64 words through `memory_read_as_submitted`. It keeps plausible aligned pointers outside existing bindings, sorts them, and for compute selects up to three distinct pools. Pool bounds and the device's storage-range limit determine the generated region sizes.

`memory_read_as_submitted`, `vk_memory.cpp:1268–1305`, first performs a guest read and, when applicable, replaces covered page fragments with capture bytes whose capture generation still matches the page stamp. This is a capture-aware CPU read, not a GPU readback of each prior shader result and not a general atomic guest-data certificate. The pointer table may legitimately retain the earlier result while the same binding generation remains cached. Recomputing it later can select different inputs from those actually uploaded.

The `NOP_UPDATE_UB` distinction matters. Its replay handler at `vk_replay.cpp:1024–1046` records inline update bytes; `ub_read` at lines 759–793 overlays the updates visible at that stream position. The extra pointer-table scan calls `memory_read_as_submitted` directly, **not `ub_read`**. It therefore does not obtain that overlay merely because an update appears earlier in the command stream. This audit preserves the existing behavior; it does not propose changing the pointer heuristic or adding overlays.

The pending physical page table is a different structure. `table_pending` and `memory_table_flush`, `vk_memory.cpp:477–545`, stage GPU address-table updates in command order with last-write-wins handling. They do not supply the C0 selector words. A record builder preserving the existing physical MISS instructions has no reason to substitute CPU `guest_pages[]` or pending physical-table entries for the eight storage-root selectors.

## The actual upload and descriptor boundary

`uniform_range`, `vk_draw.cpp:2944–3005`, extends C0 usage to include all global-memory descriptor words and rounds/clamps the upload length. For this target the owned descriptor-visible range is exactly 2224 bytes. Its normal path fills a CPU temporary, allocates an aligned range in the recording's mapped stream and copies those bytes there. The cache can instead return an earlier equal or same-generation range **within that recording**. Hence “fresh frame upload every dispatch” is inaccurate; “owned immutable selected range for the recording” is the useful contract.

`build_descriptors`, `vk_draw.cpp:3274–3549`, writes uniform descriptors before storage descriptors, retaining the actual `VkDescriptorBufferInfo` values in its thread-local descriptor writer. It computes storage bindings through `stage_cbuf_word`, applies alignment, bare-pointer/range policies, optional stretching/widening, table plausibility and shrink fallback, then records the final buffer, offset and range. Null descriptors and zero-buffer fallback differ. A future builder must consume the final descriptor infos, including null/fallback identity; a selector's size word is insufficient to reconstruct `OpArrayLength` or the selected VkBuffer owner.

The frozen graph makes that boundary concrete: binding 0 names view `buffer-121`, offset 16768, range 2224, in a 2 MiB owner with backing generation 1. It is the only descriptor view of backing `owned-buffer-193`; its device address is zero in the capture and no captured physical-table mapping names that allocation. Writable binding 6 belongs to a different owner. These are observations from this graph, not producer assertions or an all-future-stream nonalias proof. The complete visible-range SHA-256 remains `778a893b3523a792183d46f8d8e0e0d517e3957b808cdea2be97294b3b008bcf`.

## Lifetime and command ordering

`RecordingRes`, `vk_draw.cpp:394–410`, owns its streams and mapped allocations. `stream_alloc` at lines 502–506 advances an append-only offset. Growing a stream retains the old VkBuffer because already built descriptors still reference it. `draw_begin_recording`, lines 4895–4949, resets offsets and driver-bank caches when the slot begins a new recording.

The serial recording path waits for that slot's prior fence or batch completion before resetting and beginning it (`vk_replay.cpp:159–178,364–388`). Submission flushes every used upload stream (`draw_flush_recording`, `vk_draw.cpp:4885–4892`) before command-buffer submission (`vk_replay.cpp:391–469`). The parallel path also waits for worker completion, then flushes the recording's stream before its ordered submission (`vk_replay.cpp:1653–1692`). `vk_vma.cpp:261–276,299–304` creates mapped buffers and uses VMA's allocation flush; coherence is not assumed.

The stream has storage-buffer usage as well as uniform and transfer usage. That usage alone is **not** a readonly/nonalias proof. Its declared usage does not include shader device address, and the inspected guest-page registration sites cover imported/mirrored guest pools, not the driver-bank stream. A live integration should still validate the concrete allocation generation and every writable descriptor/mapped physical owner it admits, especially if allocator or stream code changes.

Compute descriptor binding occurs after `build_descriptors`, through `bind_descriptors`, then the original push constants, then dispatch (`vk_draw.cpp:9265,9320–9323,9392–9394`). Push descriptors consume the pending writer; descriptor-set mode updates a newly allocated set. The integration point must bind records from that same pending writer before it is reset/reused. No change to the physical-root push layout is needed.

Indirect dispatch has one explicit exception to immutability: it obtains a private C0 allocation, disables ordinary bank sharing, and copies the 12 grid bytes to C0 immediately before dispatch with transfer-to-uniform-read ordering (`vk_draw.cpp:2957–2964,9371–9382`). That patch is restricted to bytes 0–11, disjoint from every selector here. The captured target dispatch is direct. Future admission must retain that disjointness check rather than treat any GPU-written C0 range as automatically safe.

## Narrow live certificate still required

A future live producer can close the routing-input contract without proving that unrelated application payloads were historically atomic if it establishes all of the following at the actual recording boundary:

1. Exact original module, selector layout, typed element strides, alignment policy and supported descriptor classes match the frozen prototype.
2. Binding-0 buffer/offset/range resolves to a currently owned mapped stream allocation and recording generation. Copy the actual selected bytes from that range; do not call `pointer_table` again or use later guest reads. Preserve the original upload itself.
3. The final pending storage descriptors, null/fallback values and underlying owner identities/ranges are copied from the same descriptor-writer generation. Apply the existing per-width length arithmetic, original first-match ordering and identical typed roots.
4. The selector-containing range is not writable through any admitted shader/physical/transfer alias until this dispatch completes. Verify concrete owner/backing identity and overlaps, not merely `UNIFORM_BUFFER` usage or absence of direct stores to binding 0. Account explicitly for the disjoint indirect-grid copy if enabled.
5. The new record owner is independent, immutable, flushed and retained with the same recording lifetime. Its header/counts and exact descriptor layout satisfy the finite-work and bounds checks. Changed selector/range generations cannot reuse an old record.
6. The exact records are the ones bound with the candidate pipeline at the recorded dispatch; cache reuse, stand-in paths, a reset writer, unresolved mapping, missing ownership generation, allocation failure or unrecognized writer must fail closed to the existing path.

Source supplies a practical ownership path for these checks. It does not presently emit this certificate, prove every possible writer absent, or demonstrate that the optimized candidate improves compiled code or execution time. No historical capture completeness or performance claim changes as a result of this note.

## Source identity

Read-only packaged Hookshot source label: `v2.1.0-rc.3-306-g4cf785a0`. File digests, rather than the label alone, pin the inspected text. These are metadata; no source bodies or private shader bytes are included here.

| Source path | SHA-256 |
|---|---|
| `gpu/vk/vk_bank.cpp` | `cf2ead3c6828ec5eba7a470ebe41e476d37aa8874a222ce74e114ef28547cc72` |
| `gpu/vk/vk_draw.cpp` | `e0f809aaaa88f1ff945de1c5a082a3c417f7ae62357caa652716f9bf32473199` |
| `gpu/vk/vk_replay.cpp` | `a971dbb387cf67bb3c809da5505d014339f051a412d367f5a757330b602b5163` |
| `gpu/vk/vk_shader.cpp` | `8398859a6fae30035b25efb8e654902f5e900b7257f5d447f87ca1a89f006997` |
| `gpu/vk/vk_memory.cpp` | `e71643084c7edf308d2cdb2e14034181ebbab9c41635a6ef9c54b565cde65a5b` |
| `gpu/vk/vk_vma.cpp` | `aa5e6e786acdbf930fb3fcbabf859772a16d4af50f419b2fba7c78ef71c0a74d` |
| `gpu/vk/vk_state.h` | `e420f553b469744d943b7004f97dfc13e3cb0df4f4f5330df22a6237cfc7d0af` |

Validation was source tracing, independent seed-layout metadata extraction with a matching native digest, inspection of the already frozen manifest/UBO, and publication-content audit of this note. No GPU/Vulkan call or renderer mutation was performed.
