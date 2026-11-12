# Compute target memory contract — first static pass

Date: 2026-11-12. Target runtime key: `cc62764061c44f6a`; cached variant: `095931037888515a`.

This analysis pins the development SPIR-V SHA-256:

```
b9af52c0ba07f64eb8b0ca2905c432d094999fdf669aa95182ff7754b510e97b
```

`tools/spirv_contract.py` validates the private binary with SPIRV-Tools, computes the static entry-point call graph, groups descriptor declarations by set/binding, and reports memory-access sites without exporting instructions. The complete executable and disassembly remain private. This is metadata and dependency triage, **not a proof of race freedom or complete resource capture**.

## Verified static facts

- The module defines seven functions. Selected entry `main` reaches three of them through 72 static call sites; four helper functions are unreachable from that entry. Counts below describe reachable instruction sites, not dynamic execution frequency.
- The module contains six integer-to-physical-pointer conversions. Four are reachable: two in a scalar global-load helper and two in a four-word global-load helper. The two conversions in the two-word global-load helper are unreachable.
- The reachable conversions are used by four physical-storage `OpLoad` sites. Physical addressing is operational, not just a declared capability.
- The physical address expression depends on the third push-constant member at byte offset **48**. The helper reads a 64-bit page-table root, computes an entry address, loads a page address, then loads requested data through a second physical pointer.
- No reachable explicit subgroup/group operations, atomics or barriers were found, and the module declares no Workgroup-storage variable. These absences do not establish absence of communication through ordinary global memory.
- Built-ins are `LocalInvocationId` and `WorkgroupId`; inspected entry logic uses their X components. Workgroup dimensions are 64 × 1 × 1. The actual dispatch grid is a runtime input; non-X dispatch dimensions must be captured, not assumed to be one.

## Descriptor identities

All descriptors use set zero. Multiple variables with different scalar/vector element types alias the same binding; they are not separate allocations.

| Binding | Descriptor type | Descriptor count | Reachable use |
|---|---|---|---|
| 0, 1 | Uniform buffer | One each | Reads; integer, float and vector aliases |
| 2, 3, 4, 5 | Storage buffer | One each | Reads and array-length queries |
| 6 | Storage buffer | One | Reads **and writes**, through scalar/two-word/four-word aliases |
| 7 | Storage buffer array | Three | Reads and array-length queries |
| 8, 9, 10, 11 | Combined image sampler | One each | Image sampling/fetch/query inputs; binding 10 uses an arrayed 2D image |

Binding 6 has 17 reachable load sites and 16 reachable store sites. The other declared storage bindings have no reachable store sites in this variant. This is instruction classification, not a declaration that their underlying memory cannot overlap binding 6 or physical-address targets.

The push-constant structure has declared members at offsets 0, 16 and 48; its final member is 64 bits, so its declared maximum extent is 56 bytes. A pipeline layout can cover that extent, but a replay must preserve the exact bytes and member semantics.

## What can and cannot be inferred about independent invocations

The entry computes an index from `WorkgroupId.x * 64 + LocalInvocationId.x`. Inspected descriptor-store address expressions depend on that index and use a 320-byte record stride. This supports investigating per-invocation records, but it does not yet establish non-overlapping writes or read/write independence:

- Uniform values supply base addresses and mapped buffer bases; these must be captured with correct ranges and alignment.
- Arithmetic is finite-width; record bounds, dispatch bounds and address wrapping need explicit constraints.
- Ordinary loads can reach the read/write binding and physical-address allocations. Their relationship to the output region is not recoverable from static descriptor numbers alone.
- The three unused global-store helpers do not add reachable writes for this entry. Counting all module functions would incorrectly inflate its footprint.
- Aliasing can occur between different descriptor bindings, and physical pointers can refer to the same allocations. Distinct binding numbers do not prove independence.
- No original algorithm name or asset archive has been established.

The current result therefore supports a compile-only subgroup experiment, but **does not justify executing an unpinned variant as a supported correctness fix**.

## Required capture extension

A descriptor-only or logical-buffer-only capture schema must reject this target until it supports its physical-address contract. Capture must preserve the page-table root and entries, every reachable page allocation, original address relationships or a verified relocation scheme, read/write aliasing, relevant descriptor/image state, and the complete written outputs. Resource bytes must reflect the selected GPU command position, not later guest memory.

For each captured dispatch, inspect whether output-record ranges are disjoint across invocations and whether any reachable read may observe another invocation's writes. Repeat baseline outputs before defining floating-point tolerances. Compare integer metadata, indirect arguments and control counters exactly.

## Reproduce locally

Requires SPIRV-Tools `spirv-dis` and `spirv-val` plus Python 3.11+. The command is CPU-only and never creates a Vulkan device or executes shader code.

```sh
python3 tools/spirv_contract.py --spirv local/shader-target.spv \
  --expected-sha256 b9af52c0ba07f64eb8b0ca2905c432d094999fdf669aa95182ff7754b510e97b \
  > local/target-memory-contract.json
python3 -m unittest discover -s tests -p test_spirv_contract.py -v
```

The tool's pointer-origin tracking is intentionally partial. Unknown calls, pointer origins, atomic/copy operations remain explicit. An empty unknown-origin list is not a certificate of memory safety, temporal self-containment or resource completeness. Structural validation is performed for Vulkan 1.3 with uniform-buffer-standard-layout enabled, matching this target's known layout requirement.
