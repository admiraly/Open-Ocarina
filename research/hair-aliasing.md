# Hair and foliage aliasing investigation

Date: 2026-11-12. Status: source inspection only; no GPU run, quality setting change, controlled motion capture or proven aliasing fix. The actual hair draw/material has not been identified. A static image cannot establish shimmer or temporal ghosting.

## Verified source mechanisms

Source inspected: [Hookshot commit 4cf785a0](https://github.com/NotRexed/Hookshot/tree/4cf785a0b143bb34026506c9a3fe4f4bb93c797d).

| Mechanism | Source location | Established fact and limitation |
|---|---|---|
| Gameplay network replacement | `gpu/vk/vk_draw.cpp`, `taau_key`, `taau_stand_in`; `gpu/vk/shaders/taau_upscale.comp` | The default target key `fd029a317a9a4b94` is replaced because the translation reportedly loses blue. The stand-in samples scene color and applies FXAA by default. A clamp against previous output is an optional flash limiter, not motion-compensated temporal reconstruction. |
| Original network semantics | `gpu/vk/vk_temporal.cpp` introductory documentation versus the stand-in comments | Comments are inconsistent: the stand-in calls lost AA temporal, while the temporal-chain description says the original network samples color alone and the game has no velocity target. We can establish replacement of the native network, but not original temporal accumulation from these comments. Inspect the original program's resources/algorithm or trustworthy reference output before claiming missing native TAA. |
| Temporal FSR 3.1 | `gpu/vk/vk_fsr3.cpp`, `chain_evaluate`; `gpu/vk/vk_temporal.cpp`, `temporal_upscale` and `actor_motion` | Inputs include depth, motion, camera jitter and history/reset state. Actor motion redraws recognized skinned geometry using current and previous poses. Hair's particular draw has not been shown to enter that path. |
| Native-resolution AA option | `gpu/vk/vk_menu.cpp`, `hn_resolution`; `gpu/vk/upscaler_rows.h` | FSR 3.1's render-resolution row includes 100% and labels it native AA. This differs from merely increasing output size while keeping a smaller internal scene. It is an available experiment, not a measured Arc improvement. |
| Moving foliage and other effects | `gpu/vk/vk_temporal.cpp` introductory documentation and `actor_motion` | Skinned actors may receive object motion. Vertex-animated foliage, mesh-shader draws, particles and water retain camera-only motion unless covered by another mechanism. This is a concrete temporal-reconstruction risk. |
| Coverage | `gpu/vk/vk_draw.cpp`, graphics pipeline multisample state and `dynamic_state` | Rasterization samples follow `Targets::samples`; alpha-to-coverage follows `s.ms.a2c` unless explicitly disabled. There is no inspected global hair-specific MSAA solution. Additional samples require compatible attachments/resolves; enabling a state bit alone is not sufficient. |
| Shader sample masks | `gpu/vk/eden/shader_recompiler/backend/spirv/emit_spirv_context_get_set.cpp`, `EmitSetSampleMask`; `spirv_emit_context.cpp`, output declaration | The translator has a SampleMask output path. Correct masks for the actual hair draw are unverified. |
| Filtering and mip bias | `gpu/vk/vk_draw.cpp`, `sampler` and `upscale_lod_bias` | Filters and min/max LOD are translated; negative material bias can receive an additional upscale bias. Native mip/filter correctness must be checked before adding blur or sharpening. Anisotropy is not a substitute for silhouette or alpha-coverage anti-aliasing. |
| Texture detail policy | `gpu/vk/vk_texture.cpp`, `create` and `low_vram_texture_mips` | Oversized textures can lose top mip levels under allocation policies, including decoded ASTC limits. The small-budget Minimum preset has an additional policy. These policies must be recorded for native-quality claims; there is no evidence that they explain Link's hair. |
| Reactive mask | `gpu/vk/reactive_policy.h`, `gpu/vk/vk_reactive.h`, FSR `chain_evaluate` | Optional masking classifies color-blended, depth-tested draws without depth writes as transparent. Cutout/depth-writing edges are excluded by that rule. The source explicitly calls classification heuristic and unchecked; it is not a proven hair mask. |
| Shadow stabilization | `gpu/vk/vk_draw.cpp`, `mask_weight` and `mask_stabilize` | An opt-in history pass addresses changing tree-shadow lookups. It is off by default after reported device-loss correlations. Do not recommend enabling it as a general AA fix. |

## Competing explanations to test

1. **Native-network mismatch:** replacing the game's network changes edge reconstruction. Establish original output behavior before deciding whether to repair that translation or use a documented alternative.
2. **Spatial undersampling:** correctly rendered strands or leaf edges cross subpixels at the internal scene resolution. Check full-resolution rendering with matched sharpening, not only output resolution.
3. **Coverage or texture translation:** incorrect alpha discard, sample masks, actual sample count, mip availability, filtering or bias produces unstable thin features. Identify the exact draw and material before changing global policy.
4. **Temporal-input deficiency:** motion, jitter, depth, disocclusion or history reset is wrong or unavailable. A temporal algorithm can turn this into ghosting even while reducing stair steps.
5. **Lighting/shadow instability:** a changing shadow term can resemble geometric aliasing. Compare the native scene and intermediate lighting masks so AA does not conceal a correctness error.

These are hypotheses, not an ordered diagnosis from a captured hair sequence.

## First diagnostic capture

Extend runtime graphics identity to identify the draw that contributes Link's hair, including native stages, variants, descriptor identities, vertex/mesh path, depth/blend/discard/sample-mask state, target sample count, texture mip availability and sampler settings. Do not assume the first or last program of a timed pass is the hair program.

For identical camera, pose, native frame index and lighting, save private pre-reconstruction scene color, depth, motion, reconstructed color and final output. Preserve the jitter sequence and history reset reason. For skinned hair, verify that its pixels receive the actor-motion pass; for alpha-tested edges, distinguish material coverage from ordinary polygon edges. For moving foliage, specifically measure the consequence of camera-only motion.

Then compare short deterministic sequences: still camera/animated character, slow pan, rotation, disocclusion and a cut or scene transition. Register crops to stable geometry before quantifying instability; otherwise motion itself is counted as shimmer. Repeat baseline-versus-baseline to establish natural variation before tolerances. Require unchanged counters/readbacks and no omitted rendering work, and keep HUD/text outside any scene-only AA treatment.

The first enhancement experiment should use the existing correctly fed FSR 3.1 native-resolution path if supported and verified on the target device. Whether it improves hair, preserves foliage and fits the budget is unmeasured. Only then consider a targeted coverage/temporal change; do not promise that FXAA, MSAA, temporal AA or sharpening alone will solve all cases.
