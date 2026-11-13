# Opt-in native mesh warp32 lowering

The [independently authored integration patch](../patches/hookshot/mesh-warp32-lowering.patch) now builds an experimental native renderer path for the exact reviewed `d8e59341cf4f629c` mesh stage. It remains **disabled by default**. This handoff establishes source/build checks and a compile-only generator comparison; any renderer pilot, real mesh-output replay or performance result needs separate frozen evidence. It does not establish native-quality 60 FPS or an image-quality change.

The patch follows the nine-patch renderer chain in [the build receipt](../research/native-mesh-warp32-integration.json). It embeds program/module digests and independently authored transformation code, with no game shader blob. Its C++ structural core ports the reviewed [CPU prototype](../research/mesh-warp32-transform.md). The existing translator emits the original module first; a guarded post-emission pass changes only its two ballots and logical subgroup ID/Lt-mask behavior. The generic Eden subgroup emitter and `profile_graphics` remain unchanged.

## Narrow eligibility

`RX_VK_OO_MESH_WARP32=8` requests the reviewed experimental width; `16` is also accepted. Unset, empty, zero,32 or any other string disables the lowering. Configuration is immutable for the process; changing mode requires a fresh process and separately controlled caches.

Every condition must hold before replacement:

- Native mesh program `d8e59341cf4f629c`, variant `0c8f08626a9d98a4`, exactly two stages: NVN7/Vulkan mesh128 followed by NVN1/fragment16.
- Original mesh SHA-256 `835aa59799c6ecb621034d946e2fa4eadb1aa2407d293321ae3e3052882bc9b3` and unchanged fragment `569b378a012388d711a115030bd1f41af66d38aebb645118baacd1e90a8f5a3a`.
- Intel vendor `8086`, A770/DG2 device `56a0`, Mesa Intel driver ID/version `0x06802004`, native mesh and subgroup-size-control features, original32 request support, requested8/16 support and adequate mesh/shared limits.
- Exact compiler options `HS_CS32_MAX_INSTS=100000`, `HS_SPILL_BATCH=1`, `HS_PUSH_SHADOW=1`, `shader_spilling_rate=1`. `INTEL_DEBUG`, `HS_SPILL_PCT`, `HS_RA_DEBUG`, `HS_FORCE_SIMD8`, `HS_LARGE_GRF` and `RX_VK_WARP64` must be absent. `RX_VK_NOOPT` must be off using upstream's normal switch parser; enabling it rejects the lowering.
- The complete structural audit: SPIR-V1.6 Logical/GLSL450 MeshEXT main, literal local size32×1×1, exactly the reviewed subgroup builtin inventory, direct builtin loads, precisely two subgroup-scope ballots with one low-word consumer each, and a single uniformly reached, non-revisited ballot block without prefix exits/cycles/calls or intervening termination.
- Rewritten output SHA-256 exactly `8754612a7395d1f804d4e56a7c18cb5a6f23ed2e02f01879cbc51bd108682de0`.

The device check reserves14612 Workgroup bytes and14740 combined payload/shared bytes, preserving the original declared payload. It checks the local-size/output maxima against advertised limits. Workgroup size stays32; mesh grid, descriptors, fragment stage, output stores and quality work are preserved. Only the qualifying native mesh stage requests8/16; all other programs retain their original policy. There is no generic vote/shuffle emulation and no change to the task stage of `43ec86f35fd937b9`.

The driver ID/version gate does **not** hash the loaded ICD automatically. Controlled launches must separately pin the audited library `5ea19ac96aa52d061ecf2b8e23df7ed3d7d3b36c7d2ae7fe62f01c5a9b610aaa`; another patched Mesa build can share its version number. The scope is the reviewed A770 configuration, not a portable Vulkan lane-mapping rule. The [mesh mapping observation](../research/mesh-mapping-probe.md), [implementation audit](../research/mesh-lane-mapping.md), live shader identity and separately reviewed output comparisons supply evidence with different limits; none is silently promoted by this patch.

## Implementation and cache behavior

The central JIT/seed hook is `vk_shader.cpp::translate_mesh_into`, after `record_answers` knows the full variant and before cache save/ready publication. `mesh_warp32_apply` audits/rebuilds a private word vector, computes the exact candidate digest and prepares its identity before creating a replacement module. Only after successful module creation does it replace the selected stage and destroy the temporary baseline handle. Nothing is hot-swapped during draws. Every eligibility/audit/rewrite/module-creation failure retains the original module and width32 request, with a bounded reason log.

The authored binary core uses public SPIRV-Headers utilities, bounded instruction/ID parsing, checked operand accesses and minimum operand counts. It emits two distinct Workgroup uint32[32] arrays, two uniform AcquireRelease/WorkgroupMemory barriers, logical-lane/mask arithmetic from local X, and removes all residual subgroup operations/builtin interfaces. The extra storage is256 bytes. No runtime assembler, disassembler or SPIRV-Tools library is required.

The disk cache intentionally continues storing **canonical original SPIR-V**. No certificate/width field is serialized and cache format remains1. Full cache loads rerun the same identity/audit/rewrite path before ready publication, including prewarm/borrowed programs. The lightweight loader is compute-only. A nonserialized Stage width marks only the successfully selected candidate. Cache-save digest accounting explicitly preserves that candidate identity instead of overwriting it with canonical-cache bytes. Thus a cached mesh835… and an actual selected mesh875… are expected under this mode; runtime observers report the selected875… identity. Existing IR/SPIR-V dumps performed before the post-emission hook also describe the canonical original; they must not be mislabeled as selected candidate dumps.

Both new helper source files are explicit `nx2/build.py` translator-stamp dependencies: merely adding a header under `gpu/vk` would otherwise miss that scan. The program-cache stamp mixes the translator stamp with policy revision and requested width. Dependent cache/preparation stamps therefore distinguish modes. Full graphics and graphics-pipeline-library keys additionally distinguish the lowered width. All ordinary, cold-library and prewarm graphics builds pass through the same `build_graphics` stage-policy helper. Graphics/mesh-input observers use that helper as well, while the existing input-capture original-digest filter correctly refuses candidate snapshots.

The narrowed stage leaves `REQUIRE_FULL_SUBGROUPS` unset and retains local X32, matching the reviewed SPIR-V1.6 condition and previous request shape. It does not weaken unrelated stage requests. Program fields are finalized before atomic ready publication; there is no racy per-pipeline module switch.

**Pipeline creation failure has no automatic baseline recovery.** The patch preserves upstream failure semantics at that later boundary; it must not report a failed/skipped draw as successful baseline recovery. Any pipeline/device failure or omitted work invalidates a pilot or benchmark. A correct automatic recovery would require explicit per-pipeline choice/key/identity bookkeeping and is outside this patch.

## Native versus Python generator metadata

The native writer preserves generator value0 in header word2. `spirv-as` tagged the Python prototype with generator7/version0. Both modules contain44043 words; every instruction/ID word and the other header words compare exactly; native output is875… while Python output is9d23…. Both validate with Vulkan1.3 uniform-buffer-standard-layout.

Pinned Mesa frontend inspection found identical relevant generator branches for `(0,0)` and `(7,0)`: neither is glslang/shaderc, the mis-tagged LLVM/linker17 case, clay19 or spiregg14. That is implementation evidence for the audited source, not a portable guarantee about generator metadata.

A separate private compile helper added only capture-internal-representation flags/queries to the existing no-execution graphics probe. The public frozen helper/wrapper were unchanged. Two fresh compile-only cases used the audited library, runtime compiler policy, requested mesh8, identical fragment and controlled state. Both selected actual meshSIMD8/fragmentSIMD8. All executable statistics agree except the expected mesh source-hash statistic. The mesh GEN Assembly **text representation** is byte-identical at1964987 bytes, and the fragment representation is also exact. Mesh Final NIR differs on exactly one `source_blake3` metadata line; removing only that line yields identical remaining text. Push maps and all fragment representations match. Raw NIR/assembly stays private; public evidence contains only sizes/hashes and comparison outcomes.

These results isolate the observed header difference for this driver and compilation. They do not constitute a real native-digest output replay, complete runtime pipeline recreation or FPS measurement. A freshly reviewed runtime admission must identify875…; an instrumenter that reassembles the module can reset its generator tag and cannot by itself isolate that raw-header difference.

## Frozen handoff and checks

The optimized four-worker renderer build passed; source is pinned at `4cf785a0b143bb34026506c9a3fe4f4bb93c797d` plus the recorded ten-patch chain.

| Artifact | SHA-256 |
|---|---|
| Integration patch | `022dcb3aee0a38d3a8e8c3ba187867d120bbc97aea75a432e4728870f8d5c196` |
| Frozen renderer | `29bdc49d929d60464b93d8c257d1363c171129af5db7e6ff5607a45e88a063a2` |
| Frozen build receipt | `70c9e64942c7f97d6d94e16614cbaa02a9ee71209f8d79fdee0dc5e5bfb3ba94` |
| C++ structural core | `7aad4045124990b8d5d4e35ac919b1bb641ee802d0e8a34ce3e349a317790882` |
| Authored header | `9a84b314e78c9a8a358847a1ba96e21998e7b0783687523c172476b92f6ce644` |

Renderer flags are C++20/O2/g0/NDEBUG/release/x86-64-v3. The generic build banner's lifted-code O1 setting does not describe the renderer. Previous frozen binaries and Python tools remain unchanged. Private frozen artifacts are under ignored `local/mesh-warp32-native-frozen-022dcb3aee0a/`.

[The production checker](../patches/hookshot/check_mesh_warp32_lowering.py) compiles the actual authored core with O2/NDEBUG/strict warnings. Authored fixtures exercise successful lowering and11 negative cases, including malformed recognized opcodes that must reject without unchecked indexing. Compiled CPU mocks use the actual device/compiler/stage-policy functions to reject wrong hardware/version/capabilities, insufficient shared memory, incompatible environment and disabled optimization, and to preserve baseline stage requests. The checker verifies application after all nine prerequisite renderer patches using a temporary Git index; it checks unchanged native draw API arguments, JIT/load parity, digest accounting and source/policy stamps.

Four repository tests cover production fixture execution, policy mocks, source extraction refusal and the core's no-execution boundary. External core compilation needs a C++20 compiler and public SPIRV-Headers; mock compilation needs only C++20. These tests need no game, Vulkan loader/device or proprietary tools. All447 repository tests passed at handoff.

```sh
python3 patches/hookshot/check_mesh_warp32_lowering.py --checkout "$PINNED_PRIVATE_SOURCE"
python3 -m unittest tests.test_mesh_warp32_lowering_patch -v
```

No integrated game/renderer launch was performed by this build/compile task. Subsequent pilots and fresh raw-native validation must report their own exact renderer/module/driver identities, failures, image checks and performance limits.

## Subsequent integrated identity diagnostic

The parent subsequently ran this exact frozen renderer with the option disabled
and with width 8 selected. [Sanitized runtime metadata](../research/native-renderer-identity-pilot.json)
records 32 matching command-recording events per mode, the expected original or
native-rewrite digest, requested subgroup policy, gameplay state and a completed
private image capture. Both owned processes stopped afterward. The initial
cold-cache attempt reached its bounded deadline before the target; that attempt
remains separate.

This is an identity/image diagnostic, **not complete rendering acceptance**. The
observer does not capture actual executable width or the GPU-generated indirect
grid. Cameras, animation and work counts differ between the two images. The
control bridge resized them to its default 1280×720; logs independently show
1536×864 scene rendering and a 2048×1152 upscale. Full-size captures need explicit
shot dimensions. No FPS comparison follows from these runs.

Existing startup cooperative-matrix networks were rejected in both modes, and
the copied warm driver cache has unresolved compiler-override provenance. A
separate cache-key audit found missing experimental compiler settings and
presence/value collisions. Those gaps must be repaired or eliminated before
using cached results as a correctness/performance reference. The runtime
metadata deliberately leaves native-feature validation, pixel equivalence and
real 60 FPS false.
