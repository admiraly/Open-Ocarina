# Common compile-only layout for original, static and array loads

The new adapter prepares three exact modules under one common descriptor layout: original `b9af52…`, static eight-way interval `d51483…`, and [nonuniform array interval](../research/compute-interval-array-lowering.md) `7c6322…`. **No Vulkan instance, device, pipeline or dispatch was created during this preparation.** [Sanitized CPU preparation](../research/compute-interval-array-compile-preparation.json) records all hashes, repeated source proofs, native checks and limits. No new replay executor or device admission is implemented.

`tools/compute_interval_array_compile.py` reads the immutable `02` preparation through its hash-pinned `9a5e38…` adapter, validates both original controls and their complete input/proof inventories, and derives a new private compile-only native helper from the exact `9b67fb…` source. The original `02` source/helper/plans, all earlier generated shaders and all observations remain unchanged.

## Same union and same inputs

All three controls have the same 23 descriptor elements: the original fourteen, one read-only binding-12 record, and eight binding-13 aliases. The union contains seventeen storage descriptors, two UBO descriptors and four combined image/sampler descriptors. Two typed declarations at binding 13 describe those same eight descriptors; they do not multiply descriptor allocation.

Each alias copies its original descriptor's allocation/VkBuffer owner, offset, range, null state and auxiliary fields literally. Elements 0–4 correspond to binding 2–6 element zero; elements 5–7 correspond to binding 7 elements 0–2. The two null table entries remain null. Native CPU preflight compares each alias with its original descriptor. Equal copied payload bytes cannot replace identity, and the writable binding-6 view remains the source for element 4.

No memory owner is added: the original 59 owners, mappings, views, sampled inputs and guarded record owner are preserved. Every original pushed byte is preserved, including the actual 88-byte push range and page-root offset 48. Shader reflection reaches 56 bytes, as before. The original and static shaders may leave union bindings unused; that is intentional so all three pipeline layouts and enabled features are identical.

Preparation re-disassembles actual binaries and repeats static and array preservation/finite-search checks. Main, its direct stores, the five untouched functions and both physical MISS suffixes remain word-identical under the applicable transformation proofs. The array route/access/load IDs carry `NonUniform`, typed stride/floor/base rules are checked, and the `49da4b…` interval record is unchanged. These remain owned frozen-graph proofs, not live renderer capture or a replay admission.

## New native boundary

Twenty-eight counted source edits produce a complete private C diff. The adapter physically removes buffer helpers, sampled-input creation and the entire allocation/queue/dispatch tail. The resulting source and optimized binary have no resource-allocation, queue-retrieval, command-buffer, submission or idle-wait APIs. Device creation still needs a queue-family/create declaration; no queue is retrieved or used.

An execution-mode argument is rejected immediately. `--check-only` parses and checks the full graph and returns before `vkCreateInstance`. The only remaining Vulkan mode requires `compile_only` before that instance boundary and creates one optimized pipeline, checks actual compute subgroup 32 and preserves every structured executable-statistics field. It never binds or allocates the captured resources. The Python adapter exposes preparation only; it does not invoke this Vulkan mode.

Before logical-device or pipeline creation, the native helper requires at least 23 push descriptors, seventeen per-stage/set storage slots, two UBOs, four sampled-image slots, four samplers and 23 per-stage resources. It retains record range/alignment and null-descriptor checks and the original int16/int64, BDA, robust buffers/robustness2, float controls and subgroup feature gates. It additionally queries and enables `shaderStorageBufferArrayNonUniformIndexing` for **all three** pipelines. It enables no variable-pointer feature. Unsupported statistics support or any required feature stops before logical-device creation.

The compiler policy remains the `02` physical two-control policy: `HS_SPILL_BATCH=1` and `shader_spilling_rate=1`, with `HS_PUSH_SHADOW` and MAX absent. The new profile records all thirteen selected controls explicitly and requires a fresh private cache per case. A future launcher must clear the same compiler/loader overrides as the reviewed physical policy and pin the exact E5 library/ICD again. The renderer's shadow-enabled native-safe policy is a different profile and is not substituted here.

## CPU evidence and frozen handoff

Eight dedicated tests pass. Independently authored framing controls test all three module headers without submitting a shader. A `vkCreateInstance` linker trap confirms the CPU path and execution-mode rejection never reach the instance boundary. Alias owner/range/null/element mutations are refused. Thirteen pure-C capacity/feature mutations cover insufficient descriptor categories, push space, missing null descriptors and missing nonuniform indexing without any Vulkan call. A sixteen-field fake statistics set verifies that names, descriptions, formats and every value survive serialization. `nm` confirms the compiled helper has no resource/queue/dispatch imports.

The real preparation independently validates all three actual module digests and reflections, complete preserved input data, unchanged source proofs and native CPU contracts. Positive synthetic framing tests are deliberately not a GPU shader admission. No actual capacity values or compiler statistics have been queried in this lane.

Private preparation: `local/compute-interval-array-compile/final-review`.

| Artifact | SHA-256 |
|---|---|
| New adapter | `e25f93a7ddba3cfc2a2a5887beabbe2de030f8c5843574b2171945377b94d5a3` |
| Derived C | `156c2b971ada7c76a6f3c54ec95663e1167a8f50c6a31273715acbb35880d02c` |
| Optimized compile-only helper | `e1596b94893d5fd45f49b587a047c8ccc52615fe792418734cd93dc4a985d7a7` |
| Complete native C diff | `4b55040ac2a8c414b89bec7d14ee7fd1002771b6062a6c54e07d70080bf1d5d0` |
| Common profile | `d6a343cade4fec41cc005fe3b2eb7fe1ceb333f28fda7c5ae1866a43a4983982` |
| Original control | `7d253bd336fbd3e190097a63f3776fdc6edb4c9eb4157870252b396daedb896e` |
| Static control | `d74a5dab21a3f8d0594db25997d5eb7acc63a8330f0ec4b3497407e0ccb7ef36` |
| Array control | `1c34091132517b03755f535b58cc8c5fc8f1c0f8ab7358d9c8fd4685775cc6a3` |

CPU preparation only:

```sh
python3 tools/compute_interval_array_compile.py \
  --array-bundle local/compute-interval-array-lowering/frozen-review \
  --output local/compute-interval-array-compile/new-review
python3 -m unittest discover -s tests -p test_compute_interval_array_compile.py -v
```

Preparation requires Python 3.11+, a C compiler, Vulkan development headers/loader/pkg-config, SPIRV-Tools and `nm`. Tests can reproduce the frozen base C from the public source with the documented `clang-format` settings. The common profile and individual control hashes need separate review before any device/pipeline call. Compiler statistics from this future layout would be a new observation; neither previous layout's statistics nor SPIR-V size establishes runtime speed, correctness or FPS.
