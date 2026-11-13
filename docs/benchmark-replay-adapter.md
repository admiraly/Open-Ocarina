# Bounded native-record adapter

Status: an independently authored CPU decoder/adapter and synthetic tests are
implemented. It consumes one copied Hookshot-format recording and proposed typed
native events. **No native event producer is implemented or validated.** No game,
GPU execution, vendor source edit or host rebuild was performed for this work.

The adapter reaches source-defined uniform byte slices and local
submission/present/completion bookkeeping. It never identifies a primary camera,
certifies a complete scene/workload, checks rendering or emits an accepted
benchmark manifest. The [benchmark contract](benchmark.md) and its proposed
[producer revision](../research/benchmark-producer-plan.md) remain unchanged.
The [CPU observer](benchmark-observer.md) is imported unchanged at SHA-256
`314d491f60559ee5ec829e873938a7dba52f6337c39967f5ed2737be0a59d33e`.

## Source and actual binary decoding

Pinned public source: Hookshot commit
`4cf785a0b143bb34026506c9a3fe4f4bb93c797d`. The implementation records hashes of
the inspected public header, native writer, replay, draw and temporal source.
`audit_pinned_abi` checks their Git blobs and compiles 21 assertions against the
actual pinned public C headers in a temporary directory. This checks the
x86-64 record ABI and opcode values; it does not validate a runtime capture.
Compilation uses `-O2 -DNDEBUG` and does not execute the compiled object.

`gpu/render/rx_nvn.h` defines an eight-byte little-endian header `{u32 op,u32
size}`. Size includes the header and is aligned to eight bytes.
`host/nvn_native.c::EndRecording` owns a flattened contiguous BEGIN-to-END copy.
`CallCommands`/`CopyCommands` inline the called body. The adapter supports exactly
one complete copied recording, with no trailing bytes or extra handles.

| Opcode | Native payload decoded | Supported use |
| --- | --- | --- |
| BEGIN 2 / END 0 | No payload | Bound the recording; do not reset graphics state or imply a present. |
| UPDATE_UB 7 | `u64 dst,u64 size`, then exactly size bytes; at most 64 KiB | Register the copied inline write at its command ordinal. Alignment tail is not semantic data. |
| VIEWPORTS 11 | `i32 first,i32 n`, then 16 four-float rows; sizeof 264 | Decode only the active rows, requiring a valid native viewport interval. |
| SCISSOR 12 | Four i32 values | Explicit scissor observation. |
| DEPTH_RANGE 13 | Two f32 values | Explicit near/far observation. |
| BIND_UBO 19 | `i32 stage,i32 index,u64 address,u64 size`; sizeof 24 | Follow direct NVN binding stage 0/index 0 as a candidate range. |
| DRAW 25 | Five i32 values; sizeof 20, aligned record size 32 | Record direct draw intent and inspect candidate uniform slices. |
| DISPATCH 27 | Three i32 dimensions and a reserved i32 | Record nonnegative direct dispatch intent. No compute-resource coverage is inferred. |

All other opcodes, including targets/program pointers, events, copies, indirect
work, mesh draws, deferred bindings and barriers, are explicit unsupported
errors. They are never skipped to produce a successful subset digest. Malformed
sizes, truncated payloads, nested BEGIN, missing END and extra bytes fail.
Bounds are 1 MiB of stream bytes and 4,096 commands. Pointer-bearing native
records are not interpreted as portable structs or dereferenced by Python.

## Copied ranges and exact inline position

`SubmitCopy` supplies immutable stream bytes, a capture `Origin`, native queue
identity, copy timestamp and up to 128 `OwnedRange` observations totaling 2 MiB.
The origin contains a nonzero process/capture nonce, a proposed global ingress
serial and a snapshot generation. That serial needs a new producer hook; it is
not an existing global interpretation of `q->submits`, whose counter is local to
a native queue.

Each range carries the same origin, GPU address, original backing-lifetime token,
backing offset, immutable bytes and explicit source kind. Stale/mixed generations
or origins, mutable data, live/GPU-unresolved sources and unmapped reads refuse
adaptation. Backing tokens are private identity metadata, never comparison
fields. Equal guest addresses or equal bytes cannot establish shared backing.

The adapter installs every virtual mapping in the frozen observer's `Coverage`.
Different virtual mappings can alias one backing interval. Overlapping copied
alias bytes must agree exactly; agreeing portions are deduplicated, and a write
through either alias updates reads through the other. Conflicting copies fail
rather than becoming independent allocations or an arbitrary winning snapshot.
Inline updates come from the native recording's owned payload. They are applied
only to later command ordinals, including when all future writes are already
known to the decoder. Resource uses retain the exact consuming ordinal and the
observer submission stamp.

Immutability is a property of the supplied Python bytes. It does **not** establish
that a live CPU/GPU stopped writing the original backing while it was copied.
Generation equality is necessary metadata, not proof of mapping lifetime,
visibility or historical atomicity. Existing backing/page-table/descriptor
captures cannot be relabeled as this missing producer. A native bridge must prove
those properties or report unknown before supplying an owned snapshot source.

## Candidate uniform slices, not an identified camera

At `vk_draw.cpp::draw`, the existing temporal helper receives
`s.ubo[0][0].addr/size`. This is NVN vertex binding 0; the source also calls it
driver uniform buffer 3. Its temporal helper inspects a layout of at least
`0x230` bytes:

- Candidate view-projection words at `+0x30`, 16 f32 words.
- Candidate projection words at `+0x70`, 16 f32 words.
- Candidate target-size words at `+0x120/+0x124`.
- Candidate jitter from the sign-reversed projection words at `+0x78/+0x88`.
- A perspective-row pattern at `+0xa0`: `(0,0,-1,0)`.

The adapter strictly reads the complete candidate range at each direct draw,
after preceding inline updates. It requires explicit direct binding,
viewport-zero, scissor and depth state in this recording. BEGIN does not clear
the actual replay's inherited state, so missing observations cannot default to a
zero camera. The candidate's layout discrepancies are computed from the copied
words; a matching pattern is still not a shader-role or camera certificate.
There is no `camera_valid` flag, default primary camera or largest-target choice.

Only the defined matrix/size spans and explicit viewport/scissor/depth values
enter the candidate comparison record. Unclassified UBO bytes may be padding,
pointers or unrelated state. They are not silently promoted to camera fields.
Changing those unclassified bytes can therefore leave this *partial* record
identity unchanged. This is a deliberate reason not to call the digest a complete
workload/state identity.

The adapter records direct draw/dispatch **intent**, not proof that the renderer
executed it. It has no observed program, target, render-enable/conditional state,
omission outcome or complete resource closure. Every `FrameObservation` retains
unresolved scope even after local bookkeeping completes.

## Native present and recording completion events

These are proposed producer events, not packets emitted by the current source.
All originate from one ordered collector/session. Inputs have numeric observed
results and identities, not manual pass booleans.

`RecordingSignal` binds the exact copied stream SHA-256 and origin to a fresh
`Recording.part`, original `rec_timeline` semaphore lifetime, `rec_seq`, actual
submit timestamp and numeric `VkResult`. Empty stream digest is permitted only
at the separate present boundary. Reusing an ingress serial with a new generation
does not make it fresh. Ring slots, raw command-buffer pointers and `r.value`
are not recording-lifetime identifiers.

`PresentCopy` separately records original window lifetime, `o->presents+1`, texture
index, crop, ingress origin and queueing timestamp. It must match the present
signal origin and precede its actual GPU submit. Per-window native serials must
increase. Present metadata is associated with a frame token and explicit
submission dependencies; an END record cannot manufacture this association.
Crop/texture index enter an explicit producer event record. The private native
counter, window handle and timestamps do not enter normalized comparison bytes.
This still does not associate a particular uniform candidate with the presented
scene image.

`TimelinePoll` records a fresh query serial, session, semaphore lifetime, observed
counter, timestamp and numeric query result. A successful query can complete
only previously owned recording signals on that semaphore whose `rec_seq` was
reached. Stale/regressing polls, another process/session or semaphore lifetime,
failed Vulkan calls and completion timestamps before submit fail. A partial
counter leaves later work/presents pending. Observed CPU poll time is an upper
bound on GPU completion, not the exact GPU completion timestamp or display
scanout. Polling latency and observer cost are unmeasured here.

`frame_observation(frame)` requires the observer's declared dependency/record
bookkeeping to be complete and an explicit native-present record. Its returned
`bookkeeping` receipt cannot be used as benchmark eligibility. The accompanying
`unresolved` list remains nonempty; missing present or completion refuses even
this local result. There is no FPS or benchmark-certificate export operation.

## Exact missing native hook patch design

A future patch should emit owned records at these boundaries, then independently
validate the adapter against real observations. This work did not apply it.

1. **Ingress ownership.** In `host/nvn_native.c::nvnQueueSubmitCommands`, allocate
   a fresh ingress serial and preserve the native queue plus its `q->submits`
   value. Copy the flattened recordings **after** `resolve_deferred` has copied
   deferred arrays into them, before asynchronous handoff or held submission
   reuse. Own all pointed-to objects needed by any newly supported opcode.
   The present path's `held_flush` must retain these ancestry IDs. Loss of an
   ancestry edge is unknown, not a new unlabeled submission.
2. **Effective memory.** At the replay's actual command position, expose strict
   owned reads with complete snapshot/backing/generation coverage and preceding
   `NOP_UPDATE_UB` overlays equivalent to `ub_read`. `memory_read_as_submitted`
   can fall back to live bytes, so its success alone is inadequate. Preserve the
   actual resolved alias graph, not a guest-address grouping. No readback or
   ownership/quiescence proof is implemented by this Python adapter.
3. **View and shader role.** Observe after `hos_oot_free_view` finishes its final
   override and after the jitter sites in `nx2/oot_jitter.py` populate the GPU
   copy. Trace the view's uniform construction to the actual bound range and
   shader-read layout. Record effective targets/subresources and the draw/compute
   chain feeding `do_present`/upscaling. Only that reviewed chain can associate a
   candidate with the primary gameplay view/projection and native frame. Current
   SET_RT/program records contain host pointers and are unsupported here.
4. **Native present ancestry.** At `nvnQueuePresentTexture`, copy window lifetime,
   next native serial, texture index and crop around `held_flush`/`rx_nvn_present`.
   Carry the origin into `JOB_PRESENT`, deferred work and `do_present`, together
   with complete cross-queue dependencies. Do not equate `hos_present_count`,
   `presents_now`, enqueue return or texture age with completed native work.
5. **GPU submit ownership.** In `vk_replay.cpp::submit_recording`, retain the exact
   owned stream/ordinal ancestry, `r.part` and `seq` signaled on `rec_timeline`.
   Emit success only after the actual `vkQueueSubmit2` result. `map_recording`
   can publish before execution and can describe a held batch; it is insufficient.
   The current adapter is bounded to a single owned stream/present recording
   relation. Multi-part/chunked/parallel/burst/merged/batched ancestry needs a
   richer explicit mapping and must not be represented by an unsupported guess.
   Batches require a separate hook after `queue_flush_locked` actually submits.
6. **Completion.** Record the numeric result/counter from queries of that exact
   semaphore lifetime with fresh query IDs and the same monotonic timestamp
   domain. Retain mappings through slot reuse. Do not use the global submission
   `timeline` where an individual command part's `rec_timeline` is required.
   Preserve event waits and cross-queue closure. Separate exact GPU timestamps
   from sampled CPU observation times if a later metric needs them.

Native hooks also need bounded buffers, explicit loss/overflow events, versioned
source/ABI identities and independently checked producer receipts. Supplying a
matching nonce, SHA or numeric success code from an unaudited caller does not
prove source provenance. No event path above supplies full scene state, input
isolation, indirect GPU arguments or rendering verification.

## Checks and unsupported paths

```sh
python3 -m unittest discover -s tests -p test_benchmark_replay_adapter.py -v
python3 tools/benchmark_replay_adapter.py --audit-checkout local/hookshot-source
```

The audit reads pinned public Git blobs and writes only a disposable C ABI probe.
Tests use authored native-format byte fixtures: golden record bytes, exact inline
ordinals, aliases/conflicts, relocation, stale range generations, holes/live data,
missing inherited state, unclassified bytes, unsupported opcodes, fresh recording
identities, present/crop ordering and actual versus pending timeline observations.

Unsupported: complete real-game recordings; pointer-bearing target/program state;
multiple handles/inherited queue state; mesh/indexed/indirect work; events and
conditional rendering; sparse rebinding; GPU-written inputs; original source
atomicity; multi-part/batch ancestry; primary scene/camera association; exact GPU
timing; rendering correctness; and any real FPS comparison. No existing live
capture has been made to pass this adapter.
