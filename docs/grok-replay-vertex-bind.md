# Grok replay vertex-bind and push elision

Opt-in command-buffer state for one Hookshot `vk_draw.cpp` derivative. The patch is
`patches/hookshot/grok-replay-vertex-bind.patch`. Its helper is added only inside
that patch (`gpu/vk/grok-replay-bind.h`). There is no second standalone header.

This does not claim a frame-rate change, a visual match, or that enabling the
switches is safe on every future caller.

## Controls

Both variables are off unless set to a non-empty value other than `0`. `1` enables
that switch. Unset and `0` leave the original calls in place.

| Variable | Off (default) | On |
|---|---|---|
| `RX_VK_GROK_REPLAY_BIND` | one `vkCmdBindVertexBuffers2` of all 16 slots when the upstream slot compare is dirty | at most three dirty contiguous runs; four or more runs, or a new command buffer, bind all 16 |
| `RX_VK_GROK_REPLAY_PUSH` | every full-range `vkCmdPushConstants` from `push_constants` | skip that call only when the command buffer, pipeline-layout handle, stage mask and all payload bytes match the last issued full push |

Draws, barriers, descriptor writes and the selected-dispatch observer
(`oo_dp::note_push`) are not inside the skip. `oo_dp::invalidate_push()` still runs
on entry to `push_constants`.

## What was audited

Direct `vkCmdPushConstants` in `vk_draw.cpp` other than the full `kPushSize` push
are followed by `grok_replay_push_invalidate()`:

- masked color clear (fragment, 16 bytes, `clear_layout`)
- mask stabilize (compute, full struct, twice)
- mesh firsts (same game layout, 8 bytes at `MESH_FIRST_PUSH_OFFSET`, both indirect and direct)
- movie upscale (compute, 8 bytes)
- typed / TAAU upscale (compute, 12 bytes)

Opaque helpers that push on the **draw** command buffer:

- `fsr3_stand_in` used to `return fsr3_upscale(...)` with no invalidation. That return now invalidates after the call, including when the helper returns false. `vk_fsr3.cpp` and `vk_fsr3_inputs.cpp` push shorter compute ranges at offset 0.
- `temporal_stand_in` and `temporal_rejected_network` invalidate after `temporal_upscale` / `fsr3_upscale`. `vk_temporal.cpp` pushes actor constants (`VERTEX|FRAGMENT`, `actor_layout`) and several compute ranges. Those actor draws do not call `vkCmdBindVertexBuffers`; they read device addresses from push constants. `last_vb_valid` is still cleared by the existing temporal paths.
- `draw_begin_recording` invalidates. `vk_replay.cpp` calls it after `vkResetCommandPool` on the draw command buffer. The upload command buffer is a different `VkCommandBuffer` (`texture_begin_recording`).
- `memory_end_submission` and inline `framegen_record` push on a command buffer that is ended before the next `draw_begin_recording`. They are not between two `push_constants` calls on a live cache.
- `reactive::copy_scene` (`vk_reactive.cpp`) does not call `vkCmdPushConstants`. `reactive::mask` does, from FSR/DLSS paths reached through the helpers above, which now invalidate on return.

## Pipeline-layout ranges

Elision keys the layout **handle**, the stage mask and the full payload. A
different handle or stage mask reissues. Graphics and compute pushes of the same
program share `p.layout` and `p.push_stages`; a compute helper layout is a
different handle.

Game pushes use `[0, kPushSize)` (`MESH_FALLBACK_PUSH_END`). Clears, FSR, temporal,
reactive, movie and TAAU use other layouts or shorter ranges starting at 0. The cache treats those foreign pushes as invalidating its full payload.
The patch does not try to preserve the untouched tail. It invalidates and the next
full push is issued.

Mesh firsts are a subrange of the **same** layout. The tail would remain defined.
The cache is still invalidated, so the next full push is issued rather than merged.

## Not proven

A new caller that records `vkCmdPushConstants` on the draw command buffer and then
reaches `push_constants` without `draw_begin_recording` or
`grok_replay_push_invalidate` would make an enabled push cache wrong. Vertex
narrowing uses the same `last_vb` snapshot the upstream 16-slot skip already uses.
A foreign `vkCmdBindVertexBuffers` that does not clear `last_vb_valid` is outside
this patch.

No device was used. No frame time or image was compared.

## Frozen private build

The translation unit was compiled from a private copy after `patch --fuzz=0`.
The object was linked into a new `hookshot_vk.so` by substituting it into the
frozen renderer's existing object list. The other objects were not rebuilt.
The library was not loaded and no GPU work was submitted.

Flags: `-std=c++20 -O2 -g0 -DNDEBUG -march=x86-64-v3 -ffp-contract=off -fPIC`,
`VK_NO_PROTOTYPES`, `MKWVK_BUILDING`, `HOOKSHOT_RELEASE`, `MKWVK_INCLUDES=2`.
Link: `clang++ -fuse-ld=lld -shared -Wl,-Bsymbolic -Wl,--gc-sections
-static-libstdc++ -static-libgcc`.

| Artifact | SHA-256 |
|---|---|
| patch used for the frozen build | `4e75658e02dd6f6e96722386b7d7d44f26085920ce4332ce56afbafc6d5fe224` |
| unit test | `83f7339a06da9f367cbb163a78c4c2d01604c00550016b0303e91c801cc7ac49` |
| C++ harness | `b20c09b5a0b614db15a33ebee577bc56ce336ed186d4c3f7ee686e5a443a2825` |
| check tool, publication revision | `d105c5b23776374914f2db42c12df29dc0443d25226d94822dbe61d636884b85` |
| patched `vk_draw.cpp` | `f6789d5102869911b8694d44e0ec63fc3878b62e0ddd035db7dfa8bae140b4d4` |
| patched helper header | `29896d5050f9c0e9aacb1913912b0994dd4a83c88faf12542953a385dc163bf3` |
| `vk_draw.o` | `a7d7bdb7cd13395251e9bac69c0d4f540b2922d347181872bc575f62a99d1ade` |
| linked `hookshot_vk.so` | `7c4dd379f03158710aca44b95672cbc26f462e1b7baba04927f954e72c5670cc` |

The patch, compiled source, object and binary identities remain the frozen build's.
The publication revision only adds a test/tool guard preserving existing work
directories and explicit process timeouts; the original build evidence is retained.

## Checks

`tests/test_grok_replay_vertex_bind.py` extracts the header and the marked
`push_constants` / vertex callsites from the patch, compiles them at `-O2
-DNDEBUG` with mocked Vulkan entry points, and checks them against a full-bind,
always-push oracle: mixed draws, barriers, descriptors, observer notes, per-field
offset/size/stride dirtiness, four-run fallback, a new command buffer, foreign
layout invalidation, and both switches off.

`tools/grok_replay_vertex_bind_check.py` applies the patch with `--fuzz=0` to a
private copy of `vk_draw.cpp` and can compile that translation unit. It does not
modify the source tree it reads. Work directories must be fresh or empty; an
existing tree is preserved. Patch application, compilation and linking have
explicit process timeouts.

The published patch normalizes empty context lines for repository whitespace
checks. Its SHA-256 is `5dc00bd452adb7a4f91b9fa843bd35a8e1be00e9056a096fccc5bbeda77013aa`.
Zero-fuzz application reproduces the exact compiled source/header hashes above;
no renderer source instruction changed during publication.
