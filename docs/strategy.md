# Decision: Arc-first targeted reconstruction

Decision date: 2026-11-12. Four independent assessments examined full matching, targeted GPU work, verification risk and reuse of the existing architecture. Their recommendation is adopted: make correct and efficient Intel Arc execution the primary track; use byte-matched CPU reconstruction to establish the subsystem contracts needed by that work. Whole-game matching remains a valuable independent long-term goal, not a prerequisite for graphics work.

## Why

The current measured problem is translated GPU work, distributed across graphics, compute and memory transfers. Rebuilding the original ARM64 CPU executable exactly would still submit the console-oriented shader algorithms. It would not itself validate or improve NVN2-to-SPIR-V translation, Vulkan synchronization or Intel register allocation.

Hookshot already replaces SDK graphics exports through native host dispatch and exposes the renderer and shader translator as source. Subgroup policy, shader IR lowering, memory-table handling, descriptor ownership, synchronization and cache identity are editable there. We should preserve that working foundation through a pinned upstream source checkout and reviewed patch series, rather than immediately replace the entire engine.

The Open-BFME experience supports automated, parallel, small-unit reconstruction. It is a reason to retain matching as a strong tool, not a reason to put every unrelated CPU function on the graphics critical path. Our initial complete-function leaf matches establish a functioning probe; they do not establish the original compiler, C++ ABI, relocated linking or whole-game recovery.

## Critical path

1. Resolve a timed runtime program key to its exact stage, variant, SPIR-V digest and launch/resource contract. A matching cached variant is not proof of which variant was timed without runtime evidence.
2. Capture complete replay inputs: descriptors, bound ranges, initial resource contents, aliases, dispatch arguments, specialization, subgroup assumptions, barriers and relevant temporal state.
3. Establish a reference hierarchy. Original semantics and known-correct console behavior are strongest; cross-vendor behavior helps; current Arc output is a regression baseline, not automatic console truth.
4. Make one narrow transformation and compare every written resource, including indirect arguments, counters and readbacks. Repeat the baseline against itself before defining numerical tolerances.
5. Check downstream rendering and repeated, unprofiled, equivalent-scene frame times. Reject omitted work, altered gameplay or a frame-generation-only gain.
6. Expand the proven method to shader families and the largest graphics stages. One compute target is not the whole frame.

The first candidate is documented in `research/shader-target.md`. Its lack of explicit subgroup instructions or shared memory supports investigation of selective subgroup policy. It does not rule out global-memory aliasing, cross-invocation dependencies or implicit warp ordering. A hash-specific experiment is a diagnostic, not the final general portability rule. Translation, runtime pipeline creation, prewarming and cache-policy identity must agree.

## When reconstruction expands

Decompile the CPU producers or relevant shader algorithm when buffer layout, resource ownership, dispatch selection, synchronization or hidden assumptions are unclear. Extend byte matching at those boundaries. Broader matching becomes a primary track if CPU translation is a measured limit, game-side architectural changes are repeatedly necessary, or complete source ownership becomes a separate deliverable.

The strongest risk of targeted work is accumulating isolated workarounds that pass one forest scene and fail elsewhere. Counter it with complete contracts, deterministic replay where possible, a growing scene/transition corpus, upstreamable fixes and explicit unknowns. Do not silently discard that risk by calling a handful of tests perfect support.

## Scope of success

Correct rendering and gameplay across the defined coverage corpus, stable device behavior and sustained real frame time at specified A770 settings come first. Higher quality and named integrated-GPU targets follow measured budgets. Neither matching the CPU executable nor passing finite replay tests justifies an unqualified claim of perfection.
