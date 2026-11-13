# Opt-in mirror copy planner

The authored [patch](../patches/hookshot/grok-memory-mirror-plan.patch) plans
`mirror_sync` copies from sparse page notes and whole-region stamps. It is off
by default; `RX_VK_GROK_MIRROR_PLAN=1` enables it. It preserves the original
page walker for protection/GPU-page tracking and whenever planning falls back.
The [CPU observation](../research/grok-memory-mirror-plan-observation.json)
records exact source and build identities. No gameplay or FPS result is claimed.

The base is frozen Hookshot tree `58ca709617448e06af382c3443dd88dac3ffcb8e`.
The patch adds one independently authored header and changes `gpu/vk/vk_memory.cpp`.
The standalone [planner header](../src/probes/grok_memory_mirror_plan.h) is the
same payload as the patch; tests check that equality.

A page whose retained capture generation equals its current generation stays
on that capture's original slot. Other dirty pages come from guest memory.
Copy order, offsets, sizes, write notes and retained capture identities follow
the original page walk. New/retired-pool paths are unchanged. Plans are private
until every region succeeds, so falling back cannot publish a partial plan and
then duplicate its copies.

Review found three defects in the initial prototype. The revision now publishes
atomic dirty notes before the chunk and region release-stores, and acquires the
region generation before taking an atomic note snapshot. Fixed region storage
avoids racing lazy pointer initialization. All source accesses to `region_gen`
use atomic acquire/release operations. This preserves the renderer's existing
assumptions about generation allocation and chunk lifetime; it is not a proof
that every original memory path is race free.

A retained capture can match a recycled numeric generation after uint32 wrap,
even when its chunk generation is below the whole-region stamp. The planner now
classifies that noted capture instead of treating it as stale. It preserves the
old walk's numeric-generation behavior. Every captured page must have a note
from its actual `stamp_page`; an invented capture without that history does not
satisfy the source invariant. Finally, page statistics count absolute guest page
boundaries, including clipped runs crossing a boundary.

The note array occupies 16 MiB when enabled and is allocated only on its first
use. Notes are never cleared. Dense regions can still visit all 512 pages and
check retained captures, so speed is unmeasured and depends on actual stamp
patterns. No GPU wait, capture copy, barrier, draw or dispatch is removed.

Fourteen focused tests pass, including actual atomic helper execution, concurrent
notes, a release/acquire handoff, clipped counters and generation wrap. A separate
page-walk oracle also passes 4,000 source-operation steps with capture slots,
retirement, pool edges and fallback checks. These checks are not a concurrency
sanitizer or an independently reconstructed game-output oracle.

The real modified `vk_memory.cpp` compiled with Clang 22.1.8, C++20,
`-O2 -DNDEBUG -march=x86-64-v3 -ffp-contract=off -fPIC`, Release renderer defines.
It linked with the separately tested [replay patch](grok-replay-vertex-bind.md)
and the remaining frozen objects. The new combined library is
`3daf50221cfc2a33065e725e0c4de5b3731724525b9c384797cda1ea0bfecb55`.
Only those two translation units changed; the other objects were not rebuilt.
The library has not been loaded or run. Raw commands, dependencies, object
hashes and failed prototype evidence remain private.

Reproduce the CPU tests with:

```sh
python3 -m unittest tests.test_grok_memory_mirror_plan -v
```

The next runtime check requires the same save/camera/settings and warm shaders,
with the original copy path as a control. Compare complete copy decisions and
rendered outputs before interpreting memory-sync or frame-time measurements.
The native-feature, quality, stability and sustained-real-60 requirements remain
separate acceptance work.

Publication normalizes empty patch context lines. A zero-fuzz reapplication
reproduces the exact compiled source and header; both patch identities are
retained in the observation metadata.
