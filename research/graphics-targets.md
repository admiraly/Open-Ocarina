# Graphics targets: all-stage cache inspection

Observed 2026-11-12. This is CPU-only cache/source analysis. No graphics pipeline was compiled or dispatched, no frame was rendered, and no performance improvement is claimed.

The initial full GPU table names graphics keys `d8e59341cf4f629c` and `43ec86f35fd937b9` in 1536x864 color-plus-depth passes. Their timestamped spans are 6.336 and 5.877 ms per recorded frame respectively. Each row describes **0–4 draws**, with the same first and last program key. It does not establish exactly one draw, stage time, the intermediate program sequence, an asset identity, or unprofiled frame latency. The complete graphics-pass category accounts for 42.195 ms of that instrumented table, versus 25.797 ms in dispatches. These measurements make graphics a necessary parallel investigation for the native-quality 60 FPS goal; solving one compute dispatch cannot establish that goal. See [the initial profile](gpu-profile-initial.json).

## Recovered stage identities

Both programs are native Vulkan mesh pipelines in both examined caches: flags `2` (mesh), without fallback flags. The variants agree across caches; every SPIR-V digest changes. Cached variants are candidates, not evidence that the original timing run used these exact modules.

| Program | Variant | Index | NVN stage | Vulkan stage |
|---|---|---:|---:|---|
| `d8e59341cf4f629c` | `0c8f08626a9d98a4` | 0 | 7 | mesh EXT, `0x80` |
| same | same | 1 | 1 | fragment, `0x10` |
| `43ec86f35fd937b9` | `bc2d248909cf8b40` | 0 | 6 | task EXT, `0x40` |
| same | same | 1 | 7 | mesh EXT, `0x80` |
| same | same | 2 | 1 | fragment, `0x10` |

Stable cache: HSP1 stamp `7f43a9ed301dc7a0`, 614 records scanned. Nightly cache: HSP1 stamp `d6c0514818a93a6e`, 590 records scanned. Each target has one record per cache. Every serialized stage and the exact record end were consumed. Native stage-code hashes are not serialized in these records and were not recovered here.

| Cache | Program prefix | Index | Words | SPIR-V SHA-256 |
|---|---|---:|---:|---|
| stable | `d8e59341` | 0 | 44218 | `9096c34a60f2f3118449317f46b8be4ee075100495d4241d030f84d1301f3dfb` |
| stable | `d8e59341` | 1 | 29718 | `db2e71a25d255a2c21c384c1fde8359e5d431bffc996a2c81f95d07ff3217e93` |
| stable | `43ec86f3` | 0 | 23055 | `894afa18cf3b7aa07d9fdb23f28b93b88948f771fedf5ff3ceda1da8b2f3e94e` |
| stable | `43ec86f3` | 1 | 225908 | `c3c2f5191c16e8925443981167a466fd431674be577b9a6fdb9ebae7a66a7c22` |
| stable | `43ec86f3` | 2 | 26933 | `aa440fe04adbc6467228f0ab285d67d15035962c377e50868a3b7840ee0600f6` |
| nightly | `d8e59341` | 0 | 42939 | `835aa59799c6ecb621034d946e2fa4eadb1aa2407d293321ae3e3052882bc9b3` |
| nightly | `d8e59341` | 1 | 29766 | `569b378a012388d711a115030bd1f41af66d38aebb645118baacd1e90a8f5a3a` |
| nightly | `43ec86f3` | 0 | 21739 | `5e7d8f02e2dbd5ae1cd625d42366bc532dcdf7d71bb56ac3bc303b5e9655d086` |
| nightly | `43ec86f3` | 1 | 222557 | `4c5eee851f4140ff089d4abb0bdd445566c7980ee47669d962778171d143131d` |
| nightly | `43ec86f3` | 2 | 26981 | `9268b2ceafeeb6ef1acb9f029197c818e90b3bcc5516451a2298c6c613374115` |

All ten modules passed `spirv-val --target-env vulkan1.3 --uniform-buffer-standard-layout` with SPIRV-Tools v2026.1 (2026-02-16). Binaries and disassembly remain private under ignored `local/graphics-targets/`.

## Declared geometry limits and actual subgroup instructions

These properties agree across stable/nightly unless noted. Counts are **static instructions in functions reachable from `main`**, not dynamic executions. Memory numbers sum declared scalar/array data, exclude implementation padding and mesh output storage, and do not prove physical driver allocation or that every declared variable is used.

| Program/stage | Entry local size | Declared mesh output | Workgroup data bytes | Task-payload data bytes | Reachable group instructions | Barriers | Atomics |
|---|---|---|---:|---:|---|---:|---:|
| `d8…` mesh | 32x1x1 | 128 vertices, 128 triangle primitives | 14356 | 128 | 2 ballot | 2 | 0 |
| `d8…` fragment | n/a | n/a | 0 | 0 | none | 0 | 0 |
| `43…` task | 32x1x1 | n/a | 0 | 3200 | 3 ballot, 32 broadcast | 2 | 0 |
| `43…` mesh | 32x1x1 | 96 vertices, 96 triangle primitives | 9236 | 3200 | 8 ballot, 6 any, 22 shuffle, 32 broadcast | 2 | 8 stable / 6 nightly |
| `43…` fragment | n/a | n/a | 0 | 0 | none | 0 | 0 |

The `d8…` Workgroup declarations contain 12800, 1552 and 4 bytes; the `43…` mesh declarations contain 8064, 1168 and 4 bytes. Declared payload sizes correspond to 32 and 800 32-bit words. The native-mesh records' program-head workgroup is `[1,1,1]`, shared-memory field is zero, and fallback payload field is zero: those fields do **not** describe these mesh/task SPIR-V resources. Use the per-stage execution modes/types. Output maxima also do not establish actual emitted vertex counts.

The 32-lane contract is explicit in graphics, beyond the compute pipeline policy. In both `43…` task and mesh modules, the 32 broadcasts name constant subgroup source lanes **0 through 31**. Those modules also use subgroup ballot/mask/local-invocation operations. Reducing a required subgroup to 16 without rewriting the guest-warp operations cannot preserve access to source lanes 16–31. LocalSize 32 by itself would not prove a subgroup-size requirement, nor do 32-word stage-buffer blocks by themselves prove one; the actual subgroup instructions supply the additional evidence here. The `d8…` mesh ballots need their own mask/dataflow audit before any width change. Neither fragment module has reachable subgroup instructions, which supports a separate fragment compilation experiment but does not establish whole-pipeline independence or correctness of a changed policy.

A bounded CFG inspection gives `d8…` a more specific emulation candidate: in both cached mesh revisions, both ballots occur in the same basic block in `main`, after eight reconvergent selection diamonds. Cutting traversal at that block leaves 16 reachable prefix blocks, no cycles, no dead ends, no switches and no function calls. Every syntactic entry path therefore reaches the common ballot location. Both subgroup-scope ballots immediately extract component zero of their vector result as the consumed 32-bit mask. This supports investigating a Workgroup-wide reconstruction of that mask for an exactly 32-invocation group, assuming valid execution; it does not prove memory validity, complete mask/builtin equivalence, output equivalence or uniformity of any other operation. The two existing barriers alone would not establish this placement property.

In pinned upstream source revision `4cf785a0b143bb34026506c9a3fe4f4bb93c797d`, `gpu/vk/vk_draw.cpp::build_graphics` calls `pin_subgroup` on every graphics stage. That helper requests 32 only for supported bits in `dev.sg32_stages`. `vk_main.cpp` constructs the mask from subgroup-size-control capability, range, supported stages and the opt-out switch. `vk_shader.cpp::profile_graphics` selects the short 32-lane lowering for those same stage bits; task, mesh and fragment translation each use their corresponding graphics profile. The SPIR-V does not encode the runtime pipeline's required subgroup request. An actual draw-site pipeline observation is still needed to prove the request on the timed build/device.

The mesh emitter stores the guest stage-buffer attributes and primitive map in Workgroup arrays, using 32-word blocks and Workgroup barriers before exporting Vulkan mesh outputs. This identifies a concrete translator-owned source of shared storage to inspect. It does not show that these arrays, subgroup instructions, fragment work, rasterization, or attachments dominate either measured pass.

## Reading later stages safely

[The new catalog](../tools/graphics_catalog.py) complements the existing first-stage reader without changing it. Serialization in `gpu/vk/vk_progcache.cpp::put_info/get_info` writes native `sizeof(Shader::Info)` bytes, six counted descriptor arrays, a counted attribute-pair map, then counted vectors of handle-program words. A byte search for the next SPIR-V magic is ambiguous. Deserializing the raw pointer-bearing Info into host C++ objects is also unnecessary.

Instead, supply an ABI profile obtained from trusted source headers and skip each object/vector with record bounds. The parser checks every stage identity, module extent/magic, count extent, cache stamp and exact final record position. It returns hashes and metadata only. Compressed selected records are explicitly rejected; uncompressed HSP1/HSP2 records are supported. Exact framing and valid SPIR-V are strong consistency checks, not authentication of an ABI profile or evidence of original compiler provenance.

The local x86-64 Linux C++20 layout probe used Clang 22.1.8, `-DNDEBUG`, and the source's compatibility headers ahead of system headers. It measured Info **816**, descriptors **8,16,40,28,48,32** bytes in serialization order, and `Shader::IR::Attribute` **8** bytes. The matching header SHA-256 is `ce98d18acaa94b23c46ca410405ffe8ee1df8358fd698bc455b3c404208cac8e`. This is a probe of the pinned local headers, not a recovered cache-producer compiler invocation. The same sizes consumed both targets in both caches exactly and yielded ten validator-passing modules.

To repeat the layout probe, compile a small trusted program that includes `shader_recompiler/shader_info.h` and prints `sizeof(Shader::Info)`, the six descriptor types (`ConstantBufferDescriptor`, `StorageBufferDescriptor`, `TextureBufferDescriptor`, `ImageBufferDescriptor`, `TextureDescriptor`, `ImageDescriptor`), and `sizeof(Shader::IR::Attribute)`. Include directories in order: `gpu/compat`, `gpu/vk/compat`, `gpu/vk/eden`, `gpu/third_party/suyu`, relative to the trusted source checkout. Do not compile cache-supplied code. Bind the resulting profile to the cache stamp and record the source/header/compiler evidence:

```json
{
  "schema": 1,
  "cache_stamp": "d6c0514818a93a6e",
  "info_bytes": 816,
  "descriptor_bytes": [8, 16, 40, 28, 48, 32],
  "attribute_bytes": 8,
  "provenance": {
    "source_revision": "4cf785a0b143bb34026506c9a3fe4f4bb93c797d",
    "info_header_sha256": "ce98d18acaa94b23c46ca410405ffe8ee1df8358fd698bc455b3c404208cac8e",
    "layout_probe": "Clang 22.1.8, C++20, NDEBUG, x86-64 Linux, Hookshot compatibility headers"
  }
}
```

```sh
python3 tools/graphics_catalog.py local/private-programs.bin \
  --abi-profile local/private-abi.json --program 43ec86f35fd937b9
python3 -m unittest tests.test_graphics_catalog -v
```

The six synthetic regression tests cover multiple stages, pointer-like skipped bytes, descriptor/map/nested-vector framing, bad later-stage magic, enormous counts, wrong ABI/stamp, trailing data, and compressed-record refusal. They do not require the game, Hookshot, or SPIRV-Tools.

## Next decisive graphics work

1. Extend the bounded identity observer to the **actual mesh draw command sites**, preserving every stage's index, NVN/Vulkan type and digest. Record key/variant, cache/pipeline identity, stage-specific required-subgroup requests, draw ordinal, direct grid or the converted indirect/count-buffer ranges, attachments/formats/sample count, viewport/scissor, depth/blend/raster state, and resource generations. Current compute identity emission does not cover these draws. The existing `StatsQuery` site near mesh command emission and stage metadata already retained by the identity patch provide concrete integration points; coordinate source ownership before editing.
2. Use existing bounded draw-stat support to measure per-target task/mesh/fragment invocation or primitive counts **when advertised by the device**, plus a per-draw elapsed query. Keep the enclosing pass query to expose attachment/load/store and neighboring-draw cost. Invocation statistics are workload counts, not individual-stage time. Stage cost requires available performance counters or a controlled graphics replay, including all fixed state and resources. Preserve indirect conversion/synchronization and GPU-produced arguments; CPU mirrors are not sufficient.
3. Once exact runtime identities are observed, perform a compile-only graphics-pipeline probe of **all stages together**, with the actual mesh-output limits and fixed state. Request executable statistics/representations where supported, retaining stage association. Prioritize the 222557-word nightly `43…` mesh for spill/code-size inspection, and inspect its generated Workgroup export epilogue and broadcasts against the native IR. Module length is a reason to inspect; it is not a measured bottleneck. Fragment stages should receive their own statistics because they can dominate a color/depth pass despite smaller modules.
4. Capture one selected draw with its incoming color/depth state, textures/samplers, buffers, aliases, indirect/count arguments, descriptor claims, guest page table and relevant temporal inputs. Reuse the physical-memory capture work, expanding stage coverage to NVN6/7/1. Compare complete attachments and written resources before evaluating an optimization. None of the available cache metadata supplies those inputs.

Hair draw identity remains unknown. The existing coarse timing key and stage types cannot identify Link's hair, foliage or an algorithm. A bounded draw/attachment capture with visible-frame correlation is the next way to establish that mapping; image-quality experiments need that evidence and the same scene/camera.
