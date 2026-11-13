# Hookshot diagnostic patch series

`compute-identity.patch` is an opt-in metadata-only patch against official Hookshot revision:

```
4cf785a0b143bb34026506c9a3fe4f4bb93c797d
```

Upstream: <https://github.com/NotRexed/Hookshot/tree/4cf785a0b143bb34026506c9a3fe4f4bb93c797d>. Hookshot and this patch are GPL-3.0-or-later. Existing upstream notices remain applicable. The new helper and tests are independently authored; no game files, shader code or generated game translations are present.

The renderer capture series applies in order: `compute-identity.patch`,
`page-table-snapshot.patch`, `allocation-capture.patch`,
`occurrence-capture.patch`, `graphics-identity.patch`,
`sampled-image-capture.patch`, `dispatch-parameters.patch`,
`binding-provenance.patch`, then `mesh-input-capture.patch`. Before compiling
the binding observer, apply the separate host `backing-lease.patch` so its ABI
header is available. Build the host with that patch to export the opt-in ABI;
the renderer includes declarations only. The independent input-isolation patch
can be composed with that host build. See
[GPU table capture](../../docs/page-table-snapshot.md) and
[allocation payload capture](../../docs/allocation-capture.md),
[sampled-image capture](../../docs/sampled-image-capture.md) and
[dispatch parameters](../../docs/dispatch-parameters.md),
[binding provenance](../../docs/binding-provenance.md) and
[mesh inputs](../../docs/mesh-input-capture.md) for opt-in controls,
build/runtime evidence and remaining replay gaps. Each patch retains the upstream
license. The independent [input-isolation patch](../../docs/input-isolation.md)
changes host input routing and does not depend on the renderer series.

The optional tenth overlay, `mesh-warp32-lowering.patch`, adds the disabled-by-default
[guarded native mesh experiment](../../docs/native-mesh-warp32-integration.md)
after that nine-patch capture chain. It changes only the exact reviewed program
on the recorded A770/driver/compiler configuration; it is not a generic subgroup
policy or a validated gameplay-performance release.

## Purpose

The profiler records a program key, but that does not prove which texture-answer variant or SPIR-V module was actually recorded. This patch observes one explicitly selected compute key and variant immediately before the native Vulkan dispatch command. It records a canonical little-endian SPIR-V SHA-256, workgroup size, requested subgroup policy, cache stamp, direct launch dimensions or an explicitly unresolved indirect address, and a fingerprint of the NVN binding slots and registration generations.

Every event is `open-ocarina.compute-identity/v1`, with `producer.stage=identity-only` and `replay_complete=false`. Resource contents, resolved descriptor contents, physical-pointer reachability, temporal state and GPU outputs are all marked uncaptured. This output must not pass a complete replay validator.

See [runtime-capture.md](../../docs/runtime-capture.md) for interpretation and the next capture boundary.

## Apply and test

From the Open-Ocarina root:

```sh
git clone https://github.com/NotRexed/Hookshot.git local/hookshot-source
git -C local/hookshot-source checkout --detach 4cf785a0b143bb34026506c9a3fe4f4bb93c797d
git -C local/hookshot-source apply "$PWD/patches/hookshot/compute-identity.patch"
python3 patches/hookshot/check_identity_patch.py --checkout local/hookshot-source
```

The check uses the pinned checkout's index without changing it, compiles the patch's isolated helper with `-O2 -DNDEBUG`, compares SHA-256 test vectors against Python's hashlib, and checks JSON identity, missing digests, disabled/invalid filters and the record cap. It needs a C++20 compiler. `--compiler g++` provides a second compiler check. It does not start a game or submit GPU work.

The patch updates three upstream cache/seed-test fixtures to include the new Stage metadata dependency. Their checks remain intact.

## Validation

- Exact pinned-index patch-application check passed.
- Isolated helper passed Clang and GCC optimized builds and nine SHA-256 vectors, including the million-byte vector and padding boundaries. The helper also independently agreed with Python on the exact private target SPIR-V digest recorded in the shader catalog.
- `vk_draw.cpp` and `vk_progcache.cpp` passed real translation-unit syntax checks against Vulkan, SPIR-V and VMA headers.
- A complete Linux renderer-only source build linked `hookshot_vk.so` with `-O2 -DNDEBUG`, `RX_RELEASE=1`, `x86-64-v3` and four workers. The output remains private and was not installed into the working game.
- All five selected upstream program-cache checks and the seed-variant cleanup check passed, covering module cleanup, whole/light variant loading and seed-owned module lifetimes.
- The full upstream game-free suite with the installed Vulkan/SPIR-V headers finished with **501 passed, 1 failed, 20 skipped**. The remaining failure is the existing README menu-row check for `Skip opening`; it reproduces using pristine files from the pinned commit. It is not hidden or fixed by this patch. Platform/GPU-dependent skips remain explicit.
- A subsequent isolated headless run recorded the bounded set of real identity events; its sanitized metadata is in `research/runtime-identity-observation.json`. Every event matched the selected program, variant and SPIR-V digest. The run preserved the default subgroup policy and stopped cleanly; original installs and saves remained separate. This establishes runtime identity, not resource equivalence or a performance gain.

## Default behavior

With `RX_VK_OO_IDENTITY_FILE` unset, no output is written, no SPIR-V is hashed and pipeline policy stays unchanged. Stage objects gain derived, nonserialized metadata. GPU commands, barriers, descriptor writes and shader translation are unchanged. With observation enabled, only the exact selected key and variant bypass the opaque module-identifier fast path, so their ordinary module-loading path can establish a real digest. Driver pipeline caching remains available.

The cache record format and translator stamp are unchanged: the added identity fields are derived metadata and are not serialized. This patch does not unpin subgroups, change compiler optimization flags, or claim complete captures.
