# Returning real storage-buffer pointers from interval lookup

Date: 2026-11-13. Status: actual synthetic SPIR-V validated and compiled on the A770. **No shader was dispatched.** This is the next feasibility step after the [lookup-ID experiment](interval-compile-experiment.md), not an implementation in the game translator.

## Decisive result

The A770 advertises `variablePointersStorageBuffer`. With that feature explicitly enabled, a synthetic function can return a logical `StorageBuffer` pointer selected from two distinct descriptor roots, or a logical null pointer on a miss. Callers can test the returned pointer and perform their ordinary storage-buffer loads/stores through it. SPIR-V validation and optimized Mesa pipeline compilation both succeed.

The miss path remains a **separate `PhysicalStorageBuffer` operation**. It loads a page address through a pushed root, checks null/window/page-fit conditions, then loads or stores the physical data. Store misses remain dropped when follow-stores is disabled. The helper does not attempt to return one pointer type across the two storage classes, and it does not convert descriptor hits to physical reads.

The module uses `VariablePointersStorageBuffer` and `PhysicalStorageBufferAddresses`, with the `PhysicalStorageBuffer64` addressing model. It does not declare the broader `VariablePointers` capability. The private Vulkan helper enables the Vulkan 1.1 `variablePointersStorageBuffer` feature without enabling `variablePointers`, and refuses a device that does not support it.

## What was compiled

`tools/storage_pointer_compile_experiment.py` generates three independently authored variants:

1. **Cascade/value:** first-match claim search, then a statically selected descriptor's value load/store; physical miss fallback.
2. **Interval/value:** binary search of the disjoint interval record, then the same value load/store branches; physical miss fallback.
3. **Interval/pointer:** the same interval search wrapped in a function that returns the selected `StorageBuffer` pointer. Callers load/store through that pointer or invoke the existing synthetic physical fallback on null.

There are five single claims and three already-looped table entries, no compiled candidate block, four dynamic load/store queries per invocation, a 64-invocation workgroup, and required subgroup size 32. Only two statically declared payload descriptors are selected. Metadata and queries occupy separate ordinary storage buffers. No descriptor-array element is selected through a per-lane nonuniform index, so this experiment does not establish that such indexing is available without an additional capability/feature decision.

The SPIR-V transformer rewrites only the synthetic wrappers. It adds pointer-returning function types, logical null and `OpPtrEqual`, preserves the physical fallback functions, and removes debug names for wrapper locals it removed. Non-entry helpers are marked `DontInline` and loops `DontUnroll`; the driver may inline functions while retaining loops. No shared or vendor source is changed.

## Observed code generation

All three pipelines reported actual subgroup 32, eight executable loops, and zero spills, fills and scratch.

| Variant | SPIR-V words | Executable instructions | Single cold pipeline-create observation |
|---|---:|---:|---:|
| Cascade/value | 3,322 | 1,611 | 0.0476 s |
| Interval/value | 2,785 | 1,114 | 0.0257 s |
| Interval/pointer | 2,306 | 1,431 | 0.0404 s |

The pointer-return version's smaller SPIR-V did **not** produce the smallest executable. Pointer selection/null handling partly erased the interval/value variant's code-size benefit. These short single observations are not a compile-time benchmark or a GPU-speed measurement. A general pointer helper is therefore feasible on this A770, but not automatically the best integration design; returning lookup metadata followed by static value operations remains a meaningful alternative.

The profile uses the same optimized private Mesa driver already audited by `compile_probe.py`, robust buffers/robustness2, push descriptors, shader optimization enabled, and a 32-byte push range. Driver SHA-256: `5ea19ac96aa52d061ecf2b8e23df7ed3d7d3b36c7d2ae7fe62f01c5a9b610aaa`. Host helpers compile with `-O2 -DNDEBUG` and strict warnings. The variable-pointer feature is enabled equally for all three variants.

The experiment clears all nine known compiler controls before applying explicit numeric overrides: `HS_CS32_MAX_INSTS`, `HS_SPILL_BATCH`, `HS_PUSH_SHADOW`, `shader_spilling_rate`, `INTEL_DEBUG`, `HS_SPILL_PCT`, `HS_RA_DEBUG`, `HS_FORCE_SIMD8` and `HS_LARGE_GRF`. The report records every controlled value, including absent ones. This is a selected-options audit, not a complete environment snapshot. The reported run uses the four overrides shown below; all five other controlled options are absent.

To avoid changing the frozen shared helper, the tool derives a private copy using exact, checked source anchors that add the Vulkan 1.1 query/enable chain. It records both original and adapted source SHA-256 values. An unexpected helper-source shape fails rather than silently adapting an unknown version. The helper still contains no queue submission or shader dispatch.

## CPU oracle and its limits

The tool includes a bounded typed-view model, with synthetic tests for first match, nearest-candidate priority/ties, overlapping intervals, alias-preserving stores, page-crossing miss rejection and the follow-stores gate. Important source distinctions are explicit:

- Candidate ranges ignore NVN size and use the typed bound.
- Ordinary single descriptors cap against `guest + size_word`, including when that size is zero.
- Only table entries interpret a zero size word as the full typed bound.
- A typed bound is `OpArrayLength × element width`; partial trailing bytes are not an additional vector element.
- Logical indices use the source's conversion to 32 bits followed by the element-size shift. A wide load therefore floors to its typed element, rather than necessarily reading the exact requested byte address.

The model rejects overflowing ranges, including typed byte extents larger than `0xffffffff`, rather than reproducing the source's 32-bit bound-size-shift wrap case. The integer-only extent regression covers that boundary without allocating 4 GB. It is not a general native admission oracle. The compiled shader tests aligned 32-bit data only; wide accesses and candidate classification are tested on the CPU, not compiled as complete native helper families. The wide query at byte 4092 floors to the preceding typed element on a descriptor hit; the corresponding physical miss instead rejects the page-crossing query.

Per-width bounds imply that **one 32-bit interval partition is not automatically valid for 128-bit helpers**. Integration needs per-width resolved records or a demonstrated equivalent width-aware rule that preserves next-match/fallback behavior. Atomics, subword CAS helpers, arbitrary binding counts, typed aliases and all original guest windows remain additional obligations.

## Reproduce

Required tools: Python 3.11+, a C compiler and Vulkan development files, glslangValidator, spirv-as, spirv-dis and spirv-val. Use a fresh private output directory and the previously audited driver/build:

```sh
python3 -m unittest discover -s tests -p test_storage_pointer_compile_experiment.py -v
python3 tools/storage_pointer_compile_experiment.py \
  --driver "$INTEL_ICD_JSON" --mesa-build "$MESA_BUILD_DIR" \
  --output local/storage-pointer/repro \
  --env HS_CS32_MAX_INSTS=100000 --env HS_SPILL_BATCH=1 \
  --env HS_PUSH_SHADOW=1 --env shader_spilling_rate=1
```

The existing `HS_*` options identify the private driver stack; this repository does not supply that stack. All generated synthetic sources, binaries, logs and full reports stay local. The recorded run is `local/storage-pointer/final-controlled/report.json`.

## Strongest unresolved risks

- Compiling demonstrates feature and code-generation feasibility, not actual cross-binding alias behavior on the GPU or preservation of the game's inputs and outputs.
- The stand-in's two static roots avoid descriptor-array nonuniform indexing. A real interval selector for many mixed bindings can introduce that separate requirement.
- The real source has multiple typed accessor families, width-dependent bounds, optional candidates, miss logging, stores/atomics and two 64-bit guest windows. This prototype is not a drop-in replacement.
- Returning logical null and comparing pointers can add code or lower poorly on another driver. Hardware lacking the feature needs the existing path or a different value-returning helper.
- Typed `StorageBuffer` hits and physical misses must retain their original memory classes and alias rules. Equal CPU bytes do not justify merging the classes.
- Captured records can be stale or refer to snapshots different from the current page table. No complete dispatch capture or resource replay is created here.

The next decisive work is a semantics-preserving native helper design with complete per-dispatch records, followed by full resource replay. These compilation results establish a compiler feasibility step; rendering changes, GPU runtime improvement and a frame-rate gain remain unverified.
