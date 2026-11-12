# Exact compute dispatch parameters

The optional [dispatch-parameters overlay](../patches/hookshot/dispatch-parameters.patch) adds exact pushed-byte evidence to the existing page-table, allocation and sampled-image capture. It applies to [Hookshot](https://github.com/NotRexed/Hookshot) revision `4cf785a0b143bb34026506c9a3fe4f4bb93c797d`, after the compute identity, page-table snapshot, allocation capture, occurrence capture, graphics identity and sampled-image capture patches, in that order. The overlay retains GPL-3.0-or-later licensing.

Set `RX_VK_OO_DISPATCH_PARAMETERS=1` alongside the existing identity, page-table and allocation capture switches. Use a fresh private capture directory. The selected occurrence and exact program, variant and translated shader digest come from the existing capture selector. Disabled configuration adds no Vulkan commands or output artifacts and does not change subgroup policy.

Immediately after the existing `vkCmdPushConstants` call, the observer copies the exact supplied byte array into an associated shadow. It records command buffer, program, variant, pipeline layout, stage flags, range offset, recording part and logical submission value. It invalidates the shadow at every `push_constants` entry, including calls that return without pushing. At the selected occurrence it consumes the fresh matching shadow once. It never reconstructs pushed values later from game state.

The current source pushes 88 bytes. Capture bounds permit at most 256 bytes and retain the actual complete pushed range. The guest page-table root offset comes from `GUEST_PAGES_PUSH_OFFSET`; it is not a literal offset in the observer. The helper verifies that the root bytes equal the actual captured table's device address. Missing pushes, stale command/recording/layout associations or differing ranges and roots produce incomplete evidence.

`ensure_layout` retains the actual descriptor set layout inputs: set/binding number, descriptor type/count and stage flags, together with layout flags and the actual push constant range. The capture also retains the selected compute pipeline builder's subgroup request/full-subgroup flag, local size and cache stamp. These are API requests, not measured driver SIMD width. The builder's entry point is `main` with no specialization data. Direct grids come from the selected dispatch arguments. Indirect GPU counts remain unknown and cannot pass the complete direct-parameters verifier.

After the same successful recording submission reaches its exact recording timeline completion, the observer exports:

- `push-constants.bin`: the complete exact host-pushed byte array, with its size and SHA-256 recorded in the manifest.
- `dispatch-parameters.json`: `open-ocarina.dispatch-parameters/v1`, containing the pipeline/layout/push/dispatch metadata, selected occurrence and recording/completion association.
- SHA-256 associations to the committed `manifest.json`, `allocations.json` and `images.json`, or explicit missing-artifact failures.

The logical submission value is separate from the actual recording timeline sequence. A higher completion value is accepted only after that exact submitted sequence is reached. Failed or canceled submissions do not export evidence. Existing image manifests retain their original stage-specific coverage declarations; the parameter manifest adds separate evidence.

`parameters_complete` covers the direct grid, pushed bytes and retained layout/policy only. `replay_complete` always remains false. Exhaustive enabled device features and float controls, submitted descriptor interpretation, sampled-image/sampler semantics, underlying import aliases, CPU writers, temporal state and independent output comparison remain explicit gates. The retained `nullDescriptor` flag is not the complete Vulkan device feature chain. Producer revision, patch and renderer hashes require a separate frozen-build receipt; the capture does not invent self-referential binary hashes.

## Checks

```sh
python3 patches/hookshot/check_dispatch_parameters.py
python3 patches/hookshot/check_dispatch_parameters.py --checkout "$HOOKSHOT_SOURCE"
python3 patches/hookshot/check_dispatch_parameters.py --capture-dir "$CAPTURE_DIR"
python3 -m unittest discover -s tests -v
```

The source checker uses an isolated Git index, applies the exact seven-patch chain and verifies that Vulkan recording calls/arguments and subgroup policy remain unchanged. It audits the direct-dispatch path from `push_constants` to the selected observer for intervening pushes, checks selected-occurrence consumption, and verifies that export follows exact recording completion and associated manifest publication.

Synthetic C++20 checks compile with `-O2 -DNDEBUG`. They exercise exact byte retention and digesting, mutation after pushing, fresh single-use shadows, command/layout/recording/root mismatches, default disablement, incomplete metadata and indirect unknowns. Synthetic manifest regressions reject corruption, rehashed wrong roots, partial ranges, symlinks, occurrence/timeline mismatches and unsupported completeness claims. These checks require no game files or GPU. The capture verifier checks artifact integrity and association; it does not independently attest that GPU execution occurred or validate every other capture stage's resource payloads.

## Isolated runtime observation

[Sanitized evidence](../research/gpu-image-dispatch-observation.json) records a
successful combined run of the frozen image/parameter renderer. The parameter
verifier confirmed complete exact pushed bytes, descriptor layout and direct
launch policy with matching hashes for the table, buffer and image manifests.
The independent image verifier also passed all sampled inputs and linked
buffer/table ranges. The process stopped cleanly. Full replay remains false:
canonical backing identity, CPU mutation, remaining device/temporal state and
independent output comparison are separate requirements.
