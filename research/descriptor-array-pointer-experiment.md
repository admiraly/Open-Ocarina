# Typed pointers through descriptor arrays: validated, not device-tested

Date: 2026-11-13. This independently authored synthetic candidate assembles and passes `spirv-val --target-env vulkan1.2`. **No Vulkan instance, device or pipeline was created, and no shader was dispatched.** The active runtime capture was left alone. This extends the [two-static-root experiment](storage-pointer-compile-experiment.md); its earlier A770 pipeline result does not establish support for this candidate's additional feature.

## What the module contains

There are two actual storage-buffer descriptor-array bindings, each with two elements. Each binding has U32, U32x2 and U32x4 typed aliases, with element strides 4, 8 and 16. A lookup helper for each width returns its typed `StorageBuffer` pointer from the selected binding and descriptor-array element, or logical null. Callers compare the pointer with null, then perform the typed storage load/store or use a separate physical miss function. No descriptor hit is converted to physical memory.

The module declares `VariablePointersStorageBuffer`, `ShaderNonUniform`, `StorageBufferArrayNonUniformIndexing`, `PhysicalStorageBufferAddresses` and `Int64`, besides `Shader`. It has three typed storage-pointer-returning functions, 21 `NonUniform` decorations, six runtime `OpArrayLength` instructions and 3,706 SPIR-V words. The array lengths are read through each typed alias and written into the diagnostic result. Interval selection uses the separately supplied per-width record; it does **not** recompute the native descriptor walk or certify that uploaded lengths match current bindings.

The selector's descriptor index can vary across invocations. The generated access-chain pointers, descriptor index and pointer-return call results therefore carry `NonUniform`; the source also declares the storage-buffer nonuniform-indexing capability. `shaderStorageBufferArrayDynamicIndexing` alone would not justify these accesses. The Vulkan SPIR-V environment requires dynamically uniform storage-buffer selection unless the nonuniform capability is declared. [Vulkan SPIR-V environment](https://docs.vulkan.org/spec/latest/appendices/spirvenv.html). Variable storage-buffer pointers and nonuniform descriptor indexing are separate feature decisions; the former being available does not establish the latter. [Variable pointers](https://docs.vulkan.org/refpages/latest/refpages/source/VK_KHR_variable_pointers.html).

Descriptor-array length is fixed at two; the unsized arrays inside the buffers are ordinary runtime data arrays. The module does not request runtime descriptor arrays, variable descriptor counts, partially bound descriptors or the broader `VariablePointers` capability. Every descriptor element must be bound in a later experiment. No `Restrict` decoration or qualifier is introduced. Preserving roots and removing restrict assumptions is necessary for aliasing, but validation cannot demonstrate GPU alias behavior.

The physical fallback retains two synthetic windows matching the inspected address-window layout, null-root/null-page rejection, the original operand's page-fit check and the follow-stores gate. Physical data accesses retain `Aligned 4`, including vectors; table entries use `Aligned 8`. The generated entry point masks queries to four-byte alignment. Arbitrary unaligned physical operands are not established as GPU-valid by the CPU tests.

## Per-width integer model

`tools/descriptor_array_pointer_experiment.py` imports only the Python standard library. It contains a direct CPU walk and a separate partition builder/binary-search lookup. Both consume **already-classified descriptors in source traversal order**. Classification, descriptor creation, native buffer alignment and capture are outside this experiment.

The partition builder preserves these inspected operations:

- `OpArrayLength` counts whole typed elements; the count and its byte-size shift use uint32 arithmetic, including wrap, before conversion to uint64.
- Aligned base plus the typed byte extent and original guest plus the size word use uint64 wrapping arithmetic. A wrapped endpoint below the base produces no hit under the original unsigned comparisons; it is not reinterpreted as a wrapping interval.
- Candidates use the typed bound, choose the nearest containing aligned base and retain source order for equal bases. They take priority over ordinary descriptors.
- Ordinary singles cap against the NVN end even for size word zero. An unaligned zero-size single can still claim its alignment prefix. Only table entries use the full typed bound for size word zero.
- A hit selects `(binding, descriptor element, typed byte offset)`. Its offset converts the relative address to uint32 **before** shifting to the typed element. It retains the original aligned descriptor base even after an overlapping interval is split.

Each width has its own disjoint partition. The frozen example has three scalar rows, three U32x2 rows and two U32x4 rows. Reusing the scalar selection and rejecting its short vector descriptor would lose the later vector hit; the vector partition instead selects that later descriptor directly.

The inspected source file `spirv_emit_context.cpp` had SHA-256 `0ed68de9355990aff67ed44774bfecfb01e2f249f9671eec0f14d0de94b72e4c`. The relevant operations are in `DefineGlobalMemoryFunctions`, including `define_body` and the typed load/store definitions. No source text from that file is emitted into this candidate.

## CPU evidence and limits

Fourteen synthetic tests pass. They compare logical pointer choices rather than equal payload bytes, including overlap, short bounds, unaligned guest operands, cross-page fallback, ordinary/table zero-word differences, nearest candidates and ties, uint32 typed-length/shift wrap, uint64 end wrap, both guest windows, null pages, store-miss gating and a descriptor snapshot mapped elsewhere by the current page table. A shared-memoryview store/read case checks that the chosen descriptor roots preserve an explicit CPU alias. A seeded boundary grid exercises another 48 small claim sets across all three widths.

These tests establish agreement within the stated integer model. They do not model hardware bounds rounding, whole-vector versus component-wise robustness, cross-invocation races, cache coherence, native descriptor classification or the pending page-table-update protocol. A supplied table represents the intended GPU table; the tool does not derive it from CPU mappings. Large wrapped-length cases are allocation-free arithmetic regressions, not assertions that a Vulkan descriptor of that size is valid on this device.

The shader-side `OpArrayLength` diagnostic is especially useful for a later complete resource replay: its observed typed lengths must agree with the captured ranges used to build the CPU records. A stale or mismatched record cannot be repaired by rejecting only its selected descriptor, because that can hide a later native match. The entire record must be rejected and the existing path retained.

## Frozen artifacts

The validated private run is `local/descriptor-array-pointer/final/report.json`. Generated GLSL, assembly, SPIR-V and packed records remain ignored. The public synthetic plan is [descriptor-array-pointer-plan.json](descriptor-array-pointer-plan.json).

| Artifact | SHA-256 |
|---|---|
| Python generator/model | `0e4fc9d15438f078bf206e40d8838052a91a9e16af696f44cbb77a7b8a94838e` |
| CPU test source | `a431ffaacdc503c790d7172f490f73d53bce160815ff672933cf597dd6bdfa4b` |
| Canonical public plan | `b162f6a20ed01da6835474f873b08a37cc4c74fd2183d92a262c52d081349f2f` |
| Generated GLSL | `e5c6c313b6c4350b3e9bb99836de385a408f61366d3d9345076e929548f06555` |
| Transformed assembly | `8436e44cd4a714a9ffc8dbcc8d770bd90c9474fe4240e20deed9c57f7c69bf00` |
| Validated SPIR-V | `5c2b9e427817f278e6a2ff8e348624c7ed85ae82298af2c53c4f064dae0ac248` |
| Packed interval rows | `49db0684fbdc406e487119d970667ca9a2d2075e02790abf4d44219d5457942e` |

Tool versions: glslang 16.2.0 and SPIRV-Tools v2026.1, dated 2026-02-16. External tools are needed only for the explicit generation/assembly/validation command; the unit tests use Python 3.11+ and no third-party packages.

```sh
python3 -m unittest discover -s tests -p test_descriptor_array_pointer_experiment.py -v
python3 tools/descriptor_array_pointer_experiment.py \
  --output local/descriptor-array-pointer/repro
```

The output directory must be fresh and resolve inside this repository's ignored `local`, `work` or `artifacts` directory. Tool subprocesses have bounded timeouts. The tool never loads Vulkan.

## Held next step

After the parent explicitly releases device use, a separate compile-only host should query and enable `variablePointersStorageBuffer` and `shaderStorageBufferArrayNonUniformIndexing`, plus the existing int64/device-address/robustness profile. Unknown or unsupported features must stop that candidate; silently falling back to dynamically uniform indexing changes its meaning. Fixed array counts, all alias declarations, descriptor limits, a 48-byte push range, runtime typed-length diagnostics and the exact frozen plan must be checked before pipeline creation.

Only then compare the generated interval/value and interval/pointer modules under the same optimized compiler controls and required subgroup policy. The prior two-static-root timings and instruction counts do not apply here. This module's loop is not yet marked `DontUnroll`; whether the driver retains, unrolls or duplicates it remains a compile-only question. Full replay, aliases, robustness, stores/atomics and rendering correctness remain later requirements before any renderer change. There is no performance or FPS result from this lane.
