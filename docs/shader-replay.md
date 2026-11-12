# Reproducible shader investigation

The first target is [cc62764061c44f6a](../research/shader-target.md). The current tools identify input/output shader modules without publishing code. They do **not** replay a dispatch.

## Metadata catalog

Use an existing, stopped Hookshot installation. Do not read a live cache while the game writes it. Set `GAME_DIR` to a user-supplied local installation, outside the tracked repository.

```sh
mkdir -p local
python3 tools/shader_catalog.py cache "$GAME_DIR/hookshot_programs.bin" \
  --program cc62764061c44f6a > local/target-cache.json
python3 tools/shader_catalog.py seed "$GAME_DIR/hookshot_shaders.bin" \
  --program cc62764061c44f6a > local/target-seed.json
```

The cache parser checks HSP1/HSP2 framing, bounds, count limits, selected first-stage metadata and source-file stability. HSP2 compressed payloads are explicitly opaque; they are not silently treated as uncompressed data. Multi-stage records expose only their first stage because the following `Shader::Info` contains compiler-ABI-dependent storage. A cache stamp is reported but not independently authenticated against a renderer build. This is a metadata inspection tool, not a complete untrusted-cache validator or a replacement for Hookshot's reader.

The seed parser recomputes compute-stage runtime keys and validates stage extents and program indices. The stored seed key and runtime key use different hash algorithms. It never prints native instructions. Catalog outputs contain hashes, sizes, flags and identities, not buffer contents; inspect metadata before publishing it.

## Existing diagnostics and their limits

These switches were inspected in the packaged Hookshot development source. They are diagnostic operations for a separate local experiment, not commands to run simultaneously with a performance benchmark.

- `RX_VK_DUMP_STAGES=<local folder>`: native control and code as the game supplied them, named by runtime stage key. It executes on code reads; a warm cache may avoid translation and later dump paths.
- `RX_VK_IR=cc62764061c44f6a`: Eden IR during translation, plus a program-key-matched SPIR-V dump. IR files are written in the game working folder. Use a separate disposable install; never publish these files.
- `RX_VK_DUMP_SPV=<local folder>` with `RX_VK_MISS_LOG_PROG=cc62764061c44f6a`: compute SPIR-V at translation. The filter is a prefix in upstream, so use the full key. Variants share a filename and can overwrite each other; capture the variant/digest separately.
- `RX_VK_DUMP=<present>` and `RX_VK_DUMP_DIR=<local folder>` arm a frame dump. `RX_VK_DUMP_SSBO=cc62764061c44f6a` adds storage-buffer diagnostics and indirect-grid data where implemented.
- `RX_VK_DRAW_STATS_PROGS=cc62764061c44f6a` supplies descriptor/buffer diagnostics. The CPU diagnostic records only the first 2 KB of uniform bindings and 1 KB of storage bindings, plus selected nonzero data. Post-GPU sampling uses bounded slots. These are **not complete inputs or outputs**.
- `RX_VK_DUMP_LIVE=1` reads later guest state instead of submitted state for storage diagnostics. Do not use that mode to establish dispatch input identity.

Global address tables and physical-storage-buffer addresses matter. Copying only explicitly bound buffers can miss indirectly referenced allocations. An image dump or a storage-buffer excerpt cannot establish complete equivalence.

## Capture contract to implement

Add an opt-in, full-key-and-variant filter around the compute dispatch after descriptor and push-constant binding, but before executing it. Selection must include native present, submission, and dispatch ordinal: this program runs several times per frame with potentially different inputs.

The private capture must contain:

1. Native stage identity, exact SPIR-V digest/code, translator/cache stamp, pipeline layout, specialization constants, required subgroup policy, selected device features and driver identity.
2. The selected texture-answer variant, complete descriptor set contents and descriptor-buffer ranges, push constants, complete uniform data **as submitted**, inline uniform updates, direct grid or the GPU-produced indirect grid, and local workgroup dimensions.
3. Every reachable buffer and image read/write region, including pointer-table allocations, aliased ranges and physical-address targets. Preserve overlap relationships and record how original addresses are mapped. Missing or unclassified reachability is a capture failure, not permission to ignore data.
4. Correctly synchronized before/after device snapshots. Capture bytes from the GPU-visible mirror at the selected command position; later CPU guest memory is not interchangeable. Record image format, layout, mip, layer, sample count and aspect.
5. Expected complete writable outputs, with input and output hashes. Original and optimized variants must start from identical snapshots. Preserve floating-point controls and compare integer/pointer outputs exactly; any float tolerance needs an explicit per-output rationale.

A standalone Vulkan harness then loads the capture, recreates resources and pipeline state, runs a single dispatch, compares outputs, and measures repeated warm execution separately from capture overhead. Physical pointers may require controlled address replay or verified relocation of all pointer-bearing data. That remains unimplemented.

## First renderer instrumentation change

Hookshot's `compute_pipeline_info` already receives `Program::key` and has a single subgroup-pinning call. Make a keyed policy helper consume `(program key, variant, SPIR-V digest)` and use it identically for cache probes, precompile and creation. Log actual policy and stage digest with each created pipeline. Keep the policy in cache identity; avoid changing all shader stages through `RX_VK_NO_SUBGROUP_PIN`.

For cc62764061c44f6a, lack of subgroup built-ins/opcodes and zero shared memory justify examining unpinning. They do not prove absence of implicit inter-invocation communication. No selective-width patch or speedup should be merged before a complete output check.

## Acceptance criteria

- A capture is deterministic across repeated baseline replays with complete region coverage.
- Changing an input byte used by the dispatch changes the recorded input identity and is not hidden by a stale cache.
- The proposed pipeline change passes complete output checks across more than one captured dispatch/frame.
- Timings use identical captured work and a separate fully compiled gameplay check with the same camera/settings, no competing GPU workloads, and repeated alternating runs.
- Publish source and aggregate measurements only. Keep native code, SPIR-V, descriptors containing private data, resource bytes and images in ignored local storage.
