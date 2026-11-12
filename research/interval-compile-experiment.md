# Synthetic interval lookup compilation

Date: 2026-11-12. Status: independently authored synthetic shaders compiled, **not dispatched**. This tests the lookup-identifier component of the [interval proposal](radical-address-lowering.md), not a replacement for the real shader's descriptor-pointer or physical-memory operations.

## Question and construction

Does this Mesa compiler expand a binary-search lookup into a cascade again, erasing its code-size opportunity?

`tools/interval_compile_experiment.py` generates two lookup-ID helpers with the same four single-buffer descriptors, push constants and dynamic query interface:

- **Cascade:** nearest-base candidates first, then an explicitly emitted first-match single-claim chain. An optional existing table scan remains a loop; it is not misrepresented as another unrolled chain.
- **Interval:** the same nearest-base candidates, then binary search of sorted, disjoint first-match intervals. Each record returns the original logical binding owner **and the offset relative to that owner's original base**, including after an overlapping interval was split.

The helper result is an integer pair. No returned owner is used to index a descriptor array. This deliberately avoids claiming that a per-lane descriptor choice is dynamically uniform or that the current storage-buffer-array feature is sufficient for the proposed native pointer lowering. That harder integration remains open.

Every invocation makes eight calls with separately loaded queries and combines the results into its output. Queries, claim records, interval records, salt and interval count are runtime inputs, so the frontend cannot replace the lookups with known answers. Workgroup size is 64×1×1; every measured pipeline requires subgroup 32/full subgroups.

Glslang 16.2.0 targets Vulkan 1.2-compatible SPIR-V, subsequently validated for Vulkan 1.3. The generator marks the lookup helper `DontInline` and every generated loop `DontUnroll`. The synthetic binary-search module contains one loop and eight helper calls; the compiler reports eight loops in its executable. The cascade has no loops unless the optional pre-existing table scan is enabled.

## Observed executable statistics

All cases below report actual subgroup 32 and zero spills, fills and scratch. `Instructions` and `Loops` are driver executable statistics, not elapsed execution or frame time.

| Single claims | Nearest candidates | Existing looped entries | Cascade instructions | Interval instructions | Cascade / interval executable loops |
|---:|---:|---:|---:|---:|---:|
| 4 | 2 | 0 | 867 | 843 | 0 / 8 |
| 8 | 2 | 0 | 1,283 | 843 | 0 / 8 |
| 16 | 2 | 0 | 2,115 | 843 | 0 / 8 |
| 32 | 2 | 0 | 3,779 | 843 | 0 / 8 |
| 64 | 2 | 0 | 7,107 | 843 | 0 / 8 |
| 5 | 0 | 3 | 1,054 | 569 | 8 / 8 |

The last row is a **descriptor-count-inspired component shape**: the selected real module reflects five single storage-buffer bindings and three descriptors in one array. It does not reproduce that module's complete lookup metadata, candidate classification, types, call sites, aliases or physical miss path. Its existing three-entry table is already looped on the cascade side, as the inspected Intel translation intends.

The large single-count rows are scaling experiments, not a claim that the selected real target has 64 unrolled single descriptors. With only four singles, the static instruction difference is small. Retaining the small and target-shaped cases prevents the worst-case row from being sold as a real-shader result.

For the initial 16/32/64 runs, cascade SPIR-V grew from 3,735 to 9,735 words while interval SPIR-V stayed at 2,002 words. Source-level interval capacity changes the clamp constant but not its loop body. Mesa preserved the loop rather than expanding it into the large cascade in these cases.

Each pipeline creation took approximately 0.01–0.22seconds in these exploratory runs. These are single, separately cold shader-cache observations with ordinary host/loader/page-cache variation, **not a repeated compile-time benchmark**. No timing ratio is credited.

## Build and device profile

The experiment reused the optimized compile-only harness:

- Intel Arc A770 / DG2, Mesa 26.2.4 with the existing private patch stack.
- Selected driver SHA-256: `5ea19ac96aa52d061ecf2b8e23df7ed3d7d3b36c7d2ae7fe62f01c5a9b610aaa`.
- Audited Mesa build: `debugoptimized`, optimization 2, `NDEBUG`, debug symbols, no LTO. The selected library digest equals the audited build output.
- Host helper: `-O2 -DNDEBUG -Wall -Wextra -Werror`.
- Vulkan shader optimization enabled, robustBufferAccess/robustBufferAccess2 enabled, push descriptors, a 16-byte push range, equal relevant device features across shapes.
- Each policy has an independent empty Mesa shader-cache directory. The helper creates compute pipelines and queries executable statistics; it does not retrieve/submit a queue, create command buffers or dispatch shaders.

## Reproduce locally

Use a new output directory for every run. The `HS_*` options below belong to the existing private driver stack, not ordinary upstream Mesa requirements.

```sh
python3 -m unittest discover -s tests -p test_interval_compile_experiment.py -v
python3 tools/interval_compile_experiment.py \
  --driver "$INTEL_ICD_JSON" --mesa-build "$MESA_BUILD_DIR" \
  --output local/interval-compile/scaling-repro --counts 4 8 16 32 64 \
  --accesses 8 --candidates 2 --policy pinned32 --timeout 90 \
  --env HS_CS32_MAX_INSTS=100000 --env HS_SPILL_BATCH=1 \
  --env HS_PUSH_SHADOW=1 --env shader_spilling_rate=1
python3 tools/interval_compile_experiment.py \
  --driver "$INTEL_ICD_JSON" --mesa-build "$MESA_BUILD_DIR" \
  --output local/interval-compile/small-table-repro --counts 5 \
  --accesses 8 --candidates 0 --table-entries 3 --policy pinned32 --timeout 90 \
  --env HS_CS32_MAX_INSTS=100000 --env HS_SPILL_BATCH=1 \
  --env HS_PUSH_SHADOW=1 --env shader_spilling_rate=1
```

Additional dependencies are glslangValidator and the compile probe's Vulkan/SPIRV-Tools dependencies. Generated sources, SPIR-V, helper binaries, validation logs and full driver reports remain under ignored local storage. The first recorded runs used `local/interval-compile/first`, `small` and `target-shaped`.

## What this establishes and does not

The small-loop code-size opportunity survives this optimized compiler for a synthetic lookup-ID component. The CPU tests compare selected owner and original-relative offset across explicit overlap/nearest/tie/boundary cases and a deterministic random grid. They do not substitute equal-zero bytes for identity.

This does **not** prove real shader equivalence, a descriptor-pointer implementation, a physical-pointer capture, lower scratch in the real module, lower GPU execution time, or higher FPS. The synthetic shaders have no spilling problem, so their results do not establish how the real register-pressure cliff changes. Binary search introduces per-lane divergent metadata reads; a compact executable can still run slower than a short uniform cascade. A real lowering must preserve typed aliases, robust bounds, 64-bit guest arithmetic, storage class, stores/atomics and the original miss path. Actual complete replay remains required before a runtime optimization.
