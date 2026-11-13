#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Prepare independent TaskEXT broadcast-batch fixtures; no Vulkan API calls."""
import argparse
import hashlib
import json
from pathlib import Path
import struct
import subprocess

ROOT = Path(__file__).resolve().parents[1]


def assembly(kind):
    if kind not in ("native32", "shared32"):
        raise ValueError("Unknown independent task fixture kind")
    lines = ["OpCapability Shader", "OpCapability MeshShadingEXT"]
    if kind == "native32":
        lines += ["OpCapability GroupNonUniform", "OpCapability GroupNonUniformBallot"]
    lines += ['OpExtension "SPV_EXT_mesh_shader"', "OpMemoryModel Logical GLSL450",
              'OpEntryPoint TaskEXT %main "main" %local %capture %scratch',
              "OpExecutionMode %main LocalSize 32 1 1", "OpDecorate %local BuiltIn LocalInvocationId",
              "OpDecorate %words ArrayStride 4", "OpDecorate %block Block",
              "OpMemberDecorate %block 0 Offset 0", "OpDecorate %capture DescriptorSet 0",
              "OpDecorate %capture Binding 0", "%void = OpTypeVoid", "%function = OpTypeFunction %void",
              "%uint = OpTypeInt 32 0", "%xyztype = OpTypeVector %uint 3",
              "%localptr = OpTypePointer Input %xyztype", "%words = OpTypeRuntimeArray %uint",
              "%block = OpTypeStruct %words", "%blockptr = OpTypePointer StorageBuffer %block",
              "%outptr = OpTypePointer StorageBuffer %uint"]
    lines += [f"%c{value} = OpConstant %uint {value}" for value in [*range(33), 264]]
    lines += ["%scratchtype = OpTypeArray %uint %c32", "%scratchptr = OpTypePointer Workgroup %scratchtype",
              "%laneptr = OpTypePointer Workgroup %uint", "%local = OpVariable %localptr Input",
              "%capture = OpVariable %blockptr StorageBuffer", "%scratch = OpVariable %scratchptr Workgroup",
              "%main = OpFunction %void None %function", "%entry = OpLabel",
              "%xyz = OpLoad %xyztype %local", "%lane = OpCompositeExtract %uint %xyz 0",
              "%scaled = OpIMul %uint %lane %c17", "%value = OpIAdd %uint %scaled %c3",
              "%row = OpIMul %uint %lane %c32", "%publish = OpAccessChain %laneptr %scratch %lane",
              "OpStore %publish %value", "OpControlBarrier %c2 %c2 %c264"]
    for source in range(32):
        if kind == "native32":
            lines.append(f"%value{source} = OpGroupNonUniformBroadcast %uint %c3 %value %c{source}")
        else:
            lines += [f"%read{source} = OpAccessChain %laneptr %scratch %c{source}",
                      f"%value{source} = OpLoad %uint %read{source}"]
        lines += [f"%index{source} = OpIAdd %uint %row %c{source}",
                  f"%output{source} = OpAccessChain %outptr %capture %c0 %index{source}",
                  f"OpStore %output{source} %value{source}"]
    lines += ["OpControlBarrier %c2 %c2 %c264", "OpEmitMeshTasksEXT %c0 %c1 %c1", "OpFunctionEnd"]
    return "\n".join(lines) + "\n"


def expected_output():
    # Every destination lane receives the same source-lane value sequence.
    source_values = tuple(3 + 17 * source for source in range(32))
    return struct.pack("<1024I", *(source_values * 32))


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def prepare(output):
    output = Path(output).resolve()
    if output.is_relative_to(ROOT) and output.relative_to(ROOT).parts[:1] not in (("local",), ("work",), ("artifacts",)):
        raise ValueError("Synthetic output must remain private")
    if output.exists():
        raise ValueError("Choose a fresh private fixture directory")
    output.mkdir(parents=True)
    artifacts = {}
    for kind in ("native32", "shared32"):
        source, binary = output / (kind + ".spvasm"), output / (kind + ".spv")
        source.write_text(assembly(kind))
        subprocess.run(["spirv-as", "--target-env", "spv1.6", str(source), "-o", str(binary)],
                       check=True, capture_output=True, timeout=15)
        subprocess.run(["spirv-val", "--target-env", "vulkan1.3", str(binary)],
                       check=True, capture_output=True, timeout=15)
        artifacts[kind] = {"assembly_sha256": digest(source), "module_sha256": digest(binary),
                           "module_bytes": binary.stat().st_size, "SPIRV_validation_passed": True}
    golden = output / "expected.bin"
    golden.write_bytes(expected_output())
    report = {"schema": "open-ocarina.task-broadcast-batch-fixture/v1", "prepare_only": True,
              "GPU_calls": False, "only_independently_authored_sources": True,
              "tool_sha256": digest(Path(__file__)), "artifacts": artifacts,
              "task_local_size": [32, 1, 1], "output_descriptor": {"set": 0, "binding": 0, "type": "storage_buffer", "bytes": 4096},
              "words_per_lane": 32, "golden_formula": "row[lane][source] = 3 + 17*source",
              "expected_sha256": digest(golden), "control_barriers": 2, "task_mesh_groups_emitted": [0, 1, 1],
              "native_required_width_for_future_test": 32, "shared_future_widths": [8, 16, 32],
              "lane_mapping_condition": "Native required32 subgroup source-lane index must correspond to LocalInvocationId.x; physical TaskEXT mapping remains unproved",
              "limits": {"workgroups": 1, "invocations": 32, "broadcasts_per_native_invocation": 32,
                         "shared_bytes": 128, "output_bytes": 4096, "loops": 0, "divergent_branches": 0},
              "claims": {"runtime_execution_authorized": False, "target_shader_lowering_emitted": False,
                         "TaskEXT_physical_lane_mapping_proven": False, "shader_equivalence_proven": False,
                         "rendering_equivalence": False, "FPS_gain": False}}
    (output / "report.json").write_text(json.dumps(report, indent=2) + "\n")
    for path in output.iterdir():
        if path.is_file():
            path.chmod(0o444)
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(prepare(args.output), indent=2))


if __name__ == "__main__":
    main()
