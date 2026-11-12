#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Execute only independently generated, bounded synthetic logical-warp shaders."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shlex
import signal
import struct
import subprocess
import sys

from compile_probe import driver_identity, private_output

ROOT = Path(__file__).resolve().parents[1]
FIELDS = ("lane", "broadcast7", "shuffle_xor1", "ballot0", "ballot1", "ballot2", "ballot3", "any", "all", "logical_size")
CASES = ("all_false", "all_true", "alternating", "lane31_only", "deterministic_random")
BUILD_FLAGS = ["-std=c11", "-O2", "-DNDEBUG", "-Wall", "-Wextra", "-Werror"]


def inputs():
    """Fixed unsigned integer values and five independently specified predicates."""
    result = []
    for case in range(5):
        values = [((lane + 1) * 0x9E3779B9 ^ (case + 17) * 0x85EBCA6B ^ (lane << 17)) & 0xFFFFFFFF
                  for lane in range(32)]
        predicates = [[False] * 32, [True] * 32, [lane % 2 == 0 for lane in range(32)],
                      [lane == 31 for lane in range(32)],
                      [((lane * 73 + 19) ^ (lane * lane * 11)) % 17 < 8 for lane in range(32)]][case]
        result.append((values, predicates))
    return result


def packed_input():
    return pack([word for values, predicates in inputs() for word in values + [int(p) for p in predicates]])


def golden_words():
    words = []
    for values, predicates in inputs():
        mask = sum(1 << lane for lane, predicate in enumerate(predicates) if predicate)
        for lane in range(32):
            words.extend([lane, values[7], values[lane ^ 1], mask, 0, 0, 0,
                          int(any(predicates)), int(all(predicates)), 32])
    return words


def pack(words):
    return struct.pack(f"<{len(words)}I", *words)


def check_output(data):
    expected = pack(golden_words())
    if len(data) != len(expected):
        raise ValueError("Unexpected output length")
    words = struct.unpack(f"<{len(data) // 4}I", data)
    for offset, (actual, wanted) in enumerate(zip(words, golden_words())):
        if actual != wanted:
            row, field = divmod(offset, len(FIELDS))
            case, lane = divmod(row, 32)
            raise ValueError(f"Mismatch in {CASES[case]} lane {lane} {FIELDS[field]}: {actual} != {wanted}")
    return hashlib.sha256(data).hexdigest()


def shader_source(software):
    extensions = "" if software else """#extension GL_KHR_shader_subgroup_basic : require
#extension GL_KHR_shader_subgroup_ballot : require
#extension GL_KHR_shader_subgroup_shuffle : require
#extension GL_KHR_shader_subgroup_vote : require
"""
    shared = "shared uint values[32];\nshared uint bits[32];\nshared uint mask;\n" if software else ""
    operations = """values[lane] = value;
        bits[lane] = pred ? (1u << lane) : 0u;
        barrier();
        if (lane == 0u) {
            uint total = 0u;
            for (uint i = 0u; i < 32u; ++i) total |= bits[i];
            mask = total;
        }
        barrier();
        uint logicalLane = lane;
        uint broadcastValue = values[7];
        uint shuffleValue = values[lane ^ 1u];
        uvec4 ballotValue = uvec4(mask, 0u, 0u, 0u);
        bool anyValue = mask != 0u;
        bool allValue = mask == 0xffffffffu;
        uint logicalSize = 32u;""" if software else """uint logicalLane = gl_SubgroupInvocationID;
        uint broadcastValue = subgroupBroadcast(value, 7u);
        uint shuffleValue = subgroupShuffleXor(value, 1u);
        uvec4 ballotValue = subgroupBallot(pred);
        bool anyValue = subgroupAny(pred);
        bool allValue = subgroupAll(pred);
        uint logicalSize = gl_SubgroupSize;"""
    return f"""#version 450
{extensions}layout(local_size_x=32, local_size_y=1, local_size_z=1) in;
layout(set=0, binding=0, std430) readonly buffer Inputs {{ uint data[]; }} inputData;
layout(set=0, binding=1, std430) writeonly buffer Outputs {{ uint data[]; }} outputData;
{shared}void main() {{
    uint lane = gl_LocalInvocationID.x;
    for (uint c = 0u; c < 5u; ++c) {{
        uint value = inputData.data[c * 64u + lane];
        bool pred = inputData.data[c * 64u + 32u + lane] != 0u;
        {operations}
        uint base = (c * 32u + lane) * 10u;
        outputData.data[base] = logicalLane;
        outputData.data[base + 1u] = broadcastValue;
        outputData.data[base + 2u] = shuffleValue;
        outputData.data[base + 3u] = ballotValue.x;
        outputData.data[base + 4u] = ballotValue.y;
        outputData.data[base + 5u] = ballotValue.z;
        outputData.data[base + 6u] = ballotValue.w;
        outputData.data[base + 7u] = uint(anyValue);
        outputData.data[base + 8u] = uint(allValue);
        outputData.data[base + 9u] = logicalSize;
        // Every invocation reaches this barrier before shared scratch is reused.
        barrier();
    }}
}}
"""


def run_process(command, environment, output, stem, timeout):
    """Bound only this newly created process group; logs contain synthetic data."""
    with (output / f"{stem}.stdout").open("wb") as out, (output / f"{stem}.stderr").open("wb") as err:
        child = subprocess.Popen(command, env=environment, stdout=out, stderr=err, start_new_session=True)
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


def ensure_no_games():
    for name in ("hookshot", "exefile.exe"):
        result = subprocess.run(["pgrep", "-c", "-x", name], capture_output=True, text=True, timeout=5)
        if result.returncode not in (0, 1):
            raise ValueError("Could not check competing game processes")
        if result.returncode == 0:
            raise ValueError("A competing game process is running; synthetic GPU execution aborted")


def validate_result(result, width):
    if result.get("synthetic_only") is not True or result.get("dispatch_executed") is not True:
        raise ValueError("Unexpected execution helper result")
    if result.get("vendor_id") != 0x8086 or "A770" not in result.get("device", ""):
        raise ValueError("This experiment requires the explicitly selected Intel Arc A770")
    if result.get("requested_width") != width or result.get("actual_width") != width or \
            result.get("width_source") != "pipeline_executable_properties":
        raise ValueError("Actual compiled subgroup width was not verified")


def run(args):
    if not 0 <= args.device <= 255:
        raise ValueError("Device index must be 0..255")
    ensure_no_games()
    identity = driver_identity(args.driver)
    output = private_output(args.output)
    output.mkdir(parents=True)
    # Do not inherit driver experiments, layers, injection or implicit settings.
    environment = {key: value for key, value in os.environ.items()
                   if key in ("PATH", "HOME", "XDG_RUNTIME_DIR", "LANG", "TMPDIR")}
    environment.update(VK_ICD_FILENAMES=str(args.driver.resolve()), VK_DRIVER_FILES=str(args.driver.resolve()),
                       MESA_SHADER_CACHE_DIR=str(output / "cache"), MESA_SHADER_CACHE_DISABLE="true")
    tool_versions = {}
    for tool in ("cc", "glslangValidator", "spirv-val"):
        tool_versions[tool] = run_process([tool, "--version"], environment, output, f"version-{tool}", 15).splitlines()[0]
    flags = shlex.split(run_process(["pkg-config", "--cflags", "--libs", "vulkan"], environment, output, "pkg-config", 15))
    helper = output / "execution-helper"
    run_process(["cc", *BUILD_FLAGS, str(ROOT / "tools" / "vulkan_execution_probe.c"), *flags, "-o", str(helper)],
                environment, output, "build", 60)
    modules = {}
    for software in (False, True):
        name = "software" if software else "native"
        source = output / f"{name}.comp"
        source.write_text(shader_source(software))
        binary = output / f"{name}.spv"
        run_process(["glslangValidator", "-V", "--target-env", "vulkan1.3", "-Os", "-o", str(binary), str(source)],
                    environment, output, f"compile-{name}", 30)
        run_process(["spirv-val", "--target-env", "vulkan1.3", str(binary)], environment, output, f"validate-{name}", 15)
        modules[name] = {"source_sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
                         "spirv_sha256": hashlib.sha256(binary.read_bytes()).hexdigest()}
    input_path = output / "input.bin"
    input_path.write_bytes(packed_input())
    report = {"schema": 1, "synthetic_only": True, "driver": identity, "tools": tool_versions,
              "helper_build_flags": BUILD_FLAGS, "shader_flags": ["-V", "--target-env", "vulkan1.3", "-Os"],
              "helper_sha256": hashlib.sha256(helper.read_bytes()).hexdigest(),
              "helper_source_sha256": hashlib.sha256((ROOT / "tools" / "vulkan_execution_probe.c").read_bytes()).hexdigest(),
              "modules": modules, "input_sha256": hashlib.sha256(input_path.read_bytes()).hexdigest(),
              "cases": list(CASES), "fields": list(FIELDS), "runs": [],
              "limits": {"dispatches_per_process": 1, "workgroups": 1, "invocations": 32, "cases": 5,
                         "fence_timeout_seconds": 2, "process_timeout_seconds": 30,
                         "uniform_callers_only": True, "multiple_warps_supported": False},
              "claims": {"real_shader_equivalence": False, "graphics_compatibility": False, "fps_gain": False}}
    reference = None
    for name, width in (("native", 32), ("software", 8), ("software", 16), ("software", 32)):
        ensure_no_games()
        stem = f"{name}-{width}"
        destination = output / f"{stem}.bin"
        metadata = json.loads(run_process([str(helper), str(output / f"{name}.spv"), str(input_path),
                                          str(destination), str(width), str(args.device)], environment, output, stem, 30))
        validate_result(metadata, width)
        data = destination.read_bytes()
        digest = check_output(data)
        if reference is not None and data != reference:
            raise ValueError("Software output differs from native32")
        reference = data
        report["runs"].append({"implementation": name, "result": metadata,
                               "output_sha256": digest, "all_1600_words_match_cpu": True})
    report["all_outputs_identical"] = True
    (output / "report.json").write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps({"synthetic_only": True, "variants_passed": len(report["runs"]),
                      "words_per_variant": 1600, "all_outputs_identical": True}))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--driver", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--device", type=int, default=0)
    args = parser.parse_args()
    try:
        run(args)
    except (ValueError, OSError, subprocess.SubprocessError, json.JSONDecodeError) as error:
        print(str(error), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
