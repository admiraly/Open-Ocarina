# Explicit 6864 logical mesh output profile

`tools/mesh_output_profile96.py` prepares private stage-preserving instrumentation for one newly reviewed original mesh digest and its CPU-generated conditional derivative. It makes no device calls and changes no production admission, vendor source, frozen d8 generator or executor. The selected family and timing limitations are documented in [the inventory](../research/mesh-family-audit.md).

## Sources and reuse

The source original is `999c8511db93d486543421dde7350e3d0034faa834ef3edaf3553dcdc9b55d93`, program `68642bb810ee3e7c`, variant `3f3f5b29054673da`. The conditional source is `a41c6df753f30f8c0646ffa45227304e994b3fbaccdcad64927cd57f5e2327e7`, obtained with the existing pure `mesh_warp32_transform.transform_assembly` and standard SPIR-V assembly. Its digest is a new explicit CPU preparation input; the frozen transform CLI and runtime admission remain d8-specific.

To reuse existing checks without modifying the frozen handoff, this adapter reads independently authored public Python function source using `inspect.getsource`, checks the entire frozen generator SHA, applies a small counted specialization table, and compiles the functions in an isolated namespace. It specializes the audit, shadow-store generator and authored fixture generator. Input game assembly is never Python source, and no original module globals are mutated. Each expected source fragment must appear exactly the stated number of times; changing the dependency hash or a specialization context fails. The resource auditor is separately pinned. These dependencies are part of the report, alongside the new adapter SHA. This deliberately narrow mechanism needs review if the frozen infrastructure changes; it is not a general shader translation framework.

The resulting audit preserves the original control-flow, alias/pointer, readonly input, count dominance, single-owner component coverage, primitive guard and exact dead-payload checks. Differences are explicit: maxima96, three lane-plus-block indices0/32/64, ten generic attributes, UBO bindings0–3 and SSBO bindings4–8. Binding8 has three descriptors. The expected 138 original output store sites comprise Position15, generic120, and triangle3; Position.z has an intentional second store per block. The MeshEXT stage, original output instructions, SetMeshOutputsEXT, original barriers and Invariant decorations remain. The only removed original chain is the exactly proved unused task payload; original WorkgroupId loads become raw WorkgroupId plus origin.

This uses the same read-only logical input and group-origin proof obligations as d8. It does not waive input alias checks, descriptor lengths, null handling, capture temporal provenance, subgroup mapping or device limits. In particular, eleven vec4 vertex fields require checking the actual device output component limits.

## Capture interface

Capture SSBO remains set1/binding0; read-only control SSBO set1/binding1 contains two uvec4 values, originXYZ/reserved0 at byte0 and windowXYZ/reserved0 at byte16. The host must bound the window to the actual captured native XYZ launch, at most32 groups, and prevent capture/control overlap with any input or each other. Full descriptor range matters because reachable OpArrayLength queries remain present. Native group counts must come from the twelve GPU bytes of the observed converted indirect draw, not a CPU reconstruction.

The new profile name is `6864-position10-96/v1`. Each workgroup has a 36864-byte record, indexed with raw relative WorkgroupId in X-fastest order:

| Field | Byte offset | Shape |
|---|---:|---|
| Prefix guard | 0 | 16 u32 |
| Per-lane vertex/primitive counts | 64 | 32 × 2 u32 |
| Position | 320 | 96 vec4 words |
| Generic attributes | 1856 | 10 × 96 vec4 words; array stride1536 |
| Triangle indices | 17216 | 96 uvec3; element stride16 |
| Component writtenness | 18752 | 4512 u32 |
| Suffix guard | 36800 | 16 u32 |

Generic order is locations `[0,1,2,4,5,6,7,8,11,12]`. Writtenness has 4224 vertex component words (Position then attributes), followed by288 primitive component words. Initial counts/values/padding are `0xcdcdcdcd`; writtenness starts0 and stores1; inner guards are `0xa11ce55a`. The API returns record-body bytes only. An executor must add and check its own outer guards and reset the entire allocation before every run. Existing d8 fixed-size executor checks cannot consume this profile until explicitly extended and reviewed.

`prepare(source, output)` checks exact digest, assembles and validates into a new ignored private directory and writes report schema `open-ocarina.mesh-output-profile96/v1`. The report records source/instrumented/tool/dependency hashes, reflection, profile, layout, payload proof and `runtime_execution_authorized=false`, `equivalence_proven=false`. `prepare_fixture(output)` generates the independently authored fixture. CPU tools are `spirv-as`, `spirv-dis`, and `spirv-val`, with SPIR-V1.6 assembly and Vulkan1.3/uniform-buffer-standard-layout validation.

## Authored fixture and validation

The 96-vertex fixture reuses the independent synthetic generator with explicit profile changes. Its outputs depend on all three origin-adjusted WorkgroupId coordinates. Let `tag = (gx + 257*gy + 65537*gz) mod 2^32`. All32 lanes report96 vertices and `tag mod97` primitives. Vertex component values convert unsigned `(tag + vertex*8 + field*4 + component) mod2^32` to float32, with Position.z overwritten after adding1000. Emitted triangle components are `(primitive*3 + component) mod96`. The fixture exercises the 96 boundary rather than a power-of-two mask.

`initial_capture(N)` and `fixture_expected_capture(origin, groups)` expose full record images. An independent test interpreter executes the generated synthetic SPIR-V scalar operations, branches, pointer addressing and capture stores for every lane, then compares every byte against the separately implemented oracle. Cases cover zero/max/partial primitive emission, nonzero XYZ origins/windows and uint32 wrapping. Separate assertions cover every guard, padding word, count row and marker. Negative tests reject changed maxima/locations/component coverage, output loads and pointer escapes, external buffer writes, live task payload, bad digest/dependency/context and invalid windows. This tests the authored fixture/capture wiring; it does not interpret proprietary algorithms or prove real-module numerical equivalence.

Validation environment: Python3.14.8 and SPIRV-Tools v2026.1 (2026-02-16 build, reported source hash unknown). No third-party Python dependencies are needed.

CPU validation completed: 45 targeted tests passed, including11 new profile tests and the unchanged d8/core/resource suites. Exact original and conditional derivatives each passed SPIR-V validation and the same 138-store coverage/dead-payload/readonly audits. Private final reports are under ignored `local/mesh-profile96/{original-final,conditional-final,fixture-final}/report.json`.

| Artifact | SHA-256 |
|---|---|
| Profile adapter | `4619e081b652b9ed1b72820a22d1948907196b08de05a10cefebd15703f42f09` |
| Frozen d8 infrastructure | `69e97d192ca4607a2c7f50c4d92460c0467b5f0f076b9e3775dd04ac4fe960e7` |
| Resource auditor | `91bb902ee11d37ed24cda2c74a086e6c53fb15405311adc1fdd6a38f905048f7` |
| Original instrumented | `2d0bd1c6dfb72398a30c6ad4539be01dae27fbc1c92e963de2b2e3db9c381682` |
| Conditional instrumented | `8e10dad18d4b674df682ee03c00b507457de8c691d4e72f85919ce056128e573` |
| Authored fixture source | `1944ea426326138609f4fbcbdace60ad34cd4c5d8091235c8469036d44c6b162` |
| Authored fixture instrumented | `9453c1a003e070a69e2dd7462aa6ef0de5c433a91e0976fffa45da9bb70cf3a7` |

No GPU execution, native rendering result, measured improvement or frame-rate claim follows from this CPU preparation. The next phase needs a reviewed executor/profile extension and exact runtime input capture before original32/conditional8/16 comparisons; full scene/native-quality tests remain necessary afterward.
