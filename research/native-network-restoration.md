# Native network omissions and Arc restoration boundaries

Updated 2026-11-13. This is CPU-only source/asset analysis plus a physical-device property query. No logical Vulkan device, queue, pipeline or shader dispatch was created by this investigation. No renderer source, setting, binary or driver was changed. Shader instructions, weights, raw logs and game assets remain private.

The bounded pilot renderer is identified by SHA-256 `29bdc49d929d60464b93d8c257d1363c171129af5db7e6ff5607a45e88a063a2`, frozen receipt `70c9e64942c7f97d6d94e16614cbaa02a9ee71209f8d79fdee0dc5e5bfb3ba94`, and host `4eb32b56a91869b5d7ac1673f0ca1e3c0841e893a8a34f728ec85344a312cb30`. Inspected `vk_shader.cpp` and `vk_draw.cpp` hashes match that receipt. The base is Hookshot `4cf785a0b143bb34026506c9a3fe4f4bb93c797d` plus the recorded Open-Ocarina patch chain.

## Three separate native-quality gaps

1. **Movie-upscale network omission:** ten startup compute programs are deliberately rejected by the network policy in both pilot conditions. They are not ten unexplained ordinary gameplay failures.
2. **Gameplay reconstruction replacement:** the later `fd029a317a9a4b94` dispatch is a different program. The pilot records FSR 3.1 replacing its gameplay upscale from 1536×864 to 2048×1152. This is not restoration of the original network.
3. **Motion/coverage fidelity:** the hair/foliage concerns in [hair-aliasing.md](hair-aliasing.md) remain separate. Movie-network restoration alone cannot establish correct gameplay hair AA, original reconstruction, complete object motion or native sample coverage.

The current pilot therefore does not establish the full-native-feature/native-quality goal even if a mesh optimization preserves its displayed image. A 3D-scene screenshot cannot certify movie-network fidelity.

## Exact classification of the ten skipped keys

The generic rejection message alone was insufficient to label a shader a movie pass. This investigation decoded the existing user's `Movie/Ndve/Ndve-Nvn2-vid_seq5.dat.zs` asset locally with Hookshot's existing `nx2/shader_census.py` parser. It recomputed each stage's runtime FNV-1a key over the first 216 control bytes and the sized native code area. All ten pilot keys matched compute stages in that NDVE movie-upscale container exactly. Compressed/decoded asset hashes, native control/code digests, source fingerprints, declared workgroups, encoded HMMA counts and sanitized bound-slot diagnostics are in [native-network-inventory.json](native-network-inventory.json).

| Runtime key | NDVE program index | Declared workgroup | Encoded active-predicate HMMA.16816.F16 instructions | Observed grid |
|---|---|---|---|---|
| `e409733c45530d75` | 2 | 32×1×1 | 72 | 900×1×1 |
| `8306877b7dc8784b` | 3 | 32×1×1 | 36 | 720×1×1 |
| `de3c10dba4ff39d3` | 4 | 32×2×1 | 32 | 97×1×1 |
| `a41c78faa56ccf7d` | 5 | 32×1×1 | 576 | 120×1×1 |
| `e959b9b1451491c0` | 6 | 32×4×1 | 464 | 70×1×1 |
| `5a4d7378db437fed` | 7 | 32×4×1 | 144 | 60×1×1 |
| `8b73eaee02efc5e4` | 8 | 32×2×1 | 768 | 85×1×1 |
| `14140d4c1dbe754f` | 9 | 32×1×1 | 16 | 8100×1×1 |
| `4c2c129fe950b041` | 10 | 32×1×1 | 108 | 5400×1×1 |
| `3f6f2b1d671badcd` | 1 | 128×1×1 | 46 | 80×180×1 |

All have native stage 5 (compute). HMMA counts examine the declared instruction body and omit always-false predicates; they are not control-flow reachability, dynamic execution counts, inferred layer counts or neural architecture recovery. The log's image/texture/UBO/SSBO slots are observed binding diagnostics, not complete reflected use: dummy or stale slots can be present.

The startup diagnostics include a 960×540 texture, 1920×1080 intermediates, storage-buffer feature maps and a 2560×1440 output on the final listed program. `3f6f2b1d671badcd` is consistent with the terminal replacement candidate under the inspected selection logic, but the linear-movie message itself does not record the program key. Do not elevate that chronology to a separately instrumented per-pass proof. No weight values, instruction words, raw GPU addresses or image contents are published here.

## Why the current renderer rejects them

In `gpu/vk/vk_shader.cpp`, `translate_into` first translates compute into IR. The rejection occurs when `program.info.uses_cooperative_matrix` is true, before final SPIR-V emission. That flag is collected from `MatrixMulAddF16`; the native frontend maps HMMA.16816.F16 into that IR operation.

The default `RX_VK_NETWORK_KEEP` list contains only `fd029a317a9a4b94` and `9dc98871eb7fc101`, identified by the renderer as gameplay/dynamic-resolution resolve networks. None of the ten NDVE keys is in this list. The exact gate rejects an unkept matrix program when software matrix lowering is selected **or the vendor is not NVIDIA**. It also rejects when `RX_VK_NO_F16` is enabled or shaderFloat16 is unavailable, regardless of the keep-list exception. The pilot reports software matrix lowering because the exact requested cooperative-matrix shape is absent; the vendor condition also applies to this Intel device.

This is a deliberate policy omission, not evidence that these ten programs contain an entirely unsupported native ISA. Earlier historical notes mention an unsupported opcode in one of them; that is not the failure reason logged by this frozen pilot. The log reaches the cooperative-matrix rejection after native lowering/IR translation.

Keeping a program would only permit translation and compilation. It does not by itself prove matrix fragment mapping, arithmetic equivalence, network color reconstruction or acceptable execution time. `RX_VK_NETWORK_KEEP=all` additionally suppresses the automatic movie-network slow-path fallback, whereas a per-key keep list does not have that global runtime-policy meaning. Neither setting is a validated production fix.

## What replaces the movie chain

`upscale_stub` inspects bound image/texture slots for an image larger than the sampled color input, excluding aliases that are also inputs. It first offers matching gameplay formats to the temporal/FSR replacement. For a suitable RGBA8 movie output, `movie_upscale` samples the selected input through a linear sampler and writes the output with a small compute pass; the pilot explicitly logs that path. If the compute path cannot be used, the surrounding code can attempt other typed or blit fallbacks.

The skipped intermediate matrix passes are not reconstructed by that final linear sampling. The original learned/detail reconstruction and its exact numerical/color output are uncovered. The fallback preserves a displayable movie picture, not proof of native movie quality. Its existence does not establish that unrelated decoding/audio/cutscene-control features are wrong; those require separate coverage.

## What replaces gameplay reconstruction

The pilot explicitly logs `fd029a317a9a4b94` with one texture and one image at 1536×864 and 2048×1152, Vulkan format 122 (`B10G11R11_UFLOAT_PACK32`). The temporal FSR 3.1 chain then runs with reconstructed depth, camera motion, recognized skinned-actor motion and projection jitter. Its first-frame fallback and reset history must also be accounted for.

Source entry points are `fsr3_stand_in`, `temporal_stand_in`, `temporal_rejected_network`, `temporal_upscale` and the FSR `chain_evaluate`. This replaces a gameplay network dispatch, including other fixed-resolution networks when their scene/format contracts qualify. It does not run the NDVE movie tensor chain. The native network and FSR are different algorithms; comparative images and motion sequences are still needed before claiming native quality or an improvement.

As noted in [hair-aliasing.md](hair-aliasing.md), source comments disagree about whether the native color-only network has temporal behavior. The movie classification does not resolve that question. The original gameplay program's resource/history behavior needs its own reconstruction.

## Arc matrix capability: hardware path exists, exact contract does not

A bounded physical-device query used the audited Intel Vulkan library SHA-256 `5ea19ac96aa52d061ecf2b8e23df7ed3d7d3b36c7d2ae7fe62f01c5a9b610aaa`. Only instance and physical-device enumeration/property/feature APIs were called; no logical device, queue, pipeline or dispatch was created. Full query values and query source/binary hashes are in the inventory.

The A770 with this Mesa 26.2.4 driver exposes `VK_KHR_cooperative_matrix`, cooperativeMatrix, shaderFloat16, VulkanMemoryModel and device-scope memory model. It does not advertise `VK_NV_cooperative_matrix`. Its floating-point matrix property is **M8×N8×K16 with f16 A/B and f32 accumulator/result**; another property uses bfloat16 inputs with the same shape. It does **not** advertise Hookshot's required **M16×N8×K16 with f16 A/B/accumulator/result**. Its advertised subgroup-size range is 8–32, with default 32; a matrix property alone does not prove a particular fragment layout or suitable subgroup-width implementation.

The inspected Mesa source `src/intel/compiler/brw/brw_nir_lower_cooperative_matrix.c` explicitly lowers compatible cooperative-matrix multiply-adds through `nir_dpas_intel`. This is evidence of an Intel DPAS backend route, not evidence that these game networks currently run through it or that an unadvertised f16-accumulator shape is safe to request.

Hookshot's `coopmat_init` requires the exact original shape and all-f16 types, then measures fragment element order and tests multiplication. This driver fails the shape test first and selects software subgroup-shuffle lowering. Flipping the feature flag or merely changing one dimension would bypass unfulfilled semantic checks.

## Realistic restoration options and pass tests

### 1. Preserve exact software math as a correctness reference

The existing `DefineSoftwareMatrixFunction` assembles the original warp-32 fragments with subgroup shuffles, f32 FMAs and explicit intermediate half rounding; `EmitMatrixMulAddF16` selects it under `mma_software`. A bounded private experiment can keep one NDVE key, compile it without disabling its work, and validate the entire tensor-stage interface against a trustworthy reference. This investigation did not run that experiment. These networks use real shared memory and matrix shuffles, so the unrelated no-subgroup compute unpin argument does not apply.

Before gameplay timing, verify lane-fragment placement, predicates, signed zero, subnormals, infinities/NaNs, intermediate K-step rounding, weights/constants, shared-memory synchronization and complete feature-map output. Then validate the whole movie chain, final color encoding, scene transitions and audio/video pacing. A slow exact path can still be a valuable oracle; no acceptable speed is established.

### 2. Add a shape-adapted KHR/DPAS lowering

Two M8 tiles could cover the original M16 extent geometrically, but the exposed f32 accumulator/result differs from the original half-accumulation contract. Original HMMA rounding checkpoints and fragment remapping must be reconstructed; a final f16 cast alone is not sufficient. Candidate padding/splitting of K operations must be tested rather than assumed to preserve arithmetic or throughput. Layout probes must validate the actual required subgroup policy and every fragment element.

Start with independently authored matrix fixtures and exhaustive structural/lane checks plus adversarial numerical cases. Then compare one complete real stage using identical captured constants/weights/inputs, and only afterward admit the network chain through a narrowly justified policy change. The non-NVIDIA keep gate and fallback policy also need consistent treatment once an implementation is proven. No native hardware lowering was implemented or dispatched by this investigation.

### 3. Reconstruct the network at algorithm level

If instruction-level software is too expensive, recover tensor shapes, operators, padding, activation, weight interpretation and rounding contract, and author an optimized PC inference path using the same native model data. Do not infer a known neural architecture from the HMMA opcode counts. Replacing it with another trained model or FSR may be a useful quality option, but it is not restoration of the original network's semantics without evidence.

## Next decisive work

Obtain one exact stage-level network input/output reference and identify weights/feature-map bindings as actually used. The inventory's resource sizes and native stage identity provide bounded starting points. Preserve the pilot's frozen artifacts and keep this restoration experiment separate from mesh-width work and the FSR/hair-quality track. Full-native claims remain open until both the movie tensor chain and gameplay reconstruction contracts have corresponding evidence.
