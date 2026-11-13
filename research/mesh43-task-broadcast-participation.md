# Conditional task broadcast-batch participation

**The live uniformity gate remains open.** No immutable owner/alias/order certificate exists for the actual task inputs. Same uniform addresses and shader-readonly loads do not prove that lanes observe equal values while external or atomic writes may alias them. The linked 43ec mesh has known device-atomic writes. The result below is conditional on equal, stable external input bytes/ranges and defined execution for all32 original invocations; it is not a live-workload convergence or race-freedom proof.

Under those conditions, all32 task invocations reach each dynamic instance of the32-broadcast batch together, or the entire workgroup uniformly skips the enclosing loop. [Exact proof/fixture metadata](mesh43-task-broadcast-participation.json) pins the source, private checks and synthetic artifacts. The [earlier family audit](mesh43-subgroup-participation-audit.md) remains an unchanged checkpoint; this narrower result does not remove the mesh's divergent participation or writable-state blockers.

## Exact source and control proof

The source is TaskEXT stage0/NVN6/Vulkan64 of program `43ec86f35fd937b9`, variant `bc2d248909cf8b40`, SHA-256 `5e7d8f02e2dbd5ae1cd625d42366bc532dcdf7d71bb56ac3bc303b5e9655d086`. It has local size32×1×1. The candidate pattern is block `%1336`'s32 fixed-source broadcasts0–31 of one u32 SSA operand; values of that operand may differ among lanes.

1. The outer `WorkgroupId.x<=4095` test is uniform. Its false arm skips the loop for every invocation. The second entry test compares an unsigned value with zero using `>=`, so it is tautologically true under defined value behavior.
2. The invariant limit has a106-definition backward cone. Its roots are WorkgroupId and uniform-address external descriptor reads, with three calls to the same bounded readonly descriptor helper. No subgroup lane, local ID or subgroup mask enters this cone. Equal/stable values are an external proof condition, not a conclusion from those addresses.
3. The iteration counter starts at0 and increments by1 on continuation. Its invariant limit is derived from a clamped input count; the break tests the next iteration against that limit. Per-item results and lane IDs do not enter this break condition. Thus the break agrees across lanes by induction, subject to the input condition.
4. Each invocation's private safety guard starts at8192 and has exactly one decrement store, on the common continuation. Its signed nonnegative backedge test therefore agrees across lanes. It bounds loop-body visits by8193; the iteration increment cannot wrap. The readonly helper's only writes affect function-local counters, and its descriptor walk is bounded by literal3.
5. All680 blocks traversed from the per-item selection to the batch reconverge at `%1336`, without an intervening main-function cycle, return, emission or termination. Calls target the bounded readonly helper. Item rejection may skip work inside this region but does not skip the batch. The batch's outgoing break is the uniform condition above; otherwise all invocations reach the same continuation and private-guard test.

This establishes a common dynamic batch schedule under the stated conditions. It does not establish physical TaskEXT subgroup-lane mapping, safe arbitrary memory inputs, task-payload race freedom or complete 43ec shader equivalence. Those are separate gates.

## Independent CPU-prepared TaskEXT fixture

[`tools/task_broadcast_batch_fixture.py`](../tools/task_broadcast_batch_fixture.py) independently authors two small TaskEXT modules. It accepts no game shader inputs and calls only SPIR-V assembly/validation tools. Both modules use32 invocations,128 bytes of shared scratch, two uniform workgroup barriers and one4096-byte output SSBO. They contain no loop, divergent branch, images, input descriptors or task payload and emit zero mesh workgroups.

Each invocation publishes `17*localX+3` to its own scratch slot. After the first barrier, the native module broadcasts that value from each source lane0–31; the software module instead reads each corresponding workgroup slot. Every invocation writes its complete32-word row, then reaches the second barrier before task emission. Unique scratch writers and disjoint output rows avoid the overlapping initializer writes present in the game source.

The complete CPU oracle is `row[destination][source]=3+17*source`. The native interpretation requires required subgroup32 and source-lane index equal to LocalInvocationId.x. **Actual physical TaskEXT mapping remains unproved**; the software fixture does not establish it. No Vulkan instance, device, pipeline or shader dispatch was created in this preparation. A future GPU test needs a separately reviewed bounded task-stage executor and explicit grant.

| Frozen artifact | SHA-256 |
|---|---|
| Fixture generator | `fdcfafa7538d1edd21491ef00a201a24519285087fb64a37f7948cdbb533a110` |
| Native TaskEXT module | `e87b4938db885f8c0897f6d7ca87ee65c4fb3c7a14c4b17ccc3f0208a526c4e6` |
| Shared-memory TaskEXT module | `b3a879dcf54adc2dc5de3c4b2ed4012fe524d7101cdc3ef4f215e06b631e2d26` |
| Complete4096-byte CPU oracle | `c180b72977336b00658e8861e6612685c56e6d5231b59cf7973dbd33de0a243b` |
| Private fixture report | `92bd8021ac4b0a67f066dcc6bbc79a147babcedbc1cf3de1f4af9bdce3f871d8` |
| Private participation proof | `49ba3ab6c307d05eec3ac79fc756a5f78d3b090e44d5c2f7e4b373ceaa18de2f` |

Artifacts are private at `local/mesh43-task-broadcast-proof-01/independent-fixtures`. Both assembled modules pass `spirv-val --target-env vulkan1.3`; compiled disassembly confirms32 native broadcasts versus zero in the shared variant, two barriers, no conditional branches or loops and one task emission each. Three standard-library tests check the complete oracle, source contracts and independent shared publication/read scheduling for widths8/16/32 in both wave orders. No test or artifact expands target runtime admission.

Next work is to close actual input immutability/alias/order against the known mesh atomic writers and establish pinned-driver TaskEXT mapping. No target43ec lowering has been emitted. General race freedom, historical draw reproduction, universal shader equivalence, final rendering and FPS remain unproven.
