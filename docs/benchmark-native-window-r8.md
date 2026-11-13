# Native collection window r8

This note describes the tested r8 collector as a diagnostic capture. It is not a
benchmark, a rendering-quality result, a restoration result, or an FPS result.
Begin and close are CPU controller boundaries. They are not whole-frame, camera,
or workload equality.

The new patch is `patches/hookshot/benchmark-native-window-r8.patch`. It applies
to the locally assembled source tree
`85562df89be995cba1985717f26aa5df8cd118f8` (frozen v1 collector plus the explicit
`host/capture_backing.h` overlay). It does not replace
the private r5 patch
(`d9c4e15f19f4a35ba121545adb841722d9ea80e9c95a2a50f673dc67fdb4545d`), which stays
the r5 candidate. The base tree object is not distributed by this package, so
this patch alone is not an installation recipe from a clean upstream checkout.
The portable CPU checks extract the authored header directly from the patch.

`git apply --whitespace=nowarn` reproduces the executed source bytes. `git apply
--whitespace=error` rejects the patch because one executed line in
`host/control.c` ends with a space. That space is part of the tested file. The
published patch keeps it. Stripping it would make the published file differ from
the file that was compiled.

## What r8 changes

The owned command copy is still a full flatten under the same 8 MiB aggregate
and 4,096 events. A copy that does not fit is refused. The cap is not raised.
Raw command hashes stay unnormalized. Unknown bits are not cleared to force a
comparison. Startup counters and the begin preamble are not earlier history.

Coverage is an exact many-to-many link between a native submit and every
recording part it actually produced, including split, resume, and shared-burst
parts. A link needs the slot generation, the actual batch or submit result, and
the completed timeline query. Parallel-segment part 0 stays excluded. The
runtime parallel count in this acquisition is 0.

The root 7 watcher passes `observed_value` into `begin_recording`'s default
observer argument while the watch is active. The GPU scheduling tag, `r.value`,
and the command bytes are unchanged.

r8 flushes coverage links at run-submit end. While a native run is still active
or suspended, a missing link stays pending. It is not yet a fatal missing-link
fault. After that run settles, a missing owned part is fatal. Parts that are
already GPU-complete still cannot publish while their native run is active.

The r8 reader reports `native_run_lifecycle` and `all_native_runs_settled`. A
legacy CPU fixture with no native run decodes as unknown lifecycle.
`all_native_runs_settled` is false. Unknown is not a runtime permit. The actual
runner requires `all_native_runs_settled` true. Dropping the run-start or
run-end event leaves the capture unsettled.

A failed close returns terminal error state 4 with structured status and a
bounded fault list, including when `ok` is false. Control replies do not export
command bytes. Publication is write, fsync, close, exclusive link, directory
fsync, then `written` true. An existing file or symlink is not replaced.
Repeated negative or saturated statuses do not append an event per repeat.

The C function table matches the r6 table. The r8 header's `OoWindowStatus`
layout also matches r6. It does not match the smaller r5 status struct.

## Acquisition that passed

One private root run acquired a window. The counts below are the sanitized
result. The native command file, the PNGs, the raw logs, and the full captured
JSON report are not part of this candidate.

| Fact | Value |
| --- | --- |
| Owned streams | 40 |
| Native submit runs | 19, all settled |
| Native presents | 4 |
| Recording parts | 108, each with an actual submit and an observed completion |
| Payload bytes | 4362416 |
| Producer events | 1052, including begin and close (1050 core plus unselected-slot reader events) |
| Lost / pending / structural errors | 0 / 0 / 0 |
| Bounded producer faults | 3: source atomicity, burst, event wait. Origin and part are 0. Those bits sit outside the fatal close mask, so the window still reached `written` |
| Process | 2390145, TERM, reaped at 42.58s, pid gone, no KILL |
| Root plan | `35009e203f6b0d8a944ddcaefcfc2de3c72ffa640356d3a6ae890445ae398fde` |
| Host | `b667aed8ad70dee7c5e4c34f763ec1e945ebfa801479521f285c16e1ba9f1fb0` |
| Renderer | `9ca436492e73cd65c7117ea564546359d74a1881131120cfe447edf456ce8a65` |
| Native window bytes | `dee5dc3c52897dd74820f492320aeccac20410e1d67e7f33f92878715ad528e4` |
| Report digest | `83f51bfc9547c001edf2f1b8eeda1dcdca2d309d96e115baabf2cd7af123aa4f` |
| Result digest | `0651adef07f14c3551d355c28d3858626d7dd3580f094574dedf1b7a81a577e8` |

Maps, environment, driver, assets, settings, and pins were unchanged. The root
viewed a before PNG and an after PNG. Those images are not published.

Still unknown: source atomicity, bursts, event waits, and all four
camera, memory, input, and dependency bits. Ten native NN workloads were not
in this window. There is no timing, FPS, native-quality, or restoration
acceptance.

Earlier results stay as they were. The earlier r4 acquisition reported
`BAD_ORIGIN`; a valid owned stream above 1 MiB reproduced that refusal on CPU.
The r5 acquisition copied 4,374,256 bytes with no reported loss, then failed
close with `BAD_PART`; its precise runtime hook was not recorded. The r7 acquisition failed closed on a
premature missing-link diagnosis. The r6 root-03 launch never ran.

## Pins

Executed file SHA-256 after `--whitespace=nowarn` reapplication:

| Path | SHA-256 |
| --- | --- |
| `host/oo_benchmark_window.h` | `207893b73e8e884c46b0201e76cdfbc946563ae8e705ef52c458f25ad63066fc` |
| `host/nvn_native.c` | `cf22df90f2df652762a1fc697cb7d3e6f895c7b60980fb16078b61c6d798f6ab` |
| `host/control.c` | `1439a963d035d8791aa3bcb4128b998ae352285dd351ba60dbbd4b25985a8550` |
| `gpu/vk/vk_replay.cpp` | `bcc7cb05d2f24797044eea819ba391cf2f36f81cc900910b0b9a4a87142487b1` |
| `gpu/vk/oo_benchmark_renderer.h` | `f46c0102c1f98b42f29164c991359070acded85025ad9182b57c5586ad52bad3` |
| `tools/benchmark_native_window_r8.py` | `ebf8f568af5a9d0d7c5117bdb41412228270119aeb90747910f488c4ef9940dc` |

The applied tree recorded by the disposable index is
`2ca541552afc7563807f381e7d1aa50cfd9b7fa9`.

## Checks

```sh
python3 -m unittest tests.test_benchmark_native_window_r8 -v
python3 patches/hookshot/check_benchmark_native_window_r8.py --compiler gcc --prove
python3 patches/hookshot/check_benchmark_native_window_r8.py --compiler clang --prove
```

The unit fixtures are synthetic bytes. They do not read a game, a shader
binary, or a private capture. `--prove` reapplies the patch on a disposable
index when the pinned base objects are present and compiles the watcher chunk
from the applied `vk_replay.cpp`. The standard suite reconstructs the pinned r7 header from its open counts helper and reproduces both pending-run bugs before checking r8. It also checks the reader CLI outside the repository. It does not launch a game.
`--source` remains an optional private profile for the actual submit wrapper.
