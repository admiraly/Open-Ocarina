# Native image quality and optional anti-aliasing

The quality goal is correct native rendering before extra smoothing or sharpening. A faster image with missing lighting, geometry, texture detail or reconstruction passes is not a successful port. Hair and foliage aliasing are explicit quality targets, but their cause has not been diagnosed from a controlled moving capture.

## Define the baseline

Record the original game revision, reference platform, output and internal render dimensions, dynamic-resolution behavior, sample counts, reconstruction path, sharpening, exposure, frame progression and temporal-history state. Match save, camera, animation and lighting before comparing images. A named preset alone does not establish equivalent work.

The native-feature corpus should cover:

- Geometry, skinning, animation, hair silhouettes, foliage density/culling, terrain and distant LOD transitions.
- Material textures and mip chains, normals, alpha testing/blending, depth/stencil and sample/coverage behavior.
- Direct and indirect lighting, sun shafts, shadows, fog, reflections and day/night or interior/exterior transitions.
- Water, particles, smoke, glows, transparent effects, motion and disocclusion.
- The game's reconstruction/upscale behavior, color encoding, tone mapping, SDR/HDR paths where supported, and a sharp, correctly composited HUD.
- Cutscenes, streaming and scene transitions without missing work, transient corrupt resources, device loss or persistent history artifacts.

Gameplay, audio, controls, save/load and other native features need their own coverage alongside the image corpus. Image agreement alone cannot establish those features.

## What the inspected renderer already provides

The inspected baseline is Hookshot commit `4cf785a0b143bb34026506c9a3fe4f4bb93c797d`. Source mechanisms are not proof that each path works correctly on Arc.

- Its gameplay network stand-in applies a spatial FXAA filter by default. This can smooth edges but is not a general motion-compensated temporal solution.
- Its temporal chain can run FSR 3.1 using scene color, depth, projection jitter, camera motion and additional skinned-actor motion. The FSR render-resolution row includes full-resolution native AA; this is an existing option to validate, not a newly implemented fix.
- Alpha-to-coverage follows the game's draw state, either in fixed pipeline state or through dynamic state, with the target's sample count. It is not a global hair-quality switch.
- Sampler filtering, mip levels and LOD bias are translated. Upscaling can add LOD bias; anisotropy can be overridden. Texture allocation also has policies that can omit upper mip levels for oversized textures. Native-quality acceptance must report those policies rather than silently treating them as equivalent.
- The optional reactive-mask path uses draw-state heuristics and is off by default. It does not identify every hair or foliage material and is not a proven universal ghosting fix.

Detailed evidence and uncertainties are in [the hair investigation](../research/hair-aliasing.md).

## Correctness before enhancement

First classify the artifact: broken geometry/material or missing pass; inadequate alpha/mipmap/coverage handling; incorrect reconstruction inputs/history; or ordinary undersampling of a correctly rendered feature. Restore missing native behavior before compensating for it with blur.

For a controlled hair/foliage sequence, capture the native scene before reconstruction, reconstruction inputs/output, final output, depth, motion and coverage/material metadata. Keep game-derived captures private. Use static camera, slow pan, camera rotation, character animation, disocclusion and lighting changes as distinct cases. Stabilize native frame progression and preserve the same jitter sequence/history warmup.

An optional AA change must preserve strand/leaf silhouettes, color and fine texture while reducing temporal instability. It must not trade shimmer for persistent trails, disappearing thin features, haloing or excessive softness. Validate written resources and downstream behavior as well as the displayed image. Measure its GPU cost separately and include it in the real-frame budget.

## Current decision

Do not enable speculative global filtering, sample-count changes, shadow-history fixes or vendor-specific neural paths merely to improve one screenshot. First obtain exact runtime draw identity and a native-quality reference. Evaluate the existing FSR 3.1 full-resolution path only after its inputs and history are shown correct. A custom hair/foliage technique is justified if that controlled experiment demonstrates a remaining problem and an explicit quality/cost benefit.
