# Active goal: native-quality Arc A770 support

The maintainer requested sustained autonomous work toward a correct, stable native PC path on their Intel Arc A770, preserving the original game's features and visual quality, with sustained real 60 FPS in representative gameplay. Link's hair and foliage edge shimmer are explicit image-quality concerns. This goal is active, not achieved.

## Acceptance criteria

- **Gameplay and native features:** startup, navigation, gameplay systems, scene transitions, streaming, animation, physics, audio, input, save and load must continue to work. Rendering work must not hide a gameplay or readback failure.
- **Rendering fidelity:** geometry, hair, foliage, transparency, lighting, shadows, fog, effects, post-processing and reconstruction/upscale behavior must be checked against documented references. An omitted shader or pass is a correctness failure.
- **Real performance:** sustained real 60 FPS at an explicitly recorded output resolution, internal resolution and quality configuration, across a representative scene and transition corpus. Profile separately from uninstrumented timing. Generated frames alone do not meet this goal.
- **Stability:** no device loss, corrupt resources, unexplained nondeterminism, stale indirect arguments or save corruption in the coverage tests. Keep the established working install separate from experiments.
- **Hair and foliage quality:** distinguish incorrect geometry/alpha/texture sampling from spatial aliasing and temporal shimmer. Evaluate stationary and moving camera/actor sequences. Any new AA treatment must preserve detail and be tested for ghosting, blur, specular stability and cost.
- **Publication:** maintain a reproducible, tested public codebase. Keep original game files, generated translations, shader binaries, assets, screenshots, saves, private captures and credentials out of the repository.

The initial exploratory setup used 2048×1152 output and an upscale path. Those old runs are not the locked acceptance baseline: their camera framing differed. A validated benchmark manifest must record output and internal dimensions independently before performance claims. Broader resolution and quality targets will follow measured budgets; an exact feature implementation may be replaced only when its intended behavior and quality are preserved or deliberately improved.

## Working order

1. Preserve and document the existing behavior, including known omissions and unknowns.
2. Resolve exact shader identity, memory contracts and complete capture inputs.
3. Measure compilation quality with representative release settings and device features.
4. Establish differential correctness and deterministic scene benchmarks.
5. Land measured renderer/shader improvements, expanding reconstruction where needed.
6. Restore or improve deficient image-quality paths and evaluate hair/foliage AA within the frame budget.

Correct native rendering and image quality are part of the goal, not optional switches to disable for a performance headline. CPU matching and GPU optimization remain separate verification tracks under the [Arc-first strategy](strategy.md).

## Compilation policy

Use optimized, reproducible performance builds and record compiler versions, flags, enabled Vulkan features, shader optimization controls and cache identity. Do not compare a debug/no-optimization build with a release build and attribute the difference to a shader transformation. Matching probes retain the exact settings needed for identity rather than inheriting performance flags. Floating-point or aliasing assumptions must not be weakened silently by aggressive compilation flags.
