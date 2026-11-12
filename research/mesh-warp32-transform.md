# Conditional mesh ballot lowering: CPU-only prototype

Observed 2026-11-12: a digest-pinned prototype transformed the nightly `d8e59341cf4f629c` mesh module and its result passed SPIR-V validation. No Vulkan API, graphics-pipeline creation or GPU execution was performed by this tool. The result is a private **conditional candidate for compilation inspection**, with runtime execution explicitly unauthorized by its report. It is not an equivalence result or a deployable renderer patch.

[`tools/mesh_warp32_transform.py`](../tools/mesh_warp32_transform.py) defaults to structural auditing. Emitting the candidate additionally requires `--compile-only-candidate`. It accepts only the exact reviewed original SHA-256, validates a private snapshot, disassembles it, audits the complete subgroup inventory/control-flow structure, emits independently authored replacement instructions, assembles the candidate and validates it again. Binary/disassembly/log outputs remain private. The standard-library tests use independently authored mesh fixtures and do not need game files or GPU access.

## The unresolved lane-mapping condition

Vulkan provides no guaranteed mapping from subgroup-local invocation IDs to workgroup-local IDs. Its [`SubgroupLocalInvocationId` reference](https://docs.vulkan.org/refpages/latest/refpages/source/SubgroupLocalInvocationId.html) states: “There is no direct relationship between `SubgroupLocalInvocationId` and `LocalInvocationId` or `LocalInvocationIndex`.” [`LocalInvocationId`](https://docs.vulkan.org/refpages/latest/refpages/source/LocalInvocationId.html) applies to mesh stages as well as compute. Full subgroups guarantee active subgroup lanes; they do not supply that missing mapping. These are primary Khronos specification references.

The inspected translator source also treats the IDs separately. At pinned Hookshot source revision `4cf785a0b143bb34026506c9a3fe4f4bb93c797d`, `EmitLaneId` in `gpu/vk/eden/shader_recompiler/backend/spirv/emit_spirv_warp.cpp` reads the subgroup-local builtin, while `EmitLocalInvocationId` reads the workgroup-local builtin. That source behavior does not prove they are interchangeable on the target mesh stage.

This prototype deliberately uses `LocalInvocationId.x` as its logical lane and reconstructs logical 32-lane masks. It would preserve the original lane-dependent behavior only if the original mesh stage's subgroup32 lane mapping agrees with that choice, or if a separately proved source-level logical-lane contract justifies the replacement. Neither has been established here. The earlier [compute synthetic experiment](warp32-emulation.md) observed agreement for its own compute pipeline; it supplies no mesh-stage guarantee. The exact original runtime subgroup request has also not been observed at the draw site.

Accordingly, every report retains `lane_mapping_verified=false`, `source_runtime_required_width_verified=false`, `equivalence_proven=false` and `runtime_execution_authorized=false`. Passing `spirv-val` does not change these fields.

## Structural evidence and rejection rules

The source is SPIR-V1.6, `Logical GLSL450`, with exactly one `MeshEXT main` and a literal workgroup size of 32×1×1. All module group instructions are exactly two subgroup-scope ballots in the same main-function basic block. Both results have exactly one consumer: component-zero extraction. Three functions are reachable from main. The complete declared subgroup builtin inventory is Eq/Lt/Le/Gt/Ge masks and subgroup-local invocation ID. Across the whole module, only the local ID and Lt mask are loaded, once each; the other mask declarations are unused. Direct loads are the only accepted builtin use, preventing pointer aliases from escaping the rewrite.

The ballot block is reached by every syntactic entry path: cutting traversal there leaves 16 prefix blocks, no early exits, no cycles and no function calls. Both ballots are in that common block, with no intervening function call or invocation termination. No suffix path can return to the block. Thus, for valid execution of all 32 workgroup invocations, the added barriers are reached uniformly once. This is a bounded control-flow argument, not a proof of valid incoming resource contents or race-free original memory behavior. SPIR-V1.6/full-subgroup rules address initially inactive physical lanes when the relevant subgroup-size requirements hold; the original runtime policy remains an external condition.

The analyzer rejects a changed source digest, another execution model/local size/memory model, unknown or duplicated subgroup builtins, aliased builtin pointers, any additional group instruction, another ballot shape/scope/consumer, ballots in different blocks, unsupported CFG terminators, a prefix early exit/cycle/call, an intervening call, or a path that revisits the ballot block. Input and output sizes, external CPU-tool timeouts, and private output placement are bounded. The validator checks the complete source and resulting SPIR-V, beyond the custom analyzer's restricted structural checks.

## Independently authored replacement

Each ballot gets its own `Workgroup uint[32]` scratch array. An invocation writes either zero or its logical lane's bit to its unique array slot, reaches a `Workgroup` execution/memory-scope control barrier with `AcquireRelease | WorkgroupMemory` semantics (`264`), then reads and ORs all 32 slots. All invocations construct the same four-word result with the low mask and three zero upper words. The 32 reads are statically unrolled for this initial compilation experiment. Separate arrays and the no-reentry check avoid a scratch-reuse race; no atomics or lane0-only control path are needed.

The subgroup-local ID load becomes a workgroup-local X load. Loaded mask builtins can be reconstructed from `eq = 1u << lane`, `lt = eq - 1`, `le = eq | lt`, `gt = ~le`, and `ge = ~lt`, with zero upper words. All shifts remain in range0–31. The actual source loads only Lt. Unused subgroup variables, their decorations/debug names, their entry-interface entries and the now-unused subgroup capabilities are removed. Two fresh scratch variables are added to the entry-point interface, as required for SPIR-V1.6 variables used by the entry point. Existing descriptors, mesh outputs, task payload and original Workgroup variables are preserved.

The candidate adds 256 declared Workgroup bytes, taking the independently counted logical total from14,356 to14,612 bytes before implementation padding/driver allocation, and adds two barriers to the original two. Added synchronization can affect compiler scheduling and performance. It also does not repair any pre-existing invalid resource access or data race. No performance conclusion follows from the algorithm or module size.

## Frozen CPU artifact

Private final artifacts are in ignored `local/mesh-warp32/run-03/`:

| Artifact | SHA-256 / size |
|---|---|
| Reviewed original module | `835aa59799c6ecb621034d946e2fa4eadb1aa2407d293321ae3e3052882bc9b3`, 42,939 words |
| Conditional transformed module | `9d23b7ebf3677f53fe6901c9b94f2c4927f1f0c47ed207c04c53df9b59ae9638`, 44,043 words |
| Transformer source | `1b8aa9d2af553f5a19fe007facdf4a520682dae3ae290c09d85408659ca7bb61` |

SPIRV-Tools v2026.1 accepted both real modules with `spirv-val --target-env vulkan1.3 --uniform-buffer-standard-layout`. Independently authored synthetic source and transformed mesh fixtures also assembled and validated. Eight regression tests cover inventory, uniform prefix, early exits/cycles/reentry, calls before/between ballots, unexpected operations/consumers, mask edge lanes, interface/scratch construction and digest refusal before external tools.

```sh
python3 tools/mesh_warp32_transform.py "$PRIVATE_MESH_MODULE" \
  --output local/mesh-warp32/new-audit
python3 tools/mesh_warp32_transform.py "$PRIVATE_MESH_MODULE" \
  --output local/mesh-warp32/new-candidate --compile-only-candidate
python3 -m unittest discover -s tests -p test_mesh_warp32_transform.py -v
```

The next safe use of this artifact is coordinated compile-only mesh-pipeline inspection, comparing register allocation/spilling/code size with the exact original and retaining all pipeline state/features. Before execution, establish a valid mesh lane mapping or a stronger guest-lane source contract, observe the original draw/pipeline subgroup policy, capture complete resources and fixed state, and perform real differential output/rendering validation. This prototype grants no blanket subgroup-policy change to this shader or any other graphics stage.
