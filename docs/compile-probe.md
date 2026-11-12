# Compile-only Vulkan shader probe

`tools/compile_probe.py` compares pipeline compilation of a hash-pinned compute module with required subgroup size 32 or 16/full subgroups and with no stage subgroup requirement. It does **not** dispatch the shader. No command pool, command buffer, resource allocation/update, queue retrieval or submission is implemented in the C helper.

This provides compiler evidence before complete replay exists. It does not test semantic equivalence, memory ordering, rendering correctness or actual GPU execution time.

## Requirements and supported profile

- Python 3.11+, a C compiler, `pkg-config`, SPIRV-Tools `spirv-val` and Vulkan development headers/loader.
- Vulkan 1.3 compute device with required shader features and subgroup size 32 support.
- `VK_EXT_robustness2` and `VK_KHR_push_descriptor`; executable statistics use `VK_KHR_pipeline_executable_properties` when exposed.
- A private SPIR-V file with an explicitly supplied SHA-256, plus a driver ICD JSON whose library is directly readable for hashing.

The bounded reflector reads descriptor sets/bindings, literal descriptor-array counts and explicit push-constant member offsets. Compatible typed aliases collapse to one binding. Unknown, runtime-sized or conflicting descriptor layouts fail instead of being guessed. The current execution helper supports the target's single set-zero push-descriptor layout, not arbitrary Vulkan programs.

The exact initial target has twelve bindings: two uniform buffers, six storage-buffer bindings (the last has three descriptors), and four combined image samplers. The reflected push-constant extent is 56 bytes. The default pipeline range is 88 bytes, matching the inspected Hookshot `ensure_layout`/`MESH_FALLBACK_PUSH_END` layout. Override the range explicitly for a different supported module.

Both policy runs enable identical device features, including robust buffer access and robust buffer access 2, standard uniform layout, physical buffer addresses, 16/64-bit shader integers, dynamic storage-buffer-array indexing, extended image gather and subgroup-control features. Only the stage-required subgroup size/full-subgroup flag changes. Null descriptors are enabled if the device exposes them, as in the inspected renderer. No shader optimization-disable flag is used.

## Run locally

```sh
python3 tools/compile_probe.py \
  --input local/shader-target.spv --sha256 "$SHADER_SHA256" \
  --driver "$INTEL_ICD_JSON" --mesa-build "$MESA_BUILD_DIR" \
  --output local/compile-probes/run-001 --policy both \
  --env HS_CS32_MAX_INSTS=100000 --env HS_SPILL_BATCH=1 \
  --env HS_PUSH_SHADOW=1 --env shader_spilling_rate=1
```

The `HS_*` overrides describe the existing private Mesa patch stack; they are not settings supplied by this repository or required by ordinary Mesa. Only the listed explicit numeric overrides are accepted. Inherited versions of those overrides and `INTEL_DEBUG` are cleared before each compiler child. The input module is hash-checked, reflected and copied into a new private output directory so subsequent source-file changes cannot silently substitute a different module.

The input snapshot must pass `spirv-val` for Vulkan 1.3 with standard uniform-buffer layout before any Vulkan device is created. Each policy uses a separate initially empty Mesa shader-cache directory and a new process/device. The output directory must not already exist. A timeout stops only the helper's process group. Outputs include the private shader snapshot, validation logs, layout, helper binary, per-policy metadata/stderr and combined `report.json`.

Host helper compilation uses `-O2 -DNDEBUG` with strict warnings. `--mesa-build` additionally checks that the selected ICD library's SHA-256 equals the supplied Mesa build output, records Meson optimization settings and checks an actual compute-compiler command for optimization and `NDEBUG`. `debugoptimized` means optimized code with debug symbols; it does not mean `-O0`. Host compilation flags and Vulkan shader-pipeline flags are distinct.

## Observations and limitations

The first controlled feature-profile result is recorded as metadata in `research/compile-probe-initial.json`. A later, separately recorded required16 probe is in `research/compile-probe-subgroup16.json`; it does not replace the first report. `--policy all` compiles 32, 16 and unpinned in order; `both` preserves the initial 32/unpinned pair. Required subgroup 32 and unpinned compilation chose different actual widths and substantially different instruction, spill/fill and scratch metrics. Those are Vulkan executable statistics supplied by the driver, not inferred from an omitted requirement. Driver-reported cycle values are compiler estimates, not measured runtime. The zero-valued GRF-register statistic is preserved as reported and must not be interpreted as actual zero-register execution.

This probe uses the cached development SPIR-V variant identified by the catalog. The profile log's program key alone still does not prove it is the variant timed in that original capture. The two configurations compile the same module and layout with equal relevant features, but they do not reproduce the full live renderer or its resources.

Smaller code or scratch is a reason to continue the selective-policy experiment, **not** a speedup claim. A fixed 64-invocation workgroup needs more subgroups at width 8 than width 32. Raw executable instruction counts therefore do not give a per-workgroup runtime ratio, and reported scratch must not be extrapolated into a total-workgroup memory saving. The target reads guest memory through a pushed page-table address and writes storage-buffer aliases. Full input/output replay and cross-invocation dependency checks remain necessary before executing the candidate as a supported change. No game install or driver install is modified by this tool.
