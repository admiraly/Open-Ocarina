# Cached mesh family audit

CPU-only observation, 2026-11-13. Select `68642bb810ee3e7c` first for the next explicit output-capture profile. Its earlier instrumented graphics pass is 1.958 ms; `3b8c07f2eb4bb7d9`, with the same output and descriptor shape, follows at 1.867 ms. These spans bound whole graphics passes, include other stages, and come from the initial profiled scene. They establish neither mesh-only cost, savings, current-scene cost, nor ordinary FPS. No runtime admission was expanded.

[Machine-readable inventory](mesh-family-audit.json) pins input cache/log and tool hashes, all 49 program/variant identities, 46 distinct original mesh digests, structural results and the twelve passing resource contracts. Raw modules, assembly, extraction scripts and logs stay private. Catalog extraction consumed every serialized stage and validated exact record ends under the observed cache ABI; all 46 distinct original mesh modules passed CPU `spirv-val --target-env vulkan1.3 --uniform-buffer-standard-layout`.

## Identity and timing scope

Pilot02 baseline and candidate logs report 21 and 48 distinct `in play: mesh` program keys respectively; their union is 49. The candidate cache has 48 mesh records, the baseline/source-nightly cache 49, with `7210c04a4d923d4b` the additional baseline key. Cache membership and a log appearance provide no per-program timing. Several keys share one mesh digest, giving 46 unique modules.

The caches retain the original d8 module `835aa597…`. The native derivative `8754612…`, observed at the transformed draw sites with requested subgroup width 8, is **absent from the serialized program caches**: lowering happens later in the pipeline path. Cache extraction must not be described as discovering native `8754`. Runtime stage identity still needs independent provenance.

## Twelve passing original modules

Every row has literal local size 32×1×1, two ballots in one main-function block, and ballot results consumed only through component zero. The core audit proves the paths preceding that block have no calls, loop merges, early exits or cycles; this is a narrow control-flow proof, not a general assertion that arbitrary ballots are uniform. Each has two original workgroup barriers. All twelve have a declared 128-byte task payload whose sole load is the false operand of a constant-true select choosing WorkgroupId.x. The exact dead-chain proof permits task-free replay.

All twelve use only logical read-only external buffers: zero reachable descriptor stores, zero image descriptors, zero reachable physical-pointer accesses, zero push-constant variables and no unresolved memory origins. Typed descriptor aliases are merged by set/binding; repeated declarations do not multiply descriptor elements. `OpArrayLength` remains sensitive to captured descriptor ranges. Their declared capabilities alone overstate actual image/subgroup operations.

| Program key | Max vertices/primitives | Generic vec4 locations | UBO / SSBO elements | Workgroup bytes | Output store sites |
|---|---:|---|---:|---:|---:|
| `22063ecfa481a731` | 128 / 128 | 5 | 5 / 1 | 5140 | 40 |
| `3b8c07f2eb4bb7d9` | 96 / 96 | 0,1,2,4,5,6,7,8,11,12 | 4 / 7 | 11156 | 138 |
| `68642bb810ee3e7c` | 96 / 96 | 0,1,2,4,5,6,7,8,11,12 | 4 / 7 | 11156 | 138 |
| `6afe3fda3f4b1752` | 96 / 96 | 5 | 3 / 1 | 3476 | 30 |
| `6b4dea601798c255` | 96 / 96 | 0,1,2,4,5,6,7,8,11,12 | 4 / 7 | 11156 | 138 |
| `9d8403e2e566c773` | 128 / 128 | 2,5 | 4 / 1 | 6676 | 56 |
| `a6a688bae3c9b80c` | 96 / 96 | 5 | 3 / 1 | 3476 | 30 |
| `c36a4f3f667c886a` | 128 / 128 | 0,1,3,4,5,6,7,10,11 | 5 / 6 | 14356 | 168 |
| `cb71f79f6129aab8` | 96 / 96 | 0,6 | 3 / 2 | 4244 | 42 |
| `d8e59341cf4f629c` | 128 / 128 | 0,1,3,4,5,6,7,10,11 | 5 / 6 | 14356 | 168 |
| `e28434e1afe8321a` | 96 / 96 | 0,6 | 3 / 5 | 4244 | 42 |
| `f93c1d64658c850b` | 96 / 96 | 0,6 | 3 / 2 | 4244 | 42 |

All also emit Position vec4 and PrimitiveTriangleIndicesEXT uvec3. No per-primitive generic attributes occur. One SetMeshOutputsEXT has vertex count equal to the stated maximum and primitive count UMin(shared count, maximum). Storage byte counts sum SPIR-V logical types without layout padding; device mesh-memory accounting must be checked separately. Resource contracts in JSON retain exact locations, descriptor counts and count expressions without publishing instruction bodies.

For 6864/3b8/6b4, set0 bindings0–3 are UBOs, 4–7 singleton SSBOs, and binding8 is a three-element SSBO array. This uses the same eleven buffer elements as d8, with a different UBO/SSBO split. The 96-vertex output profile has eleven vec4 fields including Position, compared with ten fields at 128 vertices for d8. It therefore needs an explicit new record/checker profile and new output component limit checks, despite smaller total logical output size. Int64 and dynamically indexed buffer arrays are already present in d8; no new image, physical-address or live-task-payload feature class is needed. Per-capture alias, null descriptor, exact slice/range and device gates still apply.

`c36a4f3f667c886a` is the closest replay control: the existing pure d8 output auditor accepts its shape unchanged. Its digest is outside frozen prepare/admission policy, and no individual pass timing was established. It should not displace timed 6864 merely because it is convenient.

## Refused shapes and expensive passes

The core first-refusal counts are 14 unexpected subgroup builtin inventories, 14 modules outside the exact two-ballot count, four with calls/termination/loops before ballots, and two whose ballots occupy different blocks. These are first failures, not mutually exclusive semantic diagnoses. The JSON preserves each key/digest and first refusal.

`43ec86f35fd937b9` has the second largest earlier graphics pass at 5.877 ms. Its exact cached mesh variant `bc2d248909cf8b40` has eight ballots across five blocks, six subgroup Any operations, 22 Shuffles and 32 Broadcasts with source lanes spanning 0–31. Prefix traversal to the ballot blocks encounters up to 76 calls and an early return. This invalidates the current workgroup-barrier convergence proof; changing the ballot-count whitelist would not fix it. The program has task+mesh+fragment stages, a 3200-byte task payload, sampled-image input, 9236 logical workgroup bytes, and maxima96/96. It needs live task output and image provenance plus active-mask-aware subgroup semantics before a bounded replay can be sound.

Other measured pass keys also exceed the current transform: `51183fe750d5d0c8` (1.507 ms) and `28dd2baa17ba10e1` (1.089 ms) each have four ballots, 21 Shuffles and 32 Broadcasts; `d4abbcb4c2140609` (1.758 ms) has four ballots, 22 Shuffles, 32 Broadcasts and output maximum160. These are a separate subgroup emulation effort, not applications of the two-ballot lowering.

`89ca750f9852b7ca` (1.769 ms) is a narrower second shape: exactly one ballot, no other group operations, only component-zero ballot consumption and a 50-block prefix without calls/loops/early exits/cycles. Its first refusal is the exact two-ballot count. It warrants a separately audited one-ballot implementation and a new capture profile, with six UBO/four SSBO elements and a different nine-location output inventory. It is not admitted now.

The zero-group-operation modules belonging to `507e946d64f2d55e` and `a5da9f84b72d1471` illustrate why first refusals need interpretation: each loads subgroup lane ID directly, with no declared subgroup masks and no ballots. They might permit a simpler explicit lane mapping proof, but merely removing the subgroup-size request could change values. No individual timing establishes their priority.

## Strongest risk and decisive next input

A structural match does not establish native lane mapping, floating-point equality, complete output equality or scene correctness for another digest. Moving a workgroup barrier into divergent control flow can deadlock; replacing subgroup operations without preserving participation can silently alter primitive ownership. A few matching vertices or aggregate counts do not cover those risks.

Next capture: the exact runtime 6864 program/variant and original mesh digest, all eleven complete BEFORE descriptor slices with merged typed aliases and descriptor lengths, and the actual converted indirect argument's twelve GPU bytes. Keep recording/table provenance and the VMA buffer owner alive through completion. A CPU reconstruction of group counts is insufficient. Capture a bounded nonzero-origin window only after the same WorkgroupId substitution/no-cross-workgroup-side-effect proof, then compare every output word, component-writtenness bit, per-lane count and guard across repeated original32 and conditional8/16 runs under the new 96 profile. Native rendering, quality review and matched ordinary FPS measurements remain separate required evidence.
