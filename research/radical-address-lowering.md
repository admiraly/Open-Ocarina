# Radical address lowering: do not delete the descriptor walk

Status: design plus an abstract byte-view prototype and separate typed source-arithmetic regressions. No shader was executed, no pipeline was compiled, and no frame time was measured by this design lane.

## What the source actually does

Hookshot `DefineGlobalMemoryFunctions` (`gpu/vk/eden/shader_recompiler/backend/spirv/spirv_emit_context.cpp`) builds one DontInline load helper and one DontInline store helper per width, plus global atomics. Each helper:

1. If `native_global_bounds` is set, picks the containing candidate whose aligned base is nearest at or below the address (tie keeps the earlier index).
2. After no candidate hit, walks single storage-buffer descriptors in binding order and returns on the first range that contains the **start** address.
3. Then walks `Info::global_tables` the same way. On the Intel Mesa path those tables are one DontUnroll loop (`global_table_loop` in `gpu/vk/vk_coopmat.cpp`); the single-descriptor cascade above is still fully unrolled in SPIR-V.
4. On a load miss, translates through the guest page table. On a store or atomic miss, it does that only when follow-stores is enabled; otherwise the write is dropped.

Hit addressing is typed: `uint32(addr - aligned_base) >> shift` selects a U32/U32x2/U32x4 element, while `OpArrayLength` determines width-specific effective bounds. Candidate, ordinary-single and global-table size rules differ. The original byte-view prototype abstracts these away; the source correction below is required for a native interval implementation.

The page table (`guest_pages_set` / `memory_table_flush` in `gpu/vk/vk_memory.cpp`) has two windows, 8 GB at `0x1000000000` and 4 GB at `0x7f00000000`, one `u64` device address per 4 KB page. An access that would cross a page does not use the table. Writes into the table during a recording are deferred and applied later with `vkCmdUpdateBuffer`; last write to a page wins. The CPU mapping is not the table the GPU will read.

`push_constants` in `gpu/vk/vk_draw.cpp` copies `memory_guest_pages()` to `GUEST_PAGES_PUSH_OFFSET` (`sizeof(RescalingLayout) + sizeof(RenderAreaLayout)` in `emit_spirv.h`). Descriptor bases and sizes come from `stage_cbuf_word` at `build_descriptors`, which is the as-submitted constant buffer, not a later guest read. A binding's Vulkan range is often shorter than the mapped pool (size word, 1 MB bare-write guess, shrink-to-fit). `robustBufferAccess` / `robustBufferAccess2` turns an out-of-range storage-buffer load into zeroes. The page-table path does not.

Mesa inlines `DontInline`. On Arc the unrolled walk is therefore duplicated at the call sites. That is the compiler-size problem. A page-table load is about fifteen instructions. Replacing the walk with that load would be an order-of-magnitude shrink **if it were equivalent**. It is not a generally equivalent replacement when descriptor claims exist; a dispatch-specific proof would need its actual access domain and view semantics.

## The experiment that fails, and the one that survives

**Page-table-only** means every global helper ignores descriptors and uses `guest_lookup` only.

The original synthetic interpreter (`tests/test_pt_claim_certificate.py`) compares an **abstract byte-view** walk with page-table-only reads. Its `legacy_load`/`specialized_load` do not reproduce typed index flooring or all source descriptor classes. The separate `hookshot_typed_hit`/`hookshot_typed_load` regressions below model those inspected source operations. In the abstract model, disagreements include:

- a 4/8/16-byte access starts inside a claim and extends past the bound length (the bound descriptor applies its robust bounds policy; the page table can expose neighboring bytes that policy suppresses);
- the access crosses a 4 KB boundary inside one Vulkan buffer (the walk succeeds; the table misses and returns zero);
- two descriptors overlap and the later one is what the page table maps (first match, or nearest candidate base, wins);
- a size word of zero is bound to a short guess while the page stays mapped;
- a global store misses every claim and follow-stores is off (the walk drops it; a page-table store would write).

A deferred table run also disagrees with the CPU mapping. The GPU view, including pending runs not yet flushed, is the only view a certificate may read.

An empty effective claim set is a conservative sufficient eligibility condition for considering a page-table-only specialization **and** either the shader does not store global memory or follow-stores is the same policy in both helpers. Derive emptiness for every accessor width from the actual source classification and typed length arithmetic; a zero guest pointer alone is insufficient. Other nonempty cases might be provable over restricted access domains, but this finite CPU model does not establish them. Specialization-constant dead-code elimination must be measured rather than assumed. The target program writes at least one storage binding with scalar and vector aliases. Treat it as having claims until a capture shows otherwise. Do not aim the fast pipeline at it by default.

**Guarded interval specialization** is a candidate for preserving the existing hit/miss semantics with a smaller inlined body. Actual pointer lowering and code size still require measurement:

At dispatch, after `build_descriptors` has filled `dw().writes` and the as-submitted words, build a record:

- candidate bindings, kept as a nearest-base group (do not flatten these to first index);
- every other single descriptor and table entry, flattened in walk order to disjoint `[aligned_base, end)` intervals, each tagged with its descriptor binding and element index;
- Build **separate effective interval partitions for each typed accessor width**. Candidates use the typed bound only; ordinary singles cap the NVN end even when its size word is zero; global-table entries alone substitute the typed bound when their size word is zero. Preserve the emitter's uint32 byte-count shift, uint64 end arithmetic and width-specific `OpArrayLength`. A scalar partition cannot be reused for vectors merely by rejecting its selected hit: that can hide a later descriptor that the original vector walk would select.

Push that record in a small uniform block (or a specialization-sized array plus a UBO). The SPIR-V helper becomes one loop: nearest candidate, then a binary search of the disjoint intervals, then the existing page-table miss path. A hit computes the storage-buffer access chain it computes today, so the load stays in `StorageBuffer`. Robust tails, first match, and aliasing with direct descriptor operations stay on one storage class.

The prototype's `specialized_load` matches its abstract `legacy_load` on a mixed grid (nearest candidates, overlapping singles, page-straddle, short tail). `pt_only_load` does not. This is internal model agreement, not a claim that either byte-view model reproduces the complete emitter.

## Insertion points

1. `EmitContext::DefineGlobalMemoryFunctions`, inside `define_load`, `define_write`, and the global atomic helpers. Replace the unrolled per-descriptor cascade with a call to one new helper, `define_interval_lookup`, which returns either a storage-buffer pointer or signals a miss. `guest_lookup` stays the miss path, unchanged, including the page-fit test and the follow-stores gate. Add a specialization constant `claim_mode`: 0 = interval helper, 1 = page-table-only (legal only when the CPU proved the claim set empty and stores are safe). Pipeline-cache identity must include the constant. `global_table_loop` already requires storage-buffer array dynamic indexing on this driver. Returning or selecting pointers across distinct bindings additionally needs an appropriate variable-pointer capability and enabled Vulkan feature; dynamic descriptor indexing alone does not establish that requirement.

2. `build_descriptors` / the compute dispatch path in `gpu/vk/vk_draw.cpp`, after the as-submitted words and `VkDescriptorBufferInfo` ranges exist and before `vkCmdDispatch`. New function `claim_record_build(const Program&, const NvnState&)`. It must apply `table_pending` with last-write-wins onto a copy when it checks device identity. It must not read `guest_pages[]` while `table_defer` is set. Select `claim_mode` there. `push_constants` keeps writing the page-table root it already writes; do not hard-code a byte offset. `sizeof(RescalingLayout)` is alignment-padded, so a private note that the root sits at byte 48 has to be checked against `GUEST_PAGES_PUSH_OFFSET` before any specialization uses a literal.

3. Do not change `guest_pages_set` or the flush. The certificate consumes that protocol; it does not replace it.

## Invariants the prototype pins, and the proofs still open

Pinned within the original abstract byte-view model (not universal source semantics):

- First-match among non-candidates; nearest lower base among candidates; ties keep the earlier index.
- Start address in range is not enough for a wide load: the bound-hit path must preserve the actual robustness alignment and whole-vector or per-component bounds behavior.
- Page-table translation is `(entry) + (addr & 0xfff)` only when the access fits in the page, the address is in one of the two windows, and the entry is non-zero.
- Null or zero-length descriptors fall through.
- Pending table runs override the CPU mapping; last run wins.
- Stores that miss are dropped unless follow-stores is on.
- An interval record built with those rules matches the unrolled walk on the synthetic grid.

Still unproved, and required before a renderer patch:

- The bound `VkBufferDeviceAddress` equals the GPU page-table entry plus the in-page offset, for every page of a claim that the shader might also miss into. A snapshot or a shrink that bound a different view fails this. The prototype represents it as `claim-device` and does not have real Vulkan addresses.
- Direct storage operations on the same bytes (the aliased scalar/vector binding) and the global helper must stay in `StorageBuffer` on a hit. Moving a hit to `PhysicalStorageBuffer` is a different memory type even when the bytes match. Page-table-only does that. The interval helper must not.
- No implicit cross-invocation race is introduced by deleting the walk. Absence of subgroup, shared, barrier, and atomic opcodes does not prove that. The interval helper does not change ordering relative to today's hit path; the page-table-only helper would.
- `OpArrayLength` caps and `min_ssbo_alignment` match `storage_binding_align()` used at bind time. The prototype uses 16.
- Table entries with count greater than one, the miss-log descriptor, and bare/shrink/wide bind lengths are classified the same way `build_descriptors` classifies them. The prototype has the length rules, not that function's pool queries.
- Specialization-constant DCE of `claim_mode = 1` actually removes the interval loop on the Mesa version that ships with the A770. That is a later compile-only probe, not a result of this session.
- Historical live replay remains incomplete. The later [owned physical replay](../docs/physical-replay.md) checks one explicitly frozen graph; it does not validate this proposed interval record or upgrade a sampled objection search to a certificate.

## Strongest reasons this is still wrong

- The compiler win is "Mesa inlines a short loop instead of a long cascade." If NIR then unrolls that loop to the worst-case interval capacity, the specialized shader is large again. Cap the record (tens of intervals, not hundreds) and keep the loop control in a form this Mesa does not unroll, then measure the compiled ISA. Until that probe, "order of magnitude" is a hypothesis.
- `claim_mode = 1` almost never applies to a program that binds real buffers. Selling it as the fix for the timed compute target would be a false economy.
- Device-address identity can fail while guest addresses look fine: imported pool versus a copied snapshot, a shrunk table entry, or a stale deferred run. A certificate that compares guest ranges only would bless a wrong pointer.
- Physical and storage-buffer aliases are not coherent just because a test reads the same bytes on the CPU. The interval design avoids that split; a later "optimization" that rewrites hits to the page table reintroduces it.
- The walk's range test does not include the access size, and robust zeroing is a device feature. Even with `robustBufferAccess2`, descriptor bounds may be rounded to the device limits and wide accesses may be split into separately checked 32-bit pieces. A model that universally zeroes an entire partial vector is insufficient. Robustness mode, alignment and the selected view belong in the contract.
- Global stores and 8/16-bit CAS loops are separate helpers. Forgetting them leaves a fat cascade in the module and, worse, a second semantics for the same address.

## Smallest decisive experiment

1. Keep the prototype green: `python3 -m unittest tests.test_pt_claim_certificate -v` from the repository root. It needs Python 3.11+ and no extra packages.
2. Next, and not done here: one compile-only pipeline of a **synthetic** SPIR-V stand-in with the same helper shape (interval loop versus unrolled cascade versus page-table-only), same workgroup 64×1×1, `robustBufferAccess2` on, Mesa's reported instruction and spill counts. No guest shader, no dispatch.
3. Only after the capture contract exists: run `claim_record_build` on one real dispatch of this program and record whether the claim set is empty, whether any claim fails device identity, and whether any claim contains a page-straddle or a short tail. That single record decides whether page-table-only is even a candidate. The expectation from this model is that it is not.

## Files

- This note: `research/radical-address-lowering.md`
- Prototype: `tests/test_pt_claim_certificate.py`

## Review correction: bounds policy and finite models

The Vulkan specification permits robust-buffer-access-2 checks against rounded descriptor bounds and allows wide accesses to be split into 32-bit checks. See [Robust Buffer Access 2](https://docs.vulkan.org/spec/latest/chapters/shaders.html#shaders-robust-buffer-access2). The local A770 driver reports storage alignment 4 and uniform alignment 16; these are bounds-check alignment limits, not the separate storage-binding alignment.

The synthetic model now explicitly supports whole-access and component-wise checks plus configurable robustness alignment. Its default models one allowed choice, not a universal driver oracle. Both modeled hit policies retain counterexamples to unconditional page-table replacement, and the interval-hit path preserves the selected policy.

The prototype function is named `sampled_objections`: it searches a finite boundary grid. An empty objection list is not a sound certificate, not proof for all accesses, and must never enable an optimized runtime pipeline. Empty-claim eligibility is a conservative design opportunity, not a demonstrated necessary condition for every possible equivalent dispatch.

## Source correction: typed views and descriptor classes

A subsequent pinned-source inspection found material differences between the
original byte-view prototype and `DefineGlobalMemoryFunctions`. The prototype
is retained for its abstract counterexamples; its function names are not a
source-equivalence guarantee. Nine new typed regressions explicitly distinguish
the following operations:

- `OpArrayLength` counts complete U32, U32x2 or U32x4 elements. The emitter shifts
  that count in uint32 arithmetic before converting the byte extent to uint64.
  A 10-byte descriptor therefore exposes an 8-byte effective claim for U32/U32x2
  and no claim for U32x4. Bounds-rounding permitted by device robustness is a
  separate question from this shader-side typed-length computation.
- The hit index is `uint32(addr - aligned_base) >> shift`. An unaligned vector
  address is floored to its vector element; the original abstract exact-byte
  tail example must not be presented as that source operation. A guest operand
  spanning a page can still hit an earlier aligned storage element, while the
  physical fallback tests the original guest operand's full page fit.
- Nearest-base candidates use their typed bound without the NVN size cap.
  Ordinary singles use the smaller NVN/typed end, including a zero size word.
  Only global-table entries substitute their typed bound for a zero size word.
  A zero-size unaligned single can still claim its alignment prefix.

These rules require per-width interval construction and preserve descriptor
selection before access-chain generation. Missing the typed cap can choose the
wrong first descriptor, even if a later robustness check suppresses its read.
The new helper assumes descriptors were already classified and supplied in
source order. It does not implement classification, descriptor creation, atomics,
all access widths, original alignment validity, or a driver memory oracle.
Neither it nor the original finite grid may authorize a native pipeline.

The subsequent [actual storage-pointer compilation experiment](storage-pointer-compile-experiment.md) validates pointer returns across two synthetic storage bindings, with a separate physical miss path. It requires `variablePointersStorageBuffer` and preserves the distinction between pointer feasibility, bounded CPU model agreement and runtime shader correctness. No game address-lowering patch follows automatically from that result.
