# Equivalent-scene benchmark contract

Status: a tested manifest/evidence compatibility validator is implemented. An audited runtime camera/workload evidence producer is **not** implemented or bundled. Existing exploratory runs do not satisfy this contract. No producer is trusted by default, and the validator does not compute a speedup.

The [benchmark producer plan](../research/benchmark-producer-plan.md) identifies concrete source hooks, strict camera/resource coverage, simulation-step and frame-association requirements. It also proposes a separate contract revision for matched rendering-verification cohorts. That proposal is unimplemented and does not relax schema 1 or authorize replacing its per-frame correctness requirement with a separate screenshot run.

The immediate failure to prevent is comparing two runs of the same save while one camera looks down and the other looks forward. A save hash, input script and two FPS lines do not establish equivalent rendering work.

## What existing Hookshot controls actually do

Inspected source: the Linux nightly package identified as `v2.1.0-rc.3-306-g4cf785a0`. Paths below refer to that source tree, not independently verified behavior on every version.

- `host/nn_hid.c:953-971`, `read_input`: scripted inputs are added before normal input. By default the player's devices still count. `HOS_INPUT_EXCLUSIVE=1` returns before the device merge only while there is a script and until five script-clock seconds after its last press. A script that ends before the measured window does not isolate the rest of that window.
- `host/nn_hid.c:307-314`, `script_elapsed_ms`: `HOS_INPUT_FRAME_CLOCK=1` advances existing script seconds/holds using native presents at 60 frames per script-second. This improves scheduling consistency; it does not freeze simulation, camera or temporal history.
- `host/control.c:757-807`, `cmd_input`: frame-counted `press`, `stick` and `sequence` commands support `exclusive: true`. Neutral stick axes can maintain an exclusive lease. The lease is bounded by its requested frames and client lifetime, not a permanent device-disconnect policy.
- `host/mouse_native.c:112-166`, `hos_mouse_camera`: native mouse camera consumes `hos_mouse_take` directly while the game updates its player camera. Therefore returning early from `read_input` does not, by itself, demonstrate isolation of every mouse-camera path. `HOS_MOUSE_NATIVE=0` avoids this particular path but changes camera behavior and must be pinned consistently. Motion controls and any other out-of-band input paths also need an audit.
- `host/control.c`, command dispatch: status, frame counts, input sequences, shots and optional generic guest-memory reads exist. There is no named API producing the final gameplay camera matrix, projection or a per-frame scene identity. Generic `mem_read` operates at controller reads, not automatically at the renderer's final view/projection boundary.

These controls are useful building blocks, not an existing deterministic benchmark producer. Do not label a run deterministic solely because these environment variables are set.

## Required producer work

The existing `nx2/oot_patches.py:patch_view` inserts `hos_oot_free_view` after its selected matrix-construction anchor. In `host/hos_main.c:1625-1637`, that callback filters the gameplay view, then optionally replaces its matrix for free camera. A new observation hook after the final override can identify the actual gameplay view for that update. It still needs to capture the corresponding **final projection, viewport, temporal jitter/history and active camera identity**, and associate them with the native frame actually replayed by the renderer. Capturing yaw/pitch in `hos_mouse_camera` alone is insufficient.

The producer should:

1. Restore the same private save, pin all game modules, renderer, driver, shader policy, settings and input bytes, and disable frame generation.
2. Isolate every live input source for warmup and the complete measured interval. Record the observed isolation state rather than an intended environment setting.
3. Emit native-frame-aligned camera identities from final view/projection data. Define canonical encodings and state coverage; never use a hand-entered camera label.
4. Emit scene-context identity covering the relevant time, lighting/weather, RNG/temporal initialization and other state that changes workload. Hashing arbitrary pointers or all process memory is not a stable scene identity.
5. Record a logical workload identity at a stable boundary before renderer-specific batching, plus omitted draw/dispatch and pending-compile counters. Changing physical batch counts may be an intended optimization; silently losing logical work is not.
6. Observe competing GPU work throughout each measurement, warm caches before timing, and keep attribution instrumentation out of timing runs.
7. Tie private rendering correctness checks, exact native-frame counts and elapsed nanoseconds to each repetition. Frame generation and skipped passes cannot improve the native timing metric.

This requires reviewed instrumentation; the manifest validator cannot discover unrecorded state or prove a producer honest. The producer digest is an operator-reviewed trust anchor, not a signature. Distinct run IDs prevent literal reuse but do not independently prove that scenes were restarted or repetitions are statistically independent.

## Manifest schema 1

The validator consumes two local JSON manifests. Each has:

- `schema: 1`, `purpose: "timing"`, and empty `instrumentation`.
- `identity`: `game_modules_sha256` with exactly `main`, `sdk`, `subsdk0`, `rtld`; full `renderer_revision`; SHA-256 values for `driver_sha256`, `shader_policy_sha256`, `save_sha256`, `settings_sha256`, `input_sha256`; and the 16-byte Vulkan `device_uuid` in lowercase hexadecimal.
- `producer`: `contract: "open-ocarina-benchmark-v1"` and the digest `sha256` of the externally audited producer build. The same producer must supply both conditions.
- `native_frames_only: true`, `frame_generation: false`, `cache_warm: true`, positive `warmup_native_frames`.
- At least three `samples` with unique `run_id`, positive integer `native_frames` and `elapsed_ns`, and an `evidence` reference containing a relative `path` and full `sha256`.

Each evidence file has `schema: 1`, the matching `run_id`, `producer_sha256`, `native_frames`, `elapsed_ns`, `warmup_native_frames`, `cache_warm: true`, empty `instrumentation`, `generated_frames: 0`, `live_input_isolated: true`, and empty `competing_gpu_processes`. Its `identity_sha256` is SHA-256 of the manifest's identity object encoded as sorted-key compact JSON (`json.dumps(identity, sort_keys=True, separators=(",", ":")).encode()`).

The evidence `frames` array covers every native frame in the window, consecutively indexed by `sample_frame` starting at zero. Every row contains observed `camera_sha256`, `scene_state_sha256` and `logical_workload_sha256`; zero `pending_shader_compiles`, `skipped_draws`, `skipped_dispatches`; and `correctness_checked: true`. Digests refer to the reviewed producer's canonical encoding, not to arbitrary labels. The private underlying evidence must remain available for review.

All repetitions must reproduce the camera/state/logical-workload identity sequence. Both conditions must do so too. The comparator permits one declared experimental identity change: renderer revision, driver digest or shader-policy digest. It rejects a simultaneous settings, save, input, device or unrelated revision change. It also rejects overlapping repetition IDs, missing records, tampered evidence, symlinks and path escapes.

## Use and limits

```sh
python3 -m unittest discover -s tests -p 'test_benchmark_contract.py' -v
python3 tools/benchmark_contract.py local/baseline/run.json local/candidate/run.json \
  --change shader_policy_sha256 --trust-producer-sha256 "$AUDITED_PRODUCER_SHA256"
```

Until an actual producer is implemented, reviewed and independently exercised, no real game run should be made to pass by inventing its camera/state hashes. The tests use explicitly synthetic evidence to test the gate, not to establish a valid game benchmark. A compatible result reports `speedup_inferred: false`: statistical analysis, rendering coverage and the adequacy of sample durations remain separate review obligations.
