# Mesa/A770 mesh lane mapping: implementation evidence

Audited 2026-11-12, CPU-only. The pinned Intel path strongly supports the mapping needed by the [conditional mesh transform](mesh-warp32-transform.md): for a 32×1×1 workgroup compiled at SIMD32, workgroup-local X is expected to equal the subgroup-local execution channel. This is a device/driver-specific implementation argument. The compiler/state portion is traced below; the final hardware ordering step remains an explicit inference rather than a new mesh execution observation. The transformer and its frozen hashes/verification flags have not been changed.

Vulkan's lack of a portable relationship between these IDs does not imply that a pinned implementation cannot establish one. Conversely, a known implementation mapping would not grant portability to another driver, device, stage, workgroup shape or dispatch width. The [Khronos builtin reference](https://docs.vulkan.org/refpages/latest/refpages/source/SubgroupLocalInvocationId.html) defines the portable boundary.

## Scope and identities

The source examined is the existing patched Mesa 26.2.4 tree used by the diagnostic driver. The compiled driver library SHA-256 remains `5ea19ac96aa52d061ecf2b8e23df7ed3d7d3b36c7d2ae7fe62f01c5a9b610aaa`. The existing compile-only original mesh result identifies Intel Arc A770 DG2, device `0x56a0`, an actual mesh executable width of 32, and original module digest `835aa59799c6ecb621034d946e2fa4eadb1aa2407d293321ae3e3052882bc9b3`.

That result is pipeline compilation evidence. It is not a capture of the live game's bound pipeline or generated hardware command packet. The graphics helper collected executable properties/statistics, not internal representations or ISA. No original mesh ISA artifact was available for this audit; none was generated through a new device call.

## Traced compiler and command-state behavior

Paths and line numbers below are relative to the audited Mesa source root, not the project repository:

| Step | Source | Consequence for this target |
|---|---|---|
| Mesh local-size metadata | `src/intel/compiler/brw/brw_compile_mesh.cpp:979` | Copies the three NIR workgroup dimensions into program data; the mesh compiler selects one width and records its bit at line 1133. |
| Mesh local-ID lowering | `src/intel/compiler/brw/brw_nir_lower_cs_intrinsics.c:24` | For mesh/task, the linear value comes from `load_local_invocation_index_intel`, not a formula built from subgroup IDs. The separate compute hardware-ID heuristic at line 325 does not apply to mesh. |
| Local X calculation | same file, lines 128–176 | With dimensions 32×1×1 and no quad derivative mode, the relevant X-major and Y-major cases both simplify to `localX = payloadLinear % 32`; Y and Z are zero for valid indices 0–31. The source module declares no derivative-group mode. |
| Payload read | `src/intel/compiler/brw/brw_from_nir.cpp:4635` | Reads `payload.local_index` into the destination. |
| Mesh payload placement | `src/intel/compiler/brw/brw_thread_payload.cpp:354` | DG2 SIMD32 receives 16-bit Local_ID.X values in R1 and R2, covering channels 0–15 and 16–31. `local_index` begins at R1, line 401. |
| Subgroup-local ID | `src/intel/compiler/brw/brw_from_nir.cpp:5316` | Uses the independent `LOAD_SUBGROUP_INVOCATION` operation. |
| Channel sequence | `src/intel/compiler/brw/brw_lower.cpp:915` | Synthesizes 0–7, adds 8 for the next eight values, and adds 16 for the upper 16. Thus subgroup-local ID is the SIMD execution-channel sequence 0–31, independent of payload contents. |
| Dispatch arithmetic | `src/intel/compiler/brw/brw_shader.cpp:981` | Computes group size 32, selected width 32, one EU thread, and a full 32-bit execution mask. |
| Mesh state | `src/intel/vulkan/genX_shader.c:984` | Programs SIMD32, MessageSIMD32, one thread, mask `0xffffffff`, LocalXMaximum 31, and EmitLocalIDX=true. The mesh distribution packet uses round-robin free distribution. |

The existing private driver has an `HS_FORCE_SIMD8` override in `brw_required_dispatch_width`; it must remain excluded from any claim about a required32 path. The captured diagnostic executable property is actual width 32, so that compilation did not silently become SIMD8. This does not establish a live game's environment or policy.

## What the official hardware documentation establishes

The official [Alchemist/Arctic Sound-M PRM index](https://www.intel.com/content/www/us/en/docs/graphics-for-linux/developer-reference/1-0/alchemist-arctic-sound-m.html) identifies these manuals as covering Arc A-Series/Flex hardware.

[Volume 9, Render Engine](https://cdrdv2.intel.com/v1/dl/getcontent/774517), printed pages 159–160 and 166–168, describes mesh workgroups as one-dimensional, excludes tiled walks/partitioning, and specifies per-lane 16-bit Local_ID.X payload values in R1/R2 for SIMD32. [Volume 2d, Structures](https://cdrdv2.intel.com/v1/dl/getcontent/774485), printed pages 65–69 and 619, defines the mesh state fields, packed local IDs in SIMD lanes, the last-thread execution mask, and the register layout. It describes local-ID batching when MessageSIMD is smaller than the thread SIMD size; the audited driver programs them equal. Round-robin distribution selects workgroups' placement; it supplies no alternative lane swizzle in the inspected driver path.

These facts support the ordinary unbatched one-dimensional sequence:

```text
payloadLinear(thread, channel) = thread * dispatchWidth + channel
localX = payloadLinear % 32
subgroupLocalID = channel

required SIMD32, group32: thread = 0, channel = 0..31
therefore localX = subgroupLocalID
```

The compiler equations and state values are directly evidenced. The first equation's exact assignment of numeric payload IDs to hardware channels is a **strong implementation inference** from the documented linear/packed, untiled, equal-message-width configuration. The cited public mesh sections do not print that explicit equation or a complete lane-by-lane generation table. Payload register ordering alone would not logically exclude every possible permutation. This audit therefore does not turn that inference into an unconditional hardware proof or a measured result. It provides a much narrower, falsifiable hardware condition than a generic Vulkan portability objection.

At SIMD8/16 the software implementation still uses unique workgroup-local X values 0–31. Its scratch algorithm does not require a particular distribution of those values across physical subgroups, provided all 32 invocations participate and the added barriers remain uniform. Agreement with the original subgroup32 lane-dependent behavior is the condition that specifically needs closing.

## Proven, observed and still assumed

| Claim | Evidence class |
|---|---|
| Local X is derived from the mesh payload linear index modulo32 | Traced pinned compiler source |
| Subgroup-local ID is synthesized as execution-channel 0–31 | Traced pinned compiler source |
| The diagnostic original mesh executable is actually SIMD32 | Existing executable-property observation |
| The driver builds equal MessageSIMD32/SIMD32, one-thread, full-mask, LocalXMaximum 31 state for this configuration | Traced command-state generator and dispatch arithmetic; no captured packet |
| Hardware supplies that one thread with Local_ID.X equal to channel 0–31 | Strong implementation inference from official PRM; not independently observed here |
| The live timed draw uses this exact original module, width and environment | Not established by this audit |
| The real transformed shader preserves every output/resource and rendering result | Not established; lane agreement alone would be insufficient |

The most direct next observation is a bounded, independently authored **mesh-stage** diagnostic on the exact driver: one 32-invocation workgroup, required32, an SSBO row indexed by local X, with subgroup-local ID/size and a written sentinel per invocation. Require all 32 rows, IDs 0–31, size 32, and equality in every row. That isolates the payload/channel edge without executing game shader code. A separate narrower-width probe can verify unique local IDs and software primitives. GPU ownership, fixed bounds/fence timeouts and frozen source/module/helper hashes must follow the existing synthetic probe discipline. No such mesh dispatch was performed in this CPU-only task.

Even a successful diagnostic would be device/driver/configuration evidence rather than a guarantee for future builds. A runtime/draw-site identity and complete real resource differential test would still be needed before using the conditional transformed module in gameplay.

## Frozen source/document evidence

The private audit metadata is `local/mesh-lane-mapping/source-build-evidence.json`, SHA-256 `4098dce6c44693dafa5af50b85c68bc163c00e4bfa4fb082368088cbde893fa3`. It records source hashes and corresponding build object entries; the inspected source timestamps precede their objects, and the build library matches the diagnostic library digest. Timestamp consistency is not a reproducible-build attestation.

| Audited source | SHA-256 |
|---|---|
| `brw_nir_lower_cs_intrinsics.c` | `f671220adcbca2ff1ca37986209c0fe0f36351cea71732d543f6a65b0d8a61e0` |
| `brw_from_nir.cpp` | `b68013886748786fa4818d73fe9a77402082e054c85fa13d465ea55a0475cb0e` |
| `brw_thread_payload.cpp` | `9157c9cf5bbd045fcf8ae58a222f94a5407d5fac4deedbd465c9eaa9b6a53fa6` |
| `brw_lower.cpp` | `0587d3a82012bb24d858e7441f8b3d04a82ae21803afdd92768bcb990edf3918` |
| `brw_shader.cpp` | `f21dcbc68aefa935584ec8345120b93f224ca23a0fd59b1789e62886021ebbc0` |
| `brw_compile_mesh.cpp` | `fdb15327ca64a2bce07a01a2f9ff60d740bda9cb166ef33e61d9aa59c196ab12` |
| `genX_shader.c` | `f89eedcd6d8884a248f25d2f308d9bbd3091f6197e5772538beaaa3fe074c8b7` |

Official PDFs were downloaded and text-extracted privately. Volume 9 PDF SHA-256 is `f730c68417a6be8c5435010267caace6da9cf4e6d00ffcba9c20643f7963faa1`; Volume 2d is `1e362eb163faace3bd5d675c63f90f5953a566b3a77f44a79eda3d9fb363d781`. Both identify March 2023 revision 1.0. No proprietary shader bytes, disassembly, complete third-party manuals or raw logs are published with this document.
