# Owned typed descriptor-array runtime executor

The new executor is implemented and CPU-validated for the two exact [changed-store fixture modules](../research/descriptor-array-runtime-fixture.md). **No Vulkan instance, device, pipeline or dispatch was created during CPU preparation.** Four separately reviewed, single-use plans cover value/pointer helpers with follow-stores disabled/enabled. [Sanitized preparation evidence](../research/descriptor-array-runtime-executor-preparation.json) records every source, binary, input, golden, ABI and plan hash. A subsequent, separately authorized [four-case runtime checkpoint](../research/descriptor-array-runtime-observation.json) passed the complete synthetic oracle on E5.

`tools/descriptor_array_runtime.py` prepares the graph and performs independent whole-buffer comparison. `src/probes/descriptor_array_runtime.c` is a narrow native executor rather than an arbitrary shader runner. It accepts only the two frozen runtime shader digests. Its native `--validate` mode verifies all bytes and returns before `vkCreateInstance`.

## Admission and CPU validation

The wrapper pins its own source, the native C source and every non-standard-library import: the frozen fixture generator, pointer generator and profile reflector. It checks the original case plan, known module digest, complete descriptor/type reflection and all ten initial/golden files. The C probe independently SHA-256 checks artifacts using OpenSSL libcrypto, then reconstructs the fixture's initial patterns, metadata, typed interval records, all 704 queries, push block, changed stores and all 37 result records per lane. A substituted golden does not bypass that check even when its artifact hash is changed to match.

The native ABI admits exactly ten guarded allocations totaling 74,458 bytes, seven descriptor elements, seven page-table relocations and an exact 48-byte push block. B[0] and A[1] are both bound to allocation zero's **same VkBuffer**, offset 64 and range 8192. The short and zero-prefix views retain ranges 10 and 32. Metadata/result ranges and every relocation target are fixed. Known queries and intervals fix the 64-lane ownership recipe; arbitrary input addresses or modified table entries are refused before the instance boundary.

CPU preparation builds an optimized native helper and a second helper with `vkCreateInstance` linker-wrapped to exit 99. All four plans pass both native validators without hitting the trap. Rehashed mutations of module bytes, aliases, descriptor ranges, owned relocations, input guards, queries, table bytes and golden results are rejected. A separate CPU test forges host upload/readback files and verifies that changing one byte in output, guards, metadata, shared stores or a relocated table fails the independent comparator.

The native host uses `-std=c11 -O2 -DNDEBUG -Wall -Wextra -Werror`. Python requires 3.11+ and only the standard library. Native preparation additionally needs a C compiler, Vulkan development headers/loader, OpenSSL libcrypto development files, pkg-config and SPIR-V validation tools. Optional native CPU tests also assemble the independently authored fixtures; they need the pinned open-source E5 build and the documented glslang/SPIRV-Tools versions, not game files.

## Reviewed execution boundary

Execution requires a separate root review, exclusive device grant and the exact prepared-plan SHA. The wrapper rechecks all sources, helper/input files, reflection and E5 library/ICD hashes before launching anything. A wrapper claim and native control-file claim both use exclusive file creation; one plan cannot be dispatched twice, including after failure.

The native path requires the A770, exact E5 bytes, all 13 `native-safe` compiler controls, MAX absent, an explicit ICD, disk caching disabled and a fresh private cache. It queries and enables both storage variable pointers and nonuniform storage-buffer array indexing, int64, buffer device address, robust buffers/robustness2 and subgroup controls. Unsupported requirements stop. It checks descriptor-offset/range limits, allocates host-visible coherent memory with device-address allocation flags, and checks payload address alignment. Actual pipeline executable properties must identify compute at subgroup 32 before dispatch.

Only named owned allocations can be inserted into page-table entries and the push root. The host freezes all complete relocated uploads, expected buffers and push bytes before submission. B[0]/A[1] retain their shared buffer identity. Explicit host flushes and host-to-compute/compute-to-host barriers cover mapped inputs and readback. The only submission contains one `vkCmdDispatch(1,1,1)`, followed by a fence wait capped at two seconds. Fence failure exits without an idle wait. A native 25-second alarm and parent 30-second pipe collector bound the process; output is capped at 64 MiB combined. No child file-size/address-space limits are applied.

After successful fence completion, the native probe writes and compares every byte of all ten buffers, including guards, immutable inputs, 9,472 output words and the unwritten result tail. The Python wrapper independently reconstructs relocated uploads/goldens from the host-owned addresses, checks those frozen files and push contents, and calls the separate fixture oracle's full readback comparison. It refuses missing/overlapping allocations, wrong actual width/features, incomplete files, altered uploads or any mismatch. Sources and inputs are rechecked after terminal success or failure; there is no automatic retry or tolerant comparison.

This executor covers a small synthetic graph. It is not an admission certificate for native renderer records, arbitrary pointers, candidate priority, atomics, subword helpers or the second guest window. CPU preparation alone does not establish GPU alias behavior, actual typed lengths or execution correctness; the subsequent runtime evidence below is limited to its exact fixture graph and frozen configuration. There is no FPS result here.

## Authorized synthetic runtime observation

On 2026-11-13, the follow-off/value case executed after a separate exclusive GPU grant. The root reviewed its full result before authorizing the remaining follow-off/pointer, follow-on/value and follow-on/pointer cases in that order. Each issued exactly one 1×1×1 dispatch with local size 64 and actual compute subgroup size 32. Both pointer features and the required robustness/device-address/subgroup features were enabled. All four processes returned successfully and were reaped; there were no retries or source changes. The GPU lease was released after terminal completion.

Every case matched both the native CPU goldens and the independent Python comparison over all ten complete allocations, 74,458 bytes, all 9,472 output words, 1,280 guard bytes and the unwritten result tail. Typed-length diagnostics, short/later vector selection, ordinary/table zero-word behavior, changed store roots, reciprocal aliases, descriptor snapshots mapped elsewhere and page-crossing miss policy matched the fixture oracle. With follow-stores off, the physical allocation stayed unchanged; with it enabled, the unique B-prefixed stores matched every expected byte.

For each follow policy, value and pointer variants produced identical bytes in all nine non-address-bearing allocations, including every output word. Page-table and push-root addresses were first validated against each process's owned buffer map, then replaced with deterministic allocation-index/offset references for cross-case comparison. All four canonical tables matched; canonical pushes matched within each follow policy. No cross-process raw-address equality is required or used as an equivalence argument.

The [sanitized observation](../research/descriptor-array-runtime-observation.json) records all exact modules, source/helper/plan/driver hashes, enabled features, full buffer hashes, terminal results and canonical comparisons. This is synthetic runtime correctness evidence for this graph, not live renderer reconstruction, general address-helper equivalence, original-shader validation, a rendering comparison or a GPU timing result.

## Frozen CPU handoff

Private preparation: `local/descriptor-array-runtime-executor/final-review`.

| Artifact | SHA-256 |
|---|---|
| Wrapper | `c9724ab9a161cba7ab6f462753fd8632f764dbe57134227a09d1f278620f2a69` |
| Native C source | `9684cc90c65c9178516d331a994de15e0478b9cc71aaa4790230aeab5ea34490` |
| Optimized helper | `1b77dac8f338b9ff694324f08bbc2dbf9300375fc73ca1b600b1dd1dbd3fc648` |
| Follow-off/value plan | `d65fce0ce34236c7db7b4de5c55038b470a4430c13cadf0ab505ee88ee306bf0` |
| Follow-off/pointer plan | `e639507c2dfd231b6e736422cbf5790227e915a4488a84f6f4fe5abc528236a2` |
| Follow-on/value plan | `a3079768536cac2e51443deb03e483e7f9ef1911f40a54e93ad259c7281be9d2` |
| Follow-on/pointer plan | `dfed67d2d21b09652b03cef6732749fd692cf9ede255c3abbff8cf11cc0e50a4` |

CPU preparation only:

```sh
python3 tools/descriptor_array_runtime.py prepare \
  --fixtures local/descriptor-array-runtime-fixture/frozen-review \
  --driver "$E5_ICD_JSON" \
  --output local/descriptor-array-runtime-executor/new-review
```

Standard-library boundary tests and optional CPU-only native/linker-trap tests:

```sh
python3 -m unittest discover -s tests -p test_descriptor_array_runtime.py -v
OPEN_OCARINA_ARRAY_RUNTIME_NATIVE_TESTS=1 \
  python3 -m unittest discover -s tests -p test_descriptor_array_runtime.py -v
```

Nine dedicated tests, including all five optional native CPU tests, passed locally. At CPU handoff no execution command had been issued; the separately authorized four-case observation above followed later. All earlier frozen generators, compile hosts, runtime modules, fixture case plans and failed/successful compile observations are preserved.
