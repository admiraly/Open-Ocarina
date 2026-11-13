# Explicit 6864 mesh input capture profile

The optional [profile96 input overlay](../patches/hookshot/mesh-input-profile96.patch) adds one exact capture target after the ten existing renderer patches, including native mesh lowering revision 2 (`8d0cc053103ccf90b51e6c55c6e7ee39292636f03b85e16d74bd8d8d1221854b`). Upstream [Hookshot](https://github.com/NotRexed/Hookshot) revision is `4cf785a0b143bb34026506c9a3fe4f4bb93c797d`; patches retain GPL-3.0-or-later licensing. Existing d8 producer, checker, default filters, output instrumenters, replay admission and frozen runtime evidence remain separate.

The [mesh-family audit](../research/mesh-family-audit.md) selects this target. Its exact original program is `68642bb810ee3e7c`, variant `3f3f5b29054673da`, mesh SHA-256 `999c8511db93d486543421dde7350e3d0034faa834ef3edaf3553dcdc9b55d93`. It has stage index0/NVN7/Vulkan128 in a two-stage program. The independently pinned [output profile](mesh-output-profile96.md) describes maxima96, ten generic vec4 attributes and dead task payload. This capture stage uses only its audited read-only logical input contract: no images, physical pointers, push constants or input stores. No output shader is transformed or executed by the producer tool.

## Explicit configuration and schema

In addition to the existing allocation/page-table/graphics-identity capture switches, set:

```sh
export RX_VK_OO_MESH_INPUTS=1
export RX_VK_OO_MESH_PROFILE=6864-position10-96/v1
export RX_VK_OO_IDENTITY_PROGRAM=68642bb810ee3e7c
export RX_VK_OO_IDENTITY_VARIANT=3f3f5b29054673da
export RX_VK_OO_PT_SNAPSHOT_SHA256=999c8511db93d486543421dde7350e3d0034faa834ef3edaf3553dcdc9b55d93
export RX_VK_OO_GRAPHICS_PROGRAMS=68642bb810ee3e7c
```

Use a fresh private snapshot directory, graphics identity output, binding provenance and a bounded matching occurrence as described in the [base mesh capture workflow](mesh-input-capture.md). Serial, nonbatched replay and the exact recording timeline are still required. Missing, empty, prefix or unknown profile values do not enable 6864 capture. Program, variant, emitted digest and actual stage roles must all match; cache membership alone is insufficient.

The manifest is `mesh-inputs-6864.json`, schema `open-ocarina.mesh-input-profile96/v1`, with explicit `profile=6864-position10-96/v1`. `runtime_execution_authorized`, `equivalence_proven` and `replay_complete` are always false. The original d8 checker rejects this schema and its own accepted identity remains unchanged. Unknown or legacy unchecked data cannot silently become a reviewed profile.

## Captured contract

Required set0 positions are four singleton UBOs at bindings0–3, four singleton SSBOs at4–7, and all three SSBO elements at8: eleven positions total. Binding4 is a storage buffer in this profile. Actual retained descriptor layout types/counts/stage flags and submitted buffer offsets/ranges must agree with that contract.

Non-null positions export `mesh6864-binding-B-E.bin`, containing complete exact checked GPU BEFORE slices and SHA-256 hashes. `VK_WHOLE_SIZE` resolves against the true owner extent. Reviewed null positions remain8:1/2 only, with actual enabled `nullDescriptor`, zero handle/offset and whole range, no bytes and no invented allocation or lifetime. The profile does not assume which positions will actually be null in a future runtime capture.

`mesh6864-indirect.bin` contains the exact twelve GPU-copied bytes of one converted `vkCmdDrawMeshTasksIndirectEXT` record, stride12 and no count buffer. Its true VMA owner is retained as a separate `mesh6864-launch-input` row, never a descriptor. No CPU reconstruction supplies group counts.

Copies reuse the existing selected outside-rendering boundary after conversion, hazards and fresh descriptor construction. The original begin-rendering/binding/push/dynamic-state sequence follows, and the actual native command confirms the same program, digest, layout, recording, argument buffer/offset/count/stride and subgroup request. Descriptor reuse, debug work, count/multidraw/direct/rewrite/fallback cases remain incomplete. Native Vulkan draw arguments and subgroup policy stay unchanged.

Export occurs only after the exact successful recording submission completes. Full-buffer BEFORE payloads and table/binding-provenance manifests remain a conservative superset; later compute commands cannot manufacture mesh AFTER evidence. Exact slices and launch bytes must match authoritative full-buffer GPU BEFORE files, submitted descriptors and observed lifetimes. Slice output remains bounded to one GiB. Zero native dimensions forbid execution; later replay windows may contain at most32 groups inside the captured grid, under a separately reviewed executor.

CPU-writer quiescence, backing-generation/alias normalization, proof of the historical draw's exact inputs, fragment/raster resources and output/rendering equivalence remain unresolved. The diagnostic pass split is not a native-quality or performance certificate. This overlay adds no runtime execution admission, framebuffer comparison, shader transformation or optimization claim. A new frozen renderer, parent review and an exclusive GPU lease are required before any future capture.

## Bounded E5 capture observation

The [sanitized retry evidence](../research/gpu-mesh-profile96-observation.json) records the completed `runtime-mesh-profile96-02` capture only. It used the frozen profile renderer `81eb4976…`, host `4eb32b56…`, selected E5 driver library `e5a1305e…`, and hashed ICD with safe3 enabled and MAX/forced-width overrides absent. Native transformation mode was0. Only the controlled E5 warm Mesa cache and canonical program/notes cache from the stopped first attempt were reused; opaque Vulkan pipeline caches were not seeded. The seed and settings were hash-checked, with per-frame clock mode, cap60 and the original truncated menu script. This is not a deterministic scene or FPS certificate.

The strict profile checker passed. The actual copied GPU launch is **67×1×1** at selected occurrence64. Eleven positions are present as nine exact GPU views and two defined null descriptors. Descriptor bytes total **158,076,400**, plus twelve launch bytes: **158,076,412 raw bytes**, excluding metadata, full-buffer supersets and replay bundle framing. The record pins source/checker hashes, all four manifest hashes and the complete private artifact inventory.

Submitted recording sequence **8968** is covered by observed completed recording sequence **8971**. These are distinct values. The completion association and checked BEFORE payloads do not prove historical CPU-writer atomicity or canonical backing/alias normalization. The owned pidfd watcher stopped its game process after all four manifests committed; the GPU lease was released. Its roughly39-second wall duration is not a shader timing or performance result.

The earlier attempt reached its300-second bound while exporting a partial buffer payload and remains an incomplete, preserved attempt. It is not relabeled as successful or merged into this capture. No instrumented mesh-output/replay execution, output or image equivalence test, historical-input proof or optimization acceptance follows from this input-capture observation. Separate parent-reviewed replay/device/ownership gates remain in force.

## Verification and dependencies

```sh
python3 patches/hookshot/check_mesh_input_profile96.py
python3 patches/hookshot/check_mesh_input_profile96.py --checkout "$HOOKSHOT_SOURCE"
python3 patches/hookshot/check_mesh_input_profile96.py --capture-dir "$CAPTURE_DIR"
python3 -m unittest discover -s tests -v
```

The C++20 synthetic helper compiles at `-O2 -DNDEBUG`, makes no Vulkan calls and uses independently authored bytes. Fresh empty directories make exact opt-in/identity rejection tests independent of existing-output guards. The new checker validates only this explicit schema/profile and the four-UBO split. Regressions preserve corruption, descriptor length, null-feature/framing, recording/occurrence, lifetime, zero-grid and actual-site checks and verify d8 schema rejection in both directions.

The isolated source-index check applies all eleven overlays and requires unchanged d8 metadata helper bytes (`e129b121f6a77673852780da3d0d4510f26672912d6dd5f96bf64376203022a6`) and native revision2 patch. It checks all five native mesh API statements/arguments, subgroup policy, legal copy placement, fresh DW ownership, actual-site confirmation and BEFORE-only protection. The new profile reuses frozen d8 metadata data types; separate namespaces, registries and target checks prevent cross-profile selection.

Audited preparation dependencies are the core auditor `1b8aa9d2af553f5a19fe007facdf4a520682dae3ae290c09d85408659ca7bb61`, resource auditor `91bb902ee11d37ed24cda2c74a086e6c53fb15405311adc1fdd6a38f905048f7`, and separate output-profile adapter `4619e081b652b9ed1b72820a22d1948907196b08de05a10cefebd15703f42f09`. These hashes identify CPU preparation evidence, not a captured runtime module or an accepted native optimization.
