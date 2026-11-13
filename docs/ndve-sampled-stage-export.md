# Sampled-profile what-if for one movie stage

The CPU exporter in [ndve_sampled_stage_export.py](../tools/ndve_sampled_stage_export.py)
assigns the 21 `HostTranslateInfo` fields recorded at use and the 77-field profile
sampled at dispatch, then answers compiler queries only from the pinned journal.
The production renderer exclusion is not linked and is unchanged. No Vulkan loader,
device, pipeline, queue, or shader execution is used.

The emitted private module is 83,223 words, SHA-256
`169b30754016737e4b49bb633dc2e2656b62e6f40fbdccbd907a538393f28338`.
That is the same digest as the [conditional offline export](ndve-stage-software-export.md).
This run does not create a distinct live variant and does not relabel that module.
`spirv-val` passed for Vulkan 1.3 with uniform-buffer standard layout. All 72
static `MatrixMulAddF16` sites remain, with the software helper and no
cooperative-matrix instruction.

## What the journal actually supplied

The journal pin is `8e5c274e1fa8a16c0b6175dca377b91cbc5972634ef8aca146f4e833737addf9`
(328 rows, 78,034 bytes, four complete contexts). Translation consumed the 47
recorded queries in order: local memory 0, shared memory 3,360, 24 HLE-macro
answers of 0, 19 texture-bound-buffer answers of 0, c0 byte 88 =
`0x10c00838`, and one noninteger answer for that handle. No c1 constant was read.
The answer-record pass repeated the same c0 word and used the sampled
registration type `Color2D` once. `ReadTextureType` was not called, so that
registration is not an observed compiler query.

The 21 observed host fields match the offline helper's previous host defaults,
including descriptor limits of 0. `TranslateProgram` still applies its own
zero-to-1024 descriptor fallback after that assignment. The pinned profile
equals the separately sampled base profile. Against the old preset, only
`support_float32_atomic_add` and `need_gather_subpixel_offset` differ, both
true in the sample. Neither changed this stage's words.

Unknown queries are rejected. The gate does not substitute zero, `Color2D`, or
the synthetic slot token `0x8058`. A repeated read is accepted only when it is
the same kind, bank, offset, and answer already consumed.

Root review tightened that repeat check to search only the consumed prefix;
a known query later in the journal cannot be used early. A synthetic compiled
negative case verifies this order. Rebuilding the query driver and reusing
163 unchanged compiler objects consumed the same 47 queries and produced the
same validated module. The original export and its tool identities are retained
separately in the [observation](../research/ndve-sampled-stage-export.json).

## Resource contract for a later owned test

The reflected set 0 layout matches the reviewed conditional contract: one UBO,
SSBO bindings 1 through 4 with 4/8/16-byte aliases, binding 4's array of three,
and one float32 2D combined image/sampler at binding 5 with an unknown image
format. The push range is 56 bytes, with members at offsets 0, 16, and 48.
This is a declaration contract only. It does not supply descriptor bytes,
image pixels, guest-page backing, or a dispatch payload.

## Actual A770 pipeline compilation

The [float16 compile helper](../src/probes/ndve_software_compile.c) compiled this
module on the A770 with Mesa 26.2.4 under the recorded safe spill policy, a
required 32-lane subgroup, full subgroups, and a 56-byte push range. It enables
`shaderFloat16` and checks signed-zero/Inf/NaN preservation at both 16 and 32
bits. Missing float16 arithmetic or preservation is rejected before device
creation; CPU mocks exercise the helper's actual feature chain.

The [compiler observation](../research/ndve-software-compile-observation.json)
records 68,551 instructions, 2,348 spills, 5,998 fills, and 32,768 bytes of
scratch. Pipeline creation took 5.155 seconds with disk caching disabled.
Those are compiler statistics and host creation time. No queue was retrieved,
resource was bound, or shader was dispatched. The bounded parent observed the
pinned driver mapping, reaped the normal exit, and rechecked all input pins.
This establishes buildability of this sampled module, without restoring the
excluded network or measuring its runtime.

For a separate compile-only reproduction after the CPU export above, prepare
a fresh private directory, copy the validated module there as `input.spv`, and
write this descriptor declaration to `layout.txt`:

```text
56 6
0 0 6 1
0 1 7 1
0 2 7 1
0 3 7 1
0 4 7 3
0 5 1 1
```

Build the helper with `cc -std=c11 -O2 -DNDEBUG -Wall -Wextra -Werror
src/probes/ndve_software_compile.c -lvulkan -o local/your-case/compile-helper`.
Use the pinned E5 ICD and library from the observation, a private home/cache,
the three controls `HS_SPILL_BATCH=1`, `HS_PUSH_SHADOW=1`, and
`shader_spilling_rate=1`, and disable the Mesa disk cache. Leave
`HS_CS32_MAX_INSTS`, SIMD overrides, spill-percentage, large-GRF and debug
variables absent. Under an exclusive GPU lease and a bounded parent, invoke
`compile-helper input.spv layout.txt pinned32 0`. The recorded parent used a
180-second bound and 8 MiB per output pipe, without changing child rlimits.

## Reproduce

```sh
python3 tools/ndve_sampled_stage_export.py --source-repo "$HOOKSHOT_SOURCE" \
  --seed "$NDVE_SINGLETON_SEED" --journal "$NDVE_METADATA_JOURNAL" \
  --result "$NDVE_RESULT" --checked "$NDVE_CHECKED" \
  --output local/ndve-sampled-stage-export-new --jobs 4
python3 tools/ndve_sampled_stage_contract.py \
  --exported local/ndve-sampled-stage-export-new \
  --output local/ndve-sampled-stage-contract-new
python3 -m unittest tests.test_ndve_sampled_stage_export -v
```

The frozen offline exporter, its contract, its tests, and its published
observation stay at their reviewed digests. Native control/code remain SHA-256
`1d660217708e2a99339df4ac6814d2843c5ad83f9c20dd7b429cb529aee41afa`, local size
32×1×1, and 3,360 shared bytes. Console arithmetic, matrix revision 04 edge
cases, native-network restoration, and gameplay performance remain open.
