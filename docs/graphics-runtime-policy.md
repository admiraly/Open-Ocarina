# Historical V1 graphics codegen under the MAX-present profile

This is a historical V1 experiment. Its `runtime` profile includes `HS_CS32_MAX_INSTS=100000`, which can enable a global compute-width fallback even when set to0. Current native launches require MAX strictly absent and the three-option `native-safe` profile; see [revision2 hardening](native-mesh-warp32-integration.md). Recorded metrics and identities below remain unchanged and are not V2 runtime validation.

The recorded runtime compiler overrides materially change graphics register allocation, especially the conditional SIMD16 candidate. They do **not** remove the large conditional SIMD8 codegen difference. This is a paired compile-only experiment, with no shader execution, output comparison or FPS claim.

[Runtime identity observations](../research/runtime-graphics-identity-observation.json) now establish the exact original stage digests and subgroup requests at 256 selected mesh command sites. Both programs use color format122 (`B10G11R11_UFLOAT_PACK32`), depth126 (`D32_SFLOAT`), one sample and 1536x864 attachments. This identifies actual shaders/attachments in that recording; it does not retroactively identify earlier timing-table variants or capture complete effective dynamic state.

The [new evidence](../research/graphics-runtime-policy-observation.json) retains ten fresh cases: original `d8…`, original `43…` and conditional `d8…` mesh widths8/16/32, each compiled under both named policies. Original reports and artifacts from the [first graphics experiment](graphics-compile-probe.md) remain unchanged. Every case uses identical fixed state within this matrix, the same stage/layout configuration except for the intentional conditional module/width changes, the same optimized helper and audited Mesa library, and a separate empty shader-cache directory. Separate helper processes ensure environment-dependent driver initialization starts under the selected policy.

## Controlled policies

At this historical handoff the wrapper accepted `--driver-policy diagnostic` (cleared profile) or `--driver-policy runtime` (legacy MAX-present). The current wrapper additionally supports `native-safe`; the table below records the old experiment, not a native launch recommendation:

| Controlled option | Diagnostic | Runtime |
|---|---|---|
| `HS_CS32_MAX_INSTS` | unset | `100000` |
| `HS_SPILL_BATCH` | unset | `1` |
| `HS_PUSH_SHADOW` | unset | `1` |
| `shader_spilling_rate` | unset | `1` |
| `INTEL_DEBUG` | unset | unset |
| `HS_SPILL_PCT` | unset | unset |
| `HS_RA_DEBUG` | unset | unset |
| `HS_FORCE_SIMD8` | unset | unset |
| `HS_LARGE_GRF` | unset | unset |

These values are applied explicitly, replacing ambient values. The report records all nine, using JSON null for unset. Other process environment values are not a complete snapshot. The four runtime values agree with the retained launch configuration of the identity run. This experiment changes them as a group, so it cannot attribute a change to one override individually. In particular, a policy named runtime establishes those four compiler settings, not a recreation of every runtime pipeline feature or state.

```sh
python3 tools/graphics_compile_probe.py --manifest local/graphics-manifest.json \
  --output local/runtime-policy-compile --driver local/audited-driver.json \
  --mesa-build local/audited-mesa-build --driver-policy runtime --timeout 180
```

## State conversion and remaining differences

Retained NVN values were consistent across both selected programs: polygon2, cull0, front-face1, blend disabled, depth test enabled, depth write disabled, depth comparison3, depth clamp/alpha-to-coverage disabled. Channel masks were15 once and `0xffffffff`127 times per program; both have low nibble15. Native `vk_draw.cpp` conversion maps polygon2 to fill, cull0 to none, NVN front-face1 to Vulkan counterclockwise0, and comparison3 to Vulkan EQUAL2. `target_write_mask` additionally intersects the channel nibble with fragment-output component metadata; that effective mask was not captured here.

The new diagnostic therefore uses the observed color122/depth126/sample1/extent, depth-test on, depth-write off, raw-converted EQUAL2, fill/no cull/counterclockwise, alpha-to-coverage off and write mask15. Blending, stencil, depth clamp, depth bias and sample shading remain disabled in the helper.

This is deliberately incomplete runtime state. On Intel, native source normally changes a read-only EQUAL depth test to LESS_OR_EQUAL and adds negative depth bias64, unless its override disables or changes that adjustment. The retained identity event does not establish the effective bias or comparison. The diagnostic keeps EQUAL and no bias. The native renderer uses a negative-height viewport and dynamically supplied viewport/scissor, depth/raster/blend state; the helper uses its explicit positive-height full-attachment viewport/scissor and static state. Raster discard, actual viewport/depth range, scissor, depth bias, final component mask and complete effective dynamic state were not observed. The helper also creates one complete pipeline rather than reproducing the runtime's possible pipeline-library path. These limits prevent a full runtime-matching claim even with the runtime compiler policy.

## Paired results

All numbers are compiler executable statistics. Scratch is bytes; spill/fill and instruction counts are static compiler results, not measured dynamic operations.

| Mesh case | Policy | SIMD | Instructions | Spills | Fills | Scratch |
|---|---|---:|---:|---:|---:|---:|
| original `d8…` | diagnostic | 32 | 100640 | 4574 | 11351 | 32768 |
| original `d8…` | runtime | 32 | 102162 | 4899 | 11320 | 32768 |
| original `43…` | diagnostic | 32 | 110305 | 4596 | 11355 | 32768 |
| original `43…` | runtime | 32 | 113872 | 4955 | 11751 | 32768 |
| conditional `d8…` | diagnostic | 8 | 16741 | 110 | 174 | 4096 |
| conditional `d8…` | runtime | 8 | 17981 | 284 | 258 | 4096 |
| conditional `d8…` | diagnostic | 16 | 26232 | 1066 | 966 | 16384 |
| conditional `d8…` | runtime | 16 | 44319 | 1854 | 4068 | 16384 |
| conditional `d8…` | diagnostic | 32 | 100794 | 4575 | 11373 | 32768 |
| conditional `d8…` | runtime | 32 | 102367 | 4899 | 11335 | 32768 |

The original mesh stages remain heavily spilled under the recorded policy. The conditional16 result changes substantially: 26232→44319 instructions and966→4068 fills. The previous cleared-policy numbers therefore overstated its codegen reduction relative to runtime settings. Conditional8 still has17981 instructions and4096-byte scratch, compared with original32's102162 instructions and32768-byte scratch under the same policy. Conditional32 remains close to original32. These comparisons preserve an interesting conditional SIMD8 pressure reduction while showing why exact compiler options matter.

The policy also improves the original `43…` task executable: 7651→6613 instructions,205→147 spills,617→431 fills and8192→4096-byte scratch. Fragment results change in the opposite direction: `d8…`2588→2629 instructions and43→46 spills; `43…`2850→3071 instructions,42→68 spills and1024→2048-byte scratch. Fragments remain SIMD8, and each `d8…` fragment's statistics agree across all its original/conditional cases within each policy. These stage differences rule out treating the policy as one universal improvement or attributing an entire pass's time to the mesh executable.

The new cleared-policy results reproduce the earlier selected executable statistics despite the updated attachment/depth diagnostic. That controls those state changes for this comparison; it does not establish identical generated machine code or a complete state match. Pipeline creation times in the JSON are host wall times for one compile per case; they are not repeated performance measurements, and the concurrent CPU-only renderer build makes host-time comparisons particularly unsuitable.

The conditional module remains SHA-256 `9d23b7ebf3677f53fe6901c9b94f2c4927f1f0c47ed207c04c53df9b59ae9638`. Lane mapping and equivalence remain unverified, and real execution of that module remains unauthorized. None of these results changes that boundary. Original runtime mesh-output/resource capture and a valid lane-mapping/output proof are still required before a narrower transformed shader can be considered correct.

## Frozen implementation and checks

All ten cases pin wrapper SHA-256 `babe4464a339812d6dd980cd851617732b441338c09ff682195f4e89a6ae2b55`, unchanged helper source `78da1aec2db969a606a10c85ee8fa80f347a2a53ed02b0f60eb43cc8260c9397`, helper binary `bf1a8a0d362ba67ad50c33605c06bccd12783925dac584f9c52e3ef40277f552` and Mesa library `5ea19ac96aa52d061ecf2b8e23df7ed3d7d3b36c7d2ae7fe62f01c5a9b610aaa`. The helper was rebuilt cleanly with C11/O2/NDEBUG and strict warnings for every fresh case. Driver mesh/task and fragment compiler commands remain audited O2/NDEBUG. All modules passed Vulkan1.3 SPIR-V validation with uniform-buffer-standard-layout enabled.

Two added synthetic regressions verify that poisoned ambient compiler values cannot leak into either policy and that unknown policy names fail closed. All11 graphics-wrapper tests and the full267-test suite passed. The C helper's no-execution source is unchanged: no queue retrieval, command recording, resource allocation or submission was added. The exclusive device lease was released after the tenth compile-only case; no game or shader was executed by this experiment.
