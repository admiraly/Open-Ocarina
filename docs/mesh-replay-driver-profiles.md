# Explicit driver and mesh replay profiles

The 6864 mesh now matches repeated original32 at actual widths 8 and 16 over all 67 workgroups of one chosen frozen readonly graph on the pinned E5 driver. All twelve cases across three windows pass complete byte comparisons, including count rows, writtenness, padding and guards. The two authored fixtures also passed eight fresh-budget cases against their complete CPU golden buffers. [Combined sanitized evidence, revision 2](../research/native-mesh-replay-e5-observation.json) preserves the earlier 32-group checkpoint separately from completion, the historical timeout and the compile-only diagnostic; none establishes final rendering or FPS.

The [earlier fixture-only checkpoint](../research/mesh-replay-e5-fixture-observation.json) and [previous D8 observations](native-mesh-replay.md) retain their original source, helper, driver and plan hashes. No game shader ran in that earlier fixture checkpoint.

The current replay wrapper requires an explicit driver profile and compiler policy during CPU preparation. It supports two separately identified mesh output contracts. Each preparation requires separate review and an exclusive GPU grant before execution; the two remaining windows received those grants before the completion recorded below.

## Driver admission

| Driver profile | Pinned library SHA-256 | Allowed named policies |
|---|---|---|
| `legacy-5ea` | `5ea19ac96aa52d061ecf2b8e23df7ed3d7d3b36c7d2ae7fe62f01c5a9b610aaa` | `diagnostic`, `runtime` |
| `cache-safe-e5` | `e5a1305ea3b423a18fccbb945aaedd32f8add303926fcc39276ee01032cd456f` | `native-safe` |

E5 also requires ICD JSON SHA-256 `14f4eda1b6fd6cbf07d61edeeffdaaede166f4c09d165fec9f661e9f90556ad3`. The legacy profile pins its library; it does not require one specific ICD JSON file. Neither profile admits an arbitrary driver library.

`native-safe` clears all controlled ambient compiler options, then sets exactly `HS_SPILL_BATCH=1`, `HS_PUSH_SHADOW=1` and `shader_spilling_rate=1`. `HS_CS32_MAX_INSTS` must be absent. This is the actual policy name; “safe3” is descriptive shorthand, not a separate accepted name. Historical `runtime` includes its recorded MAX override and is rejected for E5.

Prepared plans pin all nine Python/C correctness and execution dependencies: the replay wrapper, native helper source, both output instrumenters, graphics compile probe, mesh mapping probe, compile probe, replay contract and SPIR-V resource auditor. Preparation checks that these sources remain unchanged throughout the build. Execution checks the complete source set again, alongside every frozen artifact, exact source/derivative module pairs, instrumentation reports, native control header and fixed four-case list. Changed imports invalidate a plan even when its shader and helper bytes remain unchanged.

The previous `f913f0a4…` wrapper and `aa9169a1…` native C were archived privately before this update. Old D8 prepared-plan schema v1 cannot enter the current executor; its reports remain historical evidence. A changed implementation requires fresh preparation and review rather than relabelling old artifacts.

## Separate mesh contracts

| Contract | D8 | 6864 |
|---|---|---|
| Explicit profile ID | `d8-position9-128/v1` | `6864-position10-96/v1` |
| Prepared schema | `open-ocarina.prepared-native-mesh-replay/v2` | `open-ocarina.prepared-native-mesh-replay96/v1` |
| Native control magic | `OOMESHR1` | `OOMESH96` |
| Vertices / maximum primitives | 128 / 128 | 96 / 96 |
| Vertex outputs | Position + 9 generic vec4 arrays | Position + 10 generic vec4 arrays |
| Record stride | 44,928 bytes | 36,864 bytes |
| Set 0 input positions | 5 UBO + 6 SSBO | 4 UBO + 7 SSBO |

Both layouts have eleven descriptor positions, including binding 8's three elements. Real null descriptors are supported only at reviewed elements 8:1 and 8:2 with `nullDescriptor` enabled; their buffer is NULL, offset zero and range WHOLE_SIZE. No fabricated bytes or ownership are attached to them. Every nonnull view retains its exact range and contents, including the length used by `OpArrayLength`.

The [96-vertex instrumenter](mesh-output-profile96.md) remains a separate, unchanged, hash-pinned dependency. Its raw capture schema is `open-ocarina.mesh-input-profile96/v1` with explicit profile ID and producer flags `runtime_execution_authorized=false`, `equivalence_proven=false`, `replay_complete=false`. The replay wrapper requires this framing and exact source pair. It does not treat producer metadata as permission to execute.

Native CPU validation derives descriptor types, output limits, record guards and extent from the distinct magic. It checks the module's single MeshEXT entry, literal 32×1×1 local size and exact vertex/primitive output limits. Supplying a 96-vertex module with a D8 bundle, or the reverse, fails before Vulkan instance creation. Python independently checks the profile, known derivative digest, audit, reflection and native header.

Both profiles use a fresh nonaliasing output allocation and readonly 32-byte origin/window control at set 1. Every defined component, writtenness marker, per-lane count row, padding word, unwritten tail and guard participates in full-buffer comparison. The 96-vertex checker uses its own 4,512 writtenness words and offsets. Its independent fixture and CPU oracle use 96 vertices, eleven vec4 fields and primitive count `tag % 97`, rather than reinterpreting D8 records.

## CPU preparation

```sh
python3 tools/mesh_replay.py fixture \
  --output local/native-mesh-replay/e5-d8-fixture-new \
  --driver-profile cache-safe-e5 --driver-policy native-safe \
  --origin 128,0,0 --groups 2,1,1 --null-table-tail

python3 tools/mesh_replay.py fixture \
  --output local/native-mesh-replay/e5-6864-fixture-new \
  --driver-profile cache-safe-e5 --driver-policy native-safe \
  --profile-id 6864-position10-96/v1 \
  --origin 96,0,0 --groups 2,1,1 --null-table-tail
```

These commands assemble/validate independently authored fixtures, build the optimized native helper and invoke its CPU-only `--validate` path. That path returns before `vkCreateInstance`. Each fixture produces one full-primitive and one empty-primitive record with nonzero origin. Four planned cases use the same independent fixture at actual widths 32,32,8,16; they test the executor and capture machinery, not transformation equivalence or shader-side null reads.

The native execution boundary remains exactly one draw over at most 32 workgroups, a two-second fence, competing-game refusal, no GPU polling loop and no idle wait after timeout. The fixed process budget covers pipeline compilation plus the bounded run: D8 uses 30 seconds and the 6864 profile uses 120 seconds. There is no generic timeout override. Each plan is single use. Both driver identity and the explicitly reviewed plan hash must match before execution. Preparation never schedules execution automatically.

## CPU review handoff

The two private preparations are `local/native-mesh-replay/e5-d8-fixture-review-02` and `local/native-mesh-replay/e5-6864-fixture-review-02`. They were reviewed before the separately authorized GPU checkpoint below.

| Artifact | SHA-256 |
|---|---|
| Wrapper | `e91bba8b8972a42a9456422fcffacb8fe07803dfa0d47077f448c55113edb222` |
| Native C source | `f26d99e14ee41659015b39bd83f374e6b35641bf945deb48439bd66aed6d5038` |
| Optimized helper, both plans | `b379519f9e24eed0d271f438848ecff560ec82b891b7c97f7f805bfaa938a398` |
| D8 prepared plan | `072af2bb74bcf2da03f6fa4fcb29189f13baedc844002f195408c1804829f275` |
| 6864 prepared plan | `3e18a8edc06dfc3de7affb3801c07d8592cbd39e4ff8878ed90039ca37dcb6a1` |
| D8 full expected output, 89,984 bytes | `468f2a6104f33a8adf303f928857d8609753fd73f8033236b61eb6d58d84d7d1` |
| 6864 full expected output, 73,856 bytes | `f986d679c1b1725a4a9d25ab7661c25ef295044873d0fc0d54644397721e658c` |

Sixteen dedicated standard-library tests cover profile/policy rejection, wrong-driver refusal before helper launch, all dependency source changes, incomplete source graphs, old schemas, cross-profile layouts, producer flags, exact descriptors/nulls/ranges, output counts/writtenness/guards and CPU-only preparation. The full CPU suite passed 506 tests. Strict C11/O2/NDEBUG compilation with all warnings treated as errors and both matching native validators passed; both mismatched profile/module combinations were rejected in CPU-only validation. External build dependencies are a C compiler, Vulkan headers/loader/pkg-config, SPIR-V assembler/disassembler and validator.

A private CPU pointer audit of the exact 6864 source pair found Logical/GLSL450 addressing, three reachable functions and 144 direct calls in each. Original and conditional contain respectively 12,822/13,060 module instructions and 10,264/10,479 reachable function instructions. Each has 265 UBO loads, 49 SSBO loads, ten input length queries and zero reachable input stores. Neither whole module contains pointer equality/difference/address-conversion operations; reachable pointer consumers are limited to access-chain bases, load sources, store destinations and length-query operands. This supports separate immutable descriptor views for this exact readonly graph. It does not prove historical atomicity, shader transformation equivalence, final rendering or performance.

## Authorized E5 fixture observation

On 2026-11-13, both exact plans and the helper above executed after source review and an exclusive GPU grant. Every case issued one two-workgroup draw. D8 produced 128 vertices per group with primitive counts 128 and 0; 6864 produced 96 vertices per group with primitive counts 96 and 0. Each profile's four full initialized outputs matched its CPU golden hash in the table. Actual executable widths were 32,32,8,16, with mesh stage confirmed by pipeline executable properties.

Both original32 fixture cases passed the complete CPU oracle, count/writtenness/padding and guard checks before the width8 and width16 cases ran. Input and origin-control bytes stayed unchanged. The root independently read all eight saved outputs, checked their records and compared them with the frozen expected files. Execution report hashes are `19ec4b4f421316f1b65343f27cec59802ed80937ee0d995ca5cf5b34ca3d9d0b` for D8 and `4008a08bc5a69aa831a49d2cfc8cf1b5f118042b69c3b43f04328592896a3fea` for 6864. The GPU lease was released immediately after terminal completion.

All cases used independently authored fixtures without subgroup operations or input reads. This establishes bounded executor/profile/capture behavior on the pinned configuration; it does not establish transformation equivalence, shader-side null reads, historical reproduction, rendering quality or speed. No original or transformed game module was executed as part of this fixture checkpoint.

## Per-profile process budget update

The separate [E5 real-module and compile-only evidence](../research/native-mesh-replay-e5-observation.json) records three distinct outcomes. The two-group D8 chosen-readonly comparison passed all four requested/actual widths 32,32,8,16, with complete 89,984-byte output equality and 128 vertices/32 primitives per group. Its output also matches the earlier pinned legacy-driver result for the same owned input graph. This is finite shader-output evidence, not historical draw reproduction or full rendering equivalence.

The first 6864 baseline reached the original 30-second process deadline without terminal native metadata or output. Its repeat and alternative widths did not run. The wrapper stopped only its own process group, and the spent plan remains preserved. The failed attempt's phase is unknown.

A separately reviewed private compile-only derivative then retained the exact graphics pipeline flags zero, required width32, profile/device/features/layout and optimized raster-discard state. It removed all command recording, submission, draw and fence code, and flushed milestones before/after instance, device, module and graphics pipeline creation. The one authorized invocation completed with actual mesh width32: its graphics pipeline call took 40.087696 seconds and its complete process took 40.226076 seconds. These are diagnostic host-call observations, not FPS measurements. The new observation does not retroactively identify the earlier replay's phase. Compiler statistics were omitted to preserve the original pipeline flags.

That compile duration justifies a fixed 120-second process budget for the explicit 6864 profile. D8 stays at 30 seconds. The native C source and helper remain unchanged, including the two-second GPU fence, one draw, all guards and complete byte comparisons. `checked_prepared` requires the exact complete limits dictionary for its profile, with integer values: missing, extra, tampered or incompatible process/fence/draw/input limits fail closed. Execution selects the immutable profile budget rather than an arbitrary manifest timeout.

The updated wrapper SHA-256 is `f75a509df716ffdb89c999c6967003e846622336b6be7afa791669fdd62518a6`. It invalidates every earlier plan through the source graph, including D8 and fixture plans. The prior `e91bba8b…` wrapper was archived before edits; all earlier plans, outputs and reports retain their historical identities. The fixture observation above used that earlier wrapper, not the budget update.

The budget update's initial CPU-only preparations used the unchanged helper `b379519f…`:

| Preparation | Prepared-plan SHA-256 | Process / fence budget |
|---|---|---|
| D8 authored fixture | `da4d42bf2c47d1d4a7fdbabf8008b15d246424f55dff68b50c1d851121091916` | 30 / 2 seconds |
| 6864 authored fixture | `bc235dd9898d0873342213196d675c432cf85d3fb71e10d95dc99452cee0c694` | 120 / 2 seconds |
| 6864 chosen readonly graph, origin0 + 32 groups of grid67 | `cddd6349dddfd2dfc8b1b18ea9e4fcca03dee40f133da18946c8da06832ed8e5` | 120 / 2 seconds |

Their private directories are `local/native-mesh-replay/budget-d8-fixture-review-01`, `budget-6864-fixture-review-01` and `budget-6864-frozen-origin0-review-01`, respectively. At that initial CPU handoff none had executed. Both new fixtures subsequently received separate review and grants, passed all eight cases, and released their lease before the real 6864 plan received its own review and grant. Eighteen dedicated tests and the full 589-test CPU suite passed for the frozen implementation. Historical atomicity, general race freedom of same-value shared writes, universal transformation equivalence, full rendering and FPS remain unproven.

## Authorized fresh-budget observations

The freshly reviewed D8 and 6864 authored fixtures each passed actual/requested widths 32,32,8,16 against their independently computed full expected outputs. D8 compared 89,984 bytes with primitive counts 128 and 0, under the unchanged 30-second process budget. The 6864 fixture compared 73,856 bytes with primitive counts 96 and 0, under its explicit 120-second budget. Both retained the two-second native fence. Their execution report hashes are `d134555499eb2b52c2177091439469aecd8fee163e876193ab5445e1e94366e8` and `bd24292cbff8a692a8d5cb7efb3dd1035c1e5d42e5e8d3d01832387b07db7399`, respectively. These independently authored fixtures contain no subgroup operations or input reads and establish executor/capture behavior only.

The separately authorized real plan `cddd6349…` then compared the first 32 workgroups of the chosen 67×1×1 readonly input graph. Original32, its repeat, conditional8 and conditional16 all produced the same complete 1,179,776-byte capture, SHA-256 `06cc1d2e1aafa042f6d31282587a98f726d7ffd5bcf87698df928ee4091ed694`. Pipeline executable properties verified actual widths 32,32,8,16. Execution report SHA-256 is `fa3f047484f3ff9cbe4bf4b9c9cd48768487396c0bf271c496d65ff36c5bc1ef`.

Every group emitted 96 vertices and 64 primitives: 3,072 vertices and 2,048 primitives in total. All 96 vertices per group were referenced, with 110,592 nonzero vertex-attribute bit words overall. Referenced positions had no nonfinite components or zero W. All lane counts, component markers, padding, unwritten storage, inner/outer guards and full output bytes passed. Input and control bytes stayed unchanged. The root independently read all four saved captures, rechecked the records and compared the complete files. The GPU lease was released after terminal completion.

This real-module comparison uses repeated original32 as its differential reference; it has no independent CPU algorithm oracle. It covers one finite chosen immutable graph and workgroups 0–31 only. Historical CPU atomicity and backing provenance remain unknown. The exact shader contains overlapping same-value shared initializer writes, so this observation does not prove general race freedom or universal transformation equivalence. It does not establish historical draw reproduction, final rendering or FPS. The old 30-second timeout and separate 40-second compile-only result remain distinct historical entries, with the old attempt's phase still unknown.

At the first 32-group checkpoint, the remaining windows had been prepared but not executed:

| Window | Prepared-plan SHA-256 | Full initialized capture |
|---|---|---|
| Origin32 + 32 groups | `3d88c844ee56bb9b72d8c30620ba34cd5d7f23fc6d1caf4a741f4ac8bd7af5b1` | 1,179,776 bytes |
| Origin64 + 3 groups | `5ffc39edb93b561fea63e7480440cb4456f84d3d45078a8d80e51a212a5bc6a0` | 110,720 bytes |

They retained the same source/module/helper/driver pins, readonly graph, 120-second process budget and two-second fence. Preparation supplied no permission to execute them; separate grants later authorized exactly their four-case matrices.

## Complete 67-group chosen graph

The two remaining windows subsequently passed all four actual/requested widths 32,32,8,16. Each original32 baseline and repeat passed all full-record checks before width8 and width16 ran; any difference would have stopped the matrix. All eight newly authorized processes terminated successfully, and their GPU lease was released immediately afterward. No extra window or shader ran under those grants.

| Workgroups | Cases | Complete capture per case | Full capture SHA-256 | Geometry |
|---|---|---|---|---|
| 0–31, previous checkpoint | 4 | 1,179,776 bytes | `06cc1d2e1aafa042f6d31282587a98f726d7ffd5bcf87698df928ee4091ed694` | 32 × 96 vertices / 64 primitives |
| 32–63 | 4 | 1,179,776 bytes | `e86912b7906053cfb7d25cacc2fac481e4f7360fcfbe3c970632424967e327d6` | 32 × 96 vertices / 64 primitives |
| 64–66 | 4 | 110,720 bytes | `7d8b5004d380810c9f9995ee63cda001cebb48be82a6a65786d6be31f2953e0b` | 96 vertices each; 64,64,12 primitives |

All 67 groups are nonempty. Together they contain 6,432 vertices and 4,236 primitives, with 6,354 referenced vertices summed across workgroups and 229,524 nonzero vertex-attribute bit words. Referenced positions contain no nonfinite components or zero W. Every full initialized output, lane-count row, component marker, padding word, unwritten tail and inner/outer guard passed. Input/control buffers remained unchanged. This includes the shorter final group, rather than extrapolating from full groups.

Concatenating the 67 baseline record bodies in workgroup order, excluding each window's outer guards but retaining per-record inner guards, yields 2,469,888 bytes with SHA-256 `04af610e9f5cef3fb294c2740b3a9bc6f9a40037c573b19e74f638148405a178`. The defined Position and ten generic vertex arrays alone total 1,132,032 bytes, SHA-256 `5e90efb67ef0791976fc0e585a1cc574b43d63eb3924fa3b1bc60b638c2fe58a`. These hashes identify saved output data; no shader or output bytes are published.

Execution report hashes for windows32 and64 are respectively `f60676b9b7775fd21a819e30f4d6237c5198076155902f3e8b7aef9f6b0ec78f` and `75ed3d29c27fa8a2a555d329afb01aec508098cea3e0a40f40db99c43551983c`. Revision 2 of the sanitized observation retains its predecessor metadata SHA-256 `b0efe3b6721e2e490ef03a401143a21ab2fffdff67a1c26e73529a4c85d5fca7` and the exact earlier first32 scope and source identities.

This covers the complete launch grid of one explicitly chosen immutable input graph. It tests original source `999c8511…` and the exact Python-transformed conditional source `a41c6df7…`; a separate native C++ lowering implementation was not part of these replay grants. No general race-freedom, universal transformation-equivalence, historical live-draw reproduction, final rendering or FPS claim follows. Frozen replay sources, native helper, optimization flags and two-second fence were unchanged throughout.

A subsequent [CPU-only instrumentation correspondence](native-mesh-profile96-instrumentation-correspondence.md) shows that the exact native raw434 source produces the same instrumented conditional 8e10 bytes under the frozen instrumenter. That adds native source lineage without changing the earlier a41c source labels or claiming direct raw434 dispatch. It introduces no new replay profile or GPU run; raw uninstrumented driver-pipeline comparison remains separate.
