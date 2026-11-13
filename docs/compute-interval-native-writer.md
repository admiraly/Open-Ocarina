# Native CPU writer for the compute interval prototype

The [authored patch](../patches/hookshot/compute-interval-native-writer.patch) adds one standalone C++17 header, `gpu/vk/compute_interval_spirv_writer.h`. It implements the structural rewrite previously performed by the frozen Python tool `3176a7e…00ca`. The patch has been applied only in fresh temporary/private workspaces. It changes no shared vendor source, renderer build, descriptor writer, runtime control or device path.

The production API `open_ocarina::interval_spirv::write_target(words)` hashes its actual input internally and accepts only `b9af52c0ba07f64eb8b0ca2905c432d094999fdf669aa95182ff7754b510e97b`. It then decodes the module, generates the record declarations and two load-selector prefixes, copies all other instructions, reparses its result and checks the exact native output digest. There are no embedded original or candidate instruction arrays.

## Exact output relationship

GCC and Clang UBSan independently emit the same 256432-byte native candidate:

- Native SHA-256: `4c0b19efdac3bb721636a9ce1bbeeba83b09b557cf722df10526d76246041629`.
- Frozen Python/SPIRV-Tools candidate: `d5148342b4517882b25a48e63700cbfb8695454cc05d131da718509ac2563dc0`.
- **Every instruction word and numeric ID is identical.** The sole difference is header word 2: the native writer retains the original unspecified generator, zero; SPIRV-Tools supplied generator 458752. Normalizing that provenance field reproduces the exact frozen candidate digest.

The native writer pins its own output digest, including the retained generator, before returning. It does not claim to have been emitted by SPIRV-Tools. The original and candidate ID bounds are 11181 and 11330. The binary validates under Vulkan 1.3 with uniform-buffer-standard-layout enabled.

## Defensive decoding and preservation

The bounded binary decoder checks header/version/schema, instruction framing and recognized opcode operand counts. It distinguishes IDs from literal strings, numeric constants, image operand masks, alignment literals and decorations. Result IDs must be nonzero, unique and below the bound; referenced IDs must exist. Function-local values and control-flow labels cannot escape their functions. Literal strings must terminate with zero padding. Unsupported opcode/operand classes, duplicate decorations and unsupported memory operands fail closed.

The structural inspection requires the exact seven-function inventory and one compute `main`. It reconstructs the two load helpers through their returned StorageBuffer loads and verifies:

- The scalar/vector types, storage classes, element pointer types, runtime-array strides, zero member offsets and the table's three-element type.
- The five single roots followed by the table root, the exact C0 selector offsets and UBO0 source layout.
- The aligned guest base, typed address subtraction, uint32 truncation and width-specific floor shift.
- The source endpoint expression: uint32 ArrayLength shift before uint64 conversion, wrapping uint64 endpoint arithmetic, ordinary minimum, and the table-only zero-size substitution.
- A private uint32 table counter initialized to zero, a `<3` bound, and a unit increment in its continuation block. Physical operations must follow the retained MISS merge.

Only helper functions 107 and 850 receive new selector prefixes. The writer compares the five other function bodies and both physical MISS suffixes against their originals before returning. Independent Python preservation checks on the actual output confirm the same result: all five bodies remain binary-word identical, and each MISS suffix retains all 166 words. Original globals remain identical except for the appended entry-interface variable; independently generated record types, seven uint32 constants, the readonly variable and its layout decorations are added.

The generated helpers match the reviewed value-return design: bounded binary interval lookup followed by eight constant typed roots. They introduce no pointer phi, nonuniform descriptor index, device feature, push byte or alternate physical path. The original stores, main work, image operations, physical-root access and follow-store policy remain unchanged. The existing finite-work auditor independently recognizes `lo=0`, frozen count 6, midpoint `lo + ((hi-lo)>>1)`, left `hi=mid`, right `lo=mid+1`, and no additional control-flow cycle, for each width.

SPIR-V validation remains an independent check. This decoder covers the reviewed opcode subset and structural contract; it does not implement universal SPIR-V type checking or dominance analysis. The production entry's exact input/output hashes bind these checks to the two already validated modules.

## Authored fixtures and dependencies

The public tests generate an independent seven-function shader fixture containing five singles, a three-element table walk, scalar/vector typed views and a small physical suffix. The original fixture and transformed result both pass `spirv-val`. Its native and Python transforms match every instruction word and numeric ID after generator normalization. The fixture entry is available only when `OPEN_OCARINA_INTERVAL_WRITER_TESTING` is explicitly defined; a compile-negative test verifies its absence in the default build. The unrestricted structural transform is private to the implementation class.

Eleven tests cover fixture preservation/equality, production SHA rejection, malformed framing/result/reference IDs, string padding, short operands, alignment literals, function scope, missing functions, escaped prefix values, occupied binding 12, selector adjacency, minimum endpoints, table bounds and unit progress. The same eleven tests pass with Clang UBSan and no diagnostics. Eighteen SHA tests include empty input, both sides of padding/block boundaries, multiple blocks and one million bytes, compared with Python `hashlib`.

The writer reuses the existing reviewed SHA helper through `compute_identity.h`; it adds no crypto implementation. Dependency identities are:

| Dependency | SHA-256 |
|---|---|
| `patches/hookshot/compute-identity.patch` | `2f14873edfdb47b1f915e75bc4bbe6683ef6632bc66fcbfa1d918aa1c176f018` |
| Extracted `gpu/vk/compute_identity.h` | `70cf662ba8300be8612be3ae75459dacb638d83d4cc600675985a92bf60476e4` |
| Public SPIRV-Headers `spirv.hpp` used for compilation | `2d117943804fa4e2b9e902d18b115ce8496c8608114cdb54a5110ddb95fe1191` |

Tests require an optional C++17 compiler and public SPIRV-Headers. The fixture assembly, binary comparison and validation additionally use optional SPIRV-Tools. Observed versions were GCC 16.2.1, Clang 22.1.8 and SPIRV-Tools v2026.1. No Vulkan headers, link library, instance or device are used by this header or its harness.

## Frozen handoff and remaining boundary

Public source identities:

- Patch: `c907f5a671a2f66f95448e49dfac4d803e32a34135dd591b1c1899fdb7a0beb8`.
- Extracted authored header: `831d964341723adec64e27fa4cd74f4357ae4f82e8c8d78df1a591b032ed8d09`.
- Tests: `bb1afbb3dac7e49de37417581143d7363b0c60d009e5bcae1843ccf08d98ae5a`.

The private production test binaries are pinned in the [observation metadata](../research/compute-interval-native-writer-observation.json). The full repository suite passed 779 tests with five optional skips. The new patch also passes `git apply --check` and applies to a fresh temporary directory, reproducing the exact tested header bytes. All private inputs, generated binaries and validation reports remain under ignored `local/native-compute-interval-writer-01/`.

The four frozen record-core files, Python tool, original module, prior candidate and 592-byte record remain unchanged. This writer provides a CPU component for a future reviewed optional overlay. Extracting a live immutable C0/descriptor bundle, establishing its ownership lease, allocating/binding records, updating the pipeline layout/cache identity and admitting a device policy still require the separate [integration boundary](compute-interval-record-core.md). No GPU pipeline was compiled or executed, no live or production feature was enabled, and no performance or whole-shader correctness claim is added.
