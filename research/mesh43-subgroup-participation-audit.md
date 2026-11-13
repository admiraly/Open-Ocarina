# 43ec subgroup participation and write effects

CPU-only audit of cached program `43ec86f35fd937b9`, variant `bc2d248909cf8b40`. No lowering was emitted: this family has partial logical warps, live task payload, sampled-image reads and externally visible device atomics. The current uniform two-ballot transformation and readonly replay contract cannot be extended by changing an operation whitelist. [Sanitized exact metadata](mesh43-subgroup-participation-audit.json) records stage identities, sites, resources, finite control and proof gaps; private shader bytes remain untracked.

| Stage | Exact source SHA-256 | Reachable group operations |
|---|---|---|
| TaskEXT, index0/NVN6/Vulkan64 | `5e7d8f02e2dbd5ae1cd625d42366bc532dcdf7d71bb56ac3bc303b5e9655d086` | 3 ballots, 32 broadcasts |
| MeshEXT, index1/NVN7/Vulkan128 | `4c5eee851f4140ff089d4abb0bdd445566c7980ee47669d962778171d143131d` | 8 ballots, 6 Any, 22 shuffles, 32 broadcasts |

Both stages have literal local size32×1×1 and passed CPU SPIR-V validation for Vulkan1.3 with uniform-buffer standard layout. The hash-pinned serialized cache provides the linked stage identities; cache membership is not proof of a live submitted pipeline or current cost. The mesh maxima are96 vertices and96 primitives, with3200 logical task-payload bytes and9236 logical shared bytes before padding.

## Concrete partial-warp blocker

The outer mesh guard depends on the logical subgroup lane as well as task-produced item count. Each accepted item uses a pair of lanes; a mesh workgroup covers16 items. For count N=1, an emitted first mesh group can have lanes0–1 enter the guarded work and lanes2–31 skip it. N=17 similarly permits a two-lane tail in group1. These are algebraic counterexamples in the count domain, not observed live task captures. The source does not prove that N is always a multiple of16.

The large late block `%3757` lies behind this guard and contains four ballots,22 shuffles and32 broadcasts from constant source lanes0–31. All broadcasts read the same u32 value operand. Earlier conditional branches also affect participation at four other ballot blocks and six Any sites. `Ballot(true)` explicitly materializes active membership; replacing it with a full32-lane mask changes results. A workgroup barrier introduced inside these sites could be reached by only part of the workgroup. Masking scratch values does not make that barrier valid.

Mesh main is acyclic with810 basic blocks. Its single return follows the export reconvergence, rather than an unconditional early termination before the late block. Thus the earlier prefix audit's “early exit” means a CFG path that bypasses the target site, not necessarily an `OpReturn` instruction immediately before it. The original two workgroup barriers sit at entry and export reconvergence; their existence does not prove convergence of newly inserted barriers. The shader also directly loads subgroup lane ID55 times and LtMask twice, requiring logical-lane/builtin handling beyond replacing intrinsics.

## Device atomic writes invalidate readonly replay

There are six reachable `OpAtomicIAdd` instruction sites, all using typed `StorageBuffer` pointers, Device scope and relaxed memory semantics. Five occur in descriptor-walk helper `%986`, called at four main-function sites; their roots are set0 bindings11,12,13,14 and the three-element array at15. Main additionally updates binding14 directly. The helper resolves a logical address through descriptor bounds and returns the old atomic value. These externally visible effects must be preserved even if their return values are unused at a particular caller.

The generic resource auditor reports these as `unsupported-memory-operation:OpAtomicIAdd`. Its zero descriptor `OpStore` count therefore does **not** establish readonly input. This does not alter the proven readonly contracts of the twelve earlier passing modules; it is an explicit blocker for43ec. Immutable independent descriptor copies cannot reproduce writable aliasing, atomic return dependencies or cross-workgroup/draw effects.

Mesh inputs have six UBO and seven SSBO buffer elements, plus one combined sampled-image descriptor at binding16. Task inputs have two UBO and five SSBO buffer elements. Live task payload and writable buffers rule out the current dead-payload/owned-readonly capture shortcut. Any replay needs linked task output, exact image/sampler/subresource state, submitted native arguments, full writable alias/backing ownership, before/after contents and ordering with other work.

## Finite control and narrow next proof

The mesh has four reachable functions and77 direct calls. Its three helper loops each initialize a local counter to0, test against literal3 and increment by1; early returns may shorten them. Main itself has no CFG cycle. Task has two reachable functions,695 main blocks, one main loop and one three-element descriptor-walk helper loop. Its per-invocation private guard starts at8192, decrements at the sole continue and ends the backedge when signed-negative, bounding main-loop body visits by8193. Input-dependent exits can shorten this. Finite control is not proof of safe bounds, race freedom or affordable compilation/execution.

The smallest additional operation pattern worth proving is the **task-stage batch of32 fixed-source broadcasts** in block `%1336`. It is outside the per-item selection, but inside a loop. Before authoring a target lowering, prove that all32 lanes reach each dynamic batch together: the loop invariants and exits must be uniform, selection diamonds must reconverge even for rejected items, and no caller may terminate or skip an iteration. Separately prove TaskEXT lane-to-local mapping for the pinned Mesa/A770 configuration; previous mesh-stage mapping observations alone do not establish task-stage mapping.

Once those conditions are established, an independently authored full-participation prototype can publish one u32 per logical lane, synchronize uniformly, read constant lanes0–31, and synchronize again before scratch reuse. No such target prototype is emitted by this audit because the proof is unfinished.

For the mesh, the algorithm path is broader: retain a logical active mask at each phase, if-convert or split divergent work into a common bounded schedule before adding barriers, and lower ballot/Any/shuffle/broadcast together with active-source validity and logical builtin rewrites. Inactive lanes must participate in the common synchronization while suppressing the original operations and side effects. Device atomics and writable aliases remain part of that contract. Full task/image/writable-state capture is a separate prerequisite for validating the result.

No GPU call, vendor edit, runtime admission, shader equivalence, rendering result or performance claim follows from this audit. Existing frozen sources, replay modules, plans and observations remain unchanged.
