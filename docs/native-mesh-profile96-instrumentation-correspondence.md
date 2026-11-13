# Native 6864 instrumentation correspondence

The raw native C++ candidate and Python conditional source produce the exact same instrumented module under the unchanged, hash-pinned 96-vertex instrumenter. This connects the native source's logical-output lineage to the existing finite 67-group replay result. It does not relabel those earlier runs as direct execution of the raw native bytes. [Sanitized correspondence metadata](../research/native-mesh-profile96-instrumentation-correspondence.json) records every source, derivative, report and private assembly hash.

| Artifact | SHA-256 |
|---|---|
| Native raw conditional | `43434b023ff17ee4b92ed8e49f19c937c86f15cf31a7034161dd11878f944099` |
| Python conditional | `a41c6df753f30f8c0646ffa45227304e994b3fbaccdcad64927cd57f5e2327e7` |
| Shared instrumented conditional | `8e10dad18d4b674df682ee03c00b507457de8c691d4e72f85919ce056128e573` |
| Original raw source | `999c8511db93d486543421dde7350e3d0034faa834ef3edaf3553dcdc9b55d93` |
| Unchanged instrumented original | `2d0bd1c6dfb72398a30c6ad4539be01dae27fbc1c92e963de2b2e3db9c381682` |
| Frozen profile96 instrumenter | `4619e081b652b9ed1b72820a22d1948907196b08de05a10cefebd15703f42f09` |

The CPU preparation first checks the exact native raw digest, then calls the frozen profile96 `_prepare_bytes(data, output, 'conditional')` infrastructure without changing its source, globals or reported identity. Fresh Python and original preparations use that same frozen implementation. All sources and derivatives pass SPIR-V validation for Vulkan1.3 with uniform-buffer standard layout. Disassembling native raw434 with raw IDs and reassembling with `spirv-as --target-env spv1.6`, without an optimizer, additionally produces the exact Python a41c source bytes.

The two instrumented conditional binaries match byte for byte. Their assembly text retains different SSA names, and their audit reports differ only in the raw source digest and descriptor variable names; removing those SSA names leaves identical descriptor audits. Every other audit field matches, including 138 output store sites, four conditional barriers, preserved output/count/barrier operations, read-only input descriptors, zero push constants, the 36,864-byte record layout, dead-payload proof and workgroup-origin substitution. Exact canonical module and derivative identity preserves the encoded instruction/math/barrier/layout content; no approximate numeric comparison is involved. The original source and derivative remain unchanged.

The [existing replay record](../research/native-mesh-replay-e5-observation.json) still identifies Python source a41c and its instrumented derivative 8e10. That derivative passed repeated original32 versus conditional8/16 across all 67 groups of one explicitly chosen immutable input graph, with full counts/writtenness/padding/guard/output equality. No new executor profile, native control magic, schema, helper or GPU run was introduced for this correspondence. The brief profile prototype was preserved privately and the public wrapper/C/tests restored to their reviewed versions; all prior plans and observations retain their source identities.

This is source-to-instrumented-output provenance. The paired compilation/GEN comparison of the actual uninstrumented raw modules remains a separate graphics task. It does not establish direct dispatch of raw434 in the earlier replay, general race freedom of overlapping same-value shared writes, historical live-draw reproduction, universal transformation equivalence, final rendering or FPS.
