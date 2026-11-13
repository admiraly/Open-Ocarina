# Single-warp32 matrix fragment bridge

This is a new, independently authored fixture for the next native-network restoration boundary. It accepts original-layout matrix fragments and returns packed D fragments for one logical warp32. The [earlier matrix observation](../research/matrix-restoration-observation.json) covered row-major host buffers; it did not test this fragment bridge. Its source, helper, preparation and observations remain unchanged.

The new profile is `open-ocarina.matrix-original-warp32/v1`, with binary fixture magic `OOMATW32`. It requires exactly one `[32,1,1]` workgroup and actual physical subgroup size 32. It does not support narrower subgroups or additional logical warps.

## Layout and collective path

Each of the 32 input lanes supplies eight uint32 words, in order A0–A3, B0–B1 and C0–C1. Each word packs two half encodings, low half first. The original layout is documented in [the checkpoint investigation](matrix-restoration-probe.md). Output is exactly D0–D1 per lane, 64 uint32 words in total.

The [shader generator](../tools/matrix_warp_bridge.py) reconstructs shared A16×16, B16×8 and C16×8 using disjoint lane writes. Every A element has one writer, as do every B and C element: 256, 128 and 128 half values respectively. C values are stored as exact FP32 expansions. A subgroup shared-memory barrier and subgroup control barrier complete reconstruction before collective loads.

Two constant-bounded loops select M8 row tile 0/1 and K8 chunk 0/1. In each phase, lane `l` writes padded-tile indices `l`, `l+32`, `l+64` and `l+96`; all 128 indices have exactly one writer. Unused K entries are positive zero. Both selected and unselected address expressions remain in bounds. A uniform subgroup barrier precedes collective matrix loads and follows the matrix/checkpoint step before shared tile reuse. Four M8 N8 K16 FP16-input/FP32-accumulator matrix calls cover the two M8 rows and two K8 chunks. Every accumulator component goes through the existing explicit half-rounding checkpoint after each K8 chunk.

The collective stores write disjoint M8 regions of shared D16×8. A final subgroup shared/control barrier completes those writes before each lane reads its original-layout row/column pair and packs D0/D1. Shared arrays occupy 2,304 declared bytes. The native helper checks shared-memory, workgroup, descriptor-range and descriptor-count limits as well as the exact KHR shape and required features.

These bounds make the fixture's collective path uniform. They do not establish uniformity in an original game shader, support for 64/128-invocation workgroups, a general multi-warp mapping or a production lowering rule.

## CPU reference and diagnostics

The generator checks the frozen integer oracle's SHA-256 before importing it. It uses that oracle's original packing, FP32 fused-operation model and half checkpoints after K8/K16. It does not replace the arithmetic reference with an assumed native matrix reduction order. An independent test simulates the inspected public software shuffle addresses and compares every packed output word with the logical matrix reference.

| Fixture | Reference purpose |
| --- | --- |
| Identity | Distinguish M8 row tiles and K8 chunks; verify packed corner words against independent values |
| Layout | Nontrivial exact integer sums, bounded below 2048; full packed-word oracle |
| Rounding | Midpoint loss, negative zero, finite cancellation and minimum-half-subnormal diagnostics |

Identity and layout expected results are exact at the modeled half checkpoints. The rounding fixture reference contains 32 positive zeros, 32 negative zeros, 32 normal values and 32 half subnormals. Classification counts packed-word and half-element differences separately, including differences that change only a zero sign. NaN/infinity classification is tested synthetically; NaN payload/signaling equivalence is outside the oracle contract.

Padding native matrix calls is not proven equivalent to the original software schedule or console arithmetic. Signed-zero changes, internal native precision and reduction order remain open. A successful finite bridge fixture would establish only its tested packed layout and arithmetic domain. It would not establish full native-network restoration, hair antialiasing, native quality or FPS improvement.

## Review and runtime boundary

The new [C helper](../src/probes/matrix_warp_bridge.c) refuses the old `OOMAT001` profile, any width other than 32, malformed extents and every unexpected packed input/reference word. Its CPU-validation branch returns before `vkCreateInstance`; tests link a trap in place of that entry point to verify this boundary without calling the Vulkan API.

CPU-only preparation:

```sh
python3 tools/matrix_warp_bridge.py prepare --out-dir local/matrix-warp32-bridge-review
python3 -m unittest tests.test_matrix_warp_bridge -v
```

Preparation compiles GLSL with `-V --target-env vulkan1.3 -Os`, validates SPIR-V, builds C11 with `-O2 -DNDEBUG -Wall -Wextra -Werror`, and validates all three packed fixtures on the CPU. The [preparation receipt](../research/matrix-warp-bridge-preparation.json) freezes source, helper, module, fixtures and plan hashes. Generated modules, fixture buffers and logs remain private.

After a separate parent review and GPU grant, an explicit invocation can select one frozen case and one phase. `properties` returns before logical-device creation; `compile` creates one pipeline and verifies actual compute width 32 without buffers or commands; `dispatch` submits exactly one `[1,1,1]` dispatch. Dispatch uses one 1,024-byte readonly input payload and one 256-byte output payload, each with 256-byte guards at both ends. All input/guard bytes and all 64 packed output words are checked, with a second full-output comparison in Python. Safe exact mismatches fail; rounding differences are retained as diagnostic classifications.

Runtime admits only the exact E5 ICD/library and three-option `native-safe` policy; MAX remains absent and disk shader caching is disabled. The fence bound is two seconds and the process timeout is 30 seconds. The wrapper checks for competing game processes. Preparation itself executes no runtime phase. The separately authorized observation below changed no renderer policy, game input, network weight or installed driver.

## Authorized single-warp observation

The [runtime observation receipt](../research/matrix-warp-bridge-observation.json) pins the unchanged plan, helper, shader, six source dependencies, exact E5/native-safe policy and five structured reports. Physical-property gates passed, then the optimized bridge pipeline compiled at actual compute subgroup size 32. Identity and layout dispatches each matched every one of their 64 packed uint32 output words, representing 128 half elements. All inputs and all guards remained unchanged.

Only after both safe cases passed, the separately admitted rounding diagnostic executed one dispatch. It differed in 16 of 64 packed words, covering 32 of 128 half elements. Every difference was reference negative zero `0x8000` becoming positive zero `0x0000`; mismatched packed words were `0x80008000` versus `0x00000000`. These are all columns of logical rows 1, 5, 9 and 13, corresponding to both D words of lanes 4–7 and 20–23.

| Rounding output class | Software reference | Native bridge |
| --- | ---: | ---: |
| Positive zero | 32 | 64 |
| Negative zero | 32 | 0 |
| Half subnormal | 32 | 32 |
| Normal | 32 | 32 |
| Infinity / NaN | 0 / 0 | 0 / 0 |

Every minimum-half-subnormal value remained exactly `0x0001`; all normal values and original positive zeros also matched. All three processes verified every output word, unchanged input bytes and both buffers' guards, with actual subgroup size 32 and one `[1,1,1]` dispatch each. All five owned runtime processes reached terminal success, and the exclusive leases were released. The diagnostic process returned successfully because diagnostic differences are counted and retained; its arithmetic comparison did not pass bit-exact equivalence.

The zero-sign difference is an observed result of the entire padded bridge. Its cause is not isolated by these outputs, and it establishes neither a driver bug nor an acceptable production error. No machine IR was captured. The earlier arithmetic counterexamples, general warp/workgroup limitations and absence of native-network, scene-quality or FPS proof remain in force. Frozen matrix03 sources, preparation, observations and policies remain unchanged.
