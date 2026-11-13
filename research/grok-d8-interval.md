# D8 mesh interval candidate

CPU-only rewrite of the pinned mesh module `d8e59341cf4f629c`, original SHA-256 `835aa59799c6ecb621034d946e2fa4eadb1aa2407d293321ae3e3052882bc9b3` (42,939 words). `tools/grok-d8-interval.py` reads that binary. It does not rewrite the earlier authored stand-in. No Vulkan API, pipeline, device, or game process is used. Instruction and ISA timing are unmeasured. `execution_admitted` stays false.

## Proved cascade

The module has seven functions. Two are reachable load cascades:

| Function | Result | Calls from the mesh entry | Typed shift |
| --- | --- | --- | --- |
| 149 | uint32 | 11 | 2 (width 4) |
| 620 | u32vec4 | 61 | 4 (width 16) |

Each proved cascade is three single StorageBuffer claims, bindings 5, 6 and 7, then one `DontUnroll` loop whose trip count is the constant 3 over binding 8. Guest addresses are 8-byte loads from UBO binding 0 at byte offsets 1904, 1920 and 1936. Each size word is the next uint32, at 1912, 1928 and 1944. Table slots are 16 bytes apart starting at byte 2176; a zero size word keeps the typed end, and any other size word uses the unsigned minimum of the typed end and the NVN end. Singles always use that minimum, so a zero-size unaligned single can still own its alignment prefix. A hit returns `OpLoad` through the original StorageBuffer variable. The typed index is `uint32(address - aligned_base) >> log2(width)`.

Function 390 is the same cascade at width 8 and shift 3, with no calls. It is rejected and its words stay original. The three void helpers (277, 507, 738) store into the same roots and are not load cascades; they stay original, as does the mesh entry.

The miss suffix on both rewritten functions is five words: the original label, `OpReturnValue` of a null constant, and `OpFunctionEnd`. The module contains no `OpConvertUToPtr`. Those suffix words are unchanged. Every other function's instruction words match the original.

## Records and candidate

Reflected set 0 uses bindings 0–29. The new NonWritable StorageBuffer is binding 30. Records come from `local/runtime-mesh-input-02` (`open-ocarina.mesh-logical-input/v1`): UBO0 SHA-256 `61c1dd2f65b3850f427d6dbdf66c9e4dbd906e65c26eb59f95a1aeb883ca4fce` (2224 bytes) and the captured storage ranges, with null table elements 1 and 2 contributing a zero array length. That capture does not certify CPU-writer quiescence or import-alias ownership.

Both widths produce 4 interval rows and 9 selection cells. An independent source-order walk matches the binary search on every cell, at most 3 steps. Record SHA-256 `40c0e1d71a67998d9da47b9aec592616d3e9cb7de1ad512887835f5b087cd09a` (400 bytes). Candidate SHA-256 `c2ae946e5b4706d220e3adb940ee00e4b382f326ca4f63586d043b2990650129`. Tool SHA-256 `f21f84024417ab2896202857c6493481ef5a9b99a84efaae12c6a75ff5044650`. `spirv-val --target-env vulkan1.3 --uniform-buffer-standard-layout` accepts the original and the candidate. The private outputs are `local/grok-d8-interval/candidate.spv`, `records.bin` and `report.json`.
