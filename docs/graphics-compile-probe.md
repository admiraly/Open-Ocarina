# Compile-only graphics diagnostics

The [graphics wrapper](../tools/graphics_compile_probe.py) and [native helper](../tools/vulkan_graphics_compile_probe.c) compile a complete mesh+fragment or task+mesh+fragment pipeline and retain executable stage masks and compiler statistics. They do not retrieve a queue, record commands, allocate shader resources, submit work or render. Logical-device creation includes the required queue-family declaration; no queue is retrieved or used.

`--prepare-only` validates and reflects every hash-pinned module, writes private snapshots/layout metadata, and builds the optimized helper without opening Vulkan. Device compilation requires an explicit driver ICD. All output inside this repository must be under ignored `local/`, `work/` or `artifacts/`, in a new output directory.

Each stage's original bytes are separately validated and compiled unchanged. Descriptor reflection reuses the compute reader through an in-memory metadata adapter; adapted words never reach validation or Vulkan. Compatible descriptor bindings across stages union their stage visibility. Conflicting types/counts, unsupported entry models, missing literal mesh/task local sizes, insufficient push ranges and malformed extents fail closed. The caller explicitly supplies push versus ordinary descriptor sets, push-range size and each stage's required subgroup size (`0` means no request). Full-subgroup flags remain off, matching the inspected native graphics builder's request shape.

The manifest's fixed state is an **explicit diagnostic**, not recovered runtime state. Supported scope is one color attachment, optional depth, dynamic rendering, one viewport/scissor, fill polygons, disabled blending/stencil/depth bias/depth clamp/raster discard and sample shading. The manifest supplies color/depth Vulkan enum values, sample count, extent, depth state, culling/front face, alpha-to-coverage and color write mask. The report retains all those fields. The helper checks format support, advertised sample counts, subgroup support, push-descriptor capacity and its required feature set before pipeline creation. Driver pipeline creation remains responsible for final compatibility; no validation-layer success or rendering correctness is inferred.

A manifest skeleton for the first target is below. The full SHA-256 values are listed in [the stage catalog](../research/graphics-targets.md); private module paths resolve relative to the manifest. Mesh/task stages should retain 32 for unmodified target modules. Narrow-width compilation of an unmodified module is not an equivalence test.

```json
{
  "schema": 1,
  "stages": [
    {"stage": "mesh", "path": "private-mesh.spv", "sha256": "FULL_EXPECTED_DIGEST", "required_subgroup_size": 32},
    {"stage": "fragment", "path": "private-fragment.spv", "sha256": "FULL_EXPECTED_DIGEST", "required_subgroup_size": 0}
  ],
  "push_constant_bytes": 88,
  "descriptor_mode": "push",
  "diagnostic_state": {
    "color_format": 37, "depth_format": 126, "samples": 1,
    "extent": [1536, 864], "depth_test": true, "depth_write": true,
    "depth_compare_op": 3, "cull_mode": 0, "front_face": 0,
    "alpha_to_coverage": false, "color_write_mask": 15
  }
}
```

These example formats are `R8G8B8A8_UNORM` and `D32_SFLOAT`; the profile's opaque format label does not establish those runtime formats. To add the second target, insert its task stage before mesh, keep the fragment last, and select `"descriptor_mode": "sets"`. Native-source layout inspection and module reflection establish 30 bindings/32 descriptors for `d8e59341cf4f629c`, versus 36 bindings/40 descriptors for `43ec86f35fd937b9`. The recorded A770 push limit is 32, so native layout selection uses push descriptors for the first and ordinary sets for the second. Per-stage reflected push usage is zero for the first and 56 bytes in the second's fragment stage; native pipeline layout still reserves 88 bytes for all included stage bits.

```sh
python3 tools/graphics_compile_probe.py --manifest local/graphics-manifest.json \
  --output local/graphics-prepared --prepare-only
python3 tools/graphics_compile_probe.py --manifest local/graphics-manifest.json \
  --output local/graphics-compiled --driver local/audited-driver.json \
  --mesa-build local/audited-mesa-build --timeout 180
python3 -m unittest tests.test_graphics_compile_probe -v
```

The helper builds with C11, `-O2 -DNDEBUG -Wall -Wextra -Werror`. It leaves shader optimization enabled and requests executable statistics when supported. It enables robust buffer access and robustness2, uniform-buffer standard layout, buffer-device address, integer/half-float features, graphics mesh features and dynamic rendering; exact policy and actual optional null-descriptor/statistics state are recorded. Optional Mesa build auditing verifies the selected driver library against build output, optimization/NDEBUG flags in both `brw_compile_mesh.cpp` (task and mesh) and `brw_compile_fs.cpp`, and relevant Meson options. No driver source or installed files are changed.

The default `--driver-policy diagnostic` deliberately clears the local compiler overrides. **This differs from the established runtime spill policy**. The explicit `--driver-policy runtime` option now sets all four recorded runtime values and records every controlled knob; see [the paired policy experiment](graphics-runtime-policy.md). The initial results below used the cleared profile. The batch-spill choice in the pinned local register allocator applies to graphics as well as compute. These initial diagnostic results match neither complete runtime fixed state nor complete runtime compiler options. Other inherited environment values were not fully snapshotted. The supplied ICD/build guard rejected the installed-library/build mismatch before opening Vulkan; the recorded runs instead use the matching development-build ICD and audited library.

The initial nine synthetic tests cover stage selection, shared-binding visibility and conflicts, explicit state/policy/hash requirements, push coverage, task pipelines, graphics compiler optimization auditing, preparation without device use and absence of execution entry points. The full suite passed 235 tests at that handoff. The named driver-policy extension adds two regression tests; its later full-suite result and frozen fingerprints are recorded in the paired experiment. External preparation compiled cleanly with the required warning flags, and all five original target modules passed SPIRV-Tools v2026.1 Vulkan1.3 validation with uniform-buffer-standard-layout enabled.

## A770 observations

[Sanitized evidence](../research/graphics-compile-observation.json) pins every module, driver, helper source/binary, wrapper, configuration and executable association. All five cases used the same frozen helper source SHA-256 `78da1aec2db969a606a10c85ee8fa80f347a2a53ed02b0f60eb43cc8260c9397`, binary `bf1a8a0d362ba67ad50c33605c06bccd12783925dac584f9c52e3ef40277f552`, wrapper `ce9d57931f593bfa866733fd686788937cd1397e482b36e586b79f00fa1843c3` and audited Mesa library `5ea19ac96aa52d061ecf2b8e23df7ed3d7d3b36c7d2ae7fe62f01c5a9b610aaa`. Required subgroup-stage mask was `0xe0`: task/mesh/compute, excluding fragment. The device advertises 256 mesh vertices/primitives, 65536 bytes mesh shared memory, 65504 bytes task payload and 131040 bytes combined mesh payload/shared memory. Advertised mesh/task maximum workgroup invocations are 1024.

| Diagnostic executable | SIMD | Instructions | Spills | Fills | Scratch bytes | Max live registers |
|---|---:|---:|---:|---:|---:|---:|
| original `d8…` mesh | 32 | 100640 | 4574 | 11351 | 32768 | 712 |
| original `d8…` fragment | 8 | 2588 | 43 | 96 | 2048 | 159 |
| original `43…` task | 32 | 7651 | 205 | 617 | 8192 | 228 |
| original `43…` mesh | 32 | 110305 | 4596 | 11355 | 32768 | 678 |
| original `43…` fragment | 8 | 2850 | 42 | 74 | 1024 | 159 |
| conditional `d8…` mesh | 8 | 16741 | 110 | 174 | 4096 | 195 |
| conditional `d8…` mesh | 16 | 26232 | 1066 | 966 | 16384 | 366 |
| conditional `d8…` mesh | 32 | 100794 | 4575 | 11373 | 32768 | 712 |

Both original graphics mesh executables exhibit substantial register pressure in this diagnostic policy. Their code sizes are 2050752 and 2188880 bytes. Fragment executable statistics remain identical across all `d8…` cases, with the same fragment module and fixed state. Shader compiler cycle estimates, spill/fill counts, code size and pipeline-creation wall time are not measured GPU execution time.

The conditional mesh module digest is `9d23b7ebf3677f53fe6901c9b94f2c4927f1f0c47ed207c04c53df9b59ae9638`, 44043 words. It replaces two ballots with separate Workgroup uint32[32] arrays and uniform barriers and rewrites/removes subgroup builtin use. Workgroup data grows from 14356 to 14612 bytes. **Lane mapping and equivalence are unverified, and runtime execution is unauthorized.** Narrow-width codegen shows a promising pressure reduction for this conditional construction; it does not establish a correct game shader, a speedup or image quality. Baseline and conditional32 staying close also helps isolate the width effect from the added Workgroup mechanism, without proving equivalence.

## Concrete pressure boundary

CPU-only inspection separates source complexity from compiler expansion. Static instruction counts include SPIR-V function/control instructions; they are not hardware instructions or dynamic work.

| Original nightly mesh | Reachable functions | Main instructions | Global helper call sites | Main Phi nodes | Driver loops |
|---|---:|---:|---:|---:|---:|
| `d8…` | 3 of 7 | 6726 | 72 | 92 | 72 |
| `43…` | 4 of 19 | 38689 | 77 | 708 | 77 |

`d8…` calls a 137-instruction scalar-read helper 11 times and a 137-instruction vector-read helper 61 times. Each helper repeats three explicit first-match storage descriptor claims and a loop over three table entries, including four `OpArrayLength` sites. `43…` calls its three helpers 12, 61 and 4 times; each is 164–165 instructions, with five length-query sites and one lookup loop. **The reported driver loop counts exactly equal these call-site counts.** This is strong evidence consistent with helper inlining and lookup replication, rather than evidence that native mesh export loops explain all code growth. Driver IR/ISA was not captured, so that inference remains bounded.

Neither mesh module contains physical-pointer conversion instructions. Zero reflected push usage in `d8…` rules out a physical guest-page-table fallback for this module; it does **not** rule out global-address translation through the bound storage descriptors/table. Those descriptor walks are present and repeated. The [binding-preserving interval design](../research/radical-address-lowering.md) therefore has a concrete graphics application: preserve first-match priority, aligned base/bound-length behavior, wide-access robustness and descriptor aliasing while replacing repeated claim resolution. An interval hit must keep the original StorageBuffer binding and offset. An unconditional physical pointer or page-table substitution would change the contract.

The mesh emitter is another bounded source-owned target. In `d8…`, initialization contributes 458 static instructions/113 dynamic Workgroup access chains; the translated body contributes 5336/106 and 72 helper calls; export after the final barrier contributes 932/120 and no helper calls. Corresponding `43…` counts are 298/73, 37786/66 and 605/78, with all 77 calls in the body. The source emitter unrolls clearing and output export across its attribute/map arrays in 32-word blocks. These numbers distinguish the export cost from the much larger body/replicated lookup cost. Shared storage is also material, but the counts do not prove which part causes spilling.

The next semantics-preserving boundary is **descriptor claim resolution in the mesh body**, with emit-time/range specializations grounded in as-submitted descriptor data. For `d8…` mesh, a shader-level replay may avoid the physical-allocation closure needed by the current compute target: its reachable accesses stay in the declared logical storage/uniform descriptors. It still needs complete buffer ranges/contents, aliases, binding/table words, uniform data, draw arguments, invocation mapping and outputs; this has not been captured. A full scene comparison additionally needs the fragment/texture state and incoming attachments. Do not assume uniform claim bases are immutable and hoist them across potential writes without that resource proof.

A focused next experiment can retain original32, capture those mesh-stage inputs, and compare an interval-based resolver against all original descriptor reads and generated mesh outputs. Keep export clearing/clamping unchanged initially. Independently, inspect driver IR around the repeated helper loops and live ranges; executable statistics alone cannot certify which lowering produces the spill explosion. Reducing the conditional width remains a separate path that first needs a lane-mapping proof and complete mesh-output equivalence.
