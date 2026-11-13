# Optional native 6864 lowering overlay

The new `patches/hookshot/mesh-warp32-profile96.patch` is an optional twelfth overlay after the frozen eleven-overlay renderer chain. It extends the existing two-ballot native writer to the exact timed `68642bb810ee3e7c` mesh family. It remains experimental and off by default. Separate [paired compiler evidence](../research/native-mesh-profile96-codegen-observation.json) and [bounded V3/E5 identity pilots](../research/native-renderer-v3-e5-pilot.json) now accompany the build; the frozen V2 patch, capture96 patch, structural writer and original replay/instrumentation tools remain unchanged.

[Sanitized build metadata](../research/native-mesh-profile96-lowering.json) records all twelve patch hashes, reviewed source hashes, binary identity, CPU comparisons and limitations. The [family inventory](../research/mesh-family-audit.md) explains target priority and why the larger43ec family cannot use this lowering.

## Selection and identity

Both original modules and subgroup32 policy remain when all controls are absent. Existing `RX_VK_OO_MESH_WARP32=8` or `16` continues to select d8 alone. The additional exact value `RX_VK_OO_MESH_WARP32_PROFILE96=1` permits the new target at the same selected width. Absent, empty, zero, alternate spellings and unknown widths do not enable it. Width and profile selection are immutable for the process; there is no live module replacement after ready publication.

| Identity | d8 | 6864 |
|---|---|---|
| Program | `d8e59341cf4f629c` | `68642bb810ee3e7c` |
| Variant | `0c8f08626a9d98a4` | `3f3f5b29054673da` |
| Source mesh | `835aa597…` | `999c8511…` |
| Native candidate | `8754612a…` | `43434b02…` |
| Source shared bytes | 14356 | 11156 |
| Added scratch bytes | 256 | 256 |
| Candidate shared bytes | 14612 | 11412 |
| Payload plus shared bytes | 14740 | 11540 |
| Maximum vertices/primitives | 128/128 | 96/96 |
| Vertex output components | 40 | 44 |

Full digests are in the target registry and metadata. The6864 fragment digest is `3de1d79c321db3863dbb07de0d3fa0cdb126d72c07c2f1d70f0514eda3611b10`, taken from the exact cached variant. Both stages must have their original mesh/fragment NVN and Vulkan stage identities, exactly two stages, the expected program/variant and exact source hashes. Unknown stages, programs, variants, fallback paths and digest mismatches retain the baseline modules and existing subgroup policy.

The A770/Mesa identity and strict compiler policy gates are preserved: three required controls equal1, `RX_VK_NOOPT` disabled, and the same nine forbidden controls absent. In particular, `HS_CS32_MAX_INSTS` must be absent even when someone would otherwise set it empty or zero. Per-target device checks use source shared bytes plus two32-word scratch arrays, payload plus shared bytes, local size32, requested subgroup capability, output maxima and component limits. These are logical declared memory bounds; they do not predict physical register/shared allocation or compiler time. No optimization-disabling or global SIMD force control is added.

## Native writer correspondence

The generic production C++ core accepts the exact6864 original without modification. Its native output is `43434b023ff17ee4b92ed8e49f19c937c86f15cf31a7034161dd11878f944099` (63210words), preserving original numeric IDs and generator word0. It matches every word and ID of the same Python transformation assembled with `spirv-as --preserve-numeric-ids`, except the assembler's generator word458752. The d8 native golden remains `8754612a7395d1f804d4e56a7c18cb5a6f23ed2e02f01879cbc51bd108682de0`.

The previously replayed6864 Python candidate `a41c6df753f30f8c0646ffa45227304e994b3fbaccdcad64927cd57f5e2327e7` was assembled without preserving numeric IDs. Re-disassembling both6864 candidates with raw IDs and assembling both under that same canonicalizing tool produces exactly `a41c…`. This CPU word/ID correspondence supports semantic review; **it does not mean raw43434 was executed**. The native writer is retained unchanged instead of adding an ID renumbering pass merely to reproduce an assembler's numbering.

The existing replay executor labels its admitted source a41c. Previously matching output from a41c retains that original identity. The captured launch is67×1×1. The [completed supporting comparison](../research/native-mesh-replay-e5-observation.json) covers all67 workgroups across three windows, with repeated original32 and Python candidate8/16 complete output buffers matching in each window. The subsequent [instrumentation correspondence](native-mesh-profile96-instrumentation-correspondence.md) establishes that raw43434 produces the exact same instrumented conditional module under the frozen instrumenter. Paired compiler evidence below separately checks the raw sources. Neither observation is a direct dispatch of uninstrumented raw43434. Rendering and performance remain unproved.

## Paired E5 compiler evidence

[New sanitized metadata](../research/native-mesh-profile96-codegen-observation.json)
records four sequential, optimized mesh+fragment pipeline compilations: native
43434… and Python a41c… at requested widths8 and16, using the exact same fragment
3de1… and E5/native-safe policy. Compiler executable properties confirm mesh8/16
and fragment8. All four owned processes stopped and reaped; no queue was retrieved,
no commands submitted and no shader drawn or dispatched. The actual629add renderer
remains unlaunched by this observation.

Within each width, mesh and fragment GEN Assembly and shader push maps are
byte-exact, and all fifteen numeric code-generation statistics match. The mesh's
only Final NIR difference is its exact second-row source_blake3 identity digest,
crosschecked to the uniquely named UINT64 Source hash statistic. Every other NIR
byte matches. Fragment NIR matches completely. Twenty-two comparator cases and the
saved actual D8 pair test those narrow metadata exceptions; unknown fields,
statistic shape changes, NIR body differences and codegen differences fail.

The reviewed caches were initially empty and isolated per case, with writes
enabled; disk-cache disabling was not set. No cache was imported or reused between
cases. This amendment and the source-backed empty-directory evidence are explicit
in the observation. Compile times are not compared.

This is diagnostic-state codegen evidence. Attachments and retained NVN state are
associated with the exact capture, but runtime Intel EQ_BIAS depth adjustments,
the flipped viewport and other effective dynamic state are not reproduced.
Matching compiler output neither executes raw43434 nor establishes complete
runtime-pipeline, native-quality or performance acceptance. It remains separate
from the original a41c replay and the instrumentation correspondence.

## Integrated V3 identity pilot

[Separate runtime metadata](../research/native-renderer-v3-e5-pilot.json) records
three processes using the unchanged629add renderer, E5 driver and profile96 flag1.
Mode0 selected both original mesh modules; modes8/16 selected native d8875… and
6864434… with unchanged fragments and both correct revision3/shared-byte receipts.
Each target exceeded32 matching unique records. Admission and final preserved
counts are recorded separately; the global256-event cap was not exhausted. All
owned processes stopped and reaped, with loaded-file identities/hashes, settings,
script path and immutable seeds checked.

Private2048×1152 control images show broad world/Link, with differing temporal/UI
states and unresolved flat dark shapes in baseline and both candidates. The cause
is unknown, not a proven new lowering defect. Live executable width, GPU-generated
indirect grid, full native quality and output equivalence remain unverified; no
benchmark result follows from these identity/image diagnostics.

## Cache and pipeline paths

The new registry has revision3. Translator cache policy includes revision, width and the explicit profile flag; the build's translator stamp includes the registry source. Pipeline and GPL tags use revision3 as well. Canonical shader-cache serialization still writes the original words. Selected native identity metadata records the actual candidate digest and is not overwritten by those canonical words.

JIT and full-cache load use the same exact selector and rewriter. Full graphics pipelines, GPL libraries, prewarm and pipeline observers retain the same selected-stage subgroup helper. Mesh commands, arguments, stage constant buffers, descriptors and output declarations are unchanged. This overlay does not change shader-cache serialization format or claim recovery after a later pipeline creation failure: the existing path has no baseline recovery for that failure.

## CPU checks and build freeze

`python3 patches/hookshot/check_mesh_warp32_profile96.py --checkout <private-checkout>` applies the entire twelve-overlay chain to an isolated index. It verifies unchanged frozen helpers, strict policy/device identity gates, canonical cache writes, actual identity hooks, translator/pipeline separation and full-cache/JIT/full/GPL/prewarm parity. The checker compiles production registry/limit code against CPU mocks, runs unchanged production core malformed-operand refusals, and compares C++ output with the independent Python transform on two authored fixture shapes. No Vulkan loader is linked by these checks.

Clang22.1.8 and GCC16.2.1 checks passed, including eleven malformed/structural negatives, both-target identity/device/selection negatives, strict forbidden-variable presence checks, distinct cache policies and two authored word/ID goldens. Nine focused tests passed. SPIRV-Tools v2026.1 validated both private real candidates. The renderer-only build used C++20, `-O2 -g0 -DNDEBUG -DHOOKSHOT_RELEASE -march=x86-64-v3`, four jobs and207 source files, with zero compile failures.

The broader vendor game-free suite reported449pass/9fail/64skip. All nine failing checks also failed on an untouched archive of the frozen eleven-overlay base. They concern existing source-extraction fixture/stamp gaps, menu documentation, target-image-pool wrapper expectations and a writeback fixture. They were not changed or weakened here. This is a qualified test result, not an all-green vendor suite; the missing-header checks among the skips were not treated as passes.

| Frozen artifact | SHA-256 |
|---|---|
| Optional12th patch | `e419822c42b800ea406e692a366b5891a21baa030632c8f30ec6af134dc5f116` |
| New isolated renderer | `629addc24fe06d5170ebd0d6892cd0722dc69e7171ee951abc4188f17d26405d` |
| Source manifest | `58e0a65ce8dc5b958c838a7b76cd8f83687bab32221b83efa296b3cfbc74b8ca` |
| Private build receipt | `d51a854e6dd6243785b607dcb472d100cd56f8cded59344ab94b4f6b80f773cf` |

The isolated renderer is under ignored `local/mesh-warp32-profile96-renderer-build/sa/build/hookshot_vk.so`; receipt, source manifest, comparisons and private logs are under `local/mesh-warp32-profile96-native/`. The reconstructed twelve-overlay tree is `58ca709617448e06af382c3443dd88dac3ffcb8e`. That build task did not launch a device or game; the later bounded V3/E5 observation is separate and preserves the original receipt unchanged.

The V3 pilot adds actual selected-module evidence for the unmodified629add renderer. The exact [instrumentation correspondence](native-mesh-profile96-instrumentation-correspondence.md) and paired compiler observation relate the native writer to the completed chosen67-group comparison without promoting a recording-time identity to a captured GPU-output comparison or adding a redundant ABI. Original shared-memory race-freedom limitations, native scene quality and matched ordinary execution measurements remain separate requirements.
