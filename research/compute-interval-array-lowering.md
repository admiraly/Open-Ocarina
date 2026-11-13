# Actual compute interval hits through one descriptor array

Date: 2026-11-13. Status: a separate CPU-only candidate assembled and validated for the exact `b9af52…` compute module and frozen owned input graph. **No Vulkan instance, pipeline compilation, dispatch or replay was performed.** The [sanitized report](compute-interval-array-lowering.json) records the exact module, dependencies, unchanged records, binary preservation and capacity contract. This explores another compiler shape after the static eight-way route's mixed compiler statistics; it establishes no improvement in register allocation, GPU execution or FPS.

## Narrow change

`tools/compute_interval_array_lowering.py` hash-pins the unchanged `3176a7…` source analyzer/emitter and its two imports before loading them. It retains that emitter's width-specific binary search and the exact 592-byte interval record `49da4bd…`. Only the generated hit's eight-way switch is replaced with a value-returning access through a fixed array of eight storage-buffer descriptors at new set 0 binding 13. The record stays at binding 12.

Two binding-13 variable declarations interpret those same eight descriptors as U32 and U32x4 runtime arrays with strides 4 and 16 and member offset zero. The selected route is copied to a `NonUniform` uint32 ID, then used in an ordinary `OpAccessChain`; the access-chain pointer and loaded value are also decorated `NonUniform`. The helper returns the value. No pointer return, pointer phi, variable-pointer capability or physical hit conversion is introduced.

New capabilities are `ShaderNonUniform` and `StorageBufferArrayNonUniformIndexing`. The corresponding additional Vulkan feature is `shaderStorageBufferArrayNonUniformIndexing`; there is no separate Vulkan boolean named `shaderNonUniform`. Existing dynamic indexing alone would not admit this per-lane descriptor selection. The prior [four-case synthetic alias fixture](descriptor-array-runtime-observation.json) exercised this feature and aliased storage roots on E5, but did not test this actual module or its larger layout.

## Root and typed-address contract

| Array element | Exact original descriptor |
|---:|---|
| 0–4 | Binding 2–6, element 0 |
| 5–7 | Binding 7, element 0–2 |

Every new element must use the **same VkBuffer, offset, range and null state** as its original descriptor. The old binding-2 through binding-7 variables remain declared. No existing descriptor can be replaced by a copied equal-byte view. In particular, array element 4 must alias binding 6's existing storage view so loads observe the original direct stores. The alias variables, their types and members receive no new `NonWritable`, `Restrict` or restricted-pointer promises. The independent binding-12 record remains read-only.

The selected record retains its original aligned guest base even when an overlapping claim is split. Hit indexing remains `uint32(address - aligned_base) >> log2(width)`, applied to the correct typed stride. The unchanged source model distinguishes ordinary zero words from table-zero bounds, typed `OpArrayLength` floors, uint32 length/shift wrapping and uint64 endpoints. It resolves all root-selection cells for widths 4 and 16 independently. On this exact frozen record there are six rows per width, thirteen complete uint64 selection cells and at most three search iterations; the generic reviewed cap remains 64 rows/seven iterations. The emitted recurrence is re-read to verify strict `high=mid` or `low=mid+1` progress and absence of other cycles.

This is an identity/typed-index proof within the supplied owned snapshot. It does not establish live selector atomicity, future record freshness, Vulkan handle equality or a new memory-ordering theorem. The emitted alias plan specifies handle identity as a future host obligation; it is not a runtime admission or an implementation of that obligation.

## Preservation and validation

Both modified functions are still the original scalar/vector load functions. The other five function bodies remain identical in binary instruction words, including main. Both original physical MISS suffixes remain exactly 166 words. All original globals, capabilities, types, execution modes and decorations remain unchanged; main's interface gains only the record and two array variables. Reflection still reaches 56 push bytes; the actual preserved control/layout range is 88 bytes, with page-root offset 48. All 16 direct main-store sites remain on binding 6, including its scalar, vector2 and vector4 aliases.

The only additional global capabilities are the two nonuniform capabilities. The private assembly was rebuilt with preserved numeric IDs and passes `spirv-val --target-env vulkan1.3 --uniform-buffer-standard-layout`. Nine independent synthetic tests cover alias identity/nulls, typed flooring and later vector selection, a direct old-root store observed through the declared array alias, required nonuniform decorations, forbidden alias promises, stride/offset changes, finite-search updates and binary-word mutations of main, MISS and original globals. Authored scalar/vector helper fixtures also assemble and validate; no original instructions are embedded in the tests.

Reflection reports the unchanged original fourteen descriptors plus one record descriptor and eight array elements: **23 total, 17 storage buffers**. Duplicate U32/U32x4 declarations at binding 13 do not allocate sixteen descriptors. Device capacity, push-descriptor/per-stage resource limits, null handling and the full required feature profile have not been queried for this layout. No existing replay ABI admits it, and this task adds none.

## Strongest objections before compilation

- The future host must actually preserve descriptor identity, including the two null table entries. A correct CPU route list cannot enforce a VkBuffer handle that has not been created.
- Nonuniform descriptor selection can add descriptor-addressing cost, divergent resource handling or different register pressure. A smaller SPIR-V hit body need not yield fewer SENDs or spills. The static-route result does not predict this candidate's ISA.
- The layout needs 23 descriptor elements, 17 storage-buffer slots and the original image/UBO/null requirements. Earlier smaller fixture support does not establish these limits.
- Keeping all hits in `StorageBuffer` and omitting restrict/read-only alias promises preserves the intended alias model, but the complete actual workload still needs frozen-output verification under the new binding arrangement. Prior synthetic alias results are bounded evidence, not a universal compiler guarantee.
- The immutable record is valid only for its exact descriptor-visible UBO/ranges and source classification. Neither this candidate nor its root-selection proof authorizes live renderer inputs, atomics, subword helpers or another shader.

These objections require later, separately reviewed compile-only capacity/feature checks and an appropriate owned replay contract. No compiler or replay result for this candidate exists yet.

## Frozen handoff

Private bundle: `local/compute-interval-array-lowering/frozen-review`. Its transformed SPIR-V/disassembly remain private.

| Artifact | SHA-256 |
|---|---|
| New tool | `d8c4a899e350408c52b6e1121ce74f244bc182f327b35007e4c7990ed214e13d` |
| Independent test source | `92c5d50ac9fa9eb5588db46a61975f3ad70286b74bc6cbef8dfb50de825ebb50` |
| Original module | `b9af52c0ba07f64eb8b0ca2905c432d094999fdf669aa95182ff7754b510e97b` |
| New array candidate, 255,856 bytes | `7c6322e1a7b4a84206977fd0a0af165b7dedeb0eb9cbd8fb7c211ec1af77d7bd` |
| Unchanged interval record | `49da4bdc0c4542b4fef7b5dcbb631d41fb030d80803cde3bb37185722daf9770` |
| Alias identity plan | `87556a394524d0e70acccbd9a9dd63c56ae6e40f97375e7d70af8a1ebb507c60` |

CPU reproduction with the exact original module and owned manifest:

```sh
python3 -m unittest discover -s tests -p test_compute_interval_array_lowering.py -v
python3 tools/compute_interval_array_lowering.py \
  --spirv "$ORIGINAL_COMPUTE_SPV" --manifest "$FROZEN_OWNED_MANIFEST" \
  --output local/compute-interval-array-lowering/repro
```

The tools require Python 3.11+ and SPIRV-Tools; validation used v2026.1. The frozen dependency, original module and manifest hashes must match. No old tool, source plan, vendor file or replay ABI is modified.
