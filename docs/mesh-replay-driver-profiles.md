# Explicit driver and mesh replay profiles

The current replay wrapper requires an explicit driver profile and compiler policy during CPU preparation. It supports two separately identified mesh output contracts. This update has CPU validation only; its fresh executors and plans need separate review and an exclusive GPU lease before execution. The [previous D8 observations](native-mesh-replay.md) retain their original source, helper, driver and plan hashes.

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

The native execution boundary remains exactly one draw over at most 32 workgroups, a two-second fence, a thirty-second process timeout, competing-game refusal, no GPU polling loop and no idle wait after timeout. Each plan is single use. Both driver identity and the explicitly reviewed plan hash must match before execution. Preparation never schedules execution automatically.

## CPU review handoff

The two fresh private preparations are `local/native-mesh-replay/e5-d8-fixture-review-02` and `local/native-mesh-replay/e5-6864-fixture-review-02`. No GPU execution is included in this handoff.

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
