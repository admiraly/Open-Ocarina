# Project status

Updated 2026-11-12. This page distinguishes working tools, local observations, and unproven targets. It is not a completion percentage.

## Implemented starting point

- Module inventory reads NSO header metadata and computes SHA-256 hashes without exporting executable bytes.
- GPU-profile analysis streams Hookshot timing tables, rejects incomplete captures, and ranks consumers using the combined recording/upload denominator.
- Byte-range comparison requires a hash-pinned reference and explicit bounds. It reports mismatches without dumping original bytes.
- The publication gate checks tracked/staged files for common private artifact types, binary content, credential-like strings, symlinks and machine-specific paths.
- NSO analysis conversion validates segment hashes and produces an unrelocated AArch64 ELF locally. The original `main` module was imported successfully.
- Official Ghidra 12.2, with its release digest verified, completed bounded headless decompilation of two original entries. Decompiled output remains private; original types and source identity are not recovered by this step.
- Three independently authored C probes are compiled with Clang and compared against complete extents confirmed by original unwind records. Two small leaves match; the lookup probe remains unmatched. See `research/cpu-matching-probe.md`. No project-wide source percentage is inferred.
- The shader catalog resolved the initial compute target to native seed identity and cached variant/SPIR-V metadata. Full replay capture and the timed variant's runtime identity remain to be implemented; see `research/shader-target.md`.
- Optimized compile-only Vulkan probes now cover required subgroup sizes 32 and 16 plus the unpinned driver choice. They use the exact shader, robust buffers, push descriptors, matching push range and audited driver build. Executable statistics establish code-generation differences, not execution speed or correctness; see `docs/compile-probe.md`.
- The memory-contract analyzer proves that the selected entry genuinely uses physical page-table loads and a read/write storage binding. A logical-buffer-only replay cannot cover it.
- A versioned logical-buffer replay validator and deterministic benchmark-contract checker now reject unsupported or missing evidence. Neither supplies the still-missing complete physical-memory capture/replay executor or camera-state producer.
- The optimized runtime identity patch was built and exercised in a separate headless install. All bounded events matched the selected variant/digest at real dispatch-recording sites with the established subgroup policy. Complete resource coverage remains false; see `research/runtime-identity-observation.json`.

These tools establish inputs and evidence. They do not reconstruct the game's source or prove a complete matching executable. The synthetic test suite is the CI entry point; optional external-tool probes are validated separately.

Reviewed starting metadata is committed as [the module inventory](../research/cpu-module-inventory.json) and [the selected GPU profile](../research/gpu-profile-initial.json). The latter retains source-line references and interpretation limits. Original inputs and raw captures remain local.

## Local observations

The current investigation uses an Intel Arc A770 on Linux and an existing Hookshot installation.

- The translated GPU programs assume 32-lane subgroup behavior. Forcing narrower execution previously changed rendering. Several large shaders at the required width compile with heavy register spilling.
- A full-workload GPU-pass capture spread time across graphics, compute, and transfers. No single shader was shown to explain the entire frame. Timestamp instrumentation itself changes execution, so these spans are prioritization evidence rather than normal frame latency.
- A newer Hookshot build enabled loop-based memory-table lookup and an optional native float atomic-add path. Exploratory runs remained in the low frame-rate range. Stable and nightly captures had different camera framing, so no controlled speedup claim follows from those runs.
- A local Mesa adjacency-index regression test reproduced duplicate graph edges with the original index type and passed after widening two indices. The fixed driver built successfully. That establishes a compiler correctness fix; it does not establish a gameplay FPS improvement.

## Progress measures

| Measure | State | Required evidence |
|---|---|---|
| Input identity | Inventory tooling implemented | Reproducible module hashes and header validation |
| Original toolchain reproduction | Unknown; leaf feasibility probes run | Nontrivial compiler/ABI and relocation evidence |
| Authored source reconstruction | Initial independently authored leaf probes | Wider reviewed source with provenance and bounded verification |
| Byte matching | Two complete leaf probes matched; lookup mismatch retained | Expand function complexity without weakening checks |
| Relocations and integration | Not established | Relocation-aware checks and an ordinary successful link |
| Standalone playability | Not established by this repository | Startup, gameplay, save and load tests |
| Rendering equivalence | Not established for a native rewrite | Reproducible replay and image/behavior checks |
| Real 60 FPS on A770 | Target | Repeated equivalent-scene measurements with correct rendering |
| Higher quality or integrated-GPU support | Future target | Device-specific measurements and an explicit quality budget |

## Next work

Follow [the Arc-first decision](strategy.md) and the exit tests in [the roadmap](roadmap.md). Prioritize complete shader capture and contract validation, using targeted CPU reconstruction where it resolves an unknown. Keep private analysis outputs local and publish enough metadata to reproduce the procedure.

The [active native-feature and quality goal](goal.md) includes hair/foliage image quality. [Image-quality criteria](image-quality.md) and [the aliasing investigation](../research/hair-aliasing.md) distinguish source fidelity, sampling, reconstruction and temporal behavior. No AA setting has been credited as a validated improvement.

The Grok design investigation produced a [guarded interval-lookup proposal](../research/radical-address-lowering.md) and synthetic counterexamples to unconditional page-table-only lowering. The reviewed CPU model covers explicit robustness choices and is not a runtime certificate or a proof of all inputs. A compiled synthetic helper experiment and full replay are still needed.

The subsequent [synthetic interval compilation experiment](../research/interval-compile-experiment.md) now shows that Mesa retains the smaller lookup loops for independently authored lookup-ID shaders. Small, large and descriptor-count-inspired cases are reported separately. This is not real descriptor-pointer lowering, a fix for the selected shader's spills, or a GPU runtime result. Complete replay and a semantics-preserving integration remain open.
