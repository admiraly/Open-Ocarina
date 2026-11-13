# Pending-descriptor selector extraction prototype

The new independently authored [`compute_selector_extract.h`](../src/probes/compute_selector_extract.h) implements a CPU event registry and a narrow pending-descriptor extraction adapter for compute `cc62764061c44f6a`, variant `095931037888515a`, original module `b9af52c0…`. It creates an owned immutable bundle of the selected C0 bytes and normalized descriptor ranges. This is a producer-interface prototype, not a live certificate or a renderer integration.

`patches/hookshot/compute-selector-extract-prototype.patch` is additive: it adds the exact frozen record-core header and this new extractor header to a proposed renderer tree. It changes no existing renderer file, control, descriptor binding or pipeline. The current twelve-overlay renderer source, binaries, writer policies and frozen record core remain unchanged. The [record-core contract](compute-interval-record-core.md) and [selector source audit](../research/live-selector-inputs.md) supply the preceding scope.

## Actual extraction boundary

`Registry::after_build` is intended to run after the actual `build_descriptors` completes. Its template reads the final pending `DescWriter.writes` and `bufs` fields directly. It verifies that each `pBufferInfo` points into that writer's final buffer vector before dereferencing it, rejects duplicate or unsupported uniform/storage slots, and copies the ten exact buffer elements: UBO0/1, singleton SSBO2–6 and the three elements at binding7. It retains program identity, recording part and a newly generated writer epoch. Images are not reconstructed; the exact original module's routing inputs are the buffers above. `Identity.module` is supplied metadata, not a digest computation. A future source hook must feed the actual stage hash verified by the native writer; substituting a constant for a missing stage identity would not authenticate the module.

Extraction requires an exact 2224-byte binding0 range. It searches **all** entries in the actual recording's retained `streams` vector, including a stream already outgrown. It checks buffer/allocation identity, registered mapping pointer, capacity and stream epoch. It copies from the selected stream's mapping plus the descriptor offset; it never substitutes the current stream's mapping. A separate actual-upload event must cover the whole selected range in the current recording. A retained allocation's capacity alone is insufficient. Same-record cache reuse can retain that earlier upload stamp; a later overlapping upload superseding the sealed writer rejects.

The eight storage roots preserve the actual final buffer/offset/range values, including any existing range shrinking or widening. Canonical null descriptors remain null views; an owned nonnull sixteen-byte zero-buffer fallback stays a nonnull view. Neither guest binding values nor later calls to `pointer_table`, driver-bank reconstruction or guest reads are used.

The returned `Bundle` exposes only `const Input& input()`, copies the selector-visible bytes, and keeps physical retention/import-lease objects. It has no mutable input accessor or live-to-owned conversion. Its mode is always `LiveAsSubmitted`; the frozen `eec196…` core rejects it. Every positive CPU case explicitly checks that rejection. No record-generation bypass, candidate layout, binding12 write, pipeline selection or environment switch is implemented here.

## Concrete new interfaces

The registry generates monotonically increasing allocation, backing, descriptor, recording, stream and upload identities from lifecycle events. It does not consume `owner_known=true`, `no_alias=true`, `all_writers_known=true` or similar attestations. Generated identities alone do not authenticate missing producer events.

| Interface | Concrete input and effect |
|---|---|
| `memory_vma` / `memory_free` | Actual VkDeviceMemory block-created/freed identity and extent; recycled handles receive a new instance |
| `vma_buffer_created` | Actual VmaAllocationInfo deviceMemory, offset, allocation size and mapped pointer, actual resource extent and VMA allocation handle; an absent block callback or out-of-block allocation rejects |
| `memory_import` / `imported_buffer_created` | Retained host backing lease, ABI1 framing, successful acquire/validate result, stable backing/mapping/lease fields and exact ordinary binding offset/range; unknown imports reject |
| `begin` / `submit` / `completed` | Recording slot and part, actual submission prerequisite and observed completion; slot reuse before completion rejects |
| `retained_stream` / `mapped_upload_completed` | Concrete mapped allocation and completed upload ranges in that recording; missing, stale or superseded upload generations reject |
| `writer_reset` / `after_build` | Actual writer reset/build boundaries and final buffer infos; old writer tokens reject |
| `RetainSource::acquire` | Backend pin whose current buffer, allocation, memory, offset and resource size must match the registered facts exactly |
| Writer events / `unknown_writer` | Concrete host, storage, transfer or physical possible-write owner/range; an unresolved owner or unclassified writer invalidates the registry |

The import interface preserves actual host acquire/validate return status, ABI/size, acquired generation/event and invalidation state. A later disjoint host event may advance current generation without invalidating an otherwise stable acquired lease. Different imported memory handles with the same stable host backing identity are recognized as aliases. VMA suballocations retain their real block offsets. Sparse, partial or multi-memory imported bindings are not flattened into an ordinary contiguous owner; they need another bridge or rejection.

`RetainSource` and `ImportLease` are abstract producer interfaces. The prototype's tests implement them with owned synthetic objects. No implementation calling the real current renderer retain API or host ABI is supplied. These interfaces define the missing bridge instead of guessing ownership from buffer handles.

## Selector lease and command order

The registry independently treats the target's entire writable binding6 view as a possible writer. It compares backing identity plus offsets against every full sixteen-byte selector record, including the fourth word that the record calculator does not read. Distinct VkBuffer handles are not an alias proof.

A pending GPU write can execute after a CPU snapshot even if its command was recorded earlier. Consequently, overlapping GPU writes reject regardless of their position relative to CPU selector-ready, unless actual completion has been observed. Pending cross-recording host/GPU writes are also checked conservatively. A same-record host upload completed before selector-ready may be accepted because the adapter copies its resulting mapped bytes.

The active lease retains physical pins until observed submission completion. A proposed later write overlapping a protected selector throws and invalidates the lease; an unknown writer also invalidates it. `matches_pending(bundle, dw)` rechecks current pending descriptor values as well as owner/pin/import generations. A plain ledger token cannot detect an identical unhooked reset; real reset/build callbacks remain mandatory.

Direct dispatches carry an explicit positive XYZ grid and no indirect fields. Indirect extraction requires a matching event for the actual retained argument buffer/offset and the exact twelve-byte copy into C0 bytes0–11. The argument owner is retained too. That write is disjoint from every selector; no CPU readback or reconstruction of the indirect grid is substituted. Changed copy extent, destination, source generation or writer generation rejects.

The bundle's `Input.lease_end_order` is the current extraction/dispatch cut, not invented future GPU-completion evidence. The separate registry lease remains open through real completion. This is another reason the input cannot be relabeled as a closed owned diagnostic graph or passed through the frozen record builder as live data.

A production caller must check proposed-write guards **before** emitting writes. The prototype's exceptions do not implement command-buffer recovery. Wiring them to skip a real write or dispatch would change the workload and is unsupported. A future integration must preserve the original full workload when rejecting the optimization and must retain protection after submission; merely noticing a violation after GPU execution starts is insufficient.

## Source-backed checks and remaining hook proof

`tools/compute_selector_source_probe.py` pins the current twelve-overlay draw/VMA/memory/replay/host-ABI sources. It copies the actual `DescWriter`, `RecordingRes` and nested stream declarations into a private CPU harness, plus the actual 88-byte host lease ABI declaration. It then exercises the same independently authored cases with real member names and struct behavior. It applies the twelve overlays and additive prototype to an isolated Git index and verifies that both added headers match their owned public sources. No shared working-tree file is patched.

This proves source-member compatibility and modeled extraction behavior. The mapped data, VMA-info values, import leases and physical-retain providers are authored mocks. No actual Vulkan/VMA/host import function is called. In particular, it does not turn those modeled events into observations of a running renderer.

The exact missing production hooks are:

| Current source site | Missing producer responsibility |
|---|---|
| `DescWriter::reset`, `build_descriptors` | Writer reset/build epochs, actual source-stage hash receipt and final pending descriptor association |
| `draw_begin_recording`, recording wait and submit/completion paths | Slot/part epoch, true reuse prerequisite, completion-backed retention, and a serialized order domain across parallel recording |
| Stream create/grow/destroy and `uniform_range` upload/cache paths | Every retained stream's generation and actual mapped upload completion stamp; cache reuse tied to the same recording's upload |
| VMA block callbacks, buffer create/destroy | Actual block extent, suballocation offset, mapped identity and lifetime; current optional observation registry is not an unconditional producer |
| Imported/sparse bindings and pool retirement | True backing validation and physical retention; current legacy buffer retain API alone does not expose backing generations/offsets or retain imported owners |
| Every host/transfer/storage/physical possible writer | Complete owner/range registration and conflict handling; source-level writer coverage is not established by absence of recorded events |
| Direct/indirect dispatch and C0 prefix copy | Extraction from the matching pending writer and exact actual command parameters before dispatch |
| Record allocation, writer and candidate binding | Separate reviewed live mode, independent immutable record owner, matching ABI/source identity and safe baseline rejection behavior |

The current source supplies none of these new extraction epoch/lease callbacks. Its `OoBufferOwner` exposes only size, usage and a VMA allocation pointer, and its legacy retain path is conditional on allocation-capture configuration. The optional binding ledger is observational. Reusing either as a complete live certificate would be incorrect. The new API deliberately leaves those facts unresolved until the wrappers are implemented and reviewed.

Independent review also identified bounded-prototype limits for actual wiring.
Differing acquisition generations for one imported backing are conservatively
rejected, even when another bridge might establish disjoint changes. Failed
submission/cancellation retirement and long-running journal cleanup are absent.
Host-write wrappers must serialize the guard through completion of the actual
write; releasing their lock between a proposed guard and the copy would leave
an unprotected interval. These are requirements for the future bridge, not
implemented production lifecycle support.

## Validation scope

The authored tests cover the outgrown selected stream, upload versus capacity, null versus real zero fallback, VMA block extents, shared imported backing, ABI validation, pre-ready GPU writes, cross-recording writes, protected fourth selector words, descriptor mutation, generation reuse, missing owner/pin/provider data, recording completion, actual indirect-prefix copy framing and live-core rejection. GCC warnings-as-errors and Clang UBSan passes are recorded in the [observation metadata](../research/compute-selector-extract-observation.json).

No shared source mutation, host/renderer rebuild, GPU call, candidate compilation, flag enablement, execution admission, commit or publication was performed. The producer truth gap, incomplete writer coverage, real retention bridge, parallel-order proof and post-submission conflict handling remain required work before live record generation can be considered.
