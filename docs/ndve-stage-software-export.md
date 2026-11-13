# One native movie stage exported in software

The exact NDVE program 2, `e409733c45530d75`, now has a reproducible,
validated **conditional software-matrix translation**. Its private SPIR-V is
83,223 words, SHA-256
`169b30754016737e4b49bb633dc2e2656b62e6f40fbdccbd907a538393f28338`.
This is an actual stage export through the open recompiler. Live compiler answers
and replay inputs remain missing; no device or shader execution occurred.

[Sanitized evidence](../research/ndve-e409-software-export.json) records source,
helper, compiler, configuration, module and private-report digests. Native control
and code match the [network inventory](../research/native-network-inventory.json):
SHA-256 `1d660217708e2a99339df4ac6814d2843c5ad83f9c20dd7b429cb529aee41afa`,
local size 32×1×1 and 3,360 shared bytes. Native instructions, generated shader
bodies, seed inputs and raw reports remain private.

## Translation and retained work

[The exporter](../tools/ndve_stage_export.py) validates the complete seed framing,
extracts only the exact known stage, and archives recompiler tree
`58ca709617448e06af382c3443dd88dac3ffcb8e` into a fresh private directory. Its
restricted derivative of `nx2/bake_ds` removes the device-query entry point and
accepts only this singleton stage. The 164 translation units build with at most
four CPU workers, C++2b, `-O2 -DNDEBUG -march=x86-64-v3`; `nm` verifies the
helper has no Vulkan or dynamic-loader query imports. It does not build the game
host or change a game installation or shared vendor tree.

The `tensor-software` configuration deliberately sets both
`Shader::Ampere::native_matrix_multiply=true` and backend `mma_software=true`.
The first retains native HMMA semantics in IR; the second emits the software
helper. Renderer hardware-native matrix execution is false.
`RX_VK_MMA_SOFTWARE=1` is explicit and ambient shader options are excluded.

All 72 encoded, non-always-false HMMA sites become 72 `MatrixMulAddF16` IR sites
and 72 static calls to the software helper. Translation reports one translated
program, zero failures and zero skipped programs. No tensor stub is selected and
no live keep-list policy changes. These are static counts, rather than dynamic
work counts or independent certification of every original instruction.

The source-pinned helper routes fragments through subgroup shuffles, loops over
eight K pairs, accumulates using float32 FMA, and explicitly rounds at K=8 and
K=16 before packing half values. Its module contains no cooperative-matrix
instruction. The algorithm is the inspected Hookshot software implementation;
console arithmetic equivalence remains to be independently established.

## Explicit conditional environment

Compilation asks for cbuf bank 0, byte 88. The offline path substitutes the
synthetic slot token `0x8058` and answers that its texture is noninteger. Neither
answer has live metadata provenance. A later answer-recording pass also reads
that cbuf word as zero; that row is recorded separately, not presented as a live
handle. The stage metadata supplies texture-bound buffer index zero. No texture
type, pixel-format or storage-pointer query callback was invoked in this export.

Every observed callback and answer is preserved in the query audit. The source
also fixes a synthetic host profile, subgroup maximum 32, SSBO alignment 16 and
zero-initialized runtime specialization. These are explicit compiler
configuration assumptions, not queried A770 properties or captured runtime
specialization. Live answer/profile equality must be checked, or the stage must
be re-exported with the actual configuration, before treating it as a live
selected-stage reference.

## Actual conditional resource contract

[The contract inspector](../tools/ndve_stage_contract.py) reads a hash-pinned
snapshot, reflects declarations and traces statically reachable accesses.

| Set 0 binding | Descriptor shape | Reachable static use |
| --- | --- | --- |
| 0 | One UBO; aliases with strides 4 and 8 | 69 loads |
| 1 | One SSBO; aliases with strides 4, 8 and 16 | One load, 16 store sites, one length query |
| 2 | One SSBO; same typed aliases | 17 loads, one length query |
| 3 | One SSBO; same typed aliases | Three loads, one length query |
| 4 | Three SSBO elements; same typed aliases | Three loads, three length queries |
| 5 | One float32, nonarrayed 2D combined image/sampler | 19 image-read sites |

No storage image is declared. Old rejection logs enumerate bound slots and
cannot substitute for this stage-use reflection. The UBO's declared 65,536-byte
block does not establish its submitted Vulkan descriptor range.

The declared push range is 56 bytes, with members at offsets 0, 16 and 48.
Reachable code reads offset 48 and has two physical-load sites in the memory
fallback. Logical descriptor copies alone cannot reproduce that path. The
module also retains 223 shuffle, three ballot, two vote-any and 108 barrier sites
across reachable functions. Required subgroup 32 remains an explicit future
execution condition. Valid participation at subgroup operations and barriers
still needs review. Static reachability proves neither complete footprints nor
race freedom or a finite full-dispatch budget.

The next capture needs the actual cbuf88 handle and format answer; exact UBO and
six SSBO descriptor elements, ranges, aliases and bytes; binding 1's initial and
complete resulting state; binding 5's texture/view/sampler state; all push bytes
and the guest-page/backing graph for physical loads; and the exact dispatch and
producer-order association. The historical grid 900 and bound-slot sizes are
diagnostics, not a complete replay capture. No weights or model architecture
are inferred from these counts.

## Reproduce the CPU export

With the frozen source tree available in an existing source repository and an
existing privately owned seed, use fresh private output directories:

```sh
python3 tools/ndve_stage_export.py --source-repo "$HOOKSHOT_SOURCE" \
  --seed "$SHADER_SEED" --output local/ndve-export-new --jobs 4
python3 tools/ndve_stage_contract.py --exported local/ndve-export-new \
  --output local/ndve-contract-new
python3 -m unittest tests.test_ndve_stage_export tests.test_ndve_stage_contract -v
```

The tools require Python 3.11+, Git, a compatible Clang C++ compiler, SPIR-V
headers/tools and `nm`. The inspected build used Clang 22.1.8 and SPIRV-Tools
2026.1. Each compile command is bounded at 180 seconds, offline translation at
300 seconds, and validation/disassembly at 30 seconds. There is no device,
pipeline, queue or runtime execution path. The ten independent synthetic tests
pass without private inputs; the full suite passed 887 tests with five skipped.
Rendering, whole-network restoration and FPS acceptance remain open.
