# Prepared E5 host for descriptor-array pointer compilation

Date: 2026-11-13. Status: CPU preparation complete and held for root review. **No Vulkan instance, device or pipeline has been created by this host preparation.** The active replay retained GPU ownership. This prepares the next compile-only checkpoint for the [validated array-pointer candidate](descriptor-array-pointer-experiment.md), not a rendering change or runtime correctness certificate.

`tools/descriptor_array_compile_host.py` imports only the Python standard library. It checks the frozen generator and semantic plan, both exact synthetic shader hashes, the exact E5 ICD and library bytes, and the unchanged base compute host. It derives a new private C host with checked source anchors, compiles with `-std=c11 -O2 -DNDEBUG -Wall -Wextra -Werror`, validates both shaders, and invokes only the native `--validate` path. That path returns before `vkCreateInstance`. The wrapper exposes preparation only and cannot launch the pipeline path.

The frozen value-return counterpart was produced by the independently authored generator before the pointer-wrapper rewrite. Both modules pass `spirv-val --target-env vulkan1.2`. They share the interval-record lookup, typed descriptor aliases, runtime typed-length diagnostics and separate physical fallbacks. This pair isolates pointer-return lowering within that synthetic design; it is not an unrolled native-descriptor-walk comparison.

## Exact profile

Python reflection requires one `GLCompute main`, literal local size 64×1×1, the expected physical addressing model/capability set, no workgroup/shared storage, six runtime typed-length queries, and an exact 48-byte push block with offsets 0, 16, 32, 40 and 44. It checks these descriptor declarations:

| Set/binding | Descriptor count | Data interpretation |
|---|---:|---|
| 0/0 | 2 | U32, U32x2 and U32x4 aliases |
| 0/1 | 2 | U32, U32x2 and U32x4 aliases |
| 0/2 | 1 | 32-byte interval records |
| 0/3 | 1 | 64-bit guest queries |
| 0/4 | 1 | U32x4 diagnostic results |

The six root alias declarations count as two descriptor bindings with two elements each, rather than twelve independent descriptors. Reflection records all nine storage-buffer variable declarations and merges only their identical binding/count layouts. It checks typed array strides and interval member offsets as well as the exact push layout. The native CPU guard independently checks the bounded header, stage/local size, runtime-length-query count, alias declaration counts, zero shared variables, fixed layout and required subgroup policy. Full type/stride reflection remains the wrapper's responsibility. Both shaders and all files must still match their hashes before a later launch.

The future native pipeline path requires subgroup size 32 and full subgroups. It queries and enables both `variablePointersStorageBuffer` and `shaderStorageBufferArrayNonUniformIndexing`, alongside the existing int64, device-address, uniform-buffer-layout, robust-buffer/robustness2 and subgroup profile. Unsupported features stop before device or pipeline creation; there is no uniform-indexing fallback. The broader `variablePointers` feature is not enabled. Executable-properties support is required, and the pipeline requests both statistics and internal representations. Device vendor/ID and relevant descriptor, push and compute limits must match the A770 profile. Actual robust storage/uniform bounds alignments are included in a later compile result.

The native host also refuses a compiler environment that differs from the 13-option `native-safe` policy. Exactly `HS_SPILL_BATCH=1`, `HS_PUSH_SHADOW=1` and `shader_spilling_rate=1` are set; `HS_CS32_MAX_INSTS` and the other nine controlled options are absent. An explicit `VK_DRIVER_FILES` is required. The wrapper pins E5 library SHA-256 `e5a1305ea3b423a18fccbb945aaedd32f8add303926fcc39276ee01032cd456f` and ICD SHA-256 `14f4eda1b6fd6cbf07d61edeeffdaaede166f4c09d165fec9f661e9f90556ad3` during preparation. These checks must be repeated at execution admission; the native binary alone does not hash the driver's bytes.

The future process creates one optimized compute pipeline. It contains no command-buffer, queue-retrieval, queue-submission, dispatch or idle-wait calls. Device creation still supplies a required queue-create declaration, but no queue is retrieved or used. A native 25-second alarm bounds the Vulkan path; a separately reviewed launcher must impose an outer process timeout no longer than 30 seconds. Internal-representation queries are bounded to 32 records, 16 MiB each and 32 MiB total; executable and statistic counts are bounded too. No such pipeline process was launched in this preparation.

## CPU evidence

Seven standard-library tests cover exact alias/count reflection, stage/local/count/stride/shared/push mismatches, optional-capability/variant mismatches, all 13 environment controls, checked host adaptation, absence of execution calls and wrong-driver rejection before any build or native launch. The prepared native `--validate` path accepted both exact modules and rejected private mutations for wrong stage, wrong local size, a workgroup variable and a 44-byte push range. These were CPU-only calls.

The compiler was GCC 16.2.1, and the validator was SPIRV-Tools v2026.1. The external build dependencies are a C compiler, Vulkan headers/loader/pkg-config and `spirv-val`; the unit tests require only Python 3.11+.

## Frozen handoff

The private preparation is `local/descriptor-array-host/final-review`. Its `prepared-plan.json` contains both full reflections, native CPU results, driver policy and all source/binary hashes. Pipeline authorization and feature-support-known remain false.

| Artifact | SHA-256 |
|---|---|
| New preparation wrapper | `f9ad859f39cb6b81f06d119001d0872da0c911f6deb51a0a63dd553f4eaf25d0` |
| Unchanged base C host | `69f5900a6993a0c9ab468fd85292f89f9d7405f7c0647c2383517d0daa8f81cc` |
| Derived private C host | `5bfa187279b3a99737de6f350d75c47e9c4fa333347c10cc9e5ce48d16c9a31b` |
| Optimized native host | `15b85f8de69f373cb4916ef00a32793ddb354e2722db5272f6ac8296c62daf53` |
| Prepared compile plan | `516960e3c9fa6f9eeea38839905496d8601646d2c4c701ecc023111db07a86e0` |
| Frozen generator | `0e4fc9d15438f078bf206e40d8838052a91a9e16af696f44cbb77a7b8a94838e` |
| Frozen semantic plan | `b162f6a20ed01da6835474f873b08a37cc4c74fd2183d92a262c52d081349f2f` |
| Pointer-return module | `5c2b9e427817f278e6a2ff8e348624c7ed85ae82298af2c53c4f064dae0ac248` |
| Value-return module | `a7c784c6f41f63666345a02e4a285d0e5e6e88a9d5f60435b70a2971f9bb1691` |
| Fixed native layout | `9021be527826b6cbc644983e5c9164941cba86ae0014bc6c7e527d6a7f5b49d3` |

CPU reproduction, with fresh private output and the already-generated frozen modules:

```sh
python3 -m unittest discover -s tests -p test_descriptor_array_compile_host.py -v
python3 tools/descriptor_array_compile_host.py \
  --output local/descriptor-array-host/repro \
  --driver "$E5_ICD_JSON" --pointer "$POINTER_SPV" --value "$VALUE_SPV"
```

This command prepares only. A later root grant and exclusive GPU lease must precede any pipeline call. Execution admission must rehash the prepared plan, wrapper, derived source, native binary, layout, both modules and exact E5 driver/ICD, establish the recorded environment and a fresh private cache, and enforce the 30-second outer bound. The saved native command arguments are a specification of the proposed call, not a self-contained execution approval or admission checker. No performance, executable-statistics or IR result is available yet.

Subsequent authorized checkpoint, 2026-11-13: both frozen modules compiled successfully on E5 at actual subgroup 32, with both pointer features enabled and zero spills. Value/pointer instruction counts were 2,121/1,918, with seven loops each. [Sanitized compile observation](descriptor-array-compile-observation.json) preserves exact artifact and IR hashes. No shader was dispatched. The earlier SIGXFSZ attempt remains a separate failed observation; its affected file is unknown, and interference with Mesa's 2 GB state-table memfd is an inference. Runtime aliasing, store-root correctness and native renderer integration remain untested.
