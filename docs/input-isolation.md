# Script-exclusive native mouse isolation

Status: a host-only patch, optimized game-free tests and a separate optimized host executable are implemented. No patched game run or complete deterministic-camera producer has been validated by this lane.

The original `read_input` returns before merging player devices during an exclusive control-bridge lease, or while `HOS_INPUT_EXCLUSIVE=1` owns a parsed script's time window. Native mouse camera updates consume mouse motion separately, so that return did not isolate that camera path. The patch makes both paths consult the same ownership policy and consumes mouse deltas while they are excluded.

## Behavior

- Default behavior remains unchanged: scripts are additive unless exclusivity is explicitly enabled. A persistent flag alone does not disable controls.
- `HOS_INPUT_EXCLUSIVE=1` retains the script-clock window ending five seconds after the original script's last press. `HOS_INPUT_FRAME_CLOCK=1` continues to use native presents for that script clock.
- Optional `HOS_INPUT_EXCLUSIVE_PERSIST=1`, together with `HOS_INPUT_EXCLUSIVE=1` and a parsed nonempty script, keeps player devices excluded after that window. This is a benchmark/test mode; do not place it in a normal gameplay configuration. No parsed script means it does not arm itself.
- Control-bridge exclusive input remains frame-bounded and independent of the persistent script flag. A new read-only lease query locks the existing input list but never starts a queued input, consumes it, advances its frame clock or services memory requests.
- Both controller-read early returns and native mouse-camera hooks drain excluded motion. The native camera drops its saved target while isolated and resumes from the game's current angles when released, preventing old motion or old saved angles from being reapplied.
- Script-exclusive reads skip physical ocarina-note sampling as well as the normal pad/key merge. The intended scripted buttons and stick still reach the game.

This is input routing, not a camera freeze. The game can still center, animate or switch cameras in response to simulation and scripted inputs. Host menu taps/drags, motion-control routes, live debug commands and other state-changing APIs remain separate audit obligations. The existing live-input parser does not extend the original script-end field; persistent mode avoids that expiry for an already parsed test script but does not redefine the parser's timing. Final view/projection, jitter/history and per-frame workload evidence are still required by [the benchmark contract](benchmark.md).

## Patch and local tests

The patch is against Hookshot commit `4cf785a0b143bb34026506c9a3fe4f4bb93c797d` and changes only host files plus two test-fixture imports. It is independent of the GPU identity/page-table patches. Existing upstream notices apply; the new policy helper is GPL-3.0-or-later.

```sh
git -C local/hookshot-source apply "$PWD/patches/hookshot/input-isolation.patch"
python3 patches/hookshot/check_input_isolation.py --checkout local/hookshot-source
python3 patches/hookshot/check_input_isolation.py --checkout local/hookshot-source --compiler clang
python3 -m unittest discover -s tests -p test_input_isolation.py -v
```

The checker compiles with `-O2 -DNDEBUG` and strict warnings. Without a checkout it exercises the actual patched policy header using synthetic clocks/leases. With the applied pinned checkout it additionally compiles the actual production mouse-camera source against authored fake memory/device APIs, extracts the actual read-only control query, and tests the actual script-ownership function in fresh processes.

Verified locally: active and expired script windows; explicit persistent mode; disabled/persistent-only/empty-script cases; future and ended control leases without sequence consumption; suppression and draining in production mouse hooks; release without an accumulated jump; unchanged nonexclusive mouse behavior. GCC and Clang both pass. The modified `nn_hid.c`, `mouse_native.c` and `control.c` translation units also passed optimized Linux syntax checks using existing local generated headers. Existing upstream mouse-native, scripted-input/background and player-key tests passed after adding only the new helper/header dependencies to their authored fixtures.

No live game, desktop input, GPU work, game assets or save changes are needed for these tests. They do not establish isolation of every native feature or a valid performance comparison.

## Optimized host build

A separate private host executable now links the patch against the pinned
translation/runtime object cache. The build verified cache keys, rebuilt 46 host
objects and reused 234 compatible objects, including all 80 game chunks. It used
the bundled Clang 23.1.5 and glibc 2.35 sysroot, `-O2 -DNDEBUG`,
`-march=x86-64-v3`, `-ffp-contract=off`, and four compile workers. The renderer and
existing host executables were preserved. The new executable has not yet been
run with the game.

Frozen patch SHA-256:
`5664975cb661fe5f17a2d599e22420d461fddd5b3b56c67f7c6e4896e406f66b`.
Frozen host SHA-256:
`587186b80aa3068addf8aaace5de1bab758043498d1e26a3c92e8b9ef5b0ba8c`.
Generated code, objects, executable and the detailed build receipt remain under
ignored private storage. Linking does not establish runtime isolation or
deterministic camera evidence.
