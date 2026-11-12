# Synthetic logical warp32 on narrower Arc subgroups

Observed 2026-11-12: independently authored workgroup/shared-memory primitives produced the same integer outputs as native 32-lane subgroup operations on an Intel Arc A770. The software implementations actually compiled at subgroup widths 8, 16 and 32. This establishes a bounded primitive prototype, not real shader equivalence, graphics compatibility or a performance improvement.

## Experiment

[`tools/warp32_probe.py`](../tools/warp32_probe.py) generates two synthetic compute modules, validates them, builds a separate optimized [`tools/vulkan_execution_probe.c`](../tools/vulkan_execution_probe.c), and compares every output word against a CPU golden result. The existing compile-only helper and tools retain their no-dispatch contract.

Each executable submits one workgroup of exactly 32 invocations, with five fixed iterations. Input cases use all-false, all-true, alternating, lane31-only and deterministic pseudorandom predicates, plus deterministic integer values that vary by case and lane. Each invocation writes ten words: logical lane ID, broadcast from lane7, shuffle-XOR1, four ballot words, vote-any, vote-all and logical subgroup size. All 1,600 words per variant are checked, including every ballot upper word and every invocation's result. Output buffers start with a sentinel so missing writes fail the comparison.

The native module uses subgroup instructions at required width32. The software module assigns logical lanes through `LocalInvocationID.x`, writes 32 values and predicate bits into workgroup storage, reaches a uniform barrier, reduces the predicate mask on lane0, reaches a second uniform barrier, then reads the desired lanes/mask. A final uniform barrier prevents shared scratch reuse while another invocation is still reading. The software scratch allocation is 260 bytes. All invocations execute the same five iterations; there are no early returns or barrier-containing divergent branches.

Required widths use subgroup-size control and full compute subgroups. Actual executable widths are read using `VK_KHR_pipeline_executable_properties`; a missing property or width mismatch fails the Python experiment. The required width is not treated as measurement evidence by itself.

| Implementation | Required width | Executable width | CPU words matched | Output comparison |
|---|---:|---:|---:|---|
| Native subgroup operations | 32 | 32 | 1,600 / 1,600 | Reference |
| Workgroup emulation | 8 | 8 | 1,600 / 1,600 | Byte-identical |
| Workgroup emulation | 16 | 16 | 1,600 / 1,600 | Byte-identical |
| Workgroup emulation | 32 | 32 | 1,600 / 1,600 | Byte-identical |

Device: Intel Arc A770 Graphics (DG2), PCI device ID `0x56a0`; Mesa reports `26.2.4`. The explicit private ICD selected the existing patched Intel driver. The driver library SHA-256 was `5ea19ac96aa52d061ecf2b8e23df7ed3d7d3b36c7d2ae7fe62f01c5a9b610aaa`; ICD JSON SHA-256 was `7b882d02f366202e532410da637178b53f549152ee1e8c174ed79a433c333d60`. No driver or installed game was changed.

Build: GCC `16.2.1`, `-std=c11 -O2 -DNDEBUG -Wall -Wextra -Werror`; glslang `16.2.0`, `-V --target-env vulkan1.3 -Os`; SPIRV-Tools `v2026.1` validation targeting Vulkan1.3. Pipeline optimization is enabled. Enabled device features are robust buffer access, subgroup-size control, full compute subgroups and executable-property queries. This minimal synthetic profile does not reproduce all features or resource contracts of a real game pipeline.

Frozen artifact SHA-256 values:

| Artifact | SHA-256 |
|---|---|
| Native generated SPIR-V | `d3716bd9d1bb0841134545da79b369dbff5695ec8fefca969aa5584cfe9601df` |
| Software generated SPIR-V | `d4820797fbb4ee27905bb475b847de03a13948560e86b199419505db9eda244f` |
| Execution-helper C source | `c97ae0460b0e42599fa53e9fd0bf6616c2ec36df361b44bce30ade3d75cfb35c` |
| Optimized helper binary | `624b967c0637bbe340710e8a051beef45eb16db859e5e4c753bdf34a9c59c6fc` |
| All four output buffers | `579bd8e88a3c585561060a7d4915c35d684b559117ceff33c60accdd4ac83fce` |
| Synthetic input | `cbf9dbf6a7170f3e0bd77ea94d013116be8d7a93b8ffa618022926ff956ded60` |

Generated modules, input/output bytes, private logs, exact device metadata and the full report remain under ignored `local/warp32/run-01/`. The public repository contains only independently authored generators, helper source, synthetic tests and this metadata.

## Reproduce and bounds

With Vulkan development headers/pkg-config, a C compiler, glslangValidator and spirv-val installed, and with exclusive GPU access:

```sh
python3 tools/warp32_probe.py --driver "$INTEL_ICD" --output local/warp32/new-run
python3 -m unittest discover -s tests -p test_warp32_probe.py -v
```

The tool requires an explicit driver and selects device0 unless `--device` is specified. It rejects an existing output directory and repository output paths outside ignored artifact directories. It checks for Hookshot and EVE game processes before the experiment and immediately before every execution. Process checks do not enforce exclusivity against unrelated software, so the operator must coordinate GPU ownership. It never signals existing applications.

Each shader has bounded loops and uniform barriers; there is no GPU polling. Each dispatch uses a two-second fence timeout, and each execution helper has a separate 30-second host-process timeout. The wrapper terminates only its newly created process group on interruption or timeout. A failed fence causes immediate process exit without queue/device idle calls or destruction waits. This avoids requesting an unbounded idle wait, but is not a recovery mechanism for a wedged kernel or device. Successful dispatches perform normal cleanup after the fence. Host-visible coherent input/output allocations are separate, and a shader-write-to-host-read memory barrier precedes fence completion.

The standard-library test suite does not execute Vulkan. It checks all 1,600 output positions for mismatch detection, an independent CPU scratch/reduction model, fixed predicate cases, actual-width validation and refusal to execute alongside a competing game.

## Conditions before using this in graphics

This prototype supports exactly one logical warp per 32-invocation workgroup. It does not support divergent callers, invocation early returns, multiple logical warps, or inactive logical lanes. Broadcast and shuffle source validity is fixed and known here. Shared storage usage, execution stages, synchronization semantics and barrier legality must be established separately for each real shader.

Before lowering any real operation, prove that **every emulated operation is reached in workgroup-uniform control flow**, with all 32 invocations participating in each added barrier. Rewrite every logical `SubgroupLocalInvocationID` and subgroup-size builtin to the logical 32-lane contract. Audit Eq/Lt/Le/Gt/Ge subgroup masks and other subgroup builtins as well; changing just ballots while leaving physical-width builtins is incomplete. Preserve memory semantics, existing workgroup data, payload/output contracts and all affected resources. Resource accounting must include both existing shared data and scratch.

The [graphics-target analysis](graphics-targets.md) identifies a first mesh candidate with two ballots and a workgroup size of 32. Its reconverging prefix makes those sites worth further investigation. That structural observation is not execution evidence or blanket permission to unpin the stage; the more complex task/mesh subgroup operations need their own proof. Mesh/task stages have not been executed by this prototype. Full real-shader differential replay and downstream rendering checks remain necessary, followed by equivalent-scene frame timing. No FPS measurement or graphics-quality claim follows from this experiment.
