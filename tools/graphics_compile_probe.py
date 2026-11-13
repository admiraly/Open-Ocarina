#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Prepare or run a hash-pinned, compile-only mesh graphics diagnostic."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shlex
import struct
import subprocess
import sys

try:
    from .compile_probe import ROOT, MAX_SHADER_BYTES, driver_identity, private_output, reflect, run_child
except ImportError:
    from compile_probe import ROOT, MAX_SHADER_BYTES, driver_identity, private_output, reflect, run_child

STAGES = {"fragment": (4, 16), "task": (5364, 64), "mesh": (5365, 128)}
BUILD_FLAGS = ["-std=c11", "-O2", "-DNDEBUG", "-Wall", "-Wextra", "-Werror"]
STATE_KEYS = {"color_format", "depth_format", "samples", "extent", "depth_test", "depth_write",
              "depth_compare_op", "cull_mode", "front_face", "alpha_to_coverage", "color_write_mask"}
ENABLED_FEATURES = ["robustBufferAccess", "robustBufferAccess2", "shaderInt16", "shaderInt64",
                    "shaderStorageBufferArrayDynamicIndexing", "shaderImageGatherExtended",
                    "fragmentStoresAndAtomics", "uniformBufferStandardLayout", "bufferDeviceAddress",
                    "shaderFloat16", "shaderInt8", "subgroupSizeControl", "dynamicRendering", "meshShader"]
CONTROLLED_DRIVER_OPTIONS = ("HS_CS32_MAX_INSTS", "HS_SPILL_BATCH", "HS_PUSH_SHADOW", "shader_spilling_rate",
                             "INTEL_DEBUG", "HS_SPILL_PCT", "HS_RA_DEBUG", "HS_FORCE_SIMD8", "HS_LARGE_GRF", "HS_SUBGROUP8", "HS_LARGE_GRF_BIT", "RX_VK_WARP64", "RX_VK_NOOPT")
DRIVER_POLICIES = {"diagnostic": {}, "runtime": {"HS_CS32_MAX_INSTS": "100000", "HS_SPILL_BATCH": "1",
                                               "HS_PUSH_SHADOW": "1", "shader_spilling_rate": "1"},
                   "native-safe": {"HS_SPILL_BATCH": "1", "HS_PUSH_SHADOW": "1", "shader_spilling_rate": "1"}}


def controlled_driver_environment(name, ambient):
    """Never inherit the selected compiler knobs, including unset diagnostic values."""
    if name not in DRIVER_POLICIES:
        raise ValueError("Unknown graphics driver policy")
    environment = dict(ambient)
    for key in CONTROLLED_DRIVER_OPTIONS:
        environment.pop(key, None)
    environment.update(DRIVER_POLICIES[name])
    policy = {"name": name, "controlled_options": {key: environment.get(key) for key in CONTROLLED_DRIVER_OPTIONS},
              "full_environment_snapshot": False,
              "profile_scope": "historical MAX-present compile-only comparison" if name == "runtime" else
                               "MAX absent; no global compute fallback opt-in" if name == "native-safe" else "cleared diagnostic"}
    return environment, policy


def reflect_stage(data, stage):
    """Reuse descriptor reflection through a metadata-only entry-model adapter.

    Adapted words are never written or passed to validation/the Vulkan helper.
    Original modules are separately validated and compiled without modification.
    """
    if stage not in STAGES or len(data) < 20 or len(data) % 4 or len(data) > MAX_SHADER_BYTES:
        raise ValueError("Invalid graphics module or stage")
    words = list(struct.unpack(f"<{len(data)//4}I", data))
    offset, entries, modes, capabilities = 5, [], [], []
    while offset < len(words):
        length, opcode = words[offset] >> 16, words[offset] & 65535
        if not length or offset + length > len(words):
            raise ValueError("Malformed SPIR-V instruction")
        operands = words[offset+1:offset+length]
        if opcode == 15:
            if len(operands) < 3 or operands[0] != STAGES[stage][0]:
                raise ValueError("Entry model differs from declared graphics stage")
            entries.append(operands[1])
            words[offset+1] = 5
        elif opcode == 16:
            modes.append(operands)
        elif opcode == 17:
            capabilities.extend(operands)
        offset += length
    if len(entries) != 1:
        raise ValueError("Exactly one graphics entry point is required")
    entry = entries[0]
    local = [mode[2:] for mode in modes if len(mode) == 5 and mode[:2] == [entry, 17]]
    if stage == "fragment":
        if local:
            raise ValueError("Unexpected fragment local size")
        words.extend([(6 << 16) | 16, entry, 17, 1, 1, 1])
    elif len(local) != 1:
        raise ValueError("Mesh/task requires one literal LocalSize")
    result = reflect(struct.pack(f"<{len(words)}I", *words))
    result.pop("workgroup")
    result.update(stage=stage, stage_flags=STAGES[stage][1], local_size=local[0] if local else None,
                  execution_modes=[mode[1:] for mode in modes], capabilities=sorted(set(capabilities)))
    return result


def configuration(manifest, directory):
    if not isinstance(manifest, dict) or manifest.get("schema") != 1:
        raise ValueError("Unsupported graphics diagnostic schema")
    stages = manifest.get("stages")
    if not isinstance(stages, list) or not all(isinstance(s, dict) for s in stages) or [s.get("stage") for s in stages] not in (
            ["mesh", "fragment"], ["task", "mesh", "fragment"]):
        raise ValueError("Expected ordered mesh+fragment or task+mesh+fragment stages")
    state = manifest.get("diagnostic_state")
    if not isinstance(state, dict) or set(state) != STATE_KEYS:
        raise ValueError("Every diagnostic fixed-state field must be supplied explicitly")
    for key in ("depth_test", "depth_write", "alpha_to_coverage"):
        if type(state[key]) is not bool:
            raise ValueError("Fixed-state toggles must be booleans")
    bounds = {"color_format": (1, 1000000000), "depth_format": (0, 1000000000),
              "depth_compare_op": (0, 7), "cull_mode": (0, 3), "front_face": (0, 1), "color_write_mask": (0, 15)}
    for key, (low, high) in bounds.items():
        if type(state[key]) is not int or not low <= state[key] <= high:
            raise ValueError("Unsupported diagnostic fixed-state value: " + key)
    if state["samples"] not in (1, 2, 4, 8) or type(state["samples"]) is not int:
        raise ValueError("Unsupported sample count")
    if not isinstance(state["extent"], list) or len(state["extent"]) != 2 or any(
            type(n) is not int or not 1 <= n <= 16384 for n in state["extent"]):
        raise ValueError("Unsupported diagnostic viewport extent")
    if not state["depth_format"] and (state["depth_test"] or state["depth_write"]):
        raise ValueError("Depth state requires a depth format")
    push = manifest.get("push_constant_bytes")
    if type(push) is not int or not 0 <= push <= 128 or push % 4:
        raise ValueError("Invalid explicit push range")
    if manifest.get("descriptor_mode") not in ("push", "sets"):
        raise ValueError("Explicit descriptor mode push or sets is required")
    bindings, reflected, snapshots = {}, [], []
    for stage in stages:
        path = directory / stage["path"]
        expected = stage.get("sha256")
        if path.is_symlink() or not path.is_file() or path.stat().st_size > MAX_SHADER_BYTES:
            raise ValueError("Stage input must be a bounded regular file")
        data = path.read_bytes()
        digest = hashlib.sha256(data).hexdigest()
        if not isinstance(expected, str) or digest != expected.lower():
            raise ValueError("Stage digest differs from pinned input")
        width = stage.get("required_subgroup_size")
        if type(width) is not int or width not in (0, 8, 16, 32):
            raise ValueError("Each stage requires an explicit subgroup policy (0,8,16,32)")
        row = reflect_stage(data, stage["stage"])
        if push < row["push_constant_bytes"]:
            raise ValueError("Push range does not cover reflected stage")
        for binding in row["bindings"]:
            if binding["set"] != 0:
                raise ValueError("Graphics target profile supports set zero only")
            key = binding["binding"]
            old = bindings.get(key)
            if old and any(old[k] != binding[k] for k in ("type", "count")):
                raise ValueError("Stages alias incompatible descriptor layouts")
            if old:
                old["stage_flags"] |= row["stage_flags"]
            else:
                bindings[key] = dict(binding, stage_flags=row["stage_flags"])
        row.update(sha256=digest, byte_count=len(data), required_subgroup_size=width,
                   require_full_subgroups=False)
        reflected.append(row)
        snapshots.append(data)
    if len(bindings) > 256:
        raise ValueError("Too many graphics bindings")
    return {"schema": 1, "stages": reflected, "bindings": [bindings[k] for k in sorted(bindings)],
            "push_constant_bytes": push, "descriptor_mode": manifest["descriptor_mode"],
            "diagnostic_state": state}, snapshots


def layout_text(config):
    state = config["diagnostic_state"]
    first = [config["push_constant_bytes"], len(config["bindings"]), len(config["stages"]),
             int(config["descriptor_mode"] == "push"), state["color_format"], state["depth_format"],
             state["samples"], *state["extent"], int(state["depth_test"]), int(state["depth_write"]),
             state["depth_compare_op"], state["cull_mode"], state["front_face"],
             int(state["alpha_to_coverage"]), state["color_write_mask"]]
    return " ".join(map(str, first)) + "\n" + "".join(
        f"{r['set']} {r['binding']} {r['type']} {r['count']} {r['stage_flags']}\n" for r in config["bindings"]) + "".join(
        f"{r['stage_flags']} {r['required_subgroup_size']}\n" for r in config["stages"])


def graphics_driver_identity(path, build):
    identity = driver_identity(path, build)
    if build is None:
        return identity
    commands = json.loads((build / "compile_commands.json").read_text())
    identity["graphics_compiler_flags"] = {}
    for filename in ("brw_compile_mesh.cpp", "brw_compile_fs.cpp"):
        command = next((r for r in commands if r["file"].endswith("/" + filename)), None)
        if command is None:
            raise ValueError("Missing Mesa graphics compiler command: " + filename)
        arguments = command.get("arguments") or shlex.split(command["command"])
        flags = [arg for arg in arguments if re.fullmatch(r"-O[0-3sgz]|-DNDEBUG|-g(?:[0-3])?", arg)]
        if "-O0" in flags or not any(re.fullmatch(r"-O[123sgz]", x) for x in flags) or "-DNDEBUG" not in flags:
            raise ValueError("Mesa graphics compiler is not demonstrably optimized with NDEBUG")
        identity["graphics_compiler_flags"][filename] = flags
    return identity


def run(args):
    config, modules = configuration(json.loads(args.manifest.read_text()), args.manifest.resolve().parent)
    environment, driver_policy = controlled_driver_environment(getattr(args, "driver_policy", "diagnostic"), os.environ)
    if not 1 <= args.timeout <= 600 or not 0 <= args.device <= 255:
        raise ValueError("Invalid timeout or device index")
    output = private_output(args.output)
    output.mkdir(parents=True)
    validator_version = subprocess.run([args.validator, "--version"], check=True, capture_output=True,
                                       text=True, timeout=15).stdout.splitlines()[0]
    validation = []
    for index, data in enumerate(modules):
        path = output / f"stage-{index}.spv"
        path.write_bytes(data)
        result = subprocess.run([args.validator, "--target-env", "vulkan1.3", "--uniform-buffer-standard-layout", str(path)],
                                capture_output=True, text=True, timeout=30)
        (output / f"stage-{index}.validation.txt").write_text(result.stdout + result.stderr)
        if result.returncode:
            raise ValueError("Graphics stage validation failed")
        validation.append({"stage": config["stages"][index]["stage"], "passed": True})
    layout = output / "layout.txt"
    layout.write_text(layout_text(config))
    executable = output / "compile-helper"
    flags = shlex.split(subprocess.run(["pkg-config", "--cflags", "--libs", "vulkan"],
                                      check=True, capture_output=True, text=True, timeout=15).stdout)
    subprocess.run([args.cc, *BUILD_FLAGS, str(ROOT / "tools" / "vulkan_graphics_compile_probe.c"), *flags,
                    "-o", str(executable)], check=True, capture_output=True, text=True, timeout=60)
    report = {"schema": 1, "compile_only": True, "dispatch_executed": False, "render_executed": False,
              "device_opened": False, "configuration": config, "validation": validation,
              "driver_policy": driver_policy,
              "validator": {"version": validator_version, "flags": ["--target-env", "vulkan1.3", "--uniform-buffer-standard-layout"]},
              "helper_build_flags": BUILD_FLAGS, "helper_sha256": hashlib.sha256(executable.read_bytes()).hexdigest(),
              "helper_source_sha256": hashlib.sha256((ROOT / "tools" / "vulkan_graphics_compile_probe.c").read_bytes()).hexdigest(),
              "wrapper_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
              "enabled_feature_policy": ENABLED_FEATURES + (["taskShader"] if len(modules) == 3 else []),
              "additional_feature_policy": {"nullDescriptor": "enabled if supported", "pipelineExecutableInfo": "enabled if supported",
                                            "computeFullSubgroups": False, "sampleRateShading": False},
              "limitations": ["Explicit diagnostic fixed state is not a recovered runtime pipeline or replay.",
                              "Compiler statistics and pipeline creation time do not measure GPU execution."]}
    if not args.prepare_only:
        if args.driver is None:
            raise ValueError("Explicit driver JSON required for device compilation")
        report["driver_identity"] = graphics_driver_identity(args.driver, args.mesa_build)
        environment["VK_DRIVER_FILES"] = str(args.driver.resolve())
        environment["MESA_SHADER_CACHE_DIR"] = str(output / "driver-cache")
        command = [str(executable), str(layout), str(args.device), *[str(output / f"stage-{i}.spv") for i in range(len(modules))]]
        result = run_child(command, environment, output / "compile.json", output / "compile.stderr", args.timeout)
        expected_stages = [{"stage_flags": r["stage_flags"], "required_subgroup_size": r["required_subgroup_size"],
                            "require_full_subgroups": False} for r in config["stages"]]
        if (result.get("render_executed") is not False or result.get("stages") != expected_stages
                or result.get("descriptor_mode") != config["descriptor_mode"]
                or result.get("push_constant_range_bytes") != config["push_constant_bytes"]):
            raise ValueError("Unexpected graphics helper result")
        report.update(device_opened=True, result=result)
    (output / "report.json").write_text(json.dumps(report, indent=2) + "\n")
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--prepare-only", action="store_true", help="Validate, reflect and build; never open Vulkan")
    parser.add_argument("--driver-policy", choices=tuple(DRIVER_POLICIES), default="diagnostic",
                        help="Controlled compiler overrides: cleared diagnostic, historical MAX-present runtime, or native-safe (MAX absent)")
    parser.add_argument("--driver", type=Path)
    parser.add_argument("--mesa-build", type=Path)
    parser.add_argument("--device", type=int, default=0)
    parser.add_argument("--timeout", type=int, default=180)
    parser.add_argument("--cc", default="cc")
    parser.add_argument("--validator", default="spirv-val")
    try:
        print(json.dumps(run(parser.parse_args()), indent=2))
    except (OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError) as exc:
        print(str(exc), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
