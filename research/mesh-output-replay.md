# Bounded logical mesh-output replay for d8

Assessment date: 2026-11-13. **Feasible as a bounded differential test, with explicit stage-normalization limits.** It can validate the logical vertex/primitive results of the conditional warp32 transformation before a full graphics replay is available. It cannot establish mesh-stage performance, rasterization equivalence, fragment behavior, native quality or a gameplay FPS gain.

Subsequent implementation now uses the stronger **stage-preserving** alternative described below: [mesh-output-instrument.md](../docs/mesh-output-instrument.md). Its original, conditional and authored-fixture preparations passed CPU-side SPIR-V validation. The compute-normalization plan in this assessment has not been implemented or executed.

This assessment is CPU-only. No Vulkan/device call, shader execution, renderer modification or transformed game output publication was performed for it.

## Exact scope

- Program `d8e59341cf4f629c`, observed variant `0c8f08626a9d98a4`.
- Original mesh SHA-256: `835aa59799c6ecb621034d946e2fa4eadb1aa2407d293321ae3e3052882bc9b3`.
- Conditional mesh candidate SHA-256: `9d23b7ebf3677f53fe6901c9b94f2c4927f1f0c47ed207c04c53df9b59ae9638`.
- Original entry is MeshEXT, local size 32×1×1, maxima 128 vertices and 128 triangle primitives. The original module has three reachable functions, two barriers and two subgroup ballots. The candidate preserves the body and adds the independently authored Workgroup ballot reconstruction and its two barriers.
- Reachable input resources are logical descriptors only: set 0 bindings 0–4 are uniform buffers; bindings 5–7 are individual storage buffers; binding 8 is an array of three storage buffers. Typed aliases at a binding share one descriptor identity. Reachable code reads these resources and queries storage-buffer lengths; it does not write storage inputs, use physical-storage pointers or sample images.

The last point is particularly useful: this target does not inherit the complete physical-page closure and sampled-image requirements of the first compute target. It still needs all input descriptor bytes and exact ranges, including every member of the descriptor array. A prefix dump is insufficient, especially because `OpArrayLength` affects address-table lookup bounds.

## Inspected output and payload behavior

The source has eleven Output variables: Position, nine per-vertex generic vec4 arrays at locations 0, 1, 3, 4, 5, 6, 7, 10 and 11, and PrimitiveTriangleIndicesEXT. Each array has 128 elements. No per-primitive generic, cull, layer, viewport, clip-distance or view-index output was found.

There are 168 direct output-store sites: twenty Position scalar stores, sixteen scalar stores for each generic array, and four triangle-index vector stores. Position Z is written again by the depth-conversion epilogue; preserve that order and compare its final value. Output pointers are direct AccessChains consumed by stores in the inspected module, with no output loads, pointer escapes or pointer Phi/select chains. This is an exact-module fact, not a general mesh shader assumption.

One `OpSetMeshOutputsEXT` occurs after the original final Workgroup barrier. Its vertex count is constant 128; its primitive count is the shared count clamped to 128. Every invocation reaches the output epilogue and a single final return. Per-vertex output indices are `LocalInvocationId.x + {0,32,64,96}`. Primitive indices have the same shape but their writes are guarded by the dynamic primitive count. These forms provide a bounded index/unique-writer audit for valid local IDs 0–31.

**Task payload is a real syntactic obstacle, but dead for this exact entry.** The module declares a 32-word TaskPayloadWorkgroupEXT array and loads its first word. That value is used only as the false operand of a select whose condition is equality of constant zero with itself; the selected value is WorkgroupId.x. The normalization must prove this complete use chain, replace that constant-true select with a copy of WorkgroupId.x, then remove the now-dead payload load/access/variable and interface entries. It must reject any changed or additional payload use. Inventing zero payload contents or simply relabeling live task payload as Workgroup memory would be an invalid shortcut.

## Recommended normalization: direct output records

Do **not** first move all mesh outputs into additional Workgroup arrays and copy them later. That would add roughly 22 KB of logical shared output storage to the existing roughly 14 KB, change occupancy and introduce additional shared-memory considerations. Instead, preserve the original Workgroup variables and all original computation, barriers and stores, while redirecting only output pointers into an independent capture SSBO.

The bounded rewrite is:

1. Verify the exact original or conditional module digest, entry, workgroup shape, complete builtin/output inventory, call graph and side-effect inventory. Require the existing ballot/lane-mapping acceptance evidence as a separate input; a normalizer must not silently promote the conditional shader's verification flags.
2. Perform only the proved dead-payload cleanup above. Retain the selected WorkgroupId values and reject live payload, unsupported stage builtins, external writes, atomics, additional mesh instructions or output-pointer escapes.
3. Change MeshEXT main to GLCompute, remove the mesh-output topology/count execution modes and the now-unused mesh extension/capability. Preserve LocalSize, floating-point execution modes, NoContraction decorations, input descriptors and Workgroup storage. Rewrite/remove every stage-only output decoration and pointer type consistently; validate the complete result.
4. Add a fresh capture descriptor at set 1, binding 0. Replace each direct output AccessChain with a pointer into a typed per-workgroup record, preserving its scalar/vector value type and every original store's order. Use explicit std430 member offsets/array strides. Reject any output access shape outside the audited arrays/components.
5. Replace SetMeshOutputs with two stores into a **per-lane** count row. All 32 invocations writing one common SSBO count would create a race. The verifier instead checks that every lane wrote a row and all rows agree on vertex/primitive counts. This inserts no additional branch or barrier into the original body.
6. After each original output store, mark the corresponding component-writtenness word. The target's unique-writer audit is required, including the same invocation's repeated Position Z write. Triangle vector stores mark all three components; padding stays untouched.
7. Keep the original baseline's subgroup operations and require size 32. The conditional candidate uses the already-authored Workgroup reconstruction and requests size 8. Confirm actual compute executable widths and the compute-stage lane mapping separately. Neither the earlier mesh mapping observation nor the nominal API request alone supplies those facts.

Invariant Position is a stage-only output property and cannot simply be attached to a storage-buffer member. Preserve all applicable arithmetic/NoContraction controls, but record removal of the invariant output decoration as a normalization limitation. This and the different stage backend motivate a native-mesh anchor test below.

## Exact proposed record layout

One std430 record can use the following explicit byte offsets. The primitive index array uses a 16-byte vec3 array stride; its fourth words are padding sentinels, not fourth indices.

| Byte offset | Field | Size |
|---|---|---|
| 0 | Prefix guard, sixteen uint32 words | 64 |
| 64 | Thirty-two lane rows of vertex count and primitive count | 256 |
| 320 | Position: 128 vec4 values | 2,048 |
| 2,368 | Nine generic arrays, 128 vec4 each, ascending location order | 18,432 |
| 20,800 | Triangle indices: 128 uvec3, stride 16 | 2,048 |
| 22,848 | Component-writtenness: 5,504 uint32 markers | 22,016 |
| 44,864 | Suffix guard, sixteen uint32 words | 64 |

Record stride is **44,928 bytes**, aligned to 16. Writtenness indexes the 5,120 vertex attribute components followed by 384 triangle-index components; primitive padding is not an attribute. Initialize the entire capture buffer with sentinels and writtenness with zero, preserving independent outer-buffer guards. Guard and padding bytes must remain unchanged.

Start by comparing all defined values bit-for-bit. Require all count rows to be present/equal, vertex count 128, and primitive count in 0–128. Require identical writtenness, all vertex components defined, and exactly the first `primitive_count × 3` index components written. Compare integer/index/count words exactly. Undefined primitive slots beyond the emitted count are not geometry; verify that they remain untouched rather than treating arbitrary data there as output. Do not introduce a float tolerance to make an unexplained mismatch pass.

## Bounded launch and capture schema

Use a distinct schema such as `open-ocarina.mesh-output-capture/v1`, not a compute manifest that mislabels the original NVN mesh stage as compute. It needs:

- Original mesh program/variant/module digest, conditional digest, normalizer source/build digest, derivative compute digests, exact rewrite audit and the external lane/runtime-policy evidence.
- Input allocation identities, complete descriptor ranges/content hashes and alias relationships for the five UBO and six SSBO descriptor elements. Preserve their exact lengths and guest-address table values; these are logical table inputs, not new Vulkan device addresses.
- The original command occurrence and GPU launch snapshot. Both observed target draws use converted `vkCmdDrawMeshTasksIndirectEXT` with one API draw, stride/extent 12, and no count buffer. A synchronized snapshot of those exact twelve GPU-visible bytes supplies X/Y/Z group counts. CPU-side `d.groups` is not a substitute.
- A bounded window of valid original group coordinates. For a small replay, add an immutable origin control buffer and make source WorkgroupId loads observe `compute WorkgroupId + origin`; derive output record indexes from the relative compute ID. Reject an origin/window outside the captured native grid. Do not execute a group if any captured native dimension is zero. The target does not use NumWorkgroups, but that absence must remain audited.
- Maximum replay groups, record count and checked multiplication/byte bounds. A single or small group window avoids allocating the roughly 45 KB record for every group of a large draw. Vary origins across the draw and tail/empty-primitive cases; do not only test group zero.
- Host/device/driver identities, requested and actual widths, subgroup/full-subgroup policy, feature/robustness/float-control settings, stable baseline repetitions, complete output hashes and exact comparison outcomes.

Captures are taken at the selected GPU command position, after prior producers and with the required visibility. Later guest-memory reads are not equivalent snapshots. The normalizer cannot fill in missing input descriptors or infer indirect counts from cache metadata.

## Scope of the resulting evidence and next implementation

First implement a digest-pinned CPU auditor/rewriter with synthetic mesh fixtures. Negative tests should alter the payload condition/use, add an output read or pointer escape, change an output max/index form, add a stage builtin/external write, remove the count epilogue, change a barrier or introduce an early exit. Assemble and validate both private normalized modules before any execution proposal.

Then run original-compute32 versus conditional-compute8 only through a separately reviewed, bounded execution helper against complete captured inputs. Check guards, writtenness, every emitted attribute and all lane count rows over multiple group windows. This can catch transformation errors without fragment shaders or image attachments, but it is not a proof that compute and mesh backends behave identically.

A valuable next anchor is a **stage-preserving mesh output instrumenter** using the same capture record alongside the original Output stores, with rasterizer discard, no fragment shader and no attachments. Compare its original32 logical record with normalized-compute32 for the same inputs, then compare original32 and conditional8 in the actual mesh stage. This validates the normalization and stage/lane assumptions more directly without requiring full raster replay. Instrumentation itself remains diagnostic and may change scheduling.

The final rendering gate still needs the real indirect command, graphics resources/fixed state, fragment behavior and attachments, plus the native-feature/native-quality scene checks. Logical mesh-output equality is a faster supporting gate, not permission to omit those tests or claim gameplay performance from normalized-compute timings.
