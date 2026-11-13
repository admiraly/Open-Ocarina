# Opt-in native mesh warp32 lowering — revision 2

The [independently authored integration patch](../patches/hookshot/mesh-warp32-lowering.patch) is now revision2. Its safe compiler profile requires `HS_CS32_MAX_INSTS` **absent**, not empty or zero, plus exactly `HS_SPILL_BATCH=1`, `HS_PUSH_SHADOW=1` and `shader_spilling_rate=1`. The earlier MAX-present profile could enable a global compute-width fallback and must not be used as a current runtime recommendation. Native mesh lowering remains **disabled by default**. This revision has build/CPU checks and a bounded compile-only comparison; no revision2 integrated launch, new-driver runtime validation, native-quality 60 FPS or image-quality gain is claimed.

The patch follows the nine-patch renderer chain in [the revision2 receipt](../research/native-mesh-warp32-integration-v2.json). The [revision1 receipt](../research/native-mesh-warp32-integration.json) and its observations remain historical; the public patch filename is unchanged, with V1 bytes retained in Git commit `01f8fa2` and its private freeze. It embeds program/module digests and independently authored transformation code, with no game shader blob. Its C++ structural core ports the reviewed [CPU prototype](../research/mesh-warp32-transform.md). The existing translator emits the original module first; a guarded post-emission pass changes only its two ballots and logical subgroup ID/Lt-mask behavior. The generic Eden subgroup emitter and `profile_graphics` remain unchanged.

## Narrow eligibility

`RX_VK_OO_MESH_WARP32=8` requests the reviewed experimental width; `16` is also accepted. Unset, empty, zero,32 or any other string disables the lowering. Configuration is immutable for the process; changing mode requires a fresh process and separately controlled caches.

Every condition must hold before replacement:

- Native mesh program `d8e59341cf4f629c`, variant `0c8f08626a9d98a4`, exactly two stages: NVN7/Vulkan mesh128 followed by NVN1/fragment16.
- Original mesh SHA-256 `835aa59799c6ecb621034d946e2fa4eadb1aa2407d293321ae3e3052882bc9b3` and unchanged fragment `569b378a012388d711a115030bd1f41af66d38aebb645118baacd1e90a8f5a3a`.
- Intel vendor `8086`, A770/DG2 device `56a0`, Mesa Intel driver ID/version `0x06802004`, native mesh and subgroup-size-control features, original32 request support, requested8/16 support and adequate mesh/shared limits.
- Exact compiler options `HS_SPILL_BATCH=1`, `HS_PUSH_SHADOW=1`, `shader_spilling_rate=1`. All nine forbidden variables must be absent: `HS_CS32_MAX_INSTS`, `HS_FORCE_SIMD8`, `HS_SUBGROUP8`, `HS_SPILL_PCT`, `HS_LARGE_GRF`, `HS_LARGE_GRF_BIT`, `HS_RA_DEBUG`, `INTEL_DEBUG`, `RX_VK_WARP64`. Presence rejects the lowering even for empty strings or `0`. `RX_VK_NOOPT` must be off using upstream's normal switch parser; enabling it rejects the lowering.
- The complete structural audit: SPIR-V1.6 Logical/GLSL450 MeshEXT main, literal local size32×1×1, exactly the reviewed subgroup builtin inventory, direct builtin loads, precisely two subgroup-scope ballots with one low-word consumer each, and a single uniformly reached, non-revisited ballot block without prefix exits/cycles/calls or intervening termination.
- Rewritten output SHA-256 exactly `8754612a7395d1f804d4e56a7c18cb5a6f23ed2e02f01879cbc51bd108682de0`.

The device check reserves14612 Workgroup bytes and14740 combined payload/shared bytes, preserving the original declared payload. It checks the local-size/output maxima against advertised limits. Workgroup size stays32; mesh grid, descriptors, fragment stage, output stores and quality work are preserved. Only the qualifying native mesh stage requests8/16; all other programs retain their original policy. There is no generic vote/shuffle emulation and no change to the task stage of `43ec86f35fd937b9`.

The driver ID/version gate does **not** hash the loaded ICD automatically; another patched Mesa build can share its version number. The old audited library `5ea19ac96aa52d061ecf2b8e23df7ed3d7d3b36c7d2ae7fe62f01c5a9b610aaa` was used only for the bounded revision2 compile comparison. Further integrated validation must pin a separately approved cache-safe driver and establish its runtime behavior; the ID/version gate alone supplies no such admission. The scope is the reviewed A770 configuration, not a portable Vulkan lane-mapping rule. The [mesh mapping observation](../research/mesh-mapping-probe.md), [implementation audit](../research/mesh-lane-mapping.md), live shader identity and separately reviewed output comparisons supply evidence with different limits; none is silently promoted by this patch.

## Why MAX must be absent

In the pinned local Mesa `brw_compile_cs.cpp`, `getenv("HS_CS32_MAX_INSTS") != NULL` enables a second compile attempt that changes a failed required32 compute program to8 lanes. Thus `0` disables its positive numeric instruction threshold but **does not disable the fallback**; an empty value still enables it too. The numeric threshold in `brw_reg_allocate.cpp` is separately guarded by `MESA_SHADER_COMPUTE`/SIMD32. This is a global compute concern, not the reviewed mesh-only logical-warp transformation.

Revision2 rejects all MAX presence before native mesh admission. Its eligibility guard does not sanitize the process environment or prevent another component from using a global option. A controlled launcher must enforce the safe profile for **both baseline0 and candidate8**, before device/compiler initialization. Refusing a mesh lowering while leaving an unsafe global environment is not complete native-feature protection.

The `native-safe` graphics compile-probe policy now explicitly clears all known controls before applying the three allowed values. The historical `runtime` profile still sets the same four values, and `diagnostic` still clears overrides; the current wrapper also clears newly identified controls. These are compile-only comparison profiles, not interchangeable runtime launch recommendations. The old `babe4464…` wrapper bytes were recovered exactly from the immutable V1 commit and preserved privately; new source is pinned in the V2 receipt.

## Safe-three versus legacy graphics compilation

Two fresh compile-only cases used raw native module875…, unchanged fragment569…, required mesh8, the same diagnostic state and old audited5ea library. One used safe-three with MAX absent; the other used legacy MAX100000 plus the same three settings. All executable properties/statistics matched, including source hashes. All six private internal representations were byte-identical: mesh and fragment Final NIR, push maps and GEN Assembly text. Mesh remained17981 instructions/284 spills/258 fills/4096-byte scratch; fragment remained2629/46/100/2048-byte scratch.

This agrees with the source's compute-only MAX readers and preserves the measured mesh opportunity without keeping a global compute fallback enabled. It does not validate compute outputs, startup networks, driver-cache provenance or complete rendering. No queue was retrieved, no commands submitted and no shader executed in these comparisons. Old IR and runtime artifacts remain unchanged.

## Implementation and cache behavior

The central JIT/seed hook is `vk_shader.cpp::translate_mesh_into`, after `record_answers` knows the full variant and before cache save/ready publication. `mesh_warp32_apply` audits/rebuilds a private word vector, computes the exact candidate digest and prepares its identity before creating a replacement module. Only after successful module creation does it replace the selected stage and destroy the temporary baseline handle. Nothing is hot-swapped during draws. Every eligibility/audit/rewrite/module-creation failure retains the original module and width32 request, with a bounded reason log.

The authored binary core uses public SPIRV-Headers utilities, bounded instruction/ID parsing, checked operand accesses and minimum operand counts. It emits two distinct Workgroup uint32[32] arrays, two uniform AcquireRelease/WorkgroupMemory barriers, logical-lane/mask arithmetic from local X, and removes all residual subgroup operations/builtin interfaces. The extra storage is256 bytes. No runtime assembler, disassembler or SPIRV-Tools library is required.

The disk cache intentionally continues storing **canonical original SPIR-V**. No certificate/width field is serialized and cache format remains1. Full cache loads rerun the same identity/audit/rewrite path before ready publication, including prewarm/borrowed programs. The lightweight loader is compute-only. A nonserialized Stage width marks only the successfully selected candidate. Cache-save digest accounting explicitly preserves that candidate identity instead of overwriting it with canonical-cache bytes. Thus a cached mesh835… and an actual selected mesh875… are expected under this mode; runtime observers report the selected875… identity. Existing IR/SPIR-V dumps performed before the post-emission hook also describe the canonical original; they must not be mislabeled as selected candidate dumps.

Both new helper source files are explicit `nx2/build.py` translator-stamp dependencies: merely adding a header under `gpu/vk` would otherwise miss that scan. The program-cache stamp mixes the translator stamp with policy revision and requested width. Revision2 and the full/GPL lowered-policy tag advance together; old V1 cache/preparation identities are not reused. Dependent cache/preparation stamps therefore distinguish modes. Full graphics and graphics-pipeline-library keys additionally distinguish the lowered width. All ordinary, cold-library and prewarm graphics builds pass through the same `build_graphics` stage-policy helper. Graphics/mesh-input observers use that helper as well, while the existing input-capture original-digest filter correctly refuses candidate snapshots.

The narrowed stage leaves `REQUIRE_FULL_SUBGROUPS` unset and retains local X32, matching the reviewed SPIR-V1.6 condition and previous request shape. It does not weaken unrelated stage requests. Program fields are finalized before atomic ready publication; there is no racy per-pipeline module switch.

**Pipeline creation failure has no automatic baseline recovery.** The patch preserves upstream failure semantics at that later boundary; it must not report a failed/skipped draw as successful baseline recovery. Any pipeline/device failure or omitted work invalidates a pilot or benchmark. A correct automatic recovery would require explicit per-pipeline choice/key/identity bookkeeping and is outside this patch.

## Historical V1 native-versus-Python generator comparison

The native writer preserves generator value0 in header word2. `spirv-as` tagged the Python prototype with generator7/version0. Both modules contain44043 words; every instruction/ID word and the other header words compare exactly; native output is875… while Python output is9d23…. Both validate with Vulkan1.3 uniform-buffer-standard-layout.

Pinned Mesa frontend inspection found identical relevant generator branches for `(0,0)` and `(7,0)`: neither is glslang/shaderc, the mis-tagged LLVM/linker17 case, clay19 or spiregg14. That is implementation evidence for the audited source, not a portable guarantee about generator metadata.

A separate private compile helper added only capture-internal-representation flags/queries to the existing no-execution graphics probe. The public frozen helper/wrapper were unchanged. For that V1 comparison, two fresh compile-only cases used the audited library, legacy MAX-present compiler policy, requested mesh8, identical fragment and controlled state. Both selected actual meshSIMD8/fragmentSIMD8. All executable statistics agree except the expected mesh source-hash statistic. The mesh GEN Assembly **text representation** is byte-identical at1964987 bytes, and the fragment representation is also exact. Mesh Final NIR differs on exactly one `source_blake3` metadata line; removing only that line yields identical remaining text. Push maps and all fragment representations match. Raw NIR/assembly stays private; public evidence contains only sizes/hashes and comparison outcomes.

These results isolate the observed header difference for this driver and compilation. They do not constitute a real native-digest output replay, complete runtime pipeline recreation or FPS measurement. A freshly reviewed runtime admission must identify875…; an instrumenter that reassembles the module can reset its generator tag and cannot by itself isolate that raw-header difference.

## Revision 2 frozen handoff and checks

The optimized four-worker renderer build passed; source is pinned at `4cf785a0b143bb34026506c9a3fe4f4bb93c797d` plus the recorded ten-patch chain.

| Revision2 artifact | SHA-256 |
|---|---|
| Current public patch | `8d0cc053103ccf90b51e6c55c6e7ee39292636f03b85e16d74bd8d8d1221854b` |
| Frozen renderer | `d2b60ba14de991bf335f5b46d3c64a0749489a390c3fbb2db11f162fdb7b3340` |
| Frozen receipt | `067ad822f389181f7e825c83dfabc1e4901a67e9b62d7a4eca403b3d77058265` |
| Authored header/revision | `73853cf02de8a365568a50d52fbbd2b80363359634bf3bb1655032e40b347b18` |
| Unchanged C++ rewrite core | `7aad4045124990b8d5d4e35ac919b1bb641ee802d0e8a34ce3e349a317790882` |
| Compile-probe wrapper | `ec4cbaf812276fda0c266bd4f6c43729aedfac6cdc8b78a59b1eb47b0ef09f37` |

Private V2 artifacts are under ignored `local/mesh-warp32-native-v2-frozen-8d0cc053103c/`. The native module is byte-identical to V1's875… module and passed Vulkan1.3 SPIR-V validation. The rewrite body, descriptors, physical/mesh replay tools and instrumenters were unchanged. The fresh renderer build used four workers, O2/NDEBUG/release/x86-64-v3 and completed without failures.

The production CPU mocks now refuse every forbidden option with empty,0 or1 values, plus legacy MAX100000. A new compile-wrapper regression checks that poisoned MAX empty/zero values cannot leak into `native-safe`. The focused16-test set and full473-test suite passed. Existing malformed-opcode, device/capability and stage-policy tests remain active. No V2 renderer was launched by this task; new-driver full validation remains outstanding.

[The production checker](../patches/hookshot/check_mesh_warp32_lowering.py) compiles the actual authored core with O2/NDEBUG/strict warnings. Authored fixtures exercise successful lowering and11 negative cases, including malformed recognized opcodes that must reject without unchecked indexing. Compiled CPU mocks use the actual device/compiler/stage-policy functions to reject wrong hardware/version/capabilities, insufficient shared memory, incompatible environment and disabled optimization, and to preserve baseline stage requests. The checker verifies application after all nine prerequisite renderer patches using a temporary Git index; it checks unchanged native draw API arguments, JIT/load parity, digest accounting and source/policy stamps.

Four repository tests cover production fixture execution, policy mocks, source extraction refusal and the core's no-execution boundary. External core compilation needs a C++20 compiler and public SPIRV-Headers; mock compilation needs only C++20. These tests need no game, Vulkan loader/device or proprietary tools. The original V1 handoff passed447 tests; the current V2 full-suite count is473 as recorded above.

```sh
python3 patches/hookshot/check_mesh_warp32_lowering.py --checkout "$PINNED_PRIVATE_SOURCE"
python3 -m unittest tests.test_mesh_warp32_lowering_patch -v
```

## Historical V1 frozen handoff

The following table identifies **V1**, not the current public patch/source. Those artifacts and all observation/hash values in their pilot are preserved. The public pilot JSON adds only explicit revision1/historical/source-commit qualification, with its original bytes privately archived.

| Artifact | SHA-256 |
|---|---|
| Integration patch | `022dcb3aee0a38d3a8e8c3ba187867d120bbc97aea75a432e4728870f8d5c196` |
| Frozen renderer | `29bdc49d929d60464b93d8c257d1363c171129af5db7e6ff5607a45e88a063a2` |
| Frozen build receipt | `70c9e64942c7f97d6d94e16614cbaa02a9ee71209f8d79fdee0dc5e5bfb3ba94` |
| C++ structural core | `7aad4045124990b8d5d4e35ac919b1bb641ee802d0e8a34ce3e349a317790882` |
| Authored header | `9a84b314e78c9a8a358847a1ba96e21998e7b0783687523c172476b92f6ce644` |

Renderer flags are C++20/O2/g0/NDEBUG/release/x86-64-v3. The generic build banner's lifted-code O1 setting does not describe the renderer. Previous frozen binaries and Python tools remain unchanged. Private frozen artifacts are under ignored `local/mesh-warp32-native-frozen-022dcb3aee0a/`.


The V1 source/build task did not launch the renderer; its subsequent parent pilot is recorded below. The V2 receipt has no integrated launch. Further validation must report its own exact renderer/module/driver identities, failures, image checks and performance limits.

## Historical V1 integrated identity diagnostic (legacy MAX profile)

The parent subsequently ran the **V1** frozen renderer29bd… with its legacy MAX-present profile, with the option disabled
and with width 8 selected. [Sanitized runtime metadata](../research/native-renderer-identity-pilot.json)
records 32 matching command-recording events per mode. Its explicit `integration_revision=1` and historical legacy-MAX annotation prevent treating these events as V2 validation. It retains the expected original or
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
