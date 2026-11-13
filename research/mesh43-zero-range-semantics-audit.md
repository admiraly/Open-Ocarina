# 43ec zero-range control audit

The zero pre-cull range path does reach 8193 translated loop-batch visits under stable, defined inputs when the workgroup passes its outer guard. However, the corresponding original GPU instructions also use an unsigned shift and unsigned entry comparison. This audit does **not** establish a signedness bug, a candidate-only regression, valid game use of a zero range, or measured wasted GPU work. [Source-pinned metadata](mesh43-zero-range-semantics-audit.json) separates these findings and remaining gaps.

The native seed's program-list 1650 links task stage 1360/code-key `8fb3abacd57f1ee6`, mesh `8d46218ca9ecd4f9` and fragment `7d1fd77e4a67127b`. Hookshot's recorded FNV/code-key and mesh-key recipes reproduce program `43ec86f35fd937b9` with zero written color types. The task's control-plus-end-sized-code SHA-256 is `4d6d916a9d6043cb3aa48c0be961334abf63f59f9dbc3f7ff55856f642101095`; the 627 instructions retained by the existing capture-state decoder hash to `c35ae0a28743fbda3110f6c65c59071f33fe716b5055c304154805d265a69867`. Generated native/lowered bodies remain private.

Both baseline and candidate caches identify task SPIR-V `5e7d8f02e2dbd5ae1cd625d42366bc532dcdf7d71bb56ac3bc303b5e9655d086`. Their count control is identical. The audited raw stage has 32 threads and 3200 payload bytes; those control fields do not declare the application work-range invariant.

## Translated behavior versus valid input

Here delta is the **pre-cull work-range end minus begin**, clamped to 256. It is not the accepted-item count N written after culling. The loop bound is `(min(delta,256)-1)>>5` in unsigned 32 arithmetic. With delta 0 it becomes 134217727, and the unsigned `bound>=0` entry test imposes no exclusion. Per-item work is skipped, but the loop batches continue until the generic private guard cuts them after 8193 visits. No other dominant shader condition was found except `WorkgroupId.x<=4095`, whose false arm skips the loop entirely.

For positive delta 1–256, the loop visits 1–8 batches. Every item may be culled, yielding accepted N=0, without taking the zero-delta 8193 path. Ordinary cull-to-zero output therefore does not demonstrate this suspected wasted-work case.

The actual game may guarantee strictly positive ranges for every submitted task workgroup. API-shaped buffers can contain equal endpoints, but that does not prove those bytes are valid game input. Worklist construction and the submitted task-grid/range relationship remain unproved; no live valid delta 0 occurrence was captured. Missing, stale or inconsistent inputs could also manufacture zero and indicate a separate input/provenance fault.

## Original instruction and emitter checks

At native offsets 0x140–0x160, the original stage subtracts one, performs a U32 logical right shift by 5, and compares the result with zero using unsigned greater-or-equal. The native signedness/type fields agree with the cached SPIR-V and with Mesa's SM70 encoder definitions. The existing Hookshot/Eden lowering was run CPU-only over the exact captured 627 instructions; it emits the corresponding logical shift and unsigned compare.

The native loop tests next-iteration greater than the bound at 0x2a0, saves that predicate using P2R mask 2 at 0x2b0, and tests the saved register for nonzero at 0x2610. The latter comparison's signed bit does not change equality semantics. A predicated CALL.NOINC at 0x2640 exits to0x2660; otherwise the unconditional branch at 0x2650 returns to0x260. The P2R is not an unconditional constant 2 assignment. These sites provide no evidence that the translation lost a positive-range exit.

The private 8192 counter is a separate emulator artifact: `emit_spirv.cpp`'s Repeat case injects Private 0x2000, a decrement and signed nonnegative test while the default `disable_shader_loop_safety_checks` setting is false. The cached module contains that exact pattern. It is a safety cutoff, not evidence of an incorrectly translated native 8192 loop limit.

This targeted check did not fully recompile the seed into the exact cached answer variant or execute original ISA. Semantic loop visits are not a measured instruction count or duration; a driver may optimize work and the live input distribution is unknown. The strongest remaining question is the application's positive-range contract. No signedness patch, zero-work bypass, vendor change, GPU run, visual-fault diagnosis or FPS claim is justified by this finding.
