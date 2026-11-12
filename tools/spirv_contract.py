#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Conservative SPIR-V call/resource metadata. This is not a race-freedom proof."""
import argparse
from collections import Counter, defaultdict
import hashlib
import json
from pathlib import Path
import re
import shlex
import shutil
import subprocess
import sys
import tempfile


def analyze_assembly(assembly, entry_name="main"):
    """Analyze spirv-dis text; callers must separately validate its binary provenance."""
    definitions, functions, entries, decorations = {}, {}, [], defaultdict(dict)
    member_offsets, constants, pointer_types, variables, builtins = {}, {}, {}, {}, {}
    instructions, current, capabilities = [], None, []
    for number, raw in enumerate(assembly.splitlines(), 1):
        line = raw.strip()
        if not line or line.startswith(";"):
            continue
        parts = shlex.split(line, comments=True)
        if not parts:
            continue
        result = parts[0] if len(parts) > 2 and parts[1] == "=" else None
        opcode, arguments = (parts[2], parts[3:]) if result else (parts[0], parts[1:])
        if not opcode.startswith("Op"):
            raise ValueError(f"Unrecognized disassembly syntax at line {number}")
        if result:
            if result in definitions:
                raise ValueError(f"Duplicate result ID at line {number}")
            definitions[result] = (opcode, arguments)
        if opcode == "OpFunction":
            if current is not None:
                raise ValueError("Nested function")
            current = result
            functions[current] = []
        instruction = (result, opcode, arguments)
        instructions.append(instruction)
        if current is not None:
            functions[current].append(instruction)
        if opcode == "OpFunctionEnd":
            if current is None:
                raise ValueError("Function end outside a function")
            current = None
        if opcode == "OpEntryPoint":
            entries.append({"model": arguments[0], "function": arguments[1], "name": arguments[2]})
        elif opcode == "OpCapability":
            capabilities.append(arguments[0])
        elif opcode == "OpDecorate" and len(arguments) >= 3:
            decorations[arguments[0]][arguments[1]] = arguments[2]
            if arguments[1] == "BuiltIn":
                builtins[arguments[0]] = arguments[2]
        elif opcode == "OpMemberDecorate" and len(arguments) >= 4 and arguments[2] == "Offset":
            member_offsets[(arguments[0], int(arguments[1]))] = int(arguments[3])
        elif opcode == "OpConstant" and len(arguments) == 2:
            try:
                constants[result] = int(arguments[1], 0)
            except ValueError:
                pass
        elif opcode == "OpTypePointer":
            pointer_types[result] = (arguments[0], arguments[1])
        elif opcode == "OpVariable":
            variables[result] = {"type": arguments[0], "storage_class": arguments[1]}
    if current is not None:
        raise ValueError("Truncated function")
    selected = [entry for entry in entries if entry["name"] == entry_name]
    if len(selected) != 1 or selected[0]["function"] not in functions:
        raise ValueError("Expected exactly one defined entry point with the requested name")

    reachable, unresolved, pending = set(), set(), [selected[0]["function"]]
    while pending:
        function = pending.pop()
        if function in reachable:
            continue
        if function not in functions:
            unresolved.add(function)
            continue
        reachable.add(function)
        if not any(opcode == "OpLabel" for _, opcode, _ in functions[function]):
            unresolved.add("missing-function-body:" + function)
        for _, opcode, arguments in functions[function]:
            if opcode == "OpFunctionCall":
                pending.append(arguments[1])
            elif "FunctionPointer" in opcode:
                unresolved.add("unsupported indirect call")
    active = [instruction for function in functions if function in reachable for instruction in functions[function]]
    module_counts = Counter(opcode for _, opcode, _ in instructions)
    counts = Counter(opcode for _, opcode, _ in active)

    # A resource origin is a descriptor variable, a physical pointer conversion,
    # or an explicit unresolved origin. No function-body/alias safety is inferred.
    origin_cache = {}

    def origins(identifier, visiting=None):
        if identifier in origin_cache:
            return origin_cache[identifier]
        visiting = set() if visiting is None else visiting
        if identifier in visiting:
            return {"unresolved:cycle"}
        visiting = visiting | {identifier}
        if identifier in variables:
            answer = {identifier}
        elif identifier not in definitions:
            answer = {"unresolved:definition"}
        else:
            opcode, arguments = definitions[identifier]
            if opcode in {"OpAccessChain", "OpInBoundsAccessChain", "OpPtrAccessChain", "OpInBoundsPtrAccessChain",
                          "OpCopyObject", "OpBitcast", "OpLoad", "OpImage"}:
                answer = origins(arguments[1], visiting)
            elif opcode == "OpSampledImage":
                answer = origins(arguments[1], visiting) | origins(arguments[2], visiting)
            elif opcode == "OpConvertUToPtr":
                answer = {"physical-pointer"}
            elif opcode == "OpPhi":
                answer = set().union(*(origins(value, visiting) for value in arguments[1::2]))
            elif opcode == "OpSelect":
                answer = origins(arguments[2], visiting) | origins(arguments[3], visiting)
            else:
                answer = {"unresolved:" + opcode}
        origin_cache[identifier] = answer
        return answer

    def pointer_storage(identifier):
        definition = definitions.get(identifier)
        if definition and definition[1]:
            return pointer_types.get(definition[1][0], (None, None))[0]
        return None

    descriptors = {}
    for identifier, variable in variables.items():
        decoration = decorations.get(identifier, {})
        if "Binding" not in decoration or "DescriptorSet" not in decoration:
            continue
        key = (int(decoration["DescriptorSet"]), int(decoration["Binding"]))
        pointee = pointer_types.get(variable["type"], (None, None))[1]
        array_count = 1
        while pointee in definitions and definitions[pointee][0] == "OpTypeArray":
            array_type, count_id = definitions[pointee][1]
            if count_id not in constants:
                array_count = None
                break
            array_count *= constants[count_id]
            pointee = array_type
        kind = {"Uniform": "uniform_buffer", "StorageBuffer": "storage_buffer"}.get(variable["storage_class"])
        if variable["storage_class"] == "UniformConstant":
            kind = {"OpTypeSampledImage": "combined_image_sampler", "OpTypeSampler": "sampler",
                    "OpTypeImage": "image"}.get(definitions.get(pointee, (None,))[0], "unresolved")
        record = descriptors.setdefault(key, {"set": key[0], "binding": key[1], "variables": [],
            "descriptor_type": kind or "unresolved", "descriptor_count": array_count,
            "loads": 0, "stores": 0, "length_queries": 0, "image_reads": 0,
            "image_writes": 0, "image_queries": 0})
        if record["descriptor_type"] != kind or record["descriptor_count"] != array_count:
            raise ValueError("Incompatible declarations alias one descriptor binding")
        record["variables"].append(identifier)
    variable_binding = {identifier: key for key, value in descriptors.items() for identifier in value["variables"]}
    unresolved_accesses, physical_accesses = Counter(), Counter()
    for _, opcode, arguments in active:
        if opcode == "OpLoad":
            pointer, use = arguments[1], "loads"
        elif opcode == "OpStore":
            pointer, use = arguments[0], "stores"
        elif opcode == "OpArrayLength":
            pointer, use = arguments[1], "length_queries"
        elif opcode.startswith("OpImage") and ("Sample" in opcode or "Fetch" in opcode or "Read" in opcode):
            pointer, use = arguments[1], "image_reads"
        elif opcode == "OpImageWrite":
            pointer, use = arguments[0], "image_writes"
        elif opcode.startswith("OpImageQuery"):
            pointer, use = arguments[1], "image_queries"
        elif opcode in {"OpCopyMemory", "OpCopyMemorySized"} or "Atomic" in opcode:
            unresolved_accesses["unsupported-memory-operation:" + opcode] += 1
            continue
        else:
            continue
        sources = origins(pointer)
        if pointer_storage(pointer) == "PhysicalStorageBuffer" or "physical-pointer" in sources:
            physical_accesses[opcode] += 1
        for source in sources:
            if source in variable_binding:
                descriptors[variable_binding[source]][use] += 1
            elif source.startswith("unresolved:"):
                unresolved_accesses[source] += 1
    descriptors = sorted(descriptors.values(), key=lambda item: (item["set"], item["binding"]))

    push_roots = []
    for _, opcode, arguments in active:
        if opcode in {"OpAccessChain", "OpInBoundsAccessChain"} and len(arguments) >= 3:
            variable = variables.get(arguments[1])
            member = constants.get(arguments[2])
            if variable and variable["storage_class"] == "PushConstant" and member is not None:
                structure = pointer_types.get(variable["type"], (None, None))[1]
                if (structure, member) in member_offsets:
                    push_roots.append(member_offsets[(structure, member)])
    physical_push_offsets = set()
    for _, opcode, arguments in active:
        if opcode != "OpConvertUToPtr":
            continue
        pending, visited = [arguments[1]], set()
        while pending:
            identifier = pending.pop()
            if identifier in visited or identifier not in definitions:
                continue
            visited.add(identifier)
            dependency_opcode, operands = definitions[identifier]
            if dependency_opcode in {"OpAccessChain", "OpInBoundsAccessChain"} and len(operands) >= 3:
                variable = variables.get(operands[1])
                member = constants.get(operands[2])
                if variable and variable["storage_class"] == "PushConstant" and member is not None:
                    structure = pointer_types.get(variable["type"], (None, None))[1]
                    if (structure, member) in member_offsets:
                        physical_push_offsets.add(member_offsets[(structure, member)])
            pending.extend(value for value in operands if value.startswith("%"))
    explicit_subgroup_ops = {opcode: count for opcode, count in counts.items()
                             if "GroupNonUniform" in opcode or "Subgroup" in opcode or "Group" in opcode}
    return {"schema": 1, "entry": selected[0], "module_function_count": len(functions),
        "reachable_functions": sorted(reachable), "unresolved_calls": sorted(unresolved),
        "reachable_call_instruction_count": counts["OpFunctionCall"],
        "physical_pointer_instruction_count": module_counts["OpConvertUToPtr"],
        "reachable_physical_pointer_instruction_count": counts["OpConvertUToPtr"],
        "reachable_physical_memory_accesses": dict(physical_accesses),
        "physical_pointer_push_constant_offsets": sorted(physical_push_offsets),
        "reachable_push_constant_member_offsets": sorted(set(push_roots)),
        "builtins": sorted(set(builtins.values())), "capabilities": sorted(capabilities),
        "descriptors": descriptors, "unresolved_access_origins": dict(unresolved_accesses),
        "reachable_group_opcodes": explicit_subgroup_ops,
        "reachable_barrier_instruction_count": sum(count for opcode, count in counts.items() if "Barrier" in opcode),
        "reachable_atomic_instruction_count": sum(count for opcode, count in counts.items() if "Atomic" in opcode),
        "workgroup_variable_count": sum(item["storage_class"] == "Workgroup" for item in variables.values()),
        "race_freedom_proven": False, "resource_footprints_complete": False,
        "limitations": ["Static call reachability is not runtime branch coverage or a complete memory footprint.",
            "Descriptor aliases and physical pointers can reach data not recoverable from this module alone.",
            "No global-memory race, inter-invocation independence or temporal-state proof is provided.",
            "Pointer-origin tracking is partial; unresolved origins remain explicit rather than treated as absent."]}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--spirv", type=Path, required=True)
    parser.add_argument("--expected-sha256", required=True)
    parser.add_argument("--entry", default="main")
    args = parser.parse_args()
    try:
        if not re.fullmatch(r"[0-9a-fA-F]{64}", args.expected_sha256):
            raise ValueError("Expected full SHA-256")
        if args.spirv.is_symlink() or not args.spirv.is_file():
            raise ValueError("Expected a regular private SPIR-V file")
        size = args.spirv.stat().st_size
        if size < 20 or size % 4 or size > 16 * 1024 * 1024:
            raise ValueError("SPIR-V must contain a header and whole words, within the 16 MiB analysis limit")
        with args.spirv.open("rb") as source:
            data = source.read(16 * 1024 * 1024 + 1)
        if len(data) != size:
            raise ValueError("SPIR-V input changed while taking its bounded snapshot")
        if data[:4] != b"\x03\x02\x23\x07":
            raise ValueError("Expected little-endian SPIR-V magic")
        digest = hashlib.sha256(data).hexdigest()
        if digest != args.expected_sha256.lower():
            raise ValueError("SPIR-V identity mismatch")
        disassembler, validator = shutil.which("spirv-dis"), shutil.which("spirv-val")
        if not disassembler or not validator:
            raise ValueError("SPIRV-Tools spirv-dis and spirv-val are required")
        # Both external tools see the same private, hash-verified snapshot even if
        # the source cache file is changed by another process after this point.
        with tempfile.TemporaryDirectory(prefix="open-ocarina-spirv-") as temporary:
            snapshot = Path(temporary) / "module.spv"
            snapshot.write_bytes(data)
            subprocess.run([validator, "--target-env", "vulkan1.3", "--uniform-buffer-standard-layout", str(snapshot)],
                           capture_output=True, text=True, check=True, timeout=30)
            assembly = subprocess.run([disassembler, "--raw-id", str(snapshot)], capture_output=True,
                                      text=True, check=True, timeout=30).stdout
        report = analyze_assembly(assembly, args.entry)
        report.update(spirv_sha256=digest, structurally_validated=True,
                      validation_environment="vulkan1.3, uniformBufferStandardLayout enabled")
        print(json.dumps(report, indent=2))
    except (OSError, ValueError, subprocess.SubprocessError) as exc:
        print(str(exc), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
