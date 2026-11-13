# Owned physical-memory replay foundation

`tools/physical_replay.py` and `tools/vulkan_physical_replay.c` implement a new, explicitly bounded replay path. The default work is **CPU preparation/build only**. The historical driver comparison covers four authorized original compute dispatches on one owned frozen input graph: width 32 twice, then width 16 and width 8 once each. A separately identified cache-policy driver has now repeated that four-case result; historical live equivalence remains unestablished. Raw page-table, allocation, sampled-image and dispatch-parameter capture components are not accepted as ready replay manifests.

The implementation includes a real optimized native executor: one direct compute dispatch, canonical shared buffer storage, fully relocated page-table addresses, exact pushed bytes with a relocated table root, sampled image uploads/views/samplers, a finite fence wait, and complete canonical-allocation readback/comparison. The historical driver observation below records one independently authored synthetic dispatch and four original compute dispatches. The separately identified new-driver followup is recorded at the end of this document. CPU preparation, optimized compilation and native `--check-only` parsing have also been exercised.

## Distinct evidence scopes

- **Owned frozen diagnostic:** a complete, explicitly constructed input graph is frozen into new local artifacts. Repeatability and agreement with a selected expected result are questions about this graph. Uncertainty about CPU writers during the historical capture does not imply that newly owned replay buffers can mutate arbitrarily.
- **Captured AFTER agreement:** the diagnostic may compare its complete outputs with captured AFTER snapshots, but agreement does not by itself prove historical snapshot atomicity, correct alias reconstruction, or all original semantics.
- **Historical live replay:** not implemented in this new executor. `historical-dispatch` requests fail closed until complete original device/float/synchronization/atomicity evidence has an implemented verifier. The existing strict logical capture validator is unchanged.

The first frozen run may use `expected_source:none` and save all canonical output allocations as a prospective baseline. It does not pass a correctness check vacuously. Baseline-versus-baseline and baseline-versus-candidate comparisons are separate subsequent operations over the complete output files.

## Exact target preparation

The selected module `cc62764061c44f6a`, variant `095931037888515a`, digest `b9af52c0ba07f64eb8b0ca2905c432d094999fdf669aa95182ff7754b510e97b` is supported for CPU preparation. Its resources fit the executor's native data shapes, including RG16F, RG32F, R16_UNORM array and RGBA16F inputs, core anisotropic samplers, push descriptors and null buffer entries.

Preparation recomputes `tools/physical_span_contract.py` directly from the exact shader, original page table and true original VkBuffer extents. It checks the dominated 4/16-byte load guards, complete 4096-byte owner coverage, table-window bounds and finite shader control flow. It never trusts an existing certificate's boolean fields. Queried replay addresses must preserve the required alignment, full original within-buffer contiguity, canonical offsets and nonwrapping table/data extents. No allocation is padded with invented neighboring bytes.

The `normalize` command accepts the supported raw capture components only through a recomputed backing-independence proof. Every bind edge is resolved through its Vulkan memory lifetime or original external backing identity/offset. The complete source catalog is checked for both cross-object and internal sparse alias overlaps, including images. Only a graph with zero overlaps can recreate each original buffer as a separate owned contiguous allocation. Descriptor aliases of the same original buffer remain shared. Aliasing captures require a future general canonicalization path and fail this normalizer.

External leases are validated at the common event endpoint with their complete history. Differing global acquired-generation counters do not create separate backing identities; an overlapping invalidation fails the raw checker. Equal guest addresses or equal bytes never establish an alias. The normalizer retains all source manifests, producer receipt and payloads privately, verifies their hashes and exact recording associations, and preserves exact image views/samplers and buffer descriptor ranges.

The resulting manifest is an **owned frozen diagnostic** with no expected baseline initially. Source CPU atomicity, the original complete device/float feature state and submitted descriptor-word equivalence remain unestablished. No raw producer `replay_complete` boolean can authorize execution. Parent review of the exact source, binary, finite input graph and plan is still required before a GPU lease.

## Normalized producer schema

The root identifier is `open-ocarina.physical-compute-replay/v1`. Unknown root fields and duplicate JSON keys are rejected. The complete independently authored fixture generated by the `fixture` command is an executable schema example. Regression fixtures exercise framing only and are never submitted to Vulkan.

| Field | Contract |
|---|---|
| `kind`, `mode`, `expected_source` | Exact target or authored synthetic; owned frozen diagnostic; explicit distinction between no reference, captured AFTER, owned baseline and authored synthetic reference. |
| `event` | Selected event ID, recording/occurrence identities and ordered initial/final epochs. These are cross-checked with producer evidence, not inferred from file proximity. |
| `pipeline` | Hash-pinned SPIR-V, literal compute workgroup, complete push range, reviewed physical-root offset, explicit device IDs and synthetic-source provenance where applicable. |
| `allocations` | Canonical allocation IDs, complete sizes, original backing lifetime/generation/interval, complete initial artifacts and complete expected artifacts when a comparison is requested. |
| `views`, `bindings` | True original VkBuffer base/size, original buffer offset, canonical allocation/offset/size, every descriptor array element and its exact range. Nulls remain null; identical bytes are not an alias certificate. |
| `physical_table` | Complete original table artifact, page model and root address, and exactly one canonical allocation/offset mapping for every nonzero entry. Unmapped entries remain zero. |
| `images`, `image_views`, `samplers` | Owned backing identity, format/dimensions, all view-visible color subresources, exact view range/swizzle, and exact 16 core sampler words. Extension samplers, compressed formats and image/buffer memory aliases are not implemented. |
| `evidence` | Hashed identity-bound coverage, committed backing edges, historical-writer scope, temporal-input and selected-dispatch position/completion records. Their declarations are cross-checked, not independently proven truthful. |

The authored fixture uses direct canonical backing edges. Real-target inputs must use the independently checked `disjoint-original-objects-v1` normalization described above. The normalized owned epoch is distinct from the original host's global generation counter. Bind gaps, unknown resource lifetimes, internal/cross-object source overlaps, conflicting original owner extents and unsupported image/buffer aliases fail closed. Distinct mirrors and shadows remain separate objects even when they refer to the same guest interval or contain equal bytes.

Sampled image payloads are normalized into complete tightly packed subresources. Every exposed mip/layer must exist. Supported formats are explicit uncompressed color formats; they include the observed target's four formats and the authored fixture's R32_UINT. View dimensional/numeric type is checked against the reviewed target or fixture. Anisotropy is enabled and checked against device limits, never silently clamped. Unknown sampler extensions, depth/stencil, multisampling, 3D images and unresolved memory aliases fail rather than lose semantics.

The physical table/root rewrite is the only permitted BDA relocation model. Canonical buffer addresses are queried only after allocation and binding. Each table entry receives the corresponding new allocation address plus its validated offset; the pushed root receives the new table address. Other embedded physical pointers are unsupported. The target span checker supplies the separate whole-access proof; mapped-page closure alone is insufficient.

## CPU-only development commands

For a real capture, first normalize the complete raw component directory with explicit private shader and frozen launch receipt paths. Device IDs select the intended owned diagnostic device; they do not claim that the original full feature chain was captured:

```sh
python3 tools/physical_replay.py normalize local/capture \
  --output local/physical/normalized-001 --spirv local/target.spv \
  --receipt local/launch.json --vendor-id 0x8086 --device-id 0x56a0
```

The authored fixture workflow is:

```sh
python3 tools/physical_replay.py fixture --output local/physical/fixture-001
python3 tools/physical_replay.py prepare local/physical/fixture-001 \
  --output local/physical/prepared-001
python3 tools/physical_replay.py build --output local/physical/native-001
local/physical/native-001 local/physical/prepared-001/plan.bin \
  local/physical/prepared-001 --check-only
python3 -m unittest discover -s tests -p test_physical_replay.py -v
```

`fixture` only invokes the GLSL compiler. The independently authored program performs 64 bounded integer reads through a physical root/page, reads and writes overlapping descriptors of the same output allocation, and reads a sampled integer image. The CPU reference preserves every byte outside the written 256-byte region of the 4096-byte output allocation. No original instructions or assets are included.

`prepare` structurally validates SPIR-V and, for the synthetic mode, recompiles the exact tracked fixture source and verifies the module digest. It freezes every artifact into new private read-only files while rechecking its full hash. It writes a native control frame plus metadata. Control-frame identity binds the source manifest and complete allocation extents. Preparation does not create a Vulkan instance.

The native helper is built with `-O2 -DNDEBUG -Wall -Wextra -Werror`; no shader optimization-disable flag exists. Build output paths must be new and private, so an existing frozen binary is not silently overwritten. The checker path returns before `vkCreateInstance` and does not execute a shader.

## Explicit execution boundary

Execution is a separate command and requires a reviewed helper SHA-256, exact prepared-plan SHA-256, a selected driver, a new output directory and an external GPU lease. A 16/8-lane policy needs an additional authorization derived from that exact plan and policy; it does not inherit permission from a 32-lane baseline. The exact digests and lease are operational review boundaries, not a security sandbox against an operator who can modify the program itself.

Before starting its child, the wrapper rehashes the frozen artifacts and checks the control-frame identity. Each trial creates fresh allocations initialized from the same snapshots. The helper enables robust buffers/robustBufferAccess2, physical addresses, required scalar types and the inspected push-descriptor/subgroup features. It checks actual compiled subgroup size before submitting one dispatch. The fence wait is finite; the wrapper additionally bounds the process wall time and signals only that process group on timeout.

Sampled images are uploaded into owned Vulkan images and transitioned to a read-only layout. All canonical buffers and the rewritten table are coherent host-visible allocations. Host-write, transfer and compute barriers order the owned preparation and output readback. Complete buffer outputs are saved, including padding and nominally read-only allocations; integer counters and indirect arguments receive no implicit tolerance.

`compare` rehashes the saved complete outputs, checks frozen input identity, allocation extents, native implementation and driver/device identity, then compares every canonical allocation byte. It cannot prove historical live equivalence from an owned diagnostic match. No image-quality, frame-rate or game-rendering improvement follows from this foundation alone.

## Runtime observations

The frozen optimized helper completed one authored synthetic dispatch on Intel Arc A770 (`8086:56a0`) with actual subgroup size 32. Both complete 4096-byte allocations matched independently reconstructed CPU oracles, including untouched padding. This exercises physical table/root relocation, shared buffer descriptors, a sampled image, one dispatch and complete readback together.

The same helper then completed one exact-target width-32 frozen diagnostic from the independently checked provenance capture. All 59 owned buffer allocations were saved and rehashed, totaling 1,163,919,360 bytes. Only the writable allocation changed from the frozen initial inputs: 1005 bytes changed, and its entire 4 MiB matched captured AFTER exactly. Binding 6 exposes 40,960 bytes as 128 records of 320 bytes; observed differences lie inside records 0–23, and all bytes outside that descriptor range remain unchanged. Unchanged bytes may also have been written with their initial values; this is a byte comparison rather than complete write instrumentation.

Whole-graph captured AFTER agreement was 56 of 59 allocations. Three live-import input allocations remained equal to their frozen initial bytes but differed from captured AFTER. CPU/temporal capture uncertainty and semantic reconstruction gaps remain separate possible explanations. The width-32 repeat establishes repeatability for the tested owned graph. These observations establish no frame-rate, rendering-quality or historical live-equivalence result.

The original width-32 baseline was repeated once from fresh allocations initialized from the same frozen artifacts. After complete byte agreement was verified, one width-16 and one width-8 trial ran in sequence. Each alternative was checked before proceeding. All compared the entire 59-allocation graph, including nominally read-only bytes and padding:

| Case | Requested / actual subgroup size | Dispatches | Complete allocation comparison with baseline |
|---|---|---|---|
| Original baseline | 32 / 32 | 1 | Reference output retained |
| Original repeat | 32 / 32 | 1 | All 59 allocations, 1,163,919,360 bytes match |
| Alternative | 16 / 16 | 1 | All 59 allocations, 1,163,919,360 bytes match |
| Alternative | 8 / 8 | 1 | All 59 allocations, 1,163,919,360 bytes match |

Width 16 and width 8 were each tested once. The result supports exact byte equality for this fixed graph; it does not establish general subgroup equivalence for other dispatches, captures or shader inputs. No GPU execution time, speedup, game frame rate or rendering-quality result was measured.

All five invocations, including the authored fixture, used native helper SHA-256 `105a88a8e21fd2151964a57f6b6d8a8e8b5a2aaf5341c2411c551019edf26a7c`. The original baseline and repeat used plan SHA-256 `36104f021b9f652f194230d3f9ccea0037c4a2af7f6df8c1cd9975d0786db4d3`; alternatives preserve the same source-manifest identity and complete input artifacts. The sanitized [observation record](../research/physical-replay-observation.json) pins each source, helper, plan, driver, result and comparison artifact, and lists complete output-allocation hashes. Raw inputs, outputs and logs remain private. GPU leases were released after authorized work; further dispatches require separate authorization.

## New cache-policy driver followup

A separate isolated library, SHA-256 `e5a1305ea3b423a18fccbb945aaedd32f8add303926fcc39276ee01032cd456f`, passed startup property/UUID checks and a fresh authored fixture. It then repeated the original compute graph at actual widths 32, 32, 16 and 8. All three within-driver comparisons matched every complete allocation. CPU-only cross-driver checks also matched all 59 complete file hashes against the historical baseline. The old-driver evidence above remains unchanged and is not relabeled as testing the new library.

See the [cache-policy build and validation](mesa-cache-policy.md#isolated-build-and-bounded-runtime-observations) and its [sanitized observation record](../research/mesa-cache-policy-observation.json). This followup uses the same fixed owned graph and supplies no renderer, image-quality or FPS result.
