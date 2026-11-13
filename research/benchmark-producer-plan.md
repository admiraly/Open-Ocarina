# Proposed equivalent-scene benchmark producer

Status: source-audited design, not an implemented producer or an accepted contract
revision. No game/GPU execution was performed for this design. The existing
[schema 1 contract and validator](../docs/benchmark.md) remain unchanged and do
not accept the proposed verification cohorts below. No run becomes eligible by
implementing only a subset of this plan.

The first producer should observe camera, simulation and work identities and
reject divergent repetitions. Input isolation helps reproduction; it does not
make a multithreaded game deterministic. The goal is a defensible native 60 FPS
comparison with native features and quality preserved. Unresolved state or
resource coverage must remain a failure, not a hand-entered digest.

## Source boundaries

Inspected base: Hookshot commit
`4cf785a0b143bb34026506c9a3fe4f4bb93c797d`. Names below are source locations in
that pin, not promises about other game builds. Existing diagnostic patches add
other hooks; this proposal does not change those patches or frozen builds.

| Evidence | Existing source boundary | Proposed observation |
| --- | --- | --- |
| Gameplay view | `nx2/oot_patches.py::patch_view`, `host/hos_main.c::hos_oot_free_view` | Observe after `oot_build_free_view`, whether the override is enabled or disabled. The final view is 12 floats at `view+8`. |
| Active view | `host/hos_main.c::oot_gameplay_view` | The singleton at `0x84ca6bc0`, manager, camera-zero chain and view at camera `+120` identify the candidate gameplay view. Validate the existing view type check `0x84c18a88`. |
| Submitted camera | `gpu/vk/vk_draw.cpp::draw`, beside `temporal_note_scene_draw` | Observe `s.ubo[0][0]`, the actual NVN vertex binding. The source comment also calls this driver uniform buffer 3; these are different numbering conventions. |
| Camera bytes | `gpu/vk/vk_temporal.cpp::temporal_note_scene_draw`, `gpu/vk/vk_fsr3_inputs.cpp::note_scene_draw` | Existing layout: minimum `0x230` bytes; view-projection at `+0x30`, projection at `+0x70`, target width/height at `+0x120/+0x124`. Both matrices contain 16 floats. |
| Jitter | `nx2/oot_jitter.py`, `host/rx_backend.c::hos_dlss_jitter_clip` | Jitter is inserted while uniforms are constructed, at main offsets `0x6bc708`, `0x6bcf8c`, `0x6bcf9c`. The view in that path is register x28, with its unmodified projection at `+0x1a8`. The submitted UBO's jitter is read as minus the floats at `+0x78/+0x88`. |
| Viewport and targets | `gpu/vk/vk_state.h::NvnState`, `vk_draw.cpp::draw` | Record `viewport`, `depth_near`, `depth_far`, `scissor`, resolved target descriptions and actual present crop. |
| Logical work | `gpu/vk/vk_replay.cpp::replay`, `gpu/render/rx_nvn.h` | Observe typed NVN operations in authoritative logical order, before physical draw/dispatch substitutions, batching and omission decisions. |
| Native present | `host/nvn_native.c::nvnQueuePresentTexture`, renderer `submit_q`, `present`, `Job`, `do_present` | Carry explicit tokens through ingress, replay, submission and completion. Existing frame counters alone are insufficient. |

The host hook establishes a gameplay-view candidate, not the final GPU camera.
The jitter patches alter registers feeding GPU uniforms without changing the
game's stored projection. Record the host view and the matrices actually
submitted for the selected scene draws, then establish their association. Do not
derive the final projection from yaw/pitch or assume the stored projection has
all final modifications.

Existing temporal selection is a useful starting point, not the benchmark
selector. It prefers the largest eligible target, checks a perspective matrix
and target size, and some consumers tolerate a camera from the previous frame.
A benchmark observer must work independently of whether a temporal upscaler is
enabled. Associate candidates with the actual scene input consumed by the
present/upscale chain. Include relevant auxiliary views in the canonical camera
record. Reject missing, stale or ambiguous primary-camera association; never
substitute the previous frame or select a target solely because it is largest.
The host-view-to-submitted-UBO association is still unimplemented and needs a
reviewed tag/creation path, not pointer equality between unrelated objects.

## Read the bytes the command consumes

`vk_replay.cpp::ub_read` overlays preceding `NOP_UPDATE_UB` writes on submitted
memory, with the worker segment's `ub_limit`. The existing temporal helpers read
through `memory_read_as_submitted` directly. A new observer must account for the
command-position overlay, rather than copying that read unchanged.

`vk_memory.cpp::memory_read_as_submitted` initially reads through
`host->gpu_read`, then applies available captured pages. A successful return does
not prove every byte came from a submission snapshot: missing capture coverage
can leave live guest memory in the result. The producer needs a strict read
result containing coverage, submission generation, overlays applied and any
unknown range. Require complete coverage for camera and workload inputs. A
capture is not automatically immutable while the CPU or GPU can still write its
backing; ownership, version and visibility must also be established.

This strict reader must describe the same effective bytes that descriptor
construction/upload supplies to the draw. If it cannot, camera evidence is
unavailable. It must not silently read the next frame's live buffer, zero-fill a
hole or certify a GPU-written value from an older CPU snapshot.

## Frame and submission association

Assign run-local frame and submission tokens at ordered ingress. A present closes
a logical frame only after the producer identifies the complete dependency
closure of its work, including other queues. Preserve the queue/event ordering;
do not change scheduling merely to make attribution easier.

Carry tokens into replay jobs, deferred submissions, parallel segments and the
actual GPU timeline values they signal. Record logical operation ordinals before
parallel recording. Observe an operation once in the authoritative serial walk
or dry planning walk, then attach execution outcomes from its workers. Hashing
both walks would double-count work. A resumed stream after an event wait must
continue its original ordinals.

`hos_present_count` increments after `rx_nvn_present` returns. With asynchronous
replay that can mean the present was only enqueued. Renderer `presents_now` also
advances before GPU completion, and `frame_info` exposes sampled counters.
Finalize a frame only when its required work completes successfully. A native
present submission is not display scanout; name the timing metric accordingly.

Use a completed warmup boundary followed by N completed native frames, recording
both boundary timestamps and N elapsed intervals. Avoid the N-versus-N-minus-one
error. Queue backlog must not inflate the count. Dropped tokens, unfinished work,
failed presents, device loss, resize, unknown dependencies or record-buffer
overflow invalidate the sample. Completion collection must not introduce a
blocking per-frame GPU wait into an otherwise pipelined timing run.

## Reproduction and simulation state

Use a fresh process and private save copy for every repetition. Pin all modules,
input bytes, effective settings, environment, shader policy, driver and device.
Keep the original save unchanged. Record settings changes during the window;
startup file hashes alone do not describe live menu options. Disable frame
generation and keep native resolution/quality/features fixed across conditions.

Start with a capped-60 configuration and game speed set to **per frame**, then
record the actual applied simulation step. `host/nn_vi.c::realtime_step` sets the
nominal-rate slot at `hos_gfx_module+0x2c8` in that mode. Above 60 FPS the current
code forces wall-time-based stepping, including the path that publishes
`hos_frame_seconds_bits`. A faster renderer can therefore advance a different
simulation on the same numbered frame. `HOS_INPUT_FRAME_CLOCK` schedules input
from presents; it does not fix simulation dt.

Uncapped deterministic throughput needs an explicit benchmark-only fixed-step
route at the engine update boundary. It must cover all frame-step consumers,
whole-frame counters and movie/timeline stepping without replacing clocks used
for scheduling, waits and elapsed-time measurement. This route requires its own
review and behavior tests. Capped results establish sustained 60 FPS/deadline
behavior, not uncapped performance headroom.

Available scene observations include current map, loading, childhood and event
status in `oot_map_refresh`, and game clock at `hos_step_probe+0xd58`. The map
cache refreshes only every 250 ms; a frame observer must sample the corresponding
state at an audited update boundary. These fields are not a complete scene
schema. Weather/lighting state, game RNG state, streaming readiness, relevant
actor/animation state and temporal initialization still need identified fields
and coverage review. `GenerateRandomBytes` has a fixed initial atomic counter,
but multithreaded call order can change which caller receives which bytes.
Fresh process plus fixed seed is not proof of identical state.

Define a semantic readiness condition and a fixed native-frame warmup sequence,
including jitter phase, previous-camera state and temporal reset/history state.
Do not select matching subwindows after observing timing results. Reject
divergence instead of quantizing scene differences away.

## Input, logical work and completion gates

The [input-isolation patch](../docs/input-isolation.md) drains native mouse
motion and isolates the pad/key merge. Remaining routes include
`GetSixAxisSensorStates`/`hos_sdl_motion_history`, `host_taps`, direct free-camera
key reads, live input files and state-changing control commands. Observe and
gate each route for warmup and measurement. Preserve native feature behavior in
the declared configuration; disabling a feature solely to obtain a speedup is
not an acceptable comparison. An environment flag is not an observed isolation
certificate.

Canonical logical work must include typed draw/dispatch parameters, guest shader
identities, relevant pipeline state, target/view descriptions, binding and alias
relationships, effective input bytes, clears, copies, inline uniform updates and
conditional execution outcomes. Use guest shader identities, not translated
SPIR-V whose policy is the experimental change. Physical pipeline/batch counts
are separate diagnostics. Do not serialize C structs, padding, raw pointers,
Vulkan handles or run-local allocation serials into comparison hashes. Define
fixed-width encodings, endian/float rules and canonical resource references;
preserve aliasing and offsets while normalizing addresses.

Indirect draw/mesh/dispatch argument buffers and count buffers can be GPU-written.
Matching their addresses, stride and maxima does not establish executed work.
Resolve their values at the consumption boundary with complete dependency and
visibility evidence, or mark coverage unknown. The same applies to relevant
dynamic resource inputs, conditional-render counters and GPU-produced state.
Existing backing leases do not establish contents, immutability or execution
ordering. Sampling extra GPU hashes/readbacks may help verification, but adds
work and cannot silently enter normal FPS timing. Exact low-overhead closure for
these paths remains a major unresolved implementation dependency.

Warm caches through the declared sequence. `vk_jobs.cpp::jobs_pending` can supply
queue state; it must be checked throughout the window, with new compile activity
also recorded. Add frame-associated omission outcomes at every draw and dispatch
exit. `st_pending_skips` and aggregate `st_skipped` do not by themselves separate
all omitted dispatches from draws or establish queue emptiness. Require zero
pending compilation and zero unexpected omissions for every measured frame.

Observe competing GPU work over the entire interval with an explicit observation
coverage result. A process list at startup misses short-lived work. An unreadable
client or unavailable accounting must produce unknown, not an empty competitors
list. Closing games does not establish this condition, especially if a compositor
or browser shares the measured GPU.

## Proposed contract revision: matched verification cohorts

Schema 1 asks for `correctness_checked: true` on every timing frame while
requiring `instrumentation: []`. Capturing/checking every timed output adds GPU
or readback work; a separate screenshot run does not prove the exact timed
outputs. Do not manufacture this boolean. The following is a proposal for a new
version, provisionally **schema 2 / matched verification cohorts**, requiring
separate review and validator implementation. Schema 1 must continue rejecting
unsupported claims and schema 2 must not be accepted through a schema-1 label.

1. **Timing cohort.** Retain at least three independent repetitions per condition,
   unique IDs, exact native-frame/timestamp records, complete input/camera/scene/
   logical-work coverage, no generated frames, complete GPU-competitor coverage
   and the same single permitted experimental identity change. Record a precise
   producer-observation feature list instead of claiming no instrumentation at
   all. Exclude attribution captures, output readbacks and GPU resource hashing
   from timing unless a separately named metric explicitly includes their cost.
2. **Verification cohort for each condition.** Run the same declared scene and
   warmup with private output capture and an audited correctness method. Pin the
   exact condition identity, including renderer/driver/policy, producer, settings,
   input, save and device. Verification timings are never native FPS samples.
   Retain private underlying outputs and coverage, not just a pass boolean.
3. **Exact matching.** Link each timing frame to a verification frame by its
   normalized frame position and complete observed input, camera, scene and
   logical-work signatures, plus warmup/history initialization. Match every frame
   in the selected interval, not a few screenshots. Unknown coverage or one
   mismatch rejects the repetition. Distinct cohorts need distinct run IDs; an
   artifact from another condition/build cannot be relabeled.
4. **Correctness policy.** Declare the reference, comparisons, tolerances and
   coverage before evaluating the candidate. Agreement with a broken baseline
   alone does not establish native quality. Treat inconclusive coverage as
   failure. Reference images/results remain private; tracked records contain
   appropriate metadata only. Repeated verification must expose inconsistent
   outcomes rather than selecting a passing image.
5. **Explicit linkage.** Use hash-pinned, relative artifact references from timing
   samples to verification reports, retaining the current path/symlink/size
   protections. Replace the timing-row boolean with a typed validation record
   stating `mode: matched_verification_cohort` and its coverage/report reference.
   The validator must distinguish this from direct checks of timed outputs.
6. **Trust and overhead.** Keep the audited canonical producer artifact identical
   across A/B; do not define its digest as the whole renderer being changed.
   Pin/audit each renderer's hook integration separately. Record evidence loss
   and observer overhead; do not subtract an assumed overhead to claim speedup.
   Compatibility still must not itself calculate or certify a speedup.

This revision offers rendering validation of matched executions. It explicitly
does **not** prove each exact timed output was correct: timing-dependent GPU
bugs can occur only in timing runs. It cannot compensate for missing indirect
arguments, scene fields or resource contents. Acceptance of this residual risk,
verification repetition policy and rendering coverage belongs in the reviewed
contract, not an implementation default.

## Implementation order and negative tests

Implement the CPU-side canonical record/state machine and strict coverage API
first, using authored fixtures. Then add host view/input/step observations,
renderer frame tokens, submitted camera association and logical operation
outcomes. Keep all evidence in bounded buffers; serialize private artifacts after
the measurement window. No manifest is eligible until complete coverage and the
chosen correctness protocol are implemented, reviewed and exercised.

Required game-free tests should include:

- A final free-view override changes the record; a pre-override observation fails.
- An inline UBO update changes the camera at the correct command ordinal; a later
  update cannot affect the earlier draw or another worker segment.
- Live-memory fallback, partial pages, wrong snapshot generation, a stale camera,
  ambiguous scene target and missing present-to-camera association reject frames.
- Enqueue completion cannot masquerade as GPU completion; failed/late/cross-queue
  work, resize and record overflow invalidate the sample.
- Dry/worker walks and suspended/resumed streams yield exactly one ordered logical
  record per operation. Batching changes preserve logical identity; an omitted
  draw/dispatch or changed shader input does not.
- Pointer relocation preserves canonical identity while changed alias overlap,
  viewport, projection, jitter phase or effective resource bytes changes it.
- Unresolved GPU-written indirect/count arguments reject coverage even when their
  addresses and declared maxima match.
- Different wall-time dt/RNG assignment or an unobserved input route produces
  divergence or unknown coverage. Expired isolation cannot remain certified.
- Pending compilation or missing competitor observation cannot be encoded as zero.
- Missing/reordered verification frames, changed warmup, condition mismatch,
  reused run IDs, tampered artifacts and incomplete resource/state coverage reject
  cohort linkage. A separate image cannot set the schema-1 timed-output boolean.
- N measured native frames use N elapsed completion intervals and exclude
  generated frames and accumulated queue backlog.

After these checks, real runs still need independent live-input injection tests,
camera/UBO/present association validation, actual indirect-resource closure,
repeated output verification and observer-overhead measurements. None has been
established by this design document.
