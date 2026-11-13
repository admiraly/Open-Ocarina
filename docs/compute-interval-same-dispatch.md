# Same-dispatch interval metadata builder

The new [authored patch](../patches/hookshot/compute-interval-same-dispatch.patch) adds a standalone C++17 header, `gpu/vk/compute_interval_same_dispatch.h`. Its explicit scope is **`MetadataSameDispatch`**. It calculates the interval record for the selected original dispatch's existing eight StorageBuffer roots and checks a separate new record upload. It has no Vulkan calls, shader words, renderer hooks or runtime flag.

This scope preserves the original descriptor objects, storage classes and dispatch position. The validity of those original roots remains the renderer's existing precondition for that same original dispatch. The builder does not require a new historical payload snapshot, imported-root retention bridge or physical-page/root pin. It never includes or constructs the frozen record-core/extractor types and never converts `LiveAsSubmitted` into `OwnedFrozenGraph`. Their mode gates remain unchanged.

## Two concrete CPU operations

`calculate(const MetadataSameDispatch&)` takes the exact owned 2224-byte C0 value, original stage identity and ten final buffer infos: UBO0/1, five singleton StorageBuffers 2–6 and the three literal elements of StorageBuffer 7. It returns an immutable `Plan` with the exact record bytes and original buffer infos, plus explicit producer-condition denials. Missing lifecycle evidence does not get fabricated merely to run the arithmetic. Unsupported identity, descriptor layout, missing C0 bytes or unresolved effective range prevents calculation.

`prepare(plan, current_metadata, optional_record_upload)` recalculates at a current pending-writer cut and verifies that the original infos, C0 bytes, program/writer generations, command buffer, recording slot/part/epoch and explicit dispatch instance still describe the same dispatch. A later dispatch cannot reuse a plan simply because it reused the writer. It then checks the new record's binding-12 metadata, exact uploaded bytes, owner, retained stream and visibility facts. Its readonly result contains the unchanged original ten infos and a separate record descriptor only when these local consistency checks pass.

Both result types permanently state `live_certificate=false` and `replacement_admitted=false`. A successful authored case reports **metadata consistency**, not source authentication or permission to execute a replacement. Input generations, policy enums and event counters are concrete consistency inputs; filling them in does not prove that real hooks produced them. Authentic producer extraction and its coverage require the later source review.

## Exact record arithmetic

The builder independently implements the original five-single-then-three-table walk, with no nearest phase. Each accessor width, 4 and 16, gets its own endpoint partition. The aligned base is `guest & ~15`; `OpArrayLength` is modeled as uint32, and its byte-count shift wraps in uint32 before extension. Both endpoint additions wrap in uint64. Ordinary singles retain the minimum of typed and NVN ends even with a zero size word. Only a table's zero size word substitutes its typed bound.

First match retains **binding, array element and aligned guest base**, including overlap, unaligned zero-size ordinary prefixes and typed flooring. The new record keeps the frozen 16-byte header, 48-byte rows and binding-12 route IDs. No root payload is read and no guest-page table is inspected.

Finite original `VkDescriptorBufferInfo` ranges are consumed directly under the existing original-dispatch validity precondition. `VK_WHOLE_SIZE` needs the actual resource size to derive the effective typed range; it cannot be treated as an enormous finite range. A real null descriptor has zero effective length. A real nonnull 16-byte zero-buffer fallback stays nonnull with length 16.

## Selected C0 and pending-write facts

The source checks require a current pending-writer cut, one journal domain and an exact current info rescan. The selected upload must resolve to actual VMA allocation/memory, resource and memory generations, mapping identity, resource/allocation/memory extents and the recording's retained stream. Its actual upload stamps must cover the whole selected C0 range before the sealed writer generation. A later overlapping upload rejects the plan. The owned copy needs a matching serialized copy/stamp witness.

The narrow supported ownership proof is a **dedicated VMA buffer without device-address usage**, with current memory generation and zero dedicated allocation offset. That excludes different-buffer aliases and valid physical-address/image-only access to this private buffer under the ordinary Vulkan binding preconditions. Shared blocks, BDA-visible C0 or unknown origins fail closed. No imported root's acquisition generation is needed to establish this private allocation's isolation.

Known binding6 is independently checked against every full sixteen-byte selector record. Pending GPU buffer writes overlapping selectors reject even when their commands were recorded before the CPU copy; CPU order does not establish completion. An actual submitted sequence covered by observed completion can discharge that event. A completed same-record host upload before the selected upload can be accepted. The exact indirect-grid prefix transfer at C0 bytes0–11 remains disjoint. Unknown writer classes/destinations, inconsistent journal counts/domains, stopped or failed journals and callback failures remain denials. Physical/image-only classifications must come from the real producer; an opaque source denial cannot silently be relabeled to obtain exclusion.

The stream contract identifies an actual append-only recording resource, its owner/stream generation, mapping, used extent, slot and recording part, retained until the real slot-completion path. It does not invent a future fence result or a closed diagnostic lease with positions 1–3.

## New record ownership, visibility and lifetime

Equal record bytes are one check among several. The new record must name the actual mapped VMA owner, current generations, resource extent, same recording slot/part/command buffer and retained stream. Copy/upload facts must share the same serialized completion stamp, cover the exact descriptor range and match the bytes calculated by the plan.

Visibility additionally requires either observed `HOST_VISIBLE|HOST_COHERENT` memory properties or a successful checked mapped-range flush after the upload stamp, covering the record range in the same owner generation. A registered submit-flush policy alone does not establish visibility. Failed, short, stale or unrelated flush facts reject preparation.

The record may append to the same retained stream as C0 only after its previously committed prefix, with an observed post-plan append reservation. It must also be disjoint from every original descriptor range. Another dedicated retained stream requires its own observed append reservation. A different buffer claiming the same dedicated memory is inconsistent. Pending writes to the record reject preparation. This avoids adding a blanket requirement for an independent imported-root lease or a new allocation for every dispatch.

## Exact missing observer13 producer work

The [observer](compute-selector-observer.md) and its source-v2 tree remain unchanged and continue to report certificate false. The new header consumes value objects and is not wired to that observer. The inspected source provides useful events, but its exported JSON is not a prepared-input certificate:

| Actual source location | Required next producer operation |
|---|---|
| `writer_built_values`, `writer_reset`, dispatch call site | Copy the actual current typed pending infos at dispatch, not only the sealed `Writer.values`; associate them with current writer and a dispatch-instance token generated at that actual call site |
| `State`, recording contexts and retained streams | Detach the actual journal identity/cut, slot/part/epoch, owner/memory/stream generations and stopped/failure state in one serialized value snapshot |
| `reserved_range`, `stream_alloc`, stream growth/retention | Supply the actual committed used watermark for each selected retained stream, including an old outgrown stream; capacity alone is insufficient |
| `dispatch` selected C0 copy and `MappedWrite` | Supply the selected mapping/owner identities, covering completed upload stamps and serialized owned-copy witness; preserve unknown/superseded cases as denials |
| New record allocation/copy | Supply the real retained stream/range and upload completion, without changing the original ten descriptors or dispatch identity |
| VMA memory-property query or checked flush | Supply actual coherent memory flags, or the actual successful flush result and coverage after the new upload |
| Final binding site | Recheck the same dispatch/current cut before consuming the readonly original infos plus separate binding12; review candidate layout/cache/device policy independently |

The source13 `Stream` records capacity and stamps but not the new committed-used fact. Its dispatch routine exports sealed writer values rather than performing the new typed current-info rescan. Exported owner metadata omits raw mapping/buffer/memory identities needed for the concrete key comparisons. No record allocation/upload/visibility fact exists because the observer never creates a record. These are precise next hooks, not a demand to capture the application's complete historical memory graph.

Future hooks must preserve the full original workload on denial. The value-only CPU builder can run without holding backend locks; allocation/VMA work must retain the observer's existing rule of occurring outside its mapped-write/observer mutex. Any later source adapter must preserve the actual copy-and-stamp serialization and recheck the current cut after allocating a record. This patch implements none of those source mutations.

## Verification and frozen identities

Fifteen authored tests cover 120 complete uint64-domain partitions against an independent direct source walk, plus overlap/null/typed-floor/wrap cases, unknown cuts/owners/stamps, generation changes, dedicated/BDA restrictions, pending writes, same-stream record appending, stale dispatches, record ownership/lifetime and exact visibility/flush checks. GCC warnings-as-errors and Clang UBSan both pass all fifteen tests.

The already frozen private graph calculates **exactly 592 bytes**, SHA-256 `49da4bdc0c4542b4fef7b5dcbb631d41fb030d80803cde3bb37185722daf9770`, with both host builds. This check supplies C0 and normalized frozen descriptor references only, with **no synthetic lease/order fields**. It reports the actual missing producer conditions:

- `actual_current_pending_dispatch_cut_missing`
- `same_dispatch_write_journal_cut_missing`
- `selected_C0_owner_upload_stream_facts_missing`

No actual record upload is supplied or created, so preparation of that historical fixture is not claimed. Authored lifecycle values used by the positive tests are explicitly mock facts, separate from this private calculation.

Frozen source identities are patch `d9751bb128ffa1b795d9e4e14baa477417c6c68182199be67d011cdec6662fbb`, extracted header `92c0e6f2028720a3c68a6fca8d0961ba764c6037e2050d39deac3fdf982f3b14`, and tests `aed6b842d4260cdfe00bef53062fd610be6513c7c05914c27ba887cb86a66f7b`. The [observation metadata](../research/compute-interval-same-dispatch-observation.json) pins inspected source and host binaries. Revision-1 reports remain under ignored `local/same-dispatch-interval-01/`; current revision-2 reports are under `local/same-dispatch-interval-02/`. No shared/vendor source, frozen component, observer certificate, build wiring or production flag was changed, and no GPU work ran in this lane.

## Revision 2: committed stream prefix

Independent review found that revision 1 could accept a record overwriting an earlier unbound allocation or a covering upload's tail, while missing all ten current descriptor ranges. The new record must now include an actual post-plan append reservation with journal/event, buffer and owner/stream generations, and the previous committed-used watermark. Its offset cannot precede that watermark. On C0's stream, the reservation must also preserve the plan's committed prefix and stream generation. Alignment padding before a newly appended record is allowed; old holes are occupied.

The new regressions reproduce three accepted revision-1 counterexamples, then require rejection. They also reject missing, stale or foreign reservation facts and test aligned suffix allocation. Tests now read and statically assert the real `Plan`/`Prepared` certificate constants instead of printing assumed values. All 15 tests pass with GCC and Clang UBSan; both still calculate the exact 592-byte private metadata record. Revision-1 private artifacts remain unchanged; revision-2 evidence is under ignored `local/same-dispatch-interval-02/`. A real producer must supply these reservation observations before integration. No runtime admission is established.
