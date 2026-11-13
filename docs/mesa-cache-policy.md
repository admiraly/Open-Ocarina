# Private Mesa compiler policy and cache identity

The audited private Mesa 26.2.4 driver can reuse compiled shaders across different custom compiler settings. The existing key distinguishes only two settings, only for compute, and contains deterministic framing collisions. Copying an existing warm cache therefore does not establish the actual SIMD width or compiler policy of a renderer pipeline.

This finding applies to the private driver library SHA-256 `5ea19ac96aa52d061ecf2b8e23df7ed3d7d3b36c7d2ae7fe62f01c5a9b610aaa`, ELF build ID `cd4f4d939f9bb235a18a1c058ad3ae2363c17cde`. The source checker pins the three modified files and twelve relevant compiler/runtime/cache readers. The build-tree library and selected index-fix library had the same SHA-256. An inspection of the library's `vk_pipeline_hash_shader_stage_blake3` also confirmed its compute-only branch. The existing installed driver, live source tree and caches were not changed during this audit.

## Concrete source findings

Locations below refer to the pinned private source before this proposed patch.

| Source function / location | Observed behavior |
|---|---|
| `brw_required_dispatch_width`, `src/intel/compiler/brw/brw_simd_selection.cpp:16` | Reads `HS_FORCE_SIMD8` before honoring a fixed subgroup width. Depending on its value and wide-subgroup use, it can return 8 for compute/task/mesh whose API request was 32. |
| `brw_compile_cs`, `src/intel/compiler/brw/brw_compile_cs.cpp:119` | Presence of `HS_CS32_MAX_INSTS` enables a second compilation attempt at width 8 after a required-32 allocation failure. |
| `brw_reg_alloc::assign_regs`, `src/intel/compiler/brw/brw_reg_allocate.cpp:1393` | `HS_CS32_MAX_INSTS`, `HS_SPILL_PCT`, `HS_SPILL_BATCH` and effective `compiler->spilling_rate` change allocation/spill behavior. |
| `hs_large_grf`, `src/intel/compiler/brw/brw_reg.h:43` | `HS_LARGE_GRF` changes the available compiler register count; it is also read by register validation. |
| `vk_set_subgroup_size`, `src/vulkan/runtime/vk_pipeline.c:126` | `HS_SUBGROUP8` replaces the required subgroup size for compute/task/mesh. Physical-device properties also read it. |
| `vk_pipeline_hash_shader_stage_blake3`, `src/vulkan/runtime/vk_pipeline.c:244` | Hashes raw `HS_FORCE_SIMD8` and `HS_CS32_MAX_INSTS` only for compute. It omits the other settings and returns early for builtin NIR before that block. |
| `vk_pipeline_hash_precomp_shader_stage`, `src/vulkan/runtime/vk_pipeline.c:927` | Uses that stage digest as the precompile cache key. The compute and graphics builders incorporate it into final shader keys. |
| `anv_shader_hash_state`, `src/intel/vulkan/anv_shader_compile.c:738` | Adds ordinary stage state but omits the custom policy and effective spilling rate. |
| `anv_physical_device_init_uuids`, `src/intel/vulkan/anv_physical_device.c:2551` | Pipeline-cache UUID hashes build/device/bindless state, without the custom compiler settings. |
| `anv_physical_device_init_disk_cache`, `src/intel/vulkan/anv_physical_device.c:2591` | Disk namespace derives from the driver build; flags come from `brw_get_compiler_config_value`. That function includes selected standard debug/SIMD controls, but omits the custom HS settings and spilling rate. |
| `anv_shader_init_uuid`, `src/intel/vulkan/anv_shader_compile.c:225` | Public shader-binary UUID includes effective spilling rate but omits the custom HS settings. This UUID alone does not protect ordinary pipeline cache objects. |
| `vk_pipeline_shader_deserialize`, `src/vulkan/runtime/vk_pipeline.c:549` | Calls the driver's raw deserializer directly. `anv_shader_deserialize` reads stage/code/program data without the public shader UUID header. |
| `vk_pipeline_cache_lookup_object`, `src/vulkan/runtime/vk_pipeline_cache.c:336` | Looks up the object key and then the disk key. `disk_cache_compute_key`, `src/util/disk_cache.c:684`, hashes the existing driver-key blob plus that object key; it does not independently discover custom environment settings. |

The old compute encoding has three concrete collisions:

- Absent `HS_FORCE_SIMD8` hashes the literal text `none`. Present `HS_FORCE_SIMD8=none` hashes the same bytes, although presence can force width 8 for a nonwide shader.
- Absent and empty `HS_CS32_MAX_INSTS` append the same bytes. Presence nevertheless changes whether fallback is enabled.
- `HS_FORCE_SIMD8=cs` plus `HS_CS32_MAX_INSTS=1` appends the same bytes as `HS_FORCE_SIMD8=cs1` with MAX absent. The settings have different width-selection behavior for wide compute shaders.

For task and mesh, the legacy hash contains neither value at all. Requested API width remains in the stage key, but an override can change the actual compiled width while retaining that same API request. Different driver builds normally have different namespaces; finding an old cache directory alone does not prove a wrong cache hit. The risk concerns entries from the same driver build and matching ordinary keys under unrecorded or changed compiler settings.

## Proposed identity-only patch

[`hs-compiler-cache-policy.patch`](../patches/mesa/hs-compiler-cache-policy.patch) is an MIT-licensed source proposal. It has passed CPU checks against private copies of the pinned source files. An isolated driver build and the bounded validation below have now completed. It does not alter the existing override semantics or make forced width changes conform to an API request.

One production encoder, `hs_compiler_options_blake3_update`, is used consistently for every shader stage, including builtin NIR; ANV shader-state keys; pipeline-cache UUID; disk-cache namespace; and public shader-binary UUID. It replaces the old compute-only special case. A versioned domain, scope, option name, presence byte and fixed-width little-endian byte length frame every value. Missing, empty and literal `none` remain distinct. Raw numeric spellings remain conservatively distinct even when a parser might give them the same value.

| Included setting | Why it affects compiled identity |
|---|---|
| `HS_FORCE_SIMD8` | Backend dispatch width selection, including task and mesh. |
| `HS_CS32_MAX_INSTS` | Allocation threshold and presence-controlled compute fallback. |
| `HS_SPILL_BATCH` | Batch selection of registers to spill. |
| `HS_SPILL_PCT` | Minimum spill batch size for large shaders. |
| `HS_LARGE_GRF` | Register file assumptions and allocation. |
| `HS_SUBGROUP8` | NIR subgroup bounds and reported subgroup limits. |
| `shader_spilling_rate` | Startup override spelling; ANV identities also include the effective integer from drirc. |

The effective encoder adds `brw_get_compiler_config_value()` and `compiler->spilling_rate` to ANV state, both UUIDs and the disk namespace. Including the effective value matters because application-specific drirc configuration can differ while the environment spelling is identical. All stages conservatively include all seven raw settings; harmless extra cache misses are preferable to omitted dependencies.

The new versioned domain deliberately separates even the all-unset policy from legacy entries. New pipeline/disk identities cannot inherit the old ambiguous namespace. Existing caches can remain preserved as historical artifacts; their contents do not become validation evidence for the new driver.

Three controls are excluded from the compiled-program policy:

| Excluded setting | Source-based reason and remaining scope |
|---|---|
| `HS_RA_DEBUG` | Its compiler readers print diagnostics. They do not select the allocation algorithm or rewrite shader code. Logging still affects measurement conditions. |
| `HS_PUSH_SHADOW` | `hs_push_shadow_wanted` in `anv_cmd_buffer.c` selects host-side push-descriptor storage/update handling. No compiler reader consumes it. Resource/runtime correctness must still record this setting. |
| `HS_LARGE_GRF_BIT` | `genX_init_state.c` writes the hardware `STATE_COMPUTE_MODE` bit. This is runtime hardware state, separate from the `HS_LARGE_GRF` compiler setting. Cache identity cannot prove those two settings form a valid hardware configuration. |

All HS options must be selected before Mesa initialization and remain immutable for the process lifetime. Existing compiler code caches several `getenv` results in function-local statics; this patch does not implement safe dynamic environment reconfiguration. A future codegen control must join the inventory and its regression tests. Changes to the pinned reader sources require a fresh audit.

## Game-free CPU validation

```sh
python3 patches/mesa/check_hs_compiler_cache_policy.py
python3 patches/mesa/check_hs_compiler_cache_policy.py \
  --source path/to/private-mesa-26.2.4
python3 -m unittest discover -s tests -p test_hs_compiler_cache_policy.py -v
```

The native tests compile the **exact added production header** with `-O2 -DNDEBUG -Wall -Wextra -Werror`. A tracing shim records the byte stream sent to BLAKE3; the tests validate the encoder, rather than reimplementing or claiming to retest the cryptographic algorithm. Device, compute, task and mesh scopes exercise every codegen knob, missing/empty/sentinel states, concatenation ambiguity, effective drirc/debug values, long UTF-8 values and excluded runtime/logging settings. No game files, Vulkan instance or GPU work are required.

With `--source`, the checker verifies all baseline/reader hashes, copies three source files into temporary storage, applies the patch there, audits the actual production call sites and verifies that the real source files remain unchanged. The candidate's source SHA-256 values are reported for review. No installed library or build output is overwritten.

A copied warm cache used for a renderer startup pilot remains a startup diagnostic. The observer's requested width cannot establish actual executable SIMD. No FPS, native-quality or rendering-equivalence certification follows from that pilot. Each driver build requires its own frozen binary and fresh observations; prior driver evidence cannot be relabeled as testing a new library.

## Isolated build and bounded runtime observations

The patch was built from a new full source copy using the audited Mesa options. Only the private install prefix differs. The real compiler commands use `-O2 -DNDEBUG -g`, with `debugoptimized`, optimization 2, assertions disabled and at most four workers. The original source/build inventories and audited driver/ICD remained byte-identical; compiler and shader caches were isolated.

The new library SHA-256 is `e5a1305ea3b423a18fccbb945aaedd32f8add303926fcc39276ee01032cd456f`, ELF build ID `05fee58a93a8230fbc4d5e80fc4bab875832695e`; its private ICD SHA-256 is `14f4eda1b6fd6cbf07d61edeeffdaaede166f4c09d165fec9f661e9f90556ad3`. Physical-device queries reported Intel Arc A770 (`8086:56a0`), Mesa 26.2.4 and Vulkan **1.4.354**. This version was queried from the new library rather than inferred from the ICD declaration.

Eighteen fresh processes performed physical-device/property queries only: no logical devices, shaders, pipelines, queues or dispatches. Fourteen distinct compiler profiles produced fourteen distinct pipeline-cache and shader-binary UUIDs. Repeated profiles and changes to the excluded runtime/logging controls retained their UUIDs. The cases include absent/empty/literal `none`, absent/empty/zero MAX, the old adjacent-value collision and every included compiler knob. These query tests establish startup UUID separation; they do not directly exercise every per-stage disk-cache hit/miss path.

The safe3 property profile keeps `HS_CS32_MAX_INSTS`, `HS_FORCE_SIMD8`, `HS_SUBGROUP8`, spill-percentage and large-GRF overrides absent. It uses `HS_SPILL_BATCH=1`, `shader_spilling_rate=1` and runtime `HS_PUSH_SHADOW=1`. Its measured subgroup range is 8–32, and the fixture's required features/extensions were available. Its pipeline-cache UUID is `70506e8d5b5adf7368e0030d12d8ce2e`.

The unchanged physical replay wrapper keeps the same compiler profile and runtime SHADOW absent, as in the historical authored-fixture proof. On the new driver, exactly one authored width-32 dispatch matched both complete 4096-byte CPU oracles. It was followed, under a separate authorization, by the fixed original compute graph:

| Case on new driver | Actual subgroup width | Complete outputs checked |
|---|---|---|
| Baseline | 32 | All 59 allocations saved and rehashed |
| Repeat | 32 | All 59 allocations match baseline |
| Alternative | 16 | All 59 allocations match baseline |
| Alternative | 8 | All 59 allocations match baseline |

Each case covers 1,163,919,360 complete buffer bytes, including padding and nominally read-only allocations. All four new-driver cases also match the historical old-driver baseline's complete file hashes. Those cross-driver checks are explicitly CPU-only byte-hash comparisons across different binaries; they are not performance comparisons and do not bypass the ordinary verifier's same-driver restriction.

The new writable 4 MiB allocation again matches captured AFTER exactly. Complete captured-AFTER agreement is 56/59 allocations: three live-import inputs remain equal to frozen initial bytes but differ from captured AFTER. Historical CPU/temporal atomicity and original device-state equivalence remain unproven. Width 32 was repeated once; widths 16/8 were each tested once on this one graph.

The sanitized [new-driver observation](../research/mesa-cache-policy-observation.json) pins source, build, driver, property helper, plans, output graphs and each comparison artifact. Historical [old-driver observations](../research/physical-replay-observation.json) remain unchanged. A separate authored mesh mapping observation, reported by the primary agent after this agent released its lease, also passed on the new driver: one 32-invocation workgroup wrote all 224 expected words `(lane, 0, 0, lane, lane, 32, 0)`, with actual width 32, no emitted vertices/primitives and no attachments. Its helper/source/output hashes are recorded separately; this is not a game-derived mesh equivalence result.

This record includes no game-derived native-mesh, live-renderer, native-image-quality, GPU-time, speedup or FPS validation. GPU leases were released after the authorized work.
