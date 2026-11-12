# Synthetic mesh lane-mapping observation on Arc A770

Observed 2026-11-12: one independently authored mesh workgroup on the audited Arc A770/Mesa 26.2.4 path reported `LocalInvocationId.x == LocalInvocationIndex == SubgroupLocalInvocationId` for every invocation 0–31. Local Y/Z were zero, subgroup size was 32 and subgroup ID was zero in every row. All 224 output words matched the CPU golden result. Executable properties independently confirmed an actual mesh executable width of 32.

This closes the missing **observation for this synthetic mesh configuration** in the [source/PRM mapping investigation](mesh-lane-mapping.md). It does not establish that a particular live draw uses the same policy/module, or that the conditional real mesh transform preserves its outputs/resources. No original game shader or assets were executed. No FPS or rendering-equivalence result is claimed, and the real transform's verification flags remain unchanged.

## Diagnostic design

[`tools/mesh_mapping_probe.py`](../tools/mesh_mapping_probe.py) generates an independently authored, straight-line GLSL mesh shader. It uses one workgroup of 32×1×1 invocations, requires subgroup width 32, declares small output maxima and calls `SetMeshOutputsEXT(0, 0)` uniformly. It emits no vertices or primitives. Each invocation writes seven uint32 words to a row indexed by `LocalInvocationIndex`:

| Field | Expected value for row `i` |
|---|---|
| Local X/Y/Z | `i`, `0`, `0` |
| Local invocation index | `i` |
| Subgroup-local invocation ID | `i` |
| Subgroup size | `32` |
| Subgroup ID | `0` |

The separate [`tools/vulkan_mesh_execution_probe.c`](../tools/vulkan_mesh_execution_probe.c) creates a Vulkan 1.3 mesh-only graphics pipeline, with rasterizer discard and dynamic rendering over a 1×1 area with no attachments. It records exactly one `vkCmdDrawMeshTasksEXT(1, 1, 1)` call. There is no task/fragment shader, window/swapchain, input asset, texture, image attachment or game program. Neither graphics nor compute compile-only helper was changed to execute commands.

The result SSBO is 896 bytes, allocated from host-visible coherent memory and initialized to `0xcd` sentinels. After the draw, a mesh-shader-write-to-host-read barrier precedes the fence. Every expected word is compared exactly, including the index and physical subgroup fields. Missing rows, sentinel words, permutations or a narrower width fail the check. No tolerance or skipped output positions are used.

Enabled features are `meshShader`, `dynamicRendering`, `subgroupSizeControl`, `pipelineExecutableInfo`, `robustBufferAccess` and `vertexPipelineStoresAndAtomics`. Required device extensions are `VK_EXT_mesh_shader` and `VK_KHR_pipeline_executable_properties`. Subgroup basic operations and mesh size limits are checked. The helper requires the executable-property query to identify exactly one mesh executable at width 32 before submitting the draw.

The optional `REQUIRE_FULL_SUBGROUPS` stage flag is left unset. The [Vulkan stage-create rules](https://docs.vulkan.org/refpages/latest/refpages/source/VkPipelineShaderStageCreateInfo.html) permit it for mesh/task/compute when its feature requirements are met; this probe instead uses the SPIR-V 1.6 rule. The generated module is SPIR-V 1.6, with a fixed X dimension divisible by the required subgroup size, supplying the applicable mesh full-subgroup condition. The shader directly records subgroup size and ID as additional checks. Pipeline optimization is enabled, and the environment excludes inherited driver overrides, layers and injection variables.

## Preparation, review and bounded execution

The default preparation command performs **CPU-only** shader generation, `spirv-val` validation and optimized helper compilation. It does not invoke the helper, load Vulkan or enumerate a device:

```sh
python3 tools/mesh_mapping_probe.py build --output local/mesh-mapping/new-build
sha256sum local/mesh-mapping/new-build/build-report.json
```

Execution is a separate explicit command, using the reviewed report digest:

```sh
python3 tools/mesh_mapping_probe.py execute \
  --prepared local/mesh-mapping/new-build --driver "$INTEL_ICD" \
  --build-sha256 "$REVIEWED_BUILD_REPORT_SHA256"
```

The execution path rehashes the wrapper, C source, generated GLSL/SPIR-V and helper binary against that report and performs no rebuild. It refuses changed artifacts, symlinks, an already executed output directory and repository output outside the ignored artifact directories. It checks case-insensitively for Hookshot/EVE processes and aborts if present; the operator must separately coordinate exclusive GPU ownership. It never signals an existing application. The observed run received an explicit exclusive synthetic GPU lease after the game capture stopped; the lease was released immediately after completion.

The shader contains no loops or polling. The fence wait is limited to two seconds, with a separate 30-second helper-process limit. A failed fence exits without queue/device idle calls or destruction waits. Process timeout/interruption terminates only the helper's new process group. Successful execution cleans up after the signaled fence. These are bounded host wait policies, not a recovery guarantee for a failed kernel/device.

## Frozen observed artifacts

Two separately authorized, single-draw executions passed with identical output. The final observed run uses ignored `local/mesh-mapping/build-03/`. It followed a comment-only correction and fresh preparation; the optimized binary and generated module remained identical. The earlier run and its exact tested C source are preserved under `build-02/`. The final published C source matches the fresh tested source hash below.

| Artifact | SHA-256 |
|---|---|
| Reviewed build report | `4008aa610c16c078865a2a4f414eb5b902e80c4b32b5f3f22fb5661e6decdf13` |
| Python wrapper | `70c23f954defe88101fe9001377fe391bdbf4885073b1f4c043875985d29127c` |
| Execution-helper C source | `3322c25b11aa8d3de0af9ab1d2b6503853bbbd287102c5acbf03d5d475fc2df1` |
| Optimized helper binary | `b20ee3990bb5e4e8db2397b64e59d795cc41a6634c17b574194ecc1d2ad61ac3` |
| Generated GLSL | `050b7c0814093c5be8944ad547ece44b013216462b9251ca094e507ff73d351c` |
| Generated SPIR-V | `3dec7d029c694e13d178c6d4466d9b9839d7260b6660b7394e33b7f4ad2c84d6` |
| All 224 output words | `ee3ad33deb8d802d8ffed9b83c59cf5fcb52b7f1a3d09d2ebcaf2c1c25fcdc03` |

Device: Intel Arc A770 Graphics (DG2), vendor `0x8086`, device `0x56a0`. Mesa reports 26.2.4. Driver library SHA-256 is `5ea19ac96aa52d061ecf2b8e23df7ed3d7d3b36c7d2ae7fe62f01c5a9b610aaa`; explicit ICD JSON SHA-256 is `7b882d02f366202e532410da637178b53f549152ee1e8c174ed79a433c333d60`. No driver or game installation changed.

Build tools: GCC 16.2.1 with `-std=c11 -O2 -DNDEBUG -Wall -Wextra -Werror`; glslang 16.2.0 with `-V --target-env vulkan1.3 -Os`; SPIRV-Tools v2026.1 validation. Private execution metadata records actual stage `128`/width `32`, requested width 32, dimensions, features, driver identity and output digest. Generated modules, output bytes and logs remain private.

Eight standard-library regression tests require no GPU or game files. They check every output position, sentinel/length failures, preparation's no-execution boundary, reviewed-manifest/artifact hashes, repeat-execution refusal, actual mesh-width validation, environment isolation, competing-game refusal and the separate helper's single draw/bounded wait structure.

The observed equality is useful implementation evidence for this exact platform/configuration. Generalizing it to the original mesh module requires a separately reviewed argument that the relevant compiler/payload path is the same, a draw-site identity/policy observation and complete resource/output validation. This test alone authorizes no real-shader execution or blanket subgroup-policy change.
