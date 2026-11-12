#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Hash-pin SPIR-V and inspect Vulkan compilation without dispatching shader code."""
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

ROOT = Path(__file__).resolve().parents[1]
MAX_SHADER_BYTES = 16 * 1024 * 1024


def reflect(data):
    if len(data) < 20 or len(data) % 4 or len(data) > MAX_SHADER_BYTES:
        raise ValueError("Invalid SPIR-V length")
    words = struct.unpack(f"<{len(data) // 4}I", data)
    if words[0] != 0x07230203 or not 0 < words[3] <= 1_000_000:
        raise ValueError("Unsupported SPIR-V header")
    types, constants, decorations, member_offsets, variables = {}, {}, {}, {}, []
    entry_points, local_sizes = [], {}
    offset = 5
    while offset < len(words):
        length, opcode = words[offset] >> 16, words[offset] & 65535
        if not length or offset + length > len(words):
            raise ValueError("Malformed SPIR-V instruction")
        operands = words[offset + 1:offset + length]
        if opcode == 15:
            if len(operands) < 3:
                raise ValueError("Malformed entry point")
            raw_name = struct.pack(f"<{len(operands) - 2}I", *operands[2:])
            if b"\0" not in raw_name:
                raise ValueError("Unterminated entry-point name")
            entry_points.append((operands[0], operands[1], raw_name.split(b"\0", 1)[0].decode("utf-8")))
        elif opcode == 16 and len(operands) == 5 and operands[1] == 17:
            local_sizes[operands[0]] = list(operands[2:])
        elif 19 <= opcode <= 33 and operands:
            types[operands[0]] = (opcode, operands[1:])
        elif opcode == 43 and len(operands) == 3:
            constants[operands[1]] = operands[2]
        elif opcode == 59 and len(operands) >= 3:
            variables.append(operands[:3])
        elif opcode == 71 and len(operands) >= 2:
            decorations.setdefault(operands[0], {})[operands[1]] = operands[2:]
        elif opcode == 72 and len(operands) == 4 and operands[2] == 35:
            member_offsets[(operands[0], operands[1])] = operands[3]
        offset += length
    mains = [entry for entry in entry_points if entry[0] == 5 and entry[2] == "main"]
    if len(entry_points) != 1 or len(mains) != 1 or mains[0][1] not in local_sizes:
        raise ValueError("Probe requires exactly one GLCompute main with literal LocalSize")
    group = local_sizes[mains[0][1]]
    if not all(group) or group[0] * group[1] * group[2] > 1024:
        raise ValueError("Unsupported compute workgroup")

    def size_of(identity, seen=frozenset()):
        if identity in seen or identity not in types:
            raise ValueError("Recursive or missing push-constant type")
        seen = seen | {identity}
        opcode, args = types[identity]
        if opcode in (21, 22) and args and args[0] in (8, 16, 32, 64):
            return args[0] // 8
        if opcode == 23 and len(args) == 2:
            return size_of(args[0], seen) * args[1]
        if opcode == 28 and len(args) == 2 and args[1] in constants:
            stride = decorations.get(identity, {}).get(6)
            return (stride[0] if stride else size_of(args[0], seen)) * constants[args[1]]
        if opcode == 30:
            if any((identity, i) not in member_offsets for i in range(len(args))):
                raise ValueError("Missing explicit push-constant member offsets")
            return max((member_offsets[(identity, i)] + size_of(t, seen) for i, t in enumerate(args)), default=0)
        raise ValueError("Unsupported push-constant layout")

    bindings, push_size = {}, 0
    for pointer, identity, storage in variables:
        if storage not in (0, 2, 9, 12):
            continue
        if pointer not in types or types[pointer][0] != 32 or len(types[pointer][1]) != 2:
            raise ValueError("Malformed descriptor pointer")
        pointer_storage, pointee = types[pointer][1]
        if pointer_storage != storage:
            raise ValueError("Pointer and variable storage classes differ")
        if storage == 9:
            push_size = max(push_size, size_of(pointee))
            continue
        annotation = decorations.get(identity, {})
        if len(annotation.get(33, ())) != 1 or len(annotation.get(34, ())) != 1:
            raise ValueError("Descriptor lacks a concrete set/binding")
        count = 1
        visited = set()
        while pointee in types and types[pointee][0] == 28:
            if pointee in visited:
                raise ValueError("Recursive descriptor array")
            visited.add(pointee)
            array = types[pointee][1]
            if len(array) != 2 or array[1] not in constants or constants[array[1]] == 0:
                raise ValueError("Descriptor array has no literal positive count")
            count *= constants[array[1]]
            pointee = array[0]
        if pointee not in types:
            raise ValueError("Missing descriptor type")
        opcode, args = types[pointee]
        if storage == 12:
            if opcode != 30 or 2 not in decorations.get(pointee, {}):
                raise ValueError("StorageBuffer descriptor is not a decorated block")
            descriptor = 7
        elif storage == 2:
            if opcode != 30:
                raise ValueError("Uniform descriptor is not a block")
            block = decorations.get(pointee, {})
            descriptor = 7 if 3 in block else 6 if 2 in block else None
            if descriptor is None:
                raise ValueError("Uniform block decoration is missing")
        elif opcode == 26:
            descriptor = 0
        elif opcode == 27:
            descriptor = 1
        elif opcode == 25 and len(args) >= 7 and args[5] in (1, 2):
            descriptor = (4 if args[5] == 1 else 5) if args[1] == 5 else (2 if args[5] == 1 else 3)
        else:
            raise ValueError("Unsupported descriptor type")
        set_index, binding = annotation[34][0], annotation[33][0]
        if set_index >= 8 or binding > 1023 or count > 65536:
            raise ValueError("Descriptor layout exceeds probe bounds")
        row = {"set": set_index, "binding": binding, "type": descriptor, "count": count}
        key = (set_index, binding)
        if key in bindings and bindings[key] != row:
            raise ValueError("Aliased descriptor variables have incompatible layouts")
        bindings[key] = row
    if push_size > 128 or push_size % 4 or len(bindings) > 256:
        raise ValueError("Layout exceeds bounded compile-probe support")
    return {"entry_point": "main", "workgroup": group, "push_constant_bytes": push_size,
            "bindings": [bindings[key] for key in sorted(bindings)]}


def private_output(path):
    resolved = path.resolve()
    if resolved.is_relative_to(ROOT) and (resolved == ROOT or resolved.relative_to(ROOT).parts[0] not in ("local", "work", "artifacts")):
        raise ValueError("Compile-probe output inside the repository must be private")
    if resolved.exists():
        raise ValueError("Output directory exists; choose a new directory")
    return resolved


def parse_environment(values):
    result = {}
    allowed = {"HS_CS32_MAX_INSTS", "HS_SPILL_BATCH", "HS_PUSH_SHADOW", "shader_spilling_rate"}
    for item in values:
        key, separator, value = item.partition("=")
        if not separator or key not in allowed or not re.fullmatch(r"\d+", value):
            raise ValueError("Only explicit numeric local-driver probe overrides are supported")
        if key in result:
            raise ValueError("Duplicate driver override")
        result[key] = value
    return result


def driver_identity(path, mesa_build=None):
    configuration = json.loads(path.read_text())
    library = Path(configuration["ICD"]["library_path"])
    if not library.is_absolute():
        library = path.resolve().parent / library
    if not library.is_file():
        raise ValueError("Driver JSON must identify a directly readable driver library")
    with library.open("rb") as source:
        digest = hashlib.file_digest(source, "sha256").hexdigest()
    identity = {"icd_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                "library_name": library.name, "library_sha256": digest}
    if mesa_build is not None:
        built_library = mesa_build / "src" / "intel" / "vulkan" / "libvulkan_intel.so"
        with built_library.open("rb") as source:
            built_digest = hashlib.file_digest(source, "sha256").hexdigest()
        if digest != built_digest:
            raise ValueError("Mesa build output differs from the driver selected by ICD")
        options = json.loads((mesa_build / "meson-info" / "intro-buildoptions.json").read_text())
        keys = {"buildtype", "optimization", "debug", "b_ndebug", "b_lto"}
        identity["mesa_build_options"] = {item["name"]: item["value"] for item in options if item["name"] in keys}
        commands = json.loads((mesa_build / "compile_commands.json").read_text())
        command = next((item for item in commands if item["file"].endswith("/brw_compile_cs.cpp")), None)
        if command is None:
            raise ValueError("Mesa compile database has no compute compiler command")
        arguments = command.get("arguments") or shlex.split(command["command"])
        flags = [argument for argument in arguments if re.fullmatch(r"-O[0-3sgz]|-DNDEBUG|-g(?:[0-3])?", argument)]
        if "-O0" in flags or not any(re.fullmatch(r"-O[123sgz]", value) for value in flags) or "-DNDEBUG" not in flags:
            raise ValueError("Mesa compute compiler is not demonstrably optimized with NDEBUG")
        identity["compute_compiler_flags"] = flags
        identity["build_library_digest_matches"] = True
    return identity


def run_child(command, environment, stdout, stderr, timeout):
    with stdout.open("w") as out, stderr.open("w") as err:
        child = subprocess.Popen(command, env=environment, stdout=out, stderr=err, start_new_session=True)
        try:
            status = child.wait(timeout=timeout)
        except (subprocess.TimeoutExpired, KeyboardInterrupt):
            os.killpg(child.pid, signal.SIGTERM)
            try:
                child.wait(timeout=5)
            except subprocess.TimeoutExpired:
                os.killpg(child.pid, signal.SIGKILL)
                child.wait()
            raise ValueError("Compile probe interrupted or exceeded timeout; only its process group stopped")
    if status:
        raise ValueError(f"Compile helper failed with status {status}; inspect the private stderr log")
    result = json.loads(stdout.read_text())
    if result.get("dispatch_executed") is not False or result.get("compile_only") is not True:
        raise ValueError("Unexpected helper result")
    return result


def run(args):
    if not re.fullmatch(r"[0-9a-fA-F]{64}", args.sha256):
        raise ValueError("A full expected shader SHA-256 is required")
    if args.input.is_symlink() or not args.input.is_file() or args.input.stat().st_size > MAX_SHADER_BYTES:
        raise ValueError("Input must be a bounded regular SPIR-V file")
    data = args.input.read_bytes()
    digest = hashlib.sha256(data).hexdigest()
    if digest != args.sha256.lower():
        raise ValueError("Shader digest differs from the explicitly pinned target")
    if not args.driver.is_file():
        raise ValueError("Explicit Vulkan driver JSON is required")
    driver = driver_identity(args.driver, args.mesa_build)
    if not 1 <= args.timeout <= 600 or not 0 <= args.device <= 255:
        raise ValueError("Timeout must be 1..600 seconds and device index 0..255")
    layout = reflect(data)
    if not layout["push_constant_bytes"] <= args.push_constant_bytes <= 128 or args.push_constant_bytes % 4:
        raise ValueError("Push range must cover reflected bytes, be 4-byte aligned, and not exceed128")
    if not layout["bindings"] or any(row["set"] != 0 for row in layout["bindings"]):
        raise ValueError("Target profile requires descriptors in set zero only")
    overrides = parse_environment(args.env)
    output = private_output(args.output)
    output.mkdir(parents=True)
    snapshot = output / "input.spv"
    snapshot.write_bytes(data)
    validation_command = [args.validator, "--target-env", "vulkan1.3", "--uniform-buffer-standard-layout", str(snapshot)]
    validation = subprocess.run(validation_command, capture_output=True, text=True, timeout=30)
    (output / "validation.stdout").write_text(validation.stdout)
    (output / "validation.stderr").write_text(validation.stderr)
    if validation.returncode:
        raise ValueError("SPIR-V validation failed; inspect the private validation logs")
    validator_version = subprocess.run([args.validator, "--version"], check=True, capture_output=True,
                                       text=True, timeout=15).stdout.splitlines()[0]
    configuration = output / "layout.txt"
    configuration.write_text(f"{args.push_constant_bytes} {len(layout['bindings'])}\n" +
        "".join(f"{row['set']} {row['binding']} {row['type']} {row['count']}\n" for row in layout["bindings"]))
    flags = shlex.split(subprocess.run(["pkg-config", "--cflags", "--libs", "vulkan"],
                                     check=True, capture_output=True, text=True, timeout=15).stdout)
    executable = output / "compile-helper"
    subprocess.run([args.cc, "-std=c11", "-O2", "-DNDEBUG", "-Wall", "-Wextra", "-Werror",
                    str(ROOT / "tools" / "vulkan_compile_probe.c"), *flags, "-o", str(executable)],
                   check=True, capture_output=True, text=True, timeout=60)
    policies = ["pinned32", "unpinned"] if args.policy == "both" else ["pinned32", "pinned16", "unpinned"] if args.policy == "all" else [args.policy]
    report = {"schema": 1, "shader_sha256": digest, "layout": layout, "driver_overrides": overrides,
              "helper_build_flags": ["-std=c11", "-O2", "-DNDEBUG", "-Wall", "-Wextra", "-Werror"],
              "driver_identity": driver,
              "validation": {"passed": True, "validator": validator_version,
                             "flags": ["--target-env", "vulkan1.3", "--uniform-buffer-standard-layout"]},
              "push_constant_range_bytes": args.push_constant_bytes, "descriptor_mode": "push",
              "compile_only": True, "dispatch_executed": False, "results": [],
              "limitations": ["No shader execution, resource comparison or rendering verification is performed.",
                              "Pipeline compilation metrics do not establish performance or semantic equivalence."]}
    for policy in policies:
        environment = os.environ.copy()
        for key in ("HS_CS32_MAX_INSTS", "HS_SPILL_BATCH", "HS_PUSH_SHADOW", "shader_spilling_rate", "INTEL_DEBUG"):
            environment.pop(key, None)
        environment.update(overrides)
        environment["VK_DRIVER_FILES"] = str(args.driver.resolve())
        environment["MESA_SHADER_CACHE_DIR"] = str(output / (policy + "-cache"))
        result = run_child([str(executable), str(snapshot), str(configuration), policy, str(args.device)],
                           environment, output / (policy + ".json"), output / (policy + ".stderr"), args.timeout)
        if result.get("policy") != policy:
            raise ValueError("Helper policy differs from requested policy")
        report["results"].append(result)
        (output / "report.json").write_text(json.dumps(report, indent=2) + "\n")
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--sha256", required=True)
    parser.add_argument("--driver", type=Path, required=True)
    parser.add_argument("--mesa-build", type=Path, help="Optional matching Mesa build directory to audit optimization flags")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--policy", choices=("pinned32", "pinned16", "unpinned", "both", "all"), default="both")
    parser.add_argument("--device", type=int, default=0)
    parser.add_argument("--timeout", type=int, default=180)
    parser.add_argument("--push-constant-bytes", type=int, default=88, help="Pipeline range; default matches inspected Hookshot layout")
    parser.add_argument("--cc", default="cc")
    parser.add_argument("--validator", default="spirv-val")
    parser.add_argument("--env", action="append", default=[], metavar="NAME=NUMBER")
    args = parser.parse_args()
    try:
        print(json.dumps(run(args), indent=2))
    except (OSError, ValueError, subprocess.SubprocessError) as exc:
        print(str(exc), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
