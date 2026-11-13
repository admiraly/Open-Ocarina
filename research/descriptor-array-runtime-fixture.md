# Changed-store oracle for typed descriptor-array pointers

Date: 2026-11-13. Status: CPU fixtures prepared; value and pointer SPIR-V variants assembled and validated. **No Vulkan device, pipeline or dispatch was created for these new fixtures. No runtime executor is implemented yet.** The [compile-only observation](descriptor-array-compile-observation.json) concerns different modules whose identical writebacks and XOR reduction cannot establish store-root correctness.

The new `tools/descriptor_array_runtime_fixture.py` authors a new entry point, reusing the frozen, independently authored lookup/pointer-helper generator only after checking its exact source hash. The original frozen generator, compile host, preparation, earlier failed attempt and successful compile logs remain unchanged. [Sanitized preparation metadata](descriptor-array-runtime-fixture-preparation.json) records the new modules, two case plans, all input/golden hashes, feature policy and ownership proof.

## Small fixture with observable writes

One 64-invocation workgroup executes 704 bounded queries, writes 37 U32x4 result records per lane and operates on ten owned allocations totaling 74,458 bytes, including all guards. The profile retains two descriptor-array bindings of two elements each, three metadata/result bindings, three typed aliases per descriptor root and an exact 48-byte push block.

| Descriptor element | Meaning |
|---|---|
| A[0] | Separate 10-byte snapshot; ordinary size word zero and an unaligned guest base give an 8-byte scalar/vector2 claim but no vector4 claim |
| B[0] | 8 KiB ordinary view into the shared allocation; supplies the later vector4 hit |
| B[1] | Separate 32-byte view; unaligned ordinary size word zero claims only its five-byte alignment prefix |
| A[1] | 8 KiB table entry with size word zero, at a different guest base but referencing the **same VkBuffer, offset and range** as B[0] |

Even lanes write through B[0]; odd lanes write through A[1]. Actual descriptor binding and array-element selection therefore vary within each subgroup. Every lane writes new scalar, vector2 and vector4 component values to three distinct spans. For example, lane zero in the follow-off case writes `A1000000`, `A2000000/A2100000` and `A4000000/A4100000/A4200000/A4300000`. It then reads those values through the reciprocal global alias and directly through both descriptor roots. Equal initial bytes in copied allocations are insufficient: A[1] and B[0] must share one buffer and offset.

Separate page-table mappings deliberately disagree with the storage views' backing allocation. Correct hits continue to use `StorageBuffer`, and the entire separately mapped allocation must remain unchanged. Physical misses address another owned allocation. The follow-off plan must leave it unchanged; the follow-on plan must write distinct B-prefixed values and read them back. The two plans are separate single-use cases, with at most one dispatch each.

Shared read-only edge queries cover the short first view/later vector hit, ordinary zero-size prefix versus table-zero full range, typed flooring of a descriptor hit at guest-page offset 4092, physical page-crossing rejection and an unmapped/out-of-window operand. The physical scalar read at offset 4092 fits; its vector2/vector4 counterparts return zero. All original operands are four-byte aligned, including those not aligned to their vector width.

## Oracle and ownership

The expected-memory oracle never calls the interval selector. It applies explicit known allocation offsets, literal changed-value recipes and the inspected edge-case outcomes. Separate tests check the generated interval records select the expected root/element for every lane and width. This separation avoids comparing the transformed selector only with itself.

Lane `n` owns bytes beginning at `256 + 64n`. Its mutable spans are `[0,4)`, `[8,16)` and `[16,32)` within that region. The tool enumerates every written byte, verifies uniqueness and checks each mutable read against its lane owner. Shared edge reads are enumerated separately and checked against all writes; protected page-tail bytes at offsets 4080–4095 are untouched. Output ranges are `[37n,37(n+1))` records. Two invocation-local buffer memory barriers order stores before alias reads; no cross-invocation synchronization or race is required by this graph.

Every result component is compared directly; no XOR/checksum reduction substitutes for output comparison. Full readback comparison covers all ten complete allocations, including untouched metadata, both immutable snapshots, separately mapped bytes, result fields, a 64-byte unwritten result tail and 64-byte prefix/suffix guards on every buffer. One-byte mutations in output, guards, shared stores, the separately mapped allocation, metadata or page table fail CPU verification.

Physical device addresses are deliberately unknown during CPU preparation. The page-table and push-root relocation lists refer only to named owned allocations. A future host patches those addresses from its own allocation map, freezes the complete uploaded/expected bytes before dispatch, and compares the entire relocated table after readback. `verify_readback` derives that expected table from host-owned addresses, not GPU output. The placeholder table's public golden hash does not certify a later relocated table by itself.

## Native helper proposal: not implemented or approved

A new separately reviewed executor is needed; the frozen compile-only host must remain unchanged. Its CPU validation path must reject wrong module/plan/source hashes, descriptor roots/ranges, relocations, aliases, query counts, ownership recipes or dispatch dimensions before `vkCreateInstance`. After a future device grant it should:

1. Recheck exact E5 library/ICD hashes and the 13-option `native-safe` policy with MAX absent, disk caching disabled and a fresh private cache. Query and enable both pointer features, int64/BDA, robust buffers/robustness2 and subgroup controls; unsupported features stop the case.
2. Require actual subgroup 32/full subgroups, a 64-byte descriptor offset compatible with the device's storage-buffer offset alignment, and payload addresses aligned to 16 bytes. Allocate and initialize exactly the named graph. Bind all four descriptor elements; preserve the B[0]/A[1] identity rather than copying one view.
3. Validate and apply only bounded owned-address relocations. Freeze complete relocated table/push inputs, perform required host flushes and upload-to-compute barriers, then issue exactly one `vkCmdDispatch(1,1,1)`.
4. Insert compute-to-readback synchronization and wait on one fence for at most two seconds, under a parent process deadline of 30 seconds. Capture only bounded output; do not use child file-size/address-space limits. On timeout or error, stop without an idle wait or automatic retry.
5. Invalidate/read all allocations and compare every byte with the independently generated goldens, including actual typed-length diagnostics. Reject any unexpected write, mismatch, missing allocation, wrong count or damaged guard. Release the GPU lease at terminal completion.

The on/off cases require separate reviewed execution admission. Passing one cannot authorize another module, plan or runtime workload. Native descriptor classification, captured renderer records, candidate priority, atomics/subword helpers and the second guest window remain outside this small fixture. Integer wrap cases are covered by the earlier CPU arithmetic tests, not large runtime allocations here. GPU alias behavior and full execution correctness remain unknown until a future executor is implemented, reviewed and run.

## Frozen CPU handoff

Private preparation: `local/descriptor-array-runtime-fixture/frozen-review`.

| Artifact | SHA-256 |
|---|---|
| New fixture tool | `658c8969cd0851ec6196441ce93b9206df9ceea6d9d8ab587ad10aa1bc362e25` |
| Generated GLSL | `576f93dcb7d7ea9db78c7f5953d3de2c627d32206263b1fc538c8072c3de9e7b` |
| New pointer module | `bf1c2e5a1379ced5b8c068b0b47498384322255bf2cab235c31ce9cc0f027cdc` |
| New value counterpart | `aa9816b41e458151632308a64d9df58aee0fcc5df287b70f47e6b067b1c76531` |
| Follow-off case plan | `0ba9c2f84a756e3ba7d441825084fc3cd4476c078184c17c5e4ac81a81131110` |
| Follow-on case plan | `805867aeab27dcbb9a046098e29556231eb3d1778428ade16691587147a08d9d` |

Eight dedicated standard-library tests pass. Both new modules pass SPIR-V validation and the existing exact profile reflection for compute/local64, zero shared memory, 48-byte push layout, typed alias strides, descriptor counts, six runtime typed-length instructions and three pointer-return helpers in the pointer variant. CPU smoke comparison covers all 74,458 bytes per case. This is test-fixture evidence, not a native runtime certificate.

```sh
python3 -m unittest discover -s tests -p test_descriptor_array_runtime_fixture.py -v
python3 tools/descriptor_array_runtime_fixture.py \
  --output local/descriptor-array-runtime-fixture/repro
```

Python 3.11+ suffices for the tests. Generation also needs glslang 16.2.0 and SPIRV-Tools v2026.1 assembler/disassembler/validator, or separately reviewed compatible versions. This command prepares and validates synthetic files only; it does not load Vulkan.
