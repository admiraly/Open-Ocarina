# Stage-preserving mesh logical-output instrumentation

`tools/mesh_output_instrument.py` prepares a diagnostic MeshEXT module. It performs CPU-side validation, a bounded structural audit, deterministic instrumentation, assembly and another validation. It never loads Vulkan, enumerates a device, executes a shader or changes a renderer install.

The tool accepts only the two frozen d8 mesh digests described in [mesh-output-replay.md](../research/mesh-output-replay.md), or generates its own independently authored synthetic fixture. Original shaders, derivative binaries, disassembly and reports remain in a new private output directory. The source and conditional modules pass the same output/input/control-flow checks; the expected barrier inventory is two for the original and four for the conditional ballot reconstruction.

## What is retained

The entry remains MeshEXT with local size 32×1×1. Every original mesh output store, SetMeshOutputsEXT instruction, Workgroup barrier and Position Invariant decoration is retained. Original shared storage, arithmetic and reachable input operations remain. The instrumenter mirrors output values into a separate storage-buffer record; it does not replace native mesh outputs or change the original output maxima/topology.

Two precisely audited exceptions are explicit in the report:

- The dead task-payload chain is removed only after proving that its sole loaded value is the false operand of a constant-true select choosing WorkgroupId.x. That select becomes a copy of its selected value. This is not a generic permission to manufacture task payload data.
- Original direct WorkgroupId loads become raw WorkgroupId plus the supplied group origin. The instrumenter audits the input-only/global side-effect boundary, rejects NumWorkgroups and other unsupported stage builtins, and keeps original per-workgroup memory/output semantics. The replay host must validate the selected window against captured native XYZ counts. Zero origin is valid; window dimensions must be positive. Actual API draw dimensions must equal the control window.

It rejects output loads, pointer escapes or unsupported access shapes, changed component-ownership patterns, out-of-range lane/block indexes, incorrectly guarded primitive outputs, altered execution modes/barriers, extra early returns, main-function cycles, unresolved calls, physical accesses, input writes and unsupported side effects. The actual module digests remain the outer identity gate; the structural auditor is deliberately not a general shader instrumentation framework.

## Added descriptor contract

Original set 0 remains unchanged: five individual uniform buffers at bindings 0–4; three individual storage buffers at 5–7; a three-element storage-buffer array at 8. All input ranges and their initial bytes must be captured completely, including descriptor lengths used by array-length queries. The original shader has no push constants or sampled images.

New set 1 contains:

- Binding 0: capture storage buffer, flat runtime array of uint32, exactly 44,928 bytes per replayed workgroup. The host may bind this range inside a larger allocation with separate outer guards.
- Binding 1: read-only 32-byte storage buffer, two uvec4 members. Bytes 0–15 contain origin X/Y/Z and reserved zero; bytes 16–31 contain window X/Y/Z and reserved zero.

Capture/control buffers must not alias each other or any original input. The preparation report does not prove the host fulfilled these obligations. Replay is limited to at most 32 groups, with checked buffer-size arithmetic and origin/window bounds. Captured indirect argument contents, not CPU guesses, define the native grid.

The capture record layout is exported as `LAYOUT` and `record_layout` in the report. See the exact offsets in [mesh-output-replay.md](../research/mesh-output-replay.md). It contains per-lane vertex/primitive count rows, final Position/generic/index values, component-writtenness and guards. Position Z's second store is retained and mirrored in its original order. Primitive vec3 padding and non-emitted primitive slots remain untouched.

Initial value/count/padding words are `0xcdcdcdcd`; writtenness is zero; each 16-word inner guard is `0xa11ce55a`. Written components receive marker one. `initial_capture(N)` returns initialized record-body bytes only, excluding any outer allocation guard. SetMeshOutputs is mirrored into thirty-two separate count rows to avoid introducing a shared count-write race. All lanes must agree on the emitted counts.

## Prepare private real modules

Requires Python 3.11 and SPIRV-Tools. The validator uses Vulkan 1.3 with uniform-buffer-standard-layout enabled. Preparation refuses an existing output directory.

```sh
python3 tools/mesh_output_instrument.py "$PRIVATE_ORIGINAL_MESH" \
  --output local/mesh-output/original
python3 tools/mesh_output_instrument.py "$PRIVATE_CONDITIONAL_MESH" \
  --output local/mesh-output/conditional
```

Python APIs are `prepare(source_path, output_directory)` and `prepare_fixture(output_directory)`. Both return the report dictionary and write `source.spv`, `instrumented.spv`, `instrumented.spvasm` and `report.json`. The report binds source/derivative/tool digests, original-input/output audits, the two transformations above and the exact capture layout. `runtime_execution_authorized` and `equivalence_proven` remain false.

A separate replay preparer should rehash the source/tool and rerun trusted deterministic preparation, rather than accept an arbitrary module labeled as instrumented by a caller's manifest. Freeze report, source, instrumenter and executable hashes before proposing execution. Source changes after a handoff require a new report and review.

## Independently authored fixture and CPU oracle

```sh
python3 tools/mesh_output_instrument.py --synthetic-fixture \
  --output local/mesh-output/fixture
python3 -m unittest discover -s tests -p test_mesh_output_instrument.py -v
```

The fixture uses the same output inventory, maxima, 168 stores and capture layout, but contains no game code or subgroup-dependent operations. It can test the recorder/executor at required widths 32, 16 and 8 without pretending to validate the real ballot transformation. Its descriptors are declared to exercise layout preparation; fixture arithmetic does not read their contents.

For effective group coordinate `(x,y,z)`, its uint32 tag is `x + 257*y + 65537*z` modulo 2^32. It emits 128 vertices and `tag % 129` primitives. Field zero is Position and fields 1–9 are the generics in ascending location order. Each attribute component is the float32 conversion of `tag + vertex*8 + field*4 + component` modulo 2^32. Position Z is subsequently overwritten with the same expression plus 1000. A triangle component is `(primitive*3 + component) & 127`.

`fixture_expected_capture(origin, groups)` returns the complete expected record-body bytes, including sentinels, guards, count rows and writtenness. It supports nonzero origins and empty/tail primitive cases and excludes outer guards. A replay host must still check every returned byte; a few sampled positions do not validate the recorder.

## CPU validation and frozen handoff

Observed 2026-11-13: the original, conditional and authored fixture preparations assembled and passed SPIR-V validation. No prepared module was executed by this lane. The 17 dedicated tests cover preservation, negative payload/index/pointer/builtin/control-flow/input-write cases, capture layout/golden values, digest/private-output refusal, and optional game-free SPIRV-Tools preparation.

| Artifact | SHA-256 |
|---|---|
| Frozen instrumenter source | `69e97d192ca4607a2c7f50c4d92460c0467b5f0f076b9e3775dd04ac4fe960e7` |
| Instrumented original mesh | `747185e77510c027a0f23e4d598292c3d1bf47d3c95a145204497d439fae2002` |
| Instrumented conditional mesh | `06ace03c7878985b3f22576356b4275c4c5bad9c6a74304b42770af8397fe558` |
| Authored fixture source module | `7cf8f030615526b4dc2025114a547a07be7c764f7ece3cc044aece6f9dcf13a8` |
| Instrumented authored fixture | `e548e6d40320c28f1156e8c910fa64ee6f87b936cba15856b41648295b5828fa` |

## Limits

The shadow record captures logical values, not native mesh-output memory/URB packing or rasterization. Diagnostic SSBO writes can change scheduling and compilation. Validation and source preservation do not establish execution safety or semantic equivalence. Full captured inputs, a separately bounded execution helper, baseline repetitions and complete output checks are required before testing the real transformation. Native-feature/native-quality rendering, fragment/attachment behavior and final scene performance remain separate gates.
