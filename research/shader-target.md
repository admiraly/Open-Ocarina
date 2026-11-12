# First shader target: cc62764061c44f6a

Status: native compute identity and a cached SPIR-V variant identified; no replay or optimization is complete. The algorithm and originating asset archive are **unknown**.

The first full-workload timing table attributes 7.517 ms/frame to this compute program across six dispatches. The combined timestamped total is 80.52 ms. Timestamp instrumentation waits for earlier GPU work, so these figures rank work; they are not normal frame latency or a promised recoverable FPS budget. A program key alone does not prove which texture-answer variant ran in that capture.

## Verified identity

`tools/shader_catalog.py` found this program in both the stable and development caches. Both decoded variants have answer hash `095931037888515a`, workgroup **64 × 1 × 1**, zero declared shared-memory bytes, one NVN compute stage (5), and Vulkan compute stage bit 32. Workgroup size is not subgroup width.

The development SPIR-V contains 64,814 words. Its SHA-256 is `b9af52c0ba07f64eb8b0ca2905c432d094999fdf669aa95182ff7754b510e97b`. The stable variant contains 65,730 words and has a different digest. A smaller module is not evidence of faster execution.

The local shader seed contains the exact native compute stage at index 31,129. Its control-plus-code SHA-256 is `d25230e04da68341232d28cee40127b6ad03fed45badc68cfcb510ea5587a8be`; its native code area is 43,264 bytes.

Important format distinction: the seed stores the truncated SHA-1 key `43c6c94992030687`, but Hookshot recomputes runtime keys using FNV-1a over the first 216 control bytes followed by the sized native code area. For compute, that runtime stage key is the profiler's program key. Searching only the seed's stored key would miss this target.

Exact metadata, including cache stamps and digests, is in [target-cc62764061c44f6a.json](target-cc62764061c44f6a.json). No executable or shader bytes are included.

## A justified experiment

Private CPU-only disassembly of the exact development SPIR-V showed:

- Only `LocalInvocationId` and `WorkgroupId` built-ins.
- No group, subgroup, cooperative-matrix or barrier opcodes; no atomic opcodes.
- No Workgroup-storage variables.
- 72 function calls and six loop merges.

It passes `spirv-val --target-env vulkan1.3 --uniform-buffer-standard-layout` with SPIRV-Tools v2026.1. The default validator rejects its uniform array stride unless that feature flag is supplied. Validation checks structural rules, not mathematical equivalence or absence of races.

This makes **selective removal of the required 32-lane subgroup size for this exact program and variant** a concrete experiment. It is not proof that unpinning is safe: global-memory accesses can encode implicit lockstep assumptions or races without explicit subgroup instructions. Inspect those dependencies and compare complete dispatch outputs first. Keep every other shader pinned. Measure actual compiler-chosen width, spills, scratch, and execution time; do not infer width from omission of the requirement.

The source insertion point is `compute_pipeline_info(const Program&, bool)` in Hookshot's `gpu/vk/vk_draw.cpp`: it has the program identity and calls `pin_subgroup(ci.stage, q.workgroup[0])`. A keyed experiment belongs there rather than globally changing device subgroup capabilities. Pipeline-cache identity must include the new policy, and cached-pipeline probes and actual creation must use the same policy. This repository does not yet carry that renderer patch.

## Source evidence

Inspected packaged source: Hookshot `v2.1.0-rc.3-306-g4cf785a0`.

- [Runtime key and stage dump](https://github.com/NotRexed/Hookshot/blob/4cf785a0/gpu/vk/vk_shader.cpp): `code_key`, `read_code`, `program_pick`, `seed_open`.
- [Cache layout](https://github.com/NotRexed/Hookshot/blob/4cf785a0/gpu/vk/vk_progcache.cpp): `put_head`, `program_cache_save`; record heads do not contain descriptor contents or dispatch grids.
- [Seed writer](https://github.com/NotRexed/Hookshot/blob/4cf785a0/nx2/shader_census.py): `HKSEED01`, native stage deduplication, stored SHA-1 key.
- [Subgroup policy and dispatch](https://github.com/NotRexed/Hookshot/blob/4cf785a0/gpu/vk/vk_draw.cpp): `pin_subgroup`, `compute_pipeline_info`, `vkCmdDispatch` / `vkCmdDispatchIndirect` call sites.
- [Intel memory-table loop](https://github.com/NotRexed/Hookshot/blob/4cf785a0/gpu/vk/eden/shader_recompiler/backend/spirv/spirv_emit_context.cpp): `DefineGlobalMemoryFunctions` emits a uniform counter, `DontUnroll`, first matching table-entry access and return. That reduces code expansion but does not eliminate table searches or prove a runtime gain.

Next: implement the complete capture contract in [shader-replay.md](../docs/shader-replay.md), then validate the selective-width experiment on this target.
