# Bounded native mesh logical-output replay

The conditional d8 mesh now matches original32 bit-for-bit at required widths 8 and 16 across 160 native groups of one chosen frozen logical input graph. Baseline32 repeats also match. All 24 real mesh cases passed complete output/count/writtenness/guard comparisons; no image comparison or FPS result follows from this test. [Sanitized evidence](../research/native-mesh-replay-observation.json) pins each plan, executable and output.

[`tools/mesh_replay.py`](../tools/mesh_replay.py) prepares this bounded native `MeshEXT` differential experiment, or an independently authored output fixture. Its separate [`tools/vulkan_mesh_replay.c`](../tools/vulkan_mesh_replay.c) preserves the stage and uses strict optimized flags. The independently authored fixture first passed four GPU cases against its CPU golden output. Real-module execution then received separate source/plan reviews and exclusive leases.

The experiment keeps the mesh stage, original mesh output stores, `SetMeshOutputsEXT`, workgroup storage, barriers and applicable floating-point controls. The [stage-preserving instrumenter](mesh-output-instrument.md) mirrors every defined output component, writtenness and per-lane output counts into an isolated SSBO. No fragment shader, images, attachments or assets are supplied; rasterizer discard and attachment-free dynamic rendering prevent raster work. This supports shader-level logical-output evidence, not rendering fidelity or an FPS result.

## Three distinct evidence modes

| Mode | Input meaning | Historical claim |
|---|---|---|
| `synthetic-fixture` | Independently authored mesh shader and owned input bytes, with a CPU golden full output | None |
| `frozen-logical-readonly` | Explicitly chosen, immutable, complete descriptor-range slices/null states for the two digest-pinned d8 modules | CPU atomicity, imported backing provenance and reproduction of the live draw remain unestablished |
| `historical-draw` | Identity-bound actual input/launch snapshots plus complete backing, alias, quiescence and ordering evidence | Validation requires the evidence, but does not authenticate producer assertions or independently prove live-draw equivalence |

The frozen-readonly mode is useful even when the raw capture correctly reports unresolved historical provenance. For these exact modules, the instrumenter's audit establishes that every external input descriptor is read-only and that input reads use logical descriptor addressing, not Vulkan device addresses. Consequently, a replay input write cannot communicate between aliased input views: there are no such writes. Each chosen descriptor slice can be an owned buffer with identical bytes and its exact original range, rebased to offset 0. The full output and control allocations are fresh and separate from every input. The report explicitly retains the unknown historical status; it does not upgrade a raw GPU snapshot into a coherent historical capture.

Distinct descriptors with equal byte digests and equal lengths may share one immutable replay input buffer. Other input views use separate exact-range copies. This preserves each bound view's bytes, byte offsets within the view and `OpArrayLength` behavior. Guest-address table values stay unchanged. This construction is permitted by the exact read-only/logical-pointer audit; it is not a general writable-alias replay scheme.

The historical mode additionally requires a hash-pinned `open-ocarina.mesh-logical-coverage/v1` evidence document tied to the complete event. It must enumerate exact slot/range/digest tuples, retained provenance/quiescence proof artifacts, canonical backing-generation pieces covering every data-bearing descriptor, original subgroup 32 policy, matching launch bytes and compiler policy, and no unresolved ownership/CPU-writer/import-alias/snapshot-order claims. Overlapping declared backing intervals must contain identical captured bytes. Missing or false claims fail closed. A manually asserted certificate is not automatically trusted evidence; reviewing the actual producer/proofs remains necessary.

## Supported modules and descriptor graph

The source pair is fixed:

- Original d8 mesh: `835aa59799c6ecb621034d946e2fa4eadb1aa2407d293321ae3e3052882bc9b3`.
- Conditional warp32 mesh: `9d23b7ebf3677f53fe6901c9b94f2c4927f1f0c47ed207c04c53df9b59ae9638`.

Preparation calls the trusted instrumenter itself, rather than accepting report-labelled arbitrary SPIR-V. The instrumenter source, source/derivative digest pairs, reflected descriptors, record layout, preservation audit and origin rewrite are checked. Execution rechecks the frozen sources, source modules, derivative modules and audit reports. Any changed algorithm, source or derivative needs a fresh reviewed preparation. Existing compile-only and earlier synthetic helpers are untouched.

Set 0 retains all eleven descriptor elements: five singleton UBO bindings 0–4, singleton SSBO bindings 5–7, and the three-element SSBO array at binding 8. Every data-bearing element needs its complete declared range and SHA-256, not a prefix or an inferred allocation length. The exact d8 audit establishes no input writes, images, physical pointers, live task payload or push constants. The original dead task-payload use is removed only through the instrumenter's explicit proof; no payload bytes are invented.

Observed null descriptors have a separate exact representation. Reviewed slots 8:1 and 8:2 may be null only when the source feature metadata enables `nullDescriptor`. They retain `VkBuffer=VK_NULL_HANDLE`, offset 0 and `VK_WHOLE_SIZE`, with no snapshot, length, allocation or fabricated ownership. The native plan uses an explicit null tag and the helper reproduces a real null Vulkan descriptor, requiring device support. It never replaces it with an empty or zero-filled buffer. The other nine descriptor positions still have their full byte slices. This distinction matters for the original descriptor-array length/query path.

## Window and output contract

The native launch grid is read from the exact twelve indirect bytes in the input graph. The selected positive replay window must lie inside it and contain at most 32 workgroups. Any zero native dimension forbids execution. The instrumenter's control buffer at set 1/binding 1 contains two `uvec4` values: originXYZ/reserved0 and windowXYZ/reserved0. Original `WorkgroupId` loads observe relative ID plus origin; capture records use relative IDs. The audit rejects a dependency on unprovided native `NumWorkgroups` or other unsupported stage inputs.

Capture set 1/binding 0 references a fresh output buffer at offset 64. Its descriptor range is exactly `44928 × window_groups`; another 64-byte outer guard follows it. The host initializes the entire allocation before every case. Each record includes inner guards, all 32 per-lane count rows, Position, nine generic vec4 arrays, triangle indices with padding, and all 5504 component-writtenness words. The [instrumenter contract](mesh-output-instrument.md) specifies the byte offsets.

Validation requires vertex count 128, primitive count 0–128 and agreement among every lane's count row. Every vertex component must be written. Exactly the emitted triangle components must be written, emitted indices must be within the vertex count, and padding/non-emitted primitive storage must remain untouched. Inner and outer guards must retain their initial patterns. All full initialized buffer bytes are compared exactly, including writtenness, counts, padding and guards. Defined float words use bitwise comparison; no tolerance hides mismatches.

Each plan has four helper cases: original32 twice, then conditional8 and conditional16. The baseline must first agree with itself. Each process gets the same frozen input bundle and a newly initialized output allocation. Executable properties must confirm the actual mesh width for every case; a required-width request alone does not count as evidence. Source input bytes and the origin control buffer are checked unchanged after the fence.

## CPU preparation and explicit execution

The preparation commands compile/validate SPIR-V and compile the host helper with `-std=c11 -O2 -DNDEBUG -Wall -Wextra -Werror`. Native binary framing checks use `--validate`, whose control path returns before `vkCreateInstance`; they neither create a device nor execute shader code.

```sh
# Independent fixture: full and empty primitive records, plus actual null framing.
python3 tools/mesh_replay.py fixture --output local/native-mesh-replay/fixture-new \
  --origin 128,0,0 --groups 2,1,1 --null-table-tail

# A chosen immutable logical graph; explicitly not a historical-draw reproduction.
python3 tools/mesh_replay.py prepare --output local/native-mesh-replay/chosen-new \
  --capture "$PRIVATE_MESH_INPUTS_JSON" --original "$ORIGINAL_MESH" \
  --conditional "$CONDITIONAL_MESH" --mode frozen-logical-readonly \
  --origin 0,0,0 --groups 1,1,1

sha256sum local/native-mesh-replay/fixture-new/prepared-plan.json
```

Preparation is the default work boundary. A separately reviewed plan and exclusive GPU lease are required before the explicit GPU command:

```sh
python3 tools/mesh_replay.py execute --prepared local/native-mesh-replay/fixture-new \
  --plan-sha256 "$REVIEWED_PLAN_SHA256" --driver "$AUDITED_INTEL_ICD"
```

The expected plan digest identifies content; it does not supply consent to execute game-derived code. Preparation remains CPU/source/build only until the parent reviews the frozen artifacts and grants an execution lease. Original or conditional game shaders must not be run implicitly after preparation. The first authorized executor test used only the independent fixture.

The current executable profile requires the audited A770 driver library, Vulkan 1.3, mesh shaders, dynamic rendering, executable-property queries, subgroup-size control, robust buffer access/robustness2, the needed integer/float features and uniform-buffer standard layout. Float 32 signed-zero/Inf/NaN preservation support is checked. Actual enabled features and the named compiler policy are recorded. `runtime` explicitly applies the four recorded compiler overrides; `diagnostic` clears them. Both clear other controlled ambient knobs. No physical-buffer-address feature, task/fragment stage or rendering attachment is needed by this logical target.

Every helper records exactly one bounded direct mesh draw. A two-second fence and thirty-second process timeout bound waiting; the shader is not replaced with a polling loop. On fence failure, the helper exits without idle/destruction waits. On process timeout/interruption, only its newly created process group is stopped. Existing processes are never signalled. Competing Hookshot/EVE processes cause refusal, while the operator must coordinate other GPU users. A started plan cannot be reused silently. These policies do not guarantee recovery from a failed kernel/device.

## Frozen fixture review handoff

The CPU-prepared review plan is `local/native-mesh-replay/fixture-review-01/`. It uses the independent fixture at all four widths/cases; there is no proprietary shader in this test. Because the fixture has no subgroup operations or input reads, it tests the executor/capture machinery, not the warp32 transformation or shader-side null-buffer reads.

Its origin 128 and X extent 2 produce one 128-primitive record and one zero-primitive record, exercising full/empty writtenness and untouched storage. The full initialized capture is 89,984 bytes. All eleven descriptor positions are present, with nine data views deduplicated into two owned buffers and two actual null descriptors.

| Artifact | SHA-256 |
|---|---|
| Reviewed prepared plan | `6b532b09515287e546d11a988f4b0a546ea23104dd6c1a92ada2e1f49cbfcea9` |
| Replay wrapper | `f913f0a4fe1cdb55ce75ee992450a3a331defecb743ab4b53a4197ffbe64f69b` |
| Native replay C source | `aa9169a15ee414a701d57633e66e6f97ce3b16251a02381884a922a5e67868fd` |
| Optimized helper | `dba1c3fc69787bb448534f5d3fdedb5e7f481b07193fe48b2d7b223d26d99a67` |
| Independent instrumented mesh | `e548e6d40320c28f1156e8c910fa64ee6f87b936cba15856b41648295b5828fa` |
| Frozen native input bundle | `06e8999c1bd656b51fc54e9834f9ea2e6c37cc5910eaf55e9878bbba5251e36a` |
| CPU expected full output | `468f2a6104f33a8adf303f928857d8609753fd73f8033236b61eb6d58d84d7d1` |

Eleven standard-library tests cover exact-range/slot/digest refusal, historical versus chosen-readonly evidence, historical overlap consistency, native input framing, actual null descriptor states, full/empty golden records, counts/writtenness/padding/guards, window bounds, no arbitrary module admission and preparation's CPU-only boundary. Strict optimized helper compilation and native framing validation pass. The full synthetic suite passed 414 tests.

## Authorized synthetic observation

Observed 2026-11-13, using the exact reviewed plan above and unchanged wrapper/C/helper/module artifacts. Every case submitted one two-workgroup draw and matched all 89,984 bytes against the independent CPU golden buffer and the other cases:

| Fixture case label | Requested / actual mesh width | Vertex counts | Primitive counts | Complete comparison |
|---|---|---|---|---|
| `original32-a` | 32 / 32 | 128,128 | 128,0 | CPU golden matched |
| `original32-b` | 32 / 32 | 128,128 | 128,0 | Baseline repeat matched |
| `conditional8` | 8 / 8 | 128,128 | 128,0 | CPU golden and baseline matched |
| `conditional16` | 16 / 16 | 128,128 | 128,0 | CPU golden and baseline matched |

These labels describe the executor case matrix. **All four used the same independent fixture shader**, which has no subgroup operations; no original or transformed game module was executed in this observation. Inner/outer guards, every defined-component marker, padding and non-emitted storage passed. Input and control bytes were unchanged; actual null descriptors were enabled and bound, and executable properties confirmed mesh stage 128 at each stated width. The fixture does not read its input descriptors, so this does not test shader-side null reads.

The execution report is private at `local/native-mesh-replay/fixture-review-01/execution-report.json`, SHA-256 `dbde162c195418c446be56eb6914a01abdaf92b9adf1ddf5b6f347c725900b13`. Every output matched SHA-256 `468f2a6104f33a8adf303f928857d8609753fd73f8033236b61eb6d58d84d7d1`. Device was Intel Arc A770 DG2, device ID `0x56a0`, Mesa 26.2.4, driver library SHA-256 `5ea19ac96aa52d061ecf2b8e23df7ed3d7d3b36c7d2ae7fe62f01c5a9b610aaa`. The exclusive GPU lease was released immediately after the fourth helper completed.

## Exact readonly/pointer observability audit

A separate CPU audit rechecked the exact source pair and both instrumented modules before real execution. Each uses `Logical GLSL450`, has three reachable functions and 72 direct call sites. The numbers below count static SPIR-V instructions, not dynamic executions or performance:

| Module | All module instructions | Reachable function instructions | External input loads | Input `ArrayLength` queries | Reachable input stores |
|---|---:|---:|---:|---:|---:|
| Original source | 8,995 | 7,000 | 213 UBO  + 29 SSBO | 8 | 0 |
| Conditional source | 9,233 | 7,215 | 213 UBO  + 29 SSBO | 8 | 0 |
| Original instrumented | 11,060 | 8,961 | Same inputs  + 2 control loads | 8 | 0 |
| Conditional instrumented | 11,298 | 9,176 | Same inputs  + 2 control loads | 8 | 0 |

Across the entire inspected modules, pointer equality/inequality/difference, pointer-to-integer/integer-to-pointer conversions, generic pointer casts, pointer bitcasts/loads/parameters and pointer arguments are absent. Every reachable pointer operand is used only as an AccessChain base, Load address, Store destination or ArrayLength operand. No unresolved pointer/access origin remains. Numeric guest-address arithmetic reads the chosen input bytes; it does not extract a new Vulkan allocation address.

The source modules do contain twelve SSBO store sites in **unreachable write helpers**. They must not be mistaken for reachable writes or silently described as absent from the whole module. The reachable call graph uses only the two read helpers. Instrumentation adds 354 reachable SSBO stores, all to the fresh capture descriptor at set 1/binding 0, with no set 0 input stores or origin-control stores. Exact ranges/null states preserve the only relevant descriptor-property observation, ArrayLength. This supports the immutable per-view construction for these entries; it does not prove original internal race freedom or historical memory consistency.

The private audit artifact SHA-256 is `cf2ecf2a89b44773bea6c06a2587c4c8bfbe57bfc804ae51dc2e7ccb3485e99e`. No wrapper, C helper, instrumenter or build artifact changed during this review or the following executions.

## Real logical graph: initial observation

The subsequent CPU-only preparation uses the validated raw dataset `local/runtime-mesh-input-02/capture/mesh-inputs.json` in **frozen-logical-readonly** mode. It retains the captured native grid 705×1×1 and selects origin 128,0,0 with a 2×1×1 window. All eleven slots are preserved: nine complete data views and two real null descriptors. The owned native input bundle is 172,953,972 bytes; UBO/SSBO ranges and null states are not truncated or substituted.

The prepared directory is `local/native-mesh-replay/d8-frozen-origin128-review-01/`; its plan SHA-256 is `4d59d9be8b59316a58eb57335f6b984f8f7257b43682413730e42318fadfd02a`, and native input bundle SHA-256 is `f21b2363d9bfe0764c6822dde501960ca57ebac6b3185e20e59e5e373978dc5a`. Wrapper, C source and helper hashes remain the reviewed values above. Both trusted source modules were instrumented/validated privately, and CPU native framing validation passed.

The raw producer explicitly leaves CPU-writer quiescence, imported alias provenance and live draw-equivalent inputs unproved. Those limitations remain unknown in the prepared plan, which makes no historical-draw reproduction claim.

After the CPU pointer/readonly audit and explicit authorization, original32 ran twice and conditional8/16 once each. Actual executable widths were 32/32/8/16. All four full 89,984-byte buffers matched SHA-256 `e0a4f3356246714cd1a4bad3a0f4a5ecdc005dc6ea4e2f9f020ab1acb71b5bbf`, with valid guards, writtenness, padding, counts and unchanged input/control bytes. Groups 128 and 129 each emitted 128 vertices/32 primitives, wrote all 5120 vertex and 96 triangle components, and contained 3552 nonzero vertex words. This was a nonempty, nonconstant logical-output comparison.

The private initial execution report SHA-256 is `d9dfc760be5ef6f758fc337d9ac849b02c6cf7d1e0e7e4fc482e5353235127e8`. The GPU lease was released immediately after those four cases.

## Broader authorized windows

Five separately reviewed 32-group windows then ran the same fixed four-case matrix. Every case matched its repeated original32 reference over the entire 1,437,824-byte capture buffer. Actual widths were confirmed by executable properties, all input/control bytes stayed unchanged, and every guard/count/writtenness/padding check passed.

| Effective group X range | Groups | Logical vertices | Primitives | Primitive-count range per group |
|---|---:|---:|---:|---|
| 0–31 | 32 | 4,096 | 1,031 | 32–36 |
| 128–159 | 32 | 4,096 | 1,025 | 32–33 |
| 256–287 | 32 | 4,096 | 1,028 | 32–34 |
| 512–543 | 32 | 4,096 | 1,025 | 32–33 |
| 673–704 | 32 | 4,096 | 2,002 | 18–64 |

These windows cover 160 distinct groups of the 705-group chosen grid; the initial two groups overlap the second window. The unique coverage contains 20,480 logical vertices, 6,111 primitives and 591,945 nonzero vertex words. The final window exercises 31 groups with 64 primitives and the partial tail with 18. No referenced vertex had a nonfinite Position component or zero Position W in these recorded outputs. That is additional data coverage, not console-reference or final-image validation.

There were 24 real mesh draw cases across the initial and broader windows, plus the earlier four independent fixture cases. Every draw was individually bounded and authorized; no other window was executed. Full output files and per-case metadata remain private. The [sanitized observation](../research/native-mesh-replay-observation.json) records exact prepared-plan/report/output hashes, count histograms, actual widths, unchanged source/helper identities, driver and compiler policy. The exclusive GPU lease was released after the twentieth broader case.

The result supports this conditional lowering for the tested finite logical inputs and windows on this driver. It does not establish every native group, another input capture, the larger task/mesh shader family, historical draw reproduction, original console correctness, rasterized/fragment output, gameplay coverage or a performance improvement. Instrumentation can change scheduling, and the existing conditional transform's general verification flags remain unchanged.
