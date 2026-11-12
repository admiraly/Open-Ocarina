# Bounded native mesh input snapshots

The optional [mesh-input overlay](../patches/hookshot/mesh-input-capture.patch) captures logical inputs for one exact d8 mesh stage. It applies to [Hookshot](https://github.com/NotRexed/Hookshot) revision `4cf785a0b143bb34026506c9a3fe4f4bb93c797d`, after the compute identity, page-table snapshot, allocation capture, occurrence capture, graphics identity, sampled-image capture, dispatch-parameters and binding-provenance overlays, in that order. It retains GPL-3.0-or-later licensing.

## Scope and configuration

The pinned program is `d8e59341cf4f629c`, variant `0c8f08626a9d98a4`, mesh stage index 0/NVN 7/Vulkan `0x80`, with SHA-256 `835aa59799c6ecb621034d946e2fa4eadb1aa2407d293321ae3e3052882bc9b3`. The [audited shader contract](../research/mesh-output-replay.md) identifies read-only logical buffer accesses, no images or physical pointers, and no push constants. Fragment-stage resources and raster outputs remain outside this contract.

Enable `RX_VK_OO_MESH_INPUTS=1` with the existing allocation/page-table capture switches. Set both the identity program/variant filters and page-table shader digest to the exact values above. Graphics identity observation must also be enabled so both fresh translations and cache loads retain actual stage digests. Use `RX_VK_OO_CAPTURE_OCCURRENCE` for a bounded matching occurrence and a fresh private output directory. Binding provenance should be enabled to associate observed resource lifetimes. Dispatch-parameter and sampled-image capture are unnecessary for this mesh-only input contract.

Only a converted `vkCmdDrawMeshTasksIndirectEXT` with one record, stride 12 and no count buffer can provide complete launch evidence in this initial version. Direct, count-buffer, multidraw, rewrite, fallback and debug-work cases remain incomplete. The observer does not replace the shader, transform its code, change native draw arguments or change subgroup policy.

The selector counts exact matching d8 preparations before rendering and confirms the selected event at the actual native command. If the selected preparation never reaches that command, or its identity, layout, recording, arguments or subgroup request differ there, the capture remains incomplete. This ordinal is a selected CPU-recording observation, not global queue order or proof of draw execution.

## Legal copy position and descriptor ownership

Copies cannot run inside dynamic rendering. At the selected boundary, after descriptor construction, draw hazards, mesh conversion/rewrite and fallback preparation, the observer ends an older pass if necessary. It records the existing table/allocation BEFORE copies outside rendering, then follows the original `begin_pass`, pipeline/descriptor binding, push and dynamic-state flow. The actual native observer confirms the launch afterward. The source checker audits the intervening command categories and unchanged native commands.

The initial supported boundary rejects block-image targets, dumps, probes, profiling and draw-statistics work. A diagnostic pass split can affect attachment behavior, aliases and scheduling; this does not establish whole-rendering equivalence or ordinary performance.

Graphics descriptor reuse can leave `dw().writes` owned by another program while retained Vulkan descriptors are valid. Complete capture therefore requires a fresh build for this draw and `dw().prog == &p`. The overlay does not force fresh builds or change normal descriptor reuse. Unsupported reuse is reported explicitly.

`ensure_layout` retains actual descriptor layout inputs. The required mesh slots are UBO bindings 0–4, SSBO bindings 5–7, and all three elements of SSBO binding 8. Their descriptor types/counts/stage flags and exact submitted buffer offsets/ranges are checked. `VK_WHOLE_SIZE` resolves against the actual owning buffer extent, preserving the range that controls `OpArrayLength`.

The converted mesh ring originally lacks transfer-source usage. Only when this diagnostic is enabled, its original creation adds `TRANSFER_SRC`. The capture retains its real VMA owner through the existing buffer-owner registry and copies it as an extra `mesh-launch-input` catalog row. It never invents a descriptor binding for the launch buffer.

## Export and remaining gates

After the exact successful recording submission reaches its recording timeline completion, the capture exports:

- `mesh-indirect.bin`: the exact twelve bytes from the GPU BEFORE copy at the actual argument-buffer offset. These bytes supply X/Y/Z, rather than CPU-side `d.groups`.
- All eleven descriptor positions: each nonnull entry has a `mesh-binding-B-E.bin` file containing the complete exact range from a checked, invalidated GPU BEFORE map, with size and SHA-256. Reviewed null positions at binding 8 elements 1/2 retain `snapshot=null` and have no payload or allocation. Slice output is bounded to one GiB in total, including the launch bytes.
- `mesh-inputs.json`: schema `open-ocarina.mesh-logical-input/v1`, containing stage identity, selected occurrence, actual-site confirmation, logical/recording/completion values, retained layout, subgroup request, original buffer offsets, and SHA-256 associations to table/allocation/binding-provenance manifests.

The allocation capture may contain whole buffers as a superset. Mesh mode records no allocation AFTER copies, including after later compute dispatches on the same command buffer. Legacy `allocations.json` therefore remains incomplete with `after_recorded=false`; the mesh input manifest depends only on authoritative BEFORE payloads. Table capture is a conservative unused superset for this shader.

`mesh_stage_inputs_present` describes all eleven descriptor positions and launch bytes only. It includes feature-backed defined null descriptors, rather than requiring bytes for nonexistent resources. `replay_complete` always remains false. Resource lifetimes are copied from the observed binding ledger where available. Backing generations and imported alias intervals must be normalized from the linked provenance; unknowns are never invented. CPU-writer quiescence, proof of the original draw's exact inputs, fragment resources and full-rendering equivalence remain explicit gaps. The capture does not emit a replay authorization or a false ownership/quiescence certificate.

Zero native grid dimensions are captured but forbid execution. A later replay may choose a window of at most 32 groups, with every origin/extent axis inside the captured grid. Preserve exact descriptor lengths, logical table values and alias evidence when normalizing those frozen inputs. Compare outputs separately; input presence is not shader equivalence or a performance result.

## Defined null descriptors

The first isolated runtime snapshot recorded a valid nonzero GPU launch and nine nonnull mesh-buffer positions. Binding 8 elements 1/2 were submitted as `VK_NULL_HANDLE`, offset 0, range `VK_WHOLE_SIZE`, with actual enabled `nullDescriptor=true`. The initial producer incorrectly required allocations and payloads for those positions and reported incomplete inputs. Its patch, verifier and frozen renderer remain preserved privately. The corrected build was tested in a separate fresh capture; the first snapshot remains incomplete.

The corrected manifest records actual enabled `features.nullDescriptor`. Every position has explicit `null_descriptor`. A defined null entry retains the original zero buffer handle, zero offset and whole range; `snapshot`, `range_bytes`, `allocation_row`, `buffer_lifetime` and `backing_generation` are null. The array count remains three. No zero-filled substitute, empty allocation or synthetic lifetime is created. Nonnull entries retain their full exact ranges as before.

This framing follows the [Vulkan descriptor buffer requirements](https://docs.vulkan.org/refpages/latest/refpages/source/VkDescriptorBufferInfo.html): null buffer handles require the feature and the prescribed offset/range. Initial support is limited to the two reviewed null positions. The verifier independently matches the feature to `allocations.json`, matches each null against its authoritative submitted descriptor, and refuses any fabricated null allocation, provenance owner or payload file. A later replay must enable the required feature and submit the same null descriptors. Null entries do not erase ownership, CPU-writer or alias gates for real resources.

## Verified second runtime snapshot

[Sanitized runtime evidence](../research/gpu-mesh-input-observation.json) records the second capture only, using the null-aware patch and frozen renderer identified there. The capture verifier passed after checking actual files, full GPU BEFORE payloads, descriptor framing, observed lifetimes and same-recording associations. The GPU indirect grid is **705×1×1** at selected occurrence 64. All eleven positions are present as nine exact snapshots and two defined null descriptors. Descriptor payloads total **172,863,616 bytes**, plus twelve launch bytes: **172,863,628 raw bytes**. Metadata, full-buffer supersets and later replay-bundle framing are excluded from that total.

This establishes artifact integrity and association for those frozen logical inputs. It does not establish that historical CPU inputs stayed immutable, complete canonical backing/alias normalization, original-draw input equivalence, shader replay/output equality, whole rendering or an optimization gain. The checker's nonzero-grid flag is a bounded-input condition, not execution authorization. The first revision's rejection remains a separate observation.

## Checks

```sh
python3 patches/hookshot/check_mesh_input_capture.py
python3 patches/hookshot/check_mesh_input_capture.py --checkout "$HOOKSHOT_SOURCE"
python3 patches/hookshot/check_mesh_input_capture.py --capture-dir "$CAPTURE_DIR"
python3 -m unittest discover -s tests -v
```

The C++20 helper checks run at `-O2 -DNDEBUG` with no Vulkan calls or game inputs. They cover all eleven positions, exact nonnull slices and launch bytes, completion, zero grids, feature-backed nulls, invalid null framing/positions and unsupported/missing cases. The source checker applies the exact nine-overlay chain through an isolated index and checks legal copy placement, fresh descriptor ownership, actual-site confirmation, launch-owner retention and protection against unrelated later AFTER copies. The capture verifier cross-checks slice/launch hashes against authoritative full-buffer BEFORE payloads and associates descriptor ranges and resource lifetimes. It reports unresolved ownership/quiescence and graphics gates explicitly.
