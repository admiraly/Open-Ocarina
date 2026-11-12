#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Build or explicitly execute a bounded, independently authored mesh diagnostic.

The build subcommand never loads Vulkan or invokes the execution helper.
Execution needs a reviewed prepared directory and exclusive GPU ownership.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shlex
import signal
import struct
import subprocess
import sys

try:
    from .compile_probe import driver_identity, private_output
except ImportError:
    from compile_probe import driver_identity, private_output

ROOT = Path(__file__).resolve().parents[1]
FIELDS = ("local_x", "local_y", "local_z", "local_index", "subgroup_local", "subgroup_size", "subgroup_id")
BUILD_FLAGS = ["-std=c11", "-O2", "-DNDEBUG", "-Wall", "-Wextra", "-Werror"]
SHADER_FLAGS = ["-V", "--target-env", "vulkan1.3", "-Os"]


def shader_source():
    return """#version 460
#extension GL_EXT_mesh_shader : require
#extension GL_KHR_shader_subgroup_basic : require
layout(local_size_x=32, local_size_y=1, local_size_z=1) in;
layout(triangles, max_vertices=3, max_primitives=1) out;
layout(set=0, binding=0, std430) writeonly buffer Result { uint data[]; } result;
void main() {
    SetMeshOutputsEXT(0u, 0u);
    uint slot = gl_LocalInvocationIndex * 7u;
    result.data[slot] = gl_LocalInvocationID.x;
    result.data[slot + 1u] = gl_LocalInvocationID.y;
    result.data[slot + 2u] = gl_LocalInvocationID.z;
    result.data[slot + 3u] = gl_LocalInvocationIndex;
    result.data[slot + 4u] = gl_SubgroupInvocationID;
    result.data[slot + 5u] = gl_SubgroupSize;
    result.data[slot + 6u] = gl_SubgroupID;
}
"""


def golden_words():
    return [word for index in range(32) for word in (index, 0, 0, index, index, 32, 0)]


def packed_golden():
    return struct.pack("<224I", *golden_words())


def check_output(data):
    if len(data) != 896:
        raise ValueError("Expected exactly 32 rows of seven uint32 output words")
    actual = struct.unpack("<224I", data)
    for index, (value, expected) in enumerate(zip(actual, golden_words())):
        if value != expected:
            slot, field = divmod(index, len(FIELDS))
            raise ValueError(f"Mapping differs at local-index slot {slot}, {FIELDS[field]}: {value} != {expected}")
    return hashlib.sha256(data).hexdigest()


def environment():
    return {key: value for key, value in os.environ.items()
            if key in ("PATH", "HOME", "XDG_RUNTIME_DIR", "LANG", "TMPDIR")}


def run_process(command, env, output, stem, timeout):
    with (output / f"{stem}.stdout").open("wb") as stdout, (output / f"{stem}.stderr").open("wb") as stderr:
        child = subprocess.Popen(command, env=env, stdout=stdout, stderr=stderr, start_new_session=True)
        try:
            status = child.wait(timeout=timeout)
        except (subprocess.TimeoutExpired, KeyboardInterrupt):
            os.killpg(child.pid, signal.SIGTERM)
            try:
                child.wait(timeout=2)
            except subprocess.TimeoutExpired:
                os.killpg(child.pid, signal.SIGKILL)
                child.wait(timeout=2)
            raise ValueError(f"{stem} interrupted or timed out; only its own process group stopped")
    if status:
        raise ValueError(f"{stem} failed ({status}); inspect private stderr")
    return (output / f"{stem}.stdout").read_text()


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def build(output_path):
    output = private_output(output_path)
    output.mkdir(parents=True)
    env = environment()
    versions = {}
    for tool in ("cc", "glslangValidator", "spirv-val"):
        versions[tool] = run_process([tool, "--version"], env, output, f"version-{tool}", 15).splitlines()[0]
    source = output / "mapping.mesh"
    source.write_text(shader_source())
    module = output / "mapping.spv"
    run_process(["glslangValidator", *SHADER_FLAGS, "-o", str(module), str(source)], env, output, "compile-shader", 30)
    run_process(["spirv-val", "--target-env", "vulkan1.3", str(module)], env, output, "validate-shader", 15)
    if struct.unpack_from("<2I", module.read_bytes()) != (0x07230203, 0x00010600):
        raise ValueError("Expected SPIR-V1.6 for mesh full-subgroup behavior")
    flags = shlex.split(run_process(["pkg-config", "--cflags", "--libs", "vulkan"], env, output, "pkg-config", 15))
    helper = output / "mesh-execution-helper"
    helper_source = ROOT / "tools" / "vulkan_mesh_execution_probe.c"
    run_process(["cc", *BUILD_FLAGS, str(helper_source), *flags, "-o", str(helper)], env, output, "build-helper", 60)
    report = {"schema": 1, "synthetic_only": True, "build_only": True, "gpu_executed": False,
              "tools": versions, "helper_build_flags": BUILD_FLAGS, "shader_flags": SHADER_FLAGS,
              "wrapper_sha256": digest(Path(__file__)), "helper_source_sha256": digest(helper_source),
              "source_sha256": digest(source), "spirv_sha256": digest(module), "helper_sha256": digest(helper),
              "fields": list(FIELDS), "output_words": 224, "output_bytes": 896,
              "limits": {"mesh_draw_calls": 1, "workgroups": [1, 1, 1], "local_size": [32, 1, 1],
                         "emitted_vertices": 0, "emitted_primitives": 0, "attachments": 0,
                         "fence_timeout_seconds": 2, "process_timeout_seconds": 30},
              "claims": {"real_shader_equivalence": False, "graphics_compatibility": False, "fps_gain": False}}
    (output / "build-report.json").write_text(json.dumps(report, indent=2) + "\n")
    return report


def prepared(path, expected_build_sha256):
    if path.is_symlink():
        raise ValueError("Prepared directory must not be a symlink")
    output = path.resolve()
    if output.is_relative_to(ROOT) and output.relative_to(ROOT).parts[:1] not in (("local",), ("work",), ("artifacts",)):
        raise ValueError("Prepared artifacts must remain private")
    manifest = output / "build-report.json"
    if not re.fullmatch(r"[0-9a-fA-F]{64}", expected_build_sha256) or manifest.is_symlink() or \
            digest(manifest) != expected_build_sha256.lower():
        raise ValueError("Prepared build report differs from reviewed SHA-256")
    report = json.loads(manifest.read_text())
    if report.get("schema") != 1 or report.get("build_only") is not True or report.get("gpu_executed") is not False:
        raise ValueError("Unexpected prepared build report")
    checks = {"source_sha256": output / "mapping.mesh", "spirv_sha256": output / "mapping.spv",
              "helper_sha256": output / "mesh-execution-helper", "wrapper_sha256": Path(__file__),
              "helper_source_sha256": ROOT / "tools" / "vulkan_mesh_execution_probe.c"}
    for key, artifact in checks.items():
        if artifact.is_symlink() or not artifact.is_file() or artifact.stat().st_size > 2 * 1024 * 1024 or digest(artifact) != report.get(key):
            raise ValueError(f"Prepared artifact/source hash mismatch: {key}")
    if (output / "mapping.mesh").read_text() != shader_source():
        raise ValueError("Prepared shader differs from independent synthetic source")
    if (output / "execution-report.json").exists() or (output / "output.bin").exists():
        raise ValueError("Prepared artifacts have already been executed; create a new build directory")
    return output, report


def ensure_no_games():
    for name in ("hookshot", "exefile[.]exe"):
        result = subprocess.run(["pgrep", "-i", "-c", "-x", name], capture_output=True, text=True, timeout=5)
        if result.returncode not in (0, 1):
            raise ValueError("Could not check competing game processes")
        if result.returncode == 0:
            raise ValueError("Competing game process found; mesh GPU execution refused")


def validate_metadata(result):
    if result.get("synthetic_only") is not True or result.get("mesh_draw_executed") is not True:
        raise ValueError("Unexpected mesh helper execution report")
    if result.get("vendor_id") != 0x8086 or "A770" not in result.get("device", ""):
        raise ValueError("The reviewed experiment requires Intel Arc A770")
    if result.get("actual_width") != 32 or result.get("requested_width") != 32 or \
            result.get("executable_stage") != 128 or result.get("width_source") != "pipeline_executable_properties":
        raise ValueError("Actual mesh executable width32 was not verified")
    if result.get("workgroup") != [32, 1, 1] or result.get("draw_mesh_tasks") != [1, 1, 1]:
        raise ValueError("Unexpected mesh execution dimensions")


def execute(path, driver, device, expected_build_sha256):
    if not 0 <= device <= 255:
        raise ValueError("Device index must be 0..255")
    output, build_report = prepared(path, expected_build_sha256)
    identity = driver_identity(driver)
    ensure_no_games()
    env = environment()
    env.update(VK_ICD_FILENAMES=str(driver.resolve()), VK_DRIVER_FILES=str(driver.resolve()),
               MESA_SHADER_CACHE_DISABLE="true", MESA_SHADER_CACHE_DIR=str(output / "cache"))
    # No helper, generated SPIR-V or C source is rebuilt after review.
    result = json.loads(run_process([str(output / "mesh-execution-helper"), str(output / "mapping.spv"),
                                    str(output / "output.bin"), str(device)], env, output, "execute", 30))
    validate_metadata(result)
    output_digest = check_output((output / "output.bin").read_bytes())
    report = {"schema": 1, "synthetic_only": True, "gpu_executed": True, "build": build_report,
              "driver": identity, "result": result, "all_224_words_match": True,
              "all_32_slots_written": True, "local_x_equals_subgroup_local": True,
              "output_sha256": output_digest,
              "claim_scope": "One synthetic mesh invocation mapping observation on this exact platform/configuration",
              "real_shader_equivalence": False, "transformer_flags_changed": False}
    (output / "execution-report.json").write_text(json.dumps(report, indent=2) + "\n")
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    build_parser = commands.add_parser("build", help="CPU-only source/module/helper preparation; no Vulkan API calls")
    build_parser.add_argument("--output", type=Path, required=True)
    execute_parser = commands.add_parser("execute", help="Explicit GPU operation; requires reviewed artifacts and exclusive GPU access")
    execute_parser.add_argument("--prepared", type=Path, required=True)
    execute_parser.add_argument("--driver", type=Path, required=True)
    execute_parser.add_argument("--build-sha256", required=True, help="SHA-256 of the reviewed build-report.json")
    execute_parser.add_argument("--device", type=int, default=0)
    args = parser.parse_args()
    try:
        report = build(args.output) if args.command == "build" else execute(args.prepared, args.driver, args.device, args.build_sha256)
        print(json.dumps(report, indent=2))
    except (OSError, ValueError, subprocess.SubprocessError) as error:
        print(str(error), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
