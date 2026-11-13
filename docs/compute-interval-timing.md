# Owned compute paired timing protocol

`tools/compute_interval_timing.py` prepares a separate profile, ABI, native helper and plan. The reviewed [profile-02 replay](compute-interval-replay.md), its helper and evidence remain unchanged. The new timing helper has passed CPU preparation and authored tests only. No Vulkan instance, pipeline, query or dispatch has been created by this new profile.

The intended measurement is the duration of one isolated frozen compute dispatch. It cannot establish normal-frame time, FPS, live-renderer speed, visual quality or general algorithm equivalence. The [preparation metadata](../research/compute-interval-timing-preparation.json) identifies the source and private handoff.

## Frozen graph and expected bytes

Preparation imports the unchanged profile-02 validator after checking its source hash and all its pinned dependencies. Both original controls are checked in full. The new control must preserve every original allocation, physical-page mapping, pushed byte, descriptor, sampled image/view and sampler word. Only the new profile identity and the separately framed second module/oracles are added. Both pipelines share the same union layout, physical page table, 59 original owners, four sampled images and independent 8784-byte guarded record. Both require actual subgroup width 32/full subgroups.

The original module is `b9af52c0ba07f64eb8b0ca2905c432d094999fdf669aa95182ff7754b510e97b`; the candidate is `d5148342b4517882b25a48e63700cbfb8695454cc05d131da718509ac2563dc0`. Record payload SHA-256 is `49da4bdc0c4542b4fef7b5dcbb631d41fb030d80803cde3bb37185722daf9770`. The driver remains the reviewed E5 library `e5a1305ea3b423a18fccbb945aaedd32f8add303926fcc39276ee01032cd456f`, with the unchanged physical policy: `HS_SPILL_BATCH=1`, `shader_spilling_rate=1`; `HS_PUSH_SHADOW`, MAX and unsafe overrides absent.

The pinned profile-02 original baseline supplies the expected complete output. Preparation rehashes all 60 reference files. An independent reachable-store audit on each actual disassembled binary traces every nonlocal store through constant descriptor roots: both modules have sixteen storage-store sites, all at binding 6. Physical stores, image writes, atomics, copy-memory operations and unresolved store roots are rejected. Binding 6 must name owner 7, original `buffer-16`, exactly 4 MiB. Every other original owner's complete baseline output and the guarded record must equal its frozen initial bytes. This static audit and byte check justify resetting only owner 7; they do not prove historical snapshot atomicity or general race freedom.

## Fixed protocol

One process creates two optimized pipelines before allocating the shared input graph. Each pipeline's actual width must be 32; structured executable statistics are retained independently. Images are uploaded once in a separate submission, which must fence successfully before any target dispatch.

The schedule is fixed in both host and native code:

1. Two original warmups, then two candidate warmups.
2. Four `A B B A` blocks, where A is the original and B the candidate: sixteen timed samples, eight per module.
3. At most twenty target dispatches total. There is no retry, adaptive sample count or alternative width.

Before **every** target dispatch, including warmups, the process resets all 4,194,304 bytes of owner 7 from the frozen initial file. It does so after the previous fence and complete oracle comparison. All other owners and images remain unchanged. A `HOST` → `ALL_COMMANDS` barrier makes host writes visible before the query bracket. Descriptor binding and push commands precede that bracket.

For timed trials, `vkCmdResetQueryPool` occurs before the start timestamp. Exactly one `vkCmdDispatch(2, 1, 1)` lies between a `TOP_OF_PIPE` timestamp and a `BOTTOM_OF_PIPE` timestamp. Image transfers, host resets, oracle comparisons, query retrieval, buffer-file output and pipeline compilation occur outside the bracket. The legacy timestamp API preserves the existing feature chain; synchronization2 is not enabled or silently assumed. These scopes follow the [Vulkan timestamp command contract](https://docs.vulkan.org/refpages/latest/refpages/source/vkCmdWriteTimestamp.html).

After each target dispatch, a shader-write → host-read barrier and a successful two-second fence precede query retrieval and CPU reads. The query request uses 64-bit results plus availability, without `WAIT`. Both query slots must be available. The queue must have 1–64 valid timestamp bits; the timestamp period must be finite and positive. The wrap horizon must exceed the **whole 120-second process bound**, which is stronger than the two-second fence requirement. The helper masks valid bits before subtraction and rejects zero, nonfinite or at-least-two-second intervals. A conservative `timestampComputeAndGraphics` capability gate is also required before logical-device creation.

After **every** dispatch, including all four warmups, the helper compares every byte of all 60 buffers against pinned expected mappings: 1,163,919,360 original bytes plus 8784 record/guard bytes. Any mismatch or Vulkan/query error terminates the sequence. Final complete outputs are saved once and independently rehashed by the host. There is no device-idle wait on failure. The host bounds its owned process group to 120 seconds and stops only that group on timeout.

## Review and CPU validation

The helper is derived from exact profile-02 native source by fourteen counted anchors. Preparation emits the complete formatted C diff and builds with `-O2 -DNDEBUG -Wall -Wextra -Werror`. New control magic `OOCITM1` prevents the old helpers and new helper from accepting each other's ABI. `--check-only` admits the complete frame, candidate file and all expected mappings before returning ahead of `vkCreateInstance`.

```sh
python3 tools/compute_interval_timing.py prepare \
  local/compute-interval-replay-owned-02 \
  local/compute-interval-replay-owned-02/baseline32-dispatch-01 \
  --output local/compute-interval-timing-owned-02
python3 -m unittest discover -s tests -p test_compute_interval_timing.py -v
```

Preparation is CPU-only. Execution is a separate exact-plan operation requiring an exclusive GPU lease and review of source/helper/plan hashes. The current private handoff is `local/compute-interval-timing-owned-01`; the example uses a fresh destination to preserve it.

Fourteen game-free CPU tests cover fixed schedules, framing, oracle extents and mutations, writable binding/owner gates, source anchors, unsupported timestamp periods/bits/horizons, wrap arithmetic, complete resets, last-byte oracle failures and native result validation. Production timestamp/dispatch functions are tested using linker wrappers, checking the exact query-reset/start/dispatch/end order and untimed warmup path. A `vkCreateInstance` linker trap proves positive CPU admission stops before Vulkan and intercepts the authorized-execution branch at its first API boundary. No authored fixture shader is passed to Vulkan.
