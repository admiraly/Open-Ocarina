# Select a later dispatch and capture renderer-owned buffer inputs

`occurrence-capture.patch` extends the three-patch identity/page-table/allocation
chain. It adds a bounded dispatch selector, real GPU copies of resolved
renderer-owned descriptor buffers, and explicit null-descriptor feature evidence.
It does not change shader code, flush guest-page-table updates early, or reorder
original dispatches and uploads. The original allocation patch and frozen renderer
remain independent evidence for their earlier captures.

## Select the occurrence explicitly

Keep the identity, page-table and allocation settings from
[allocation-capture.md](allocation-capture.md), then set, for example:

```sh
export RX_VK_OO_CAPTURE_OCCURRENCE=2
export RX_VK_OO_ALLOC_BUDGET_MIB=5120
```

The selector defaults to occurrence **1**. Only complete decimal integers from
1 through 65,536 are accepted. Empty, signed, whitespace-padded, overflowing or
out-of-range values disable selected-dispatch capture with a diagnostic. The
counter advances only after exact program, variant and emitted-SPIR-V digest
matching. The first match and requested match log that identity and their ordinal.
The completed allocation manifest records `selected_occurrence` and the exact
filter declaration. A later selected dispatch must never be described as the
first occurrence or as having identical dynamic inputs.

This addresses a concrete capture gap: an initial target dispatch can be recorded
before a later command lazily creates a larger imported pool and its mirror.
Deferred table changes for that later allocation are flushed into the recording's
upload command buffer, which executes before its dispatch command buffer. Thus
the GPU-visible table at the first dispatch can name a buffer that did not exist
when the CPU assembled its capture catalog. A larger staging budget alone cannot
capture such an owner. Selecting a subsequent occurrence lets an already-created
owner enter the catalog without changing rendering behavior.

This is not a guarantee that any chosen occurrence has stable ownership. Later
allocations may still be missing. Every actual copied GPU table entry must still
resolve against complete BEFORE/AFTER payloads, and the checker continues to
report gaps. There are no automatic unbounded retries or changes to the selected
shader to obtain favorable coverage.

## Renderer-owned descriptors and retention

An opt-in VMA registry records the exact `VkBuffer` creation size, usage flags and
`VmaAllocation` for successful calls through `create_buffer`,
`create_buffer_cached` and `create_device_buffer`. It does not substitute VMA
allocation padding for the buffer size. The registry holds at most 65,536 live
entries; a missed/unsupported owner remains an explicit descriptor coverage gap.
Normal buffer creation/destruction keeps its behavior when allocation capture is
disabled.

At the selected dispatch, a non-null descriptor absent from the pool catalog is
looked up and pinned in this registry. A resolved buffer is copied in full by the
GPU at the same BEFORE/AFTER boundaries and charged to the existing aggregate
staging budget. A renderer-owned row declares `guest_range:false`, zero guest
bounds and role `renderer-buffer`; it cannot establish physical guest-page
ownership. A BDA is queried only when the actual creation flags include
`SHADER_DEVICE_ADDRESS`. Recording upload streams do not need that flag, so their
rows legitimately have address zero.

Recording stream ownership was audited: earlier grown streams stay in
`RecordingRes::streams`; trimming happens only at slot reuse after that slot's
recording wait. The capture additionally pins resolved VMA owners. If
`destroy_buffer` is requested while pinned, actual VMA destruction is deferred
until the exact capture completion/cancellation release. This avoids depending on
the stream cache's retention interval alone. The registry does not cover buffers
created outside those VMA helpers; unknown owners remain unresolved.

All bytes still come from GPU transfer commands. CPU stream bytes or guest bytes
are not exported as a replacement for the GPU copy. Host writers are not
quiesced, and complete snapshot atomicity is not claimed.

## Null descriptors and schema v2

The producer records the actual renderer `dev.null_descriptor` state, derived
from the enabled `VK_EXT_robustness2` feature query, in
`null_descriptor_enabled`. Null descriptor entries remain represented in their
original binding/array positions. Only a null uniform/storage descriptor with the
expected zero offset and whole-size framing, together with enabled
`nullDescriptor`, is classified as a defined null entry requiring no allocation
payload. Missing feature evidence is rejected; a false feature state keeps those
entries explicitly unresolved. Replay must preserve null entries rather than
invent an allocation for them.

The new manifest schema is `open-ocarina.gpu-allocations/v2`. Use
`check_occurrence_capture.py` for it; the immutable earlier checker continues to
validate v1 artifacts. The new report separates:

- `table_payload_ranges_complete`: every nonzero copied GPU table entry resolves
  unambiguously to full payload bounds.
- `descriptor_payload_ranges_complete`: every non-null buffer descriptor resolves
  to captured payloads, and any null entries have enabled-feature evidence.
- `replay_complete:false`: images, temporal state, underlying imported-memory
  aliases, CPU writer quiescence and other replay requirements remain absent.

A complete buffer-input report therefore remains an intermediate capture stage.
No native-feature, rendering-quality or FPS improvement is claimed by it.

## Runtime observation

The frozen fourth-patch renderer and separate optimized input-isolation host
were exercised in isolated diagnostic runs. [Sanitized evidence](../research/gpu-buffer-closure-observation.json)
pins both binaries, all patches, the shader digest and exact recording completion.
Occurrence 2 still had the late-import table hole. The explicitly selected
occurrence 64 captured 128 buffer pairs; the independent checker verified every
artifact and resolved all 286,720 nonzero GPU-table entries without ambiguity,
partial pages or missing payloads. All live buffer descriptors had payloads;
two null array entries had enabled-feature evidence. Both processes stopped.

This establishes buffer/table range coverage for that later dispatch. Images,
samplers, alias normalization, CPU mutation and temporal state remain open, so
`replay_complete` stays false. The patched host entered the game and reached the
selected dispatch with persistent script exclusivity requested; this run did not
independently inject live input or prove camera/workload determinism.

## Validation and patch composition

```sh
python3 patches/hookshot/check_occurrence_capture.py --checkout local/hookshot-source
python3 -m unittest discover -s tests -p test_occurrence_capture.py -v
python3 patches/hookshot/check_occurrence_capture.py --validate-snapshot local/private-capture
```

The checker applies all four canonical patches using an alternate index, rebuilds
the standalone helper from public patch text, compiles it with optimization, and
validates a synthetic v2 capture containing a guest buffer, a nonguest buffer and
two feature-backed null descriptors. It checks filtered counting, invalid and
bounded selection, pending completion and short-write refusal. Python regression
tests cover the corresponding manifest rules, descriptor bounds, missing payloads
and guest-ownership separation. These checks execute no GPU workload and provide
no independent GPU attestation.

The fourth patch edits only `vk_memory.cpp`, `vk_vma.cpp`, `vk_internal.h` and
`allocation_capture.h`. It does not edit the separate graphics-identity patch's
`vk_draw.cpp`, `vk_progcache.cpp` or graphics helper. Composed renderer builds
require their own final hash and runtime validation.
