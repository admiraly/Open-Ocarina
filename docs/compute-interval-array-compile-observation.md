# Common23 compute compiler observation

Three separately granted compile-only calls completed on the E5 driver: original `b9af52…`, static interval `d51483…` and nonuniform array interval `7c6322…`. Each created one optimized compute pipeline with actual subgroup 32 under the same 23-element layout and physical two-control policy. No captured resources or command buffers were created; no queue was retrieved or submitted, and no dispatch was recorded. The earlier [CPU preparation](compute-interval-array-compile.md) remains preparation evidence; this is a separate observation.

The [sanitized observation](../research/compute-interval-array-compile-observation.json) retains all sixteen compiler statistics, including their descriptions, value formats and source hashes, for every case. It pins the reviewed runner, native source/helper/diff, common profile, individual review plans, terminal receipts, exact E5 library/ICD and source ledger. All 131 post-terminal file hashes matched in each case. Each call used a fresh isolated empty writable cache with no import or reuse. No child resource limits were overridden.

| Compiler statistic | Original | Static interval | Array interval |
|---|---:|---:|---:|
| Instructions | 144,485 | 128,800 | 89,834 |
| Code bytes | 2,889,872 | 2,557,616 | 1,807,200 |
| SENDs | 1,559 | 1,981 | 1,514 |
| Loops | 72 | 72 | 72 |
| Estimated cycles | 3,821,821 | 2,900,285 | 2,568,049 |
| Spills | 6,690 | 9,257 | 4,134 |
| Fills | 14,943 | 10,178 | 9,032 |
| Scratch bytes | 32,768 | 32,768 | 32,768 |

These are source compiler statistics. Estimated cycles are not measured GPU cycles. Host process wall times (14.345, 15.115 and 7.400 seconds) include compilation and executable queries; they are not GPU execution times. The array shape has fewer compiler-reported instructions, SENDs, spills and fills here, while scratch remains unchanged. This does not establish runtime speed, output equivalence, rendering fidelity or FPS.

All three helpers required the common capacity thresholds before logical-device creation: 23 push descriptors and per-stage resources, seventeen per-stage/set storage-buffer descriptors, two UBOs, four sampled images, four samplers, null descriptors and the original feature profile. `shaderStorageBufferArrayNonUniformIndexing` was queried and enabled even for the original/static modules; no variable-pointer feature was requested. The result establishes that these thresholds passed, not the device's exact maximum values. Both `HS_SPILL_BATCH` and `shader_spilling_rate` were 1; `HS_PUSH_SHADOW`, MAX and the other recorded unsafe controls were absent. This policy remains distinct from the renderer's shadow-enabled policy.

The static case has a process-scan limitation. Its scan matched a transient standalone `fpx_sticky_test` CPU-test path, and the launch did not branch on that result. The process and temporary directory disappeared before file-descriptor/dependency inspection, so its GPU-FD state could not be established retrospectively. No unrelated process was signalled. The original and array scans found no known candidate; the array launch explicitly checked the scan result. No GPU timing claim is made for any case.

The next separate [owned replay preparation](compute-interval-array-replay.md) checks actual constructed buffer-handle aliasing and prepares complete readback comparisons. These compile-only observations do not establish that replay's correctness or authorize its execution.
