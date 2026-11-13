# Owned compute paired timing observation

The interval candidate had lower measured isolated-dispatch median latency in both observed batches. Both batches also passed every complete buffer comparison. This is evidence for one frozen input graph; it does not establish normal-frame FPS, live integration or an accepted gameplay optimization.

The [full observation](../research/compute-interval-timing-observation.json) contains all **40 native trials**, including eight untimed warmups and all 32 timed samples with raw timestamp start/end values, ticks and nanoseconds. Every timed sample and outlier is retained. Untimed warmups carry zero placeholders in timestamp fields; no query timestamps were recorded for those trials. The original [protocol and CPU handoff document](compute-interval-timing.md) remains a frozen preparation record; this separate document records the subsequent execution.

| Batch | Original median (ms) | Candidate median (ms) | Timed samples per module |
|---|---:|---:|---:|
| 1 | 3.8908071966762545 | 2.89059888776207 | 8 |
| 2 | 3.880260321933746 | 2.8950780543193817 | 8 |

These are descriptive medians derived directly from all saved timed nanosecond values, without filtering. The first candidate batch includes a lower latency cluster near 2.4 ms; the second includes a higher sample near 3.365 ms. Original samples also include values near 4.37 and 4.40 ms. The observation assigns no cause to these variations and makes no general-performance inference.

## Execution and byte checks

Each batch used a fresh process, resource graph and isolated cache. Within that process, the original `b9af52…` and candidate `d51483…` pipelines shared the same 59 original owned buffers, four sampled images, pushed bytes, physical page table and independent guarded record. Both reported actual subgroup width 32. The A770 reported 36 timestamp bits and a timestamp period of 52.08333206176758 ns. Full module, driver, source, helper, profile, plan and evidence hashes are in the observation.

Each batch completed exactly two original warmups, two candidate warmups and four fixed `A B B A` blocks: twenty dispatches, with eight timed samples per module. The full 4 MiB writable owner was reset from its frozen initial bytes before **every** dispatch. After every dispatch, all 59 complete original buffers (1,163,919,360 bytes) and all 8784 record/guard bytes matched the pinned profile-02 original baseline oracle. Final complete outputs were independently rehashed and matched across batches. The complete writable owner retained SHA-256 `323334824a3f64ff80ba0cb8f8602c8f6dd7be3384a32353bbd8e1eb0628c7c2`; the record payload and guards remained unchanged.

Both processes terminated successfully. Query availability, positive finite period, valid-bit wrap horizon and bounded interval checks passed. All source/input/driver postchecks matched their reviewed identities. The second batch also preserved the first batch's saved evidence and complete outputs. No retry, adaptive warmup, clock change, alternate width or third batch was performed. Both GPU leases were released.

## Measurement scope

The timestamp bracket surrounds one dispatch. Shader compilation, shader-cache setup, image upload, CPU writable resets, complete CPU oracle comparisons and file hashing occur outside the interval. Pipeline or driver state emitted for that dispatch can still fall inside the GPU bracket, so these measurements do not isolate shader arithmetic alone. Compiler executable statistics are retained separately; their cycle estimate is not measured GPU duration.

The full CPU oracle/reset gaps can affect subsequent GPU clock and cache state. The known-process scans found no matching game/helper executable before either batch, but they do **not** prove that all GPU background activity was excluded; that remains unknown. These results make no locked-clock or equivalent device-state claim.

The complete buffer matches support the tested owned graph. They do not supply a rendered-image comparison, historical snapshot atomicity, complete original device/float-state equivalence, live selector ownership, general algorithm equivalence or whole-game correctness. Native rendering quality and accepted gameplay performance remain separate requirements.

All files covered by the original timing CPU freeze, profile 02 and both runtime batches remain unchanged. This observation adds metadata and documentation only.
