# Deterministic finite-half matrix batch, revision 04

This is a new independently authored profile for numerical coverage of the [single-warp packed bridge](matrix-warp-bridge.md). It preserves that profile and the earlier matrix03 source, helper, preparation and observations. It does not modify renderer policy or native-network selection.

Review of batch revision 03 found that diagnostic payloads could remain at their initial sentinel values and be accepted as numerical differences. Revision 03 performed properties and compilation only; it was never dispatched and has no arithmetic acceptance. Its exact source/dependency snapshot, module, helper, fixture, plan and reports remain preserved. The [historical preparation receipt](../research/matrix-warp-batch-preparation.json) records that predecessor. Revision 04 adds mandatory per-word completion evidence in a separate writable buffer.

The batch contains 64 independent workgroups. Each has local size `[32,1,1]`, requires actual compute subgroup size 32, and contains one logical original warp. Shared A/B/C reconstruction, subgroup barriers, two M8 row tiles, two padded K8 chunks and explicit half checkpoints reuse the hash-pinned single-warp source. The additional paths are group-relative indexing and completion stores after payload stores. A new module identity is required; added stores/barriers can change code generation. This does not introduce a multi-warp workgroup or establish original-game control-flow uniformity.

## Corpus and arithmetic reference

[The generator](../tools/matrix_warp_batch.py) checks the frozen bridge source hash before import; that source checks the frozen integer oracle hash. The oracle performs ordered FP32 fused operations and half ties-to-even checkpoints after K8/K16. No native reduction order is assumed.

| Groups | Domain | Comparison role |
| --- | --- | --- |
| 0–15 | Identity, nontrivial layout, positive zero and 13 positive small-integer matrices | Exact safe references |
| 16–18 | Existing checkpoint midpoint, zero-padding signed-zero and cancellation-order counterexamples | Diagnostics |
| 19–23 | Half subnormals, underflow ties, normal/subnormal boundary, overflow midpoint and even/odd midpoint ties | Diagnostics |
| 24–31 | Complementary exponent pairs, signs and mantissas | Diagnostics |
| 32–39 | Finite exponent/mantissa patterns | Diagnostics |
| 40–47 | Balanced wide-exponent products with finite, moderate-sized sums | Diagnostics |
| 48–55 | Finite half-bit samples across the full exponent range | Diagnostics |
| 56–63 | Finite low-exponent bit samples | Diagnostics |

Sampling uses SHA-256 of a fixed little-endian seed/group/matrix/element tuple, with explicit finite-exponent remapping. Every A/B/C half input is finite. Output classes are computed from the oracle; finite inputs are not labeled as producing NaNs by assumption. This fixed corpus produces the following 8,192 reference half elements:

| Reference class | Count |
| --- | ---: |
| Positive zero | 432 |
| Negative zero | 128 |
| Subnormal | 252 |
| Normal | 5,391 |
| Infinity | 1,989 |
| NaN | 0 |

These are classifications of this corpus, not a proof for every finite input. Overflow cases and cases designed to keep products/sums finite are separate. Underflow diagnostics concern half checkpoints. The existing three counterexamples retain their independently checked outcomes.

All safe inputs are nonnegative exact integers, and every complete and partial sum stays at or below 2048. The Python oracle and a separate native integer matrix calculation validate their packed references. Diagnostics use the pinned software oracle and retain full bit differences, half classes and zero-sign differences. They carry no production tolerance, NaN-payload/signaling claim, general original-console equivalence or driver-bug assertion.

## ABI, isolation and guards

The new profile is `open-ocarina.matrix-warp32-finite-batch/v2`, with fixture magic `OOMATB04`. A 48-byte header declares version, physical width, group count, three strides and counts. A group table records monotonically numbered IDs and exact-safe/diagnostic flags. The remaining input, output-reference and expected-marker slots follow those fixed strides. The complete generated fixture is 123,440 bytes.

| Storage | Per-workgroup slot | 64-workgroup extent |
| --- | --- | ---: |
| Readonly input | 16 guard words, 256 packed A/B/C words, 16 guard words | 73,728 bytes |
| Output/reference | 16 guard words, 64 packed D words, 16 guard words | 24,576 bytes |
| Writable completion markers | 16 guard words, 64 owner tokens, 16 guard words | 24,576 bytes |

Each inner guard is 64 bytes; all three separately owned Vulkan buffers additionally have 256-byte prefix/suffix guards outside descriptor ranges. Output payloads begin as sentinels; marker payloads begin as zero. Guards are initialized independently. Expected output values and marker tokens are never copied into the initial GPU payloads.

Input indices are `group*288 + 16 + lane*8 + word`; output indices are `group*96 + 16 + lane*2 + word`. CPU enumeration verifies complete, disjoint footprints of 16,384 input words and 4,096 output words, skipping all inner guards. Workgroup-shared storage remains 2,304 declared bytes per workgroup. The shader does not share workgroup arrays across groups.

The writable marker SSBO is set 0, binding 2, with its own guarded allocation. Every payload store is followed by a subgroup-scoped buffer memory barrier, then its marker store at the corresponding slot index. Token `0x57420000 | (group*64 + lane*2 + word + 1)` uniquely identifies each of the 4,096 output words. A missing, partial or wrong-owner token fails validation. The subgroup buffer barrier appears between payload and marker stores in the validated SPIR-V.

After the fence, native validation first checks all outer guards and unchanged input bytes. It then checks every output/marker inner guard and every completion token across all groups. Only after all pass does it count safe or diagnostic numerical differences. Independent Python validation repeats every marker/guard check before calling any numerical classifier. Exact safe outputs cannot compensate for missing diagnostic markers.

The helper captures all three complete guarded buffer frames, including their outer guards: 74,240 bytes for input and 25,088 bytes each for output and markers. Python checks those actual readbacks' outer guards and compares every readonly input byte before checking inner guards, tokens and numerical values. It does not rely on a native status Boolean as proof of the captured guards. Each frame spans the created buffer's 256-byte prefix, descriptor range and 256-byte suffix.

## Native CPU validator and execution boundary

[The new native helper](../src/probes/matrix_warp_batch.c) binds every fixture byte to a SHA-256 digest compiled into a separate generated header. The header, fixture, source, binary, module and seven source dependencies are all frozen in the plan. The helper checks SHA-256, ABI extents, finite input encodings, group records and inner guards before `vkCreateInstance`. It additionally decodes each safe group's original packed layout and independently verifies all 128 half results through positive integer matrix multiplication. Diagnostic arithmetic is not independently recomputed in C; its complete bytes are bound to the pinned Python oracle's frozen bundle.

SHA-256 tests compare the C implementation against Python `hashlib` at padding boundaries and over the full bundle length. Linker traps replace `vkCreateInstance` in tests. Valid CPU paths return without reaching the trap; corrupted bundle bytes fail before it. Rebinding a deliberately wrong safe reference still fails the independent integer verifier, rebinding a narrowed width still fails the structural gate, and rebinding an invalid expected marker still fails the independently calculated owner-token check.

The native `check` command runs the same complete-output acceptance function before instance creation. Mutation tests retain all 16 exact safe payloads while leaving 48 diagnostic payloads at sentinels with missing markers; native and Python both reject them before numerical classification. They also reject a missing final marker, a wrong-owner token, a marker-guard change and a truncated marker buffer. Python spies confirm that no numerical classifier runs on these failures.

The native `frames` command additionally runs the runtime's complete guard/input/output acceptance function before instance creation. Its tests reject outer-guard corruption in any of the three buffers and readonly input changes. Independent Python tests inject the same corruptions and verify that numerical classification never starts.

CPU-only preparation:

```sh
python3 tools/matrix_warp_batch.py prepare --out-dir local/matrix-warp32-batch-review
python3 -m unittest tests.test_matrix_warp_batch -v
```

Preparation compiles/validates SPIR-V with `-V --target-env vulkan1.3 -Os`, builds C11 with `-O2 -DNDEBUG -Wall -Wextra -Werror`, runs SHA self-tests, native CPU validation and native/Python completion checks, then freezes the artifacts. See the [revision 04 preparation receipt](../research/matrix-warp-batch04-preparation.json). It creates no Vulkan instance, physical-device query, device, pipeline or dispatch.

After a separate review and GPU grant, explicit `properties`, `compile` and `dispatch` phases are available. Admission requires exact E5 ICD/library hashes, the three-option `native-safe` policy with MAX absent, required compute width 32, KHR subgroup M8 N8 K16 FP16 A/B and FP32 C/result, and required features. The helper checks at least 2,304 shared bytes, 64 X workgroups, 32 local invocations, a 73,728-byte storage range and three storage descriptors per stage/set. A compile verifies actual compute width through pipeline-executable properties before any dispatch buffers or commands exist. Properties/compile counts are unexecuted placeholders and supply no arithmetic or writtenness pass.

A dispatch phase submits exactly one `[64,1,1]` dispatch, bounded by a two-second fence wait and 30-second process timeout. It checks every input byte, all three allocations' outer guards, per-group output/marker guards and all 4,096 markers before comparing every packed D word. Python independently checks writtenness before every group's full output and all 8,192 half classifications. Safe mismatches fail; completed diagnostic differences are retained without declaring arithmetic acceptance. Disk shader caching is disabled, and the wrapper checks for competing games. No runtime phase has executed for revision 04. No native network, scene-quality improvement or FPS gain is established.
