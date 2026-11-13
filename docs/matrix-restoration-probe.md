# Matrix restoration: CPU oracle and bounded native fixtures

This preparation advances the native-network restoration investigation without changing the renderer, driver, skipped-program policy or game inputs. The [inventory](../research/native-network-restoration.md) identifies the separate movie-network omissions and gameplay replacement. These fixtures test arithmetic and layout contracts needed by a possible restoration; they do not restore those workloads.

## Arithmetic contract reconstructed from source

The inspected software `DefineSoftwareMatrixFunction` in `emit_spirv_warp.cpp` unpacks half inputs, performs two ordered fused FP32 operations for each K pair, and explicitly rounds each accumulator to half with ties to even after pairs 3 and 7: K8 and K16. The rounded half value is expanded back to FP32 before the next pair. The result is packed into two half-pair words per original logical lane. `NoContraction` decorates the fused operations; the intermediate conversion uses explicit rounding because a driver can remove a pack/unpack round trip.

[The integer oracle](../tools/matrix_restore_probe.py) models each finite fused operation as an exact integer product and sum followed by one FP32 ties-to-even rounding. It then models both half checkpoints. This is a model of the inspected software lowering, not independently certified original-console arithmetic. NaNs are canonicalized for classification; their payload, signaling and vendor-specific behavior are outside this oracle's contract.

For original logical warp lane `l`, define `g=l//4` and `i=l%4`. Each word packs the first half in its low 16 bits:

| Fragment word | Logical matrix elements |
| --- | --- |
| A0 | `(g,2i)`, `(g,2i+1)` |
| A1 | `(g+8,2i)`, `(g+8,2i+1)` |
| A2 | `(g,8+2i)`, `(g,9+2i)` |
| A3 | `(g+8,8+2i)`, `(g+8,9+2i)` |
| B0 | `(2i,g)`, `(2i+1,g)` |
| B1 | `(8+2i,g)`, `(9+2i,g)` |
| C/D0 | `(g,2i)`, `(g,2i+1)` |
| C/D1 | `(g+8,2i)`, `(g+8,2i+1)` |

Those are original logical fragments. KHR cooperative-matrix components are opaque, with a different distribution that must be measured or bridged through a proven memory layout. Subgroup narrowing cannot simply retain the original shuffle indices or assume two physical SG16 groups implement one original warp32.

Three synthetic counterexamples prevent premature equivalence claims:

| Change | Software-checkpoint reference | Changed computation |
| --- | --- | --- |
| Half conversion only after K16 | `+0` | `2^-11` |
| Pad a K8 dot with eight positive-zero products | `-0` | `+0` under the illustrated sequential order |
| Reorder FP32 cancellation | `+0` | `1` |

The first uses `1 + 2^-11` before K8, then subtracts 1. The third uses products `65504*65504`, `1*1`, and `-65504*65504`. A native matrix's internal reduction order is not inferred from a shape advertisement.

## Prepared synthetic modules

The first module uses advertised KHR M8 N8 K16, FP16 A/B and FP32 C/result. The second covers logical M16 N8 with two M8 row tiles and two K8 chunks each padded to K16; it applies an explicit integer half-rounding checkpoint to every accumulator component after each chunk. This generates four native matrix calls per logical M16 product, compared with two M8 full-K calls if intermediate checkpoints were omitted. It does not establish matching operation order or signed-zero behavior. All generated modules and private logs stay outside the public tree.

Single-term padding is another diagnostic candidate: 16 K1 steps padded to K16, with FP32 accumulation between steps and half checkpoints only after K8/K16. It requires 32 M8 calls per logical M16 product, sixteen times the two-call full-K geometry, and performs many artificial zero products. Padding can still affect zero signs and exceptional values; implementation-dependent precision and addition order remain. Neither padded approach is currently an exact replacement. A finite-domain fast path with explicit fallback would need a justified input domain, complete replay comparisons and downstream rendering checks before use.

The modules were compiled by glslang 16.2.0 with `-V --target-env vulkan1.3 -Os` and validated by SPIRV-Tools 2026.1. SPIR-V validity alone does not establish native pipeline support. The separately authorized observation below confirms both synthetic pipelines at actual subgroup width 32; widths 8 and 16 remain untested.

## Native helper and review boundary

[Preparation wrapper](../tools/matrix_restore_native.py) and [C helper](../src/probes/matrix_restore_native.c) are separate from the frozen oracle. Preparation builds C11 with `-O2 -DNDEBUG -Wall -Wextra -Werror`, prepares the two modules, creates 18 fixtures, and executes only the helper's CPU-validation branch. A linker-trap regression test confirms that branch never reaches `vkCreateInstance`. Preparation creates no Vulkan instance, logical device, queue, pipeline or dispatch.

Each shape has identity, nontrivial layout and adversarial-rounding fixtures at requested widths 8, 16 and 32. Identity and layout inputs have integer products and partial sums at most 2048, making their expected results exact at both half checkpoints and independent of summation order. Groups and M8 row tiles use distinguishable data. The rounding fixture includes midpoint loss, negative zero, finite cancellation and minimum-half subnormal values; its differences from the software reference are diagnostic observations, not automatic evidence of a driver bug or acceptable original-game error.

An explicit, hash-reviewed execution selects exactly one frozen case and one phase:

1. `properties` queries the exact E5 device's features, advertised shape, subgroup bounds and required-size stage support. It creates no logical device or pipeline.
2. `compile` enables only required features, creates one synthetic pipeline and verifies its actual compute subgroup width through pipeline-executable properties. It submits no commands.
3. `dispatch` creates four small coherent host-visible buffers, each with 256-byte prefix/suffix guards; descriptors cover only payloads. It submits one `[1,1,1]` dispatch for one `[32,1,1]` workgroup. It waits at most two seconds on the fence, with a separate 30-second process timeout. Timeout termination targets only the helper's process group; a fence timeout exits without an idle wait.

The helper checks every input byte, every guard byte and every output word. The wrapper independently checks output extent and full-buffer comparison. Exact finite identity/layout mismatches fail; rounding mismatches are counted and retained. The requested subgroup width must equal the compiled width before dispatch. Successful synthetic results would establish only those isolated contracts, without proving an original fragment bridge, native-network equivalence, a rendering improvement or FPS gain.

The wrapper admits only the exact cache-safe E5 library and ICD hashes from the [existing driver profile](mesh-replay-driver-profiles.md). It clears ambient driver controls, applies only the three-option `native-safe` policy, disables disk shader caching and checks for competing game processes. `HS_CS32_MAX_INSTS` must remain absent. The earlier physical-device shape observation used the legacy audited driver; this preparation does not assert that E5's current query or pipeline result is identical.

CPU-only preparation command:

```sh
python3 tools/matrix_restore_native.py prepare --out-dir local/matrix-native-review
python3 -m unittest tests.test_matrix_restore_probe tests.test_matrix_restore_native -v
```

Runtime requires the parent agent's separate review and GPU grant. The [sanitized receipt](../research/matrix-restoration-preparation.json) pins sources, helper, modules and the private plan. No runtime phase was executed for that receipt.

## Authorized subgroup-32 observation

The separate [runtime observation receipt](../research/matrix-restoration-observation.json) records one E5 physical-device query, two compile-only processes and four synthetic dispatch processes under the unchanged frozen plan and helper. The query confirmed the nonsaturating subgroup M8 N8 K16 FP16-input/FP32-accumulator shape and all required feature gates, with subgroup default 32 and advertised range 8–32. Pipeline-executable properties confirmed actual compute width 32 for both modules before dispatch.

| Safe synthetic case | Complete output words | Exact CPU-reference match |
| --- | ---: | --- |
| Base identity | 64 | Yes |
| Base layout | 64 | Yes |
| Padded-checkpoint identity | 128 | Yes |
| Padded-checkpoint layout | 128 | Yes |

All 384 words, totaling 1,536 bytes, matched their CPU reference buffers and SHA-256 values. Each process submitted one `[1,1,1]` dispatch with local size `[32,1,1]`; all input payload bytes and all four buffers' 256-byte prefix/suffix guards remained unchanged. Every owned process reached a terminal success and each exclusive lease was released. The receipt pins all seven structured reports, the two modules, four input bundles, six source dependencies, helper source/binary and exact E5/native-safe policy. No frozen source or preparation metadata changed for this observation.

The inspected Mesa `brw_nir_lower_cooperative_matrix.c` lowers `cmat_muladd` through `nir_dpas_intel`. This supports an inference that Intel's native matrix route is available; no machine IR or disassembly was captured for these pipelines, so actual DPAS instructions are not established by this observation. Process completion times are not shader GPU timings and are excluded from performance evidence.

These safe integer identity/layout fixtures establish a narrow execution and memory-layout result. They do not establish adversarial rounding, nonfinite behavior, original logical warp32 integration, native-network restoration, native quality, hair antialiasing or FPS improvement. No narrower subgroup, adversarial input or original-game module ran in these grants. The existing midpoint, signed-zero and operation-order counterexamples still apply.
