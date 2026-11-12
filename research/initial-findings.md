# Initial observations — 2026-11-12

## Scope and evidence

The initial local environment uses an Intel Arc A770 16 GB on Linux and an existing Hookshot install. This repository does not distribute that install or provide game downloads. The CPU module inventory and GPU timing export are metadata-only observations, not reconstructed source.

The initial full-workload timing table is preserved as `gpu-profile-initial.json`, including source-log SHA-256 and source line numbers. It contains recording and upload command-buffer attribution. Timestamp serialization makes its summed spans different from ordinary frame latency. The parser intentionally uses a combined denominator for shares rather than mixing the source's per-queue percentages.

## Established locally

- The working configuration depends on the game's 32-lane subgroup behavior. Blanket unpinning previously broke rendering. Four oversized compute programs used a local narrow-width fallback; it is not evidence of a general safe subgroup-width conversion.
- Large translated shaders spill heavily on DG2. This is an optimization target, not proof that subgroup conversion is the only problem or that the hardware cannot meet a future target.
- A previous CPU descriptor-copy fix reduced avoidable reads from mapped GPU memory. The private driver carrying it is not replaced by this repository.
- Memory-sync CPU waits largely reflected pending GPU work in the measured workload. Removing one wait did not remove the corresponding GPU work.
- Lower resolution and upscaler changes did not establish a large frame-rate improvement in those exploratory runs.
- A two-line Mesa register-allocation adjacency-index correction reproduced a duplicate-edge defect before the fix and passed the same regression afterward. This validates the index correction, not a game FPS gain. See `patches/mesa/README.md`.
- A newer Hookshot build with memory-table loops reached gameplay. Native floating-point atomic add was also tried. Neither experiment established a large validated FPS gain.

## Important limitation

The stable and nightly screenshots had different camera framing and different draw/pass counts. Their exploratory frame-rate samples cannot support a controlled speedup claim. Do not copy their small numerical differences into a performance headline. The next benchmark must explicitly control the camera and workload and record image correctness before timing.

## First shader targets

The profile identifies compute key `cc62764061c44f6a` as the largest named compute span. Its algorithm has not been identified. The large graphics spans ending at `d8e59341cf4f629c` and `43ec86f35fd937b9` also warrant stage-specific attribution. No single named span accounts for the whole frame; eliminating one shader is not a demonstrated path to the full performance target.

## Open questions

- Can original ARM64 compiler behavior be reproduced across representative functions, beyond trivial leaves?
- Which exact stages, specialization and resources belong to each expensive runtime program?
- Can complete replay inputs be captured without changing the program's dependencies?
- Which algorithmic or live-range changes reduce scratch traffic while preserving results?
- What quality settings and devices define subsequent performance targets?
