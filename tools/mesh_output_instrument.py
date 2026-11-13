#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""CPU-only, digest-pinned MeshEXT logical-output instrumentation."""
import argparse
from collections import Counter, defaultdict
import hashlib
import json
from pathlib import Path
import shlex
import shutil
import struct
import subprocess
import sys

try:
    from .compile_probe import private_output
    from .spirv_contract import analyze_assembly
except ImportError:
    from compile_probe import private_output
    from spirv_contract import analyze_assembly

ROOT = Path(__file__).resolve().parents[1]
ORIGINAL = "835aa59799c6ecb621034d946e2fa4eadb1aa2407d293321ae3e3052882bc9b3"
CONDITIONAL = "9d23b7ebf3677f53fe6901c9b94f2c4927f1f0c47ed207c04c53df9b59ae9638"
RECORD_BYTES = 44928
RECORD_WORDS = 11232
VALUE_SENTINEL = 0xCDCDCDCD
INNER_GUARD = 0xA11CE55A
LOCATIONS = (0, 1, 3, 4, 5, 6, 7, 10, 11)
LAYOUT = {"stride_bytes": RECORD_BYTES, "stride_words": RECORD_WORDS,
          "prefix_guard_offset": 0, "prefix_guard_bytes": 64,
          "lane_counts_offset": 64, "lane_count": 32, "lane_count_stride": 8,
          "position_offset": 320, "generic_offset": 2368, "generic_locations": list(LOCATIONS),
          "generic_array_stride": 2048, "triangle_offset": 20800,
          "triangle_array_stride": 16, "writtenness_offset": 22848,
          "writtenness_words": 5504, "suffix_guard_offset": 44864,
          "suffix_guard_bytes": 64, "max_vertices": 128, "max_primitives": 128,
          "value_sentinel_word": VALUE_SENTINEL, "inner_guard_word": INNER_GUARD,
          "writtenness_initial_word": 0, "writtenness_written_word": 1}


def parse(assembly):
    if len(assembly) > 16 * 1024 * 1024:
        raise ValueError("Assembly exceeds analysis bound")
    instructions, definitions, functions, uses = [], {}, {}, defaultdict(list)
    current = None
    for raw in assembly.splitlines():
        if raw.lstrip().startswith(";"):
            continue
        parts = shlex.split(raw, comments=True)
        if not parts:
            continue
        result = parts[0] if len(parts) > 2 and parts[1] == "=" else None
        op, args = (parts[2], parts[3:]) if result else (parts[0], parts[1:])
        if not op.startswith("Op") or any(value.startswith("%oomcap_") for value in parts):
            raise ValueError("Unknown syntax or reserved instrumentation ID")
        item = {"result": result, "op": op, "args": args, "raw": raw, "function": current}
        if op == "OpFunction":
            if current is not None:
                raise ValueError("Nested function")
            current = result
            functions[current] = []
            item["function"] = current
        if current is not None:
            functions[current].append(item)
        if result:
            if result in definitions:
                raise ValueError("Duplicate ID")
            definitions[result] = item
        for position, value in enumerate(args):
            if value.startswith("%"):
                uses[value].append((item, position))
        instructions.append(item)
        if op == "OpFunctionEnd":
            if current is None:
                raise ValueError("Function end outside function")
            current = None
    if current is not None:
        raise ValueError("Truncated function")
    return instructions, definitions, functions, uses


def audit(assembly, source_kind):
    items, definitions, functions, uses = parse(assembly)
    summaries = analyze_assembly(assembly)
    entry = summaries["entry"]
    if entry["model"] != "MeshEXT" or entry["name"] != "main" or summaries["unresolved_calls"]:
        raise ValueError("Expected a defined MeshEXT main with direct calls only")
    modes = [item["args"][1:] for item in items if item["op"] == "OpExecutionMode"]
    required = [["LocalSize", "32", "1", "1"], ["OutputVertices", "128"],
                ["OutputPrimitivesEXT", "128"], ["OutputTrianglesEXT"]]
    if any(modes.count(mode) != 1 for mode in required) or any(
            mode not in required + [["SignedZeroInfNanPreserve", "32"]] for mode in modes):
        raise ValueError("Unsupported mesh execution modes")
    memory = [item["args"] for item in items if item["op"] == "OpMemoryModel"]
    if memory != [["Logical", "GLSL450"]]:
        raise ValueError("Expected logical GLSL450 memory model")
    pointer_types = {item["result"]: item["args"] for item in items if item["op"] == "OpTypePointer"}
    variables = {item["result"]: item for item in items if item["op"] == "OpVariable"}
    decorations = defaultdict(dict)
    for item in items:
        if item["op"] == "OpDecorate" and len(item["args"]) >= 3:
            decorations[item["args"][0]][item["args"][1]] = item["args"][2]
    builtins = {identifier: values["BuiltIn"] for identifier, values in decorations.items() if "BuiltIn" in values}
    allowed = {"WorkgroupId", "LocalInvocationId", "SubgroupEqMask", "SubgroupLtMask", "SubgroupLeMask",
               "SubgroupGtMask", "SubgroupGeMask", "SubgroupLocalInvocationId", "Position", "PrimitiveTriangleIndicesEXT"}
    if any(value not in allowed for value in builtins.values()):
        raise ValueError("Unsupported stage builtin")
    if any(item["args"][1] == "PushConstant" for item in variables.values()):
        raise ValueError("Original push constants unsupported")
    for name in ("WorkgroupId", "LocalInvocationId"):
        if sum(value == name for value in builtins.values()) != 1:
            raise ValueError("Missing or duplicate invocation builtin")
    group = next(identifier for identifier, name in builtins.items() if name == "WorkgroupId")
    local = next(identifier for identifier, name in builtins.items() if name == "LocalInvocationId")
    for item, position in uses[group]:
        if item["op"] not in {"OpName", "OpDecorate", "OpEntryPoint", "OpLoad"}:
            raise ValueError("WorkgroupId pointer escape")
        if item["op"] == "OpLoad" and (position != 1 or item["function"] != entry["function"]):
            raise ValueError("WorkgroupId loads must be direct and in main")
    expected_bindings = [(0, binding, "uniform_buffer" if binding < 5 else "storage_buffer", 3 if binding == 8 else 1)
                         for binding in range(9)]
    actual_bindings = [(row["set"], row["binding"], row["descriptor_type"], row["descriptor_count"])
                       for row in summaries["descriptors"]]
    if actual_bindings != expected_bindings or any(row["stores"] or row["image_writes"] for row in summaries["descriptors"]):
        raise ValueError("Input descriptor contract changed or input side effects found")
    if summaries["reachable_physical_memory_accesses"] or summaries["unresolved_access_origins"]:
        raise ValueError("Unresolved/physical input access")
    active = [item for identifier in summaries["reachable_functions"] for item in functions[identifier]]
    rejected = {"OpCopyMemory", "OpCopyMemorySized", "OpKill", "OpTerminateInvocation", "OpDemoteToHelperInvocationEXT",
                "OpEmitMeshTasksEXT", "OpImageWrite", "OpSwitch", "OpUnreachable"}
    if any(item["op"] in rejected or "Atomic" in item["op"] for item in active):
        raise ValueError("Unsupported side effect or control flow")
    main = functions[entry["function"]]
    if any(item["op"] == "OpLoopMerge" for item in main):
        raise ValueError("Main-loop control flow is outside the bounded instrumenter")
    if sum(item["op"] == "OpReturn" for item in main) != 1:
        raise ValueError("Early/extra main return")
    count_operations = [item for item in main if item["op"] == "OpSetMeshOutputsEXT"]
    if len(count_operations) != 1:
        raise ValueError("Expected one SetMeshOutputsEXT")
    counts = count_operations[0]
    barriers = sum(item["op"] == "OpControlBarrier" for item in main)
    if barriers != (4 if source_kind == "conditional" else 2):
        raise ValueError("Original barrier inventory changed")
    if max(index for index, item in enumerate(main) if item["op"] == "OpControlBarrier") >= main.index(counts):
        raise ValueError("Unexpected barrier after SetMeshOutputs")
    blocks, item_blocks, block = {}, {}, None
    for item in main:
        if item["op"] == "OpLabel":
            block = item["result"]
            blocks[block] = []
        elif block is not None and item["op"] != "OpFunctionEnd":
            blocks[block].append(item)
            item_blocks[id(item)] = block
    edges, predecessors = {}, defaultdict(set)
    for label, body in blocks.items():
        if not body:
            raise ValueError("Empty main block")
        terminator = body[-1]
        if terminator["op"] == "OpBranch":
            targets = terminator["args"]
        elif terminator["op"] == "OpBranchConditional":
            targets = terminator["args"][1:3]
        elif terminator["op"] == "OpReturn":
            targets = []
        else:
            raise ValueError("Unsupported main terminator")
        if any(target not in blocks for target in targets):
            raise ValueError("Main branch escapes its function")
        edges[label] = targets
        for target in targets:
            predecessors[target].add(label)
    visiting, visited = set(), set()

    def walk(label):
        if label in visiting:
            raise ValueError("Main contains a control-flow cycle")
        if label in visited:
            return
        visiting.add(label)
        for target in edges[label]:
            walk(target)
        visiting.remove(label)
        visited.add(label)

    entry_block = next(iter(blocks))
    walk(entry_block)
    if visited != set(blocks):
        raise ValueError("Unreachable main blocks are unsupported")
    dominators = {label: ({label} if label == entry_block else set(blocks)) for label in blocks}
    changed = True
    while changed:
        changed = False
        for label in blocks:
            if label == entry_block:
                continue
            value = {label} | set.intersection(*(dominators[parent] for parent in predecessors[label]))
            if value != dominators[label]:
                dominators[label], changed = value, True
    count_block = item_blocks[id(counts)]
    if any(count_block not in dominators[item_blocks[id(item)]] for item in main if item["op"] == "OpReturn"):
        raise ValueError("SetMeshOutputs does not dominate the return")

    const_cache = {}

    def constant(identifier):
        if identifier in const_cache:
            return const_cache[identifier]
        item = definitions.get(identifier)
        value = None
        if item:
            op, args = item["op"], item["args"]
            if op == "OpConstant":
                try:
                    value = int(args[1], 0)
                except ValueError:
                    pass
            elif op in {"OpConstantTrue", "OpConstantFalse"}:
                value = int(op == "OpConstantTrue")
            elif op == "OpIEqual":
                left, right = constant(args[1]), constant(args[2])
                if left is not None and right is not None:
                    value = int(left == right)
            elif op == "OpShiftRightLogical":
                left, right = constant(args[1]), constant(args[2])
                if left is not None and right is not None and 0 <= right < 32:
                    value = left >> right
            elif op == "OpExtInst" and args[2] == "UMin":
                values = [constant(identifier) for identifier in args[3:]]
                if values and all(value is not None for value in values):
                    value = min(values)
        const_cache[identifier] = value
        return value

    if constant(counts["args"][0]) != 128:
        raise ValueError("Vertex output count changed")

    def lane_offset(identifier):
        item = definitions.get(identifier)
        if not item:
            return None
        op, args = item["op"], item["args"]
        if op == "OpCompositeExtract" and args[2:] == ["0"]:
            vector = definitions.get(args[1])
            if vector and vector["op"] == "OpLoad" and vector["args"][1] == local:
                return 0
        if op == "OpIAdd":
            for left, right in ((args[1], args[2]), (args[2], args[1])):
                base, amount = lane_offset(left), constant(right)
                if base is not None and amount is not None:
                    return base + amount
        return None

    outputs, output_order = {}, []
    for identifier, item in variables.items():
        if item["args"][1] != "Output":
            continue
        decoration = decorations[identifier]
        if decoration.get("BuiltIn") == "Position":
            role, index = "position", 0
        elif decoration.get("BuiltIn") == "PrimitiveTriangleIndicesEXT":
            role, index = "triangles", 0
        elif "Location" in decoration and int(decoration["Location"]) in LOCATIONS:
            role, index = "generic", LOCATIONS.index(int(decoration["Location"]))
        else:
            raise ValueError("Unexpected mesh output")
        array_type = pointer_types[item["args"][0]][1]
        array = definitions[array_type]
        if array["op"] != "OpTypeArray" or constant(array["args"][1]) != 128:
            raise ValueError("Output maximum changed")
        outputs[identifier] = {"role": role, "index": index}
        output_order.append((role, index))
        for use, position in uses[identifier]:
            if use["op"] not in {"OpName", "OpDecorate", "OpEntryPoint", "OpAccessChain"}:
                raise ValueError("Output pointer escape or whole-variable use")
            if use["op"] == "OpAccessChain" and position != 1:
                raise ValueError("Unexpected output access")
    if sorted(output_order) != sorted([("position", 0), ("triangles", 0)] + [("generic", index) for index in range(9)]):
        raise ValueError("Output inventory changed")

    output_stores, per_output, write_pattern = {}, Counter(), Counter()
    count_position = main.index(counts)
    for item in main:
        if item["op"] != "OpAccessChain" or item["args"][1] not in outputs:
            continue
        args = item["args"]
        output = outputs[args[1]]
        offset = lane_offset(args[2])
        if offset not in (0, 32, 64, 96):
            raise ValueError("Output index is not a proved lane-plus-block index")
        if (output["role"] == "triangles" and len(args) != 3) or (output["role"] != "triangles" and
                (len(args) != 4 or constant(args[3]) not in range(4))):
            raise ValueError("Output component access changed")
        pointer_uses = uses[item["result"]]
        stores = [use for use, position in pointer_uses if use["op"] == "OpStore" and position == 0]
        if len(stores) != 1 or len(pointer_uses) != 1 or main.index(stores[0]) <= count_position:
            raise ValueError("Output pointer read/escape, repeated pointer use or store before counts")
        store_block = item_blocks[id(stores[0])]
        if count_block not in dominators[store_block]:
            raise ValueError("SetMeshOutputs does not dominate an output store")
        if output["role"] == "triangles":
            parents = predecessors[store_block]
            if len(parents) != 1:
                raise ValueError("Primitive output has multiple incoming paths")
            parent = next(iter(parents))
            branch = blocks[parent][-1]
            condition = definitions.get(branch["args"][0]) if branch["op"] == "OpBranchConditional" else None
            if (not condition or branch["args"][1] != store_block or condition["op"] != "OpULessThan"
                    or condition["args"][1:] != [args[2], counts["args"][1]]):
                raise ValueError("Primitive output is not directly guarded by index < emitted count")
        output_stores[id(stores[0])] = {**output, "vertex": args[2],
                                      "component": None if output["role"] == "triangles" else constant(args[3])}
        per_output[(output["role"], output["index"])] += 1
        write_pattern[(output["role"], output["index"], offset,
                       None if output["role"] == "triangles" else constant(args[3]))] += 1
    if per_output != Counter({("position", 0): 20, ("triangles", 0): 4,
                              **{("generic", index): 16 for index in range(9)}}):
        raise ValueError("Output store inventory changed")
    expected_pattern = Counter()
    for offset in (0, 32, 64, 96):
        expected_pattern[("triangles", 0, offset, None)] = 1
        for component in range(4):
            expected_pattern[("position", 0, offset, component)] = 2 if component == 2 else 1
            for index in range(9):
                expected_pattern[("generic", index, offset, component)] = 1
    if write_pattern != expected_pattern:
        raise ValueError("Output component ownership/coverage pattern changed")

    # Task payload is discarded only after the exact dead-value proof.
    remove, replacement, payload_audit = set(), {}, {"present": False, "proved_dead": False}
    payloads = [identifier for identifier, item in variables.items() if item["args"][1] == "TaskPayloadWorkgroupEXT"]
    if len(payloads) > 1:
        raise ValueError("Multiple task payloads")
    if payloads:
        payload = payloads[0]
        accesses = [item for item, position in uses[payload] if item["op"] == "OpAccessChain" and position == 1]
        if len(accesses) != 1 or any(item["op"] not in {"OpName", "OpEntryPoint", "OpAccessChain"} for item, _ in uses[payload]):
            raise ValueError("Live or escaped task payload")
        access = accesses[0]
        if len(access["args"]) != 3 or constant(access["args"][2]) != 0:
            raise ValueError("Task payload index changed")
        chain = uses[access["result"]]
        if len(chain) != 1 or chain[0][0]["op"] != "OpLoad" or chain[0][1] != 1:
            raise ValueError("Task payload access escaped")
        load = chain[0][0]
        consumers = uses[load["result"]]
        if len(consumers) != 1 or consumers[0][0]["op"] != "OpSelect" or consumers[0][1] != 3:
            raise ValueError("Task payload is not solely an unselected value")
        select = consumers[0][0]
        if constant(select["args"][1]) != 1:
            raise ValueError("Task payload select is not constant true")
        true = definitions.get(select["args"][2])
        vector = definitions.get(true["args"][1]) if true and true["op"] == "OpCompositeExtract" else None
        if not true or true["args"][2:] != ["0"] or not vector or vector["op"] != "OpLoad" or vector["args"][1] != group:
            raise ValueError("Task payload replacement is not WorkgroupId.x")
        remove.update((payload, access["result"], load["result"]))
        replacement[select["result"]] = f'{select["result"]} = OpCopyObject {select["args"][0]} {select["args"][2]}'
        payload_audit = {"present": True, "proved_dead": True, "word_index": 0,
                         "proof": "sole load is false arm of constant-true select choosing WorkgroupId.x"}
    if source_kind != "synthetic-fixture" and not payloads:
        raise ValueError("Expected original dead payload shape")
    return {"items": items, "definitions": definitions, "functions": functions, "uses": uses,
            "entry": entry["function"], "group": group, "local": local, "outputs": outputs,
            "output_stores": output_stores, "remove": remove, "replacement": replacement,
            "counts": counts, "constant": constant, "input_bindings": summaries["descriptors"],
            "payload_audit": payload_audit, "barriers": barriers}


def instrument_assembly(assembly, source_kind):
    state = audit(assembly, source_kind)
    definitions = state["definitions"]
    unsigned = next(identifier for identifier, item in definitions.items() if item["op"] == "OpTypeInt" and item["args"] == ["32", "0"])
    vec3 = next(identifier for identifier, item in definitions.items() if item["op"] == "OpTypeVector" and item["args"] == [unsigned, "3"])
    vec4 = next(identifier for identifier, item in definitions.items() if item["op"] == "OpTypeVector" and item["args"] == [unsigned, "4"])
    fresh_count, declarations = 0, []
    constants = {state["constant"](identifier): identifier for identifier, item in definitions.items()
                 if item["op"] == "OpConstant" and item["args"][0] == unsigned}

    def fresh():
        nonlocal fresh_count
        fresh_count += 1
        return f"%oomcap_{fresh_count}"

    def literal(value):
        if value not in constants:
            constants[value] = fresh()
            declarations.append(f"{constants[value]} = OpConstant {unsigned} {value}")
        return constants[value]

    def operation(lines, opcode, result_type, *args):
        identifier = fresh()
        lines.append(f"{identifier} = {opcode} {result_type} {' '.join(map(str, args))}")
        return identifier

    zero, one = literal(0), literal(1)
    runtime_array, capture_type, capture_pointer, uint_pointer = fresh(), fresh(), fresh(), fresh()
    control_type, control_pointer, vec4_pointer = fresh(), fresh(), fresh()
    capture, control = fresh(), fresh()
    annotations = [f"OpDecorate {runtime_array} ArrayStride 4", f"OpDecorate {capture_type} Block",
                   f"OpMemberDecorate {capture_type} 0 Offset 0", f"OpDecorate {capture} DescriptorSet 1",
                   f"OpDecorate {capture} Binding 0", f"OpDecorate {control_type} Block",
                   f"OpMemberDecorate {control_type} 0 Offset 0", f"OpMemberDecorate {control_type} 1 Offset 16",
                   f"OpDecorate {control} DescriptorSet 1", f"OpDecorate {control} Binding 1",
                   f"OpDecorate {control} NonWritable"]
    global_declarations = [f"{runtime_array} = OpTypeRuntimeArray {unsigned}",
        f"{capture_type} = OpTypeStruct {runtime_array}",
        f"{capture_pointer} = OpTypePointer StorageBuffer {capture_type}",
        f"{uint_pointer} = OpTypePointer StorageBuffer {unsigned}",
        f"{control_type} = OpTypeStruct {vec4} {vec4}",
        f"{control_pointer} = OpTypePointer StorageBuffer {control_type}",
        f"{vec4_pointer} = OpTypePointer StorageBuffer {vec4}",
        f"{capture} = OpVariable {capture_pointer} StorageBuffer",
        f"{control} = OpVariable {control_pointer} StorageBuffer"]
    prologue = []
    group_type = definitions[state["group"]]["args"][0]
    group_vector = definitions[group_type]["args"][1]
    raw_group = operation(prologue, "OpLoad", group_vector, state["group"])
    local_type = definitions[state["local"]]["args"][0]
    local_vector = definitions[local_type]["args"][1]
    raw_local = operation(prologue, "OpLoad", local_vector, state["local"])
    local_x = operation(prologue, "OpCompositeExtract", unsigned, raw_local, 0)
    origin_pointer = operation(prologue, "OpAccessChain", vec4_pointer, control, zero)
    origin = operation(prologue, "OpLoad", vec4, origin_pointer)
    origin_xyz = operation(prologue, "OpVectorShuffle", vec3, origin, origin, 0, 1, 2)
    window_pointer = operation(prologue, "OpAccessChain", vec4_pointer, control, one)
    window = operation(prologue, "OpLoad", vec4, window_pointer)
    gx, gy, gz = [operation(prologue, "OpCompositeExtract", unsigned, raw_group, component) for component in range(3)]
    wx, wy = [operation(prologue, "OpCompositeExtract", unsigned, window, component) for component in range(2)]
    zy = operation(prologue, "OpIMul", unsigned, gz, wy)
    zy = operation(prologue, "OpIAdd", unsigned, zy, gy)
    linear = operation(prologue, "OpIMul", unsigned, zy, wx)
    linear = operation(prologue, "OpIAdd", unsigned, linear, gx)
    record = operation(prologue, "OpIMul", unsigned, linear, literal(RECORD_WORDS))

    def write(lines, offset, value):
        absolute = operation(lines, "OpIAdd", unsigned, record, offset)
        pointer = operation(lines, "OpAccessChain", uint_pointer, capture, zero, absolute)
        lines.append(f"OpStore {pointer} {value}")

    def address(lines, index, stride, offset):
        scaled = operation(lines, "OpIMul", unsigned, index, literal(stride))
        return operation(lines, "OpIAdd", unsigned, scaled, literal(offset))

    generated = []
    prologue_inserted, in_entry_block = False, False
    for item in state["items"]:
        op, args, result = item["op"], item["args"], item["result"]
        if result in state["remove"] or (op in {"OpName", "OpDecorate"} and args[0] in state["remove"]):
            continue
        if op == "OpEntryPoint":
            args = [arg for arg in args if arg not in state["remove"]] + [capture, control]
            generated.append(f'OpEntryPoint {args[0]} {args[1]} "{args[2]}" {" ".join(args[3:])}')
            continue
        if item["function"] == state["entry"] and op == "OpLabel" and not prologue_inserted:
            generated.append(item["raw"])
            in_entry_block = True
            continue
        if in_entry_block and op not in {"OpVariable", "OpLine", "OpNoLine"}:
            generated.extend(prologue)
            prologue_inserted, in_entry_block = True, False
        if result in state["replacement"]:
            generated.append(state["replacement"][result])
        elif op == "OpLoad" and args[1] == state["group"]:
            generated.append(f"{result} = OpIAdd {args[0]} {raw_group} {origin_xyz}")
        else:
            generated.append(item["raw"])
        if op == "OpSetMeshOutputsEXT":
            offset = address(generated, local_x, 2, 16)
            write(generated, offset, args[0])
            offset = operation(generated, "OpIAdd", unsigned, offset, one)
            write(generated, offset, args[1])
        if id(item) not in state["output_stores"]:
            continue
        output, value = state["output_stores"][id(item)], args[1]
        if output["role"] == "triangles":
            for component in range(3):
                word = operation(generated, "OpCompositeExtract", unsigned, value, component)
                offset = address(generated, output["vertex"], 4, 5200 + component)
                write(generated, offset, word)
                marker = address(generated, output["vertex"], 3, 5712 + 5120 + component)
                write(generated, marker, one)
        else:
            word = operation(generated, "OpBitcast", unsigned, value)
            generic_index = output["index"] if output["role"] == "generic" else -1
            data_offset = (592 + generic_index * 512) if generic_index >= 0 else 80
            marker_offset = 5712 + ((generic_index + 1) * 512 if generic_index >= 0 else 0)
            offset = address(generated, output["vertex"], 4, data_offset + output["component"])
            write(generated, offset, word)
            marker = address(generated, output["vertex"], 4, marker_offset + output["component"])
            write(generated, marker, one)
    if not prologue_inserted:
        raise ValueError("No entry prologue insertion point")
    annotation_at = next(index for index, line in enumerate(generated) if "= OpType" in line)
    generated[annotation_at:annotation_at] = annotations
    declaration_at = next(index for index, line in enumerate(generated) if "= OpFunction " in line)
    generated[declaration_at:declaration_at] = declarations + global_declarations
    transformed = "\n".join(generated) + "\n"
    # The output and collective instructions are retained verbatim, not rebuilt.
    original_lines = Counter(item["raw"] for item in state["items"] if id(item) in state["output_stores"]
                             or item["op"] in {"OpSetMeshOutputsEXT", "OpControlBarrier"})
    transformed_lines = Counter(transformed.splitlines())
    if any(transformed_lines[line] != count for line, count in original_lines.items()):
        raise ValueError("Original output/collective instruction multiplicity changed")
    metadata = {"source_kind": source_kind, "original_output_store_sites": len(state["output_stores"]),
        "all_original_output_stores_preserved": True, "all_original_set_mesh_outputs_preserved": True,
        "all_original_control_barriers_preserved": True, "original_barriers": state["barriers"],
        "original_input_bindings_readonly": True, "original_push_constant_bytes": 0,
        "input_bindings": state["input_bindings"], "dead_payload_cleanup": state["payload_audit"],
        "workgroup_origin_substitution": {"enabled": True, "operation": "original WorkgroupId load becomes raw WorkgroupId + control.origin",
            "proof_scope": "read-only logical inputs; no NumWorkgroups/DrawIndex or live task payload; outputs implicit per workgroup",
            "host_obligations": "origin/window must be inside captured native XYZ grid, positive, at most32 groups and match actual draw; capture/control must not alias each other or any original input"},
        "capture_descriptor": {"set": 1, "binding": 0, "type": "storage_buffer"},
        "control_descriptor": {"set": 1, "binding": 1, "type": "storage_buffer", "byte_size": 32, "readonly": True,
            "layout": "uvec4 originXYZ/reserved0 at0; uvec4 windowXYZ/reserved0 at16"},
        "record_layout": LAYOUT, "runtime_execution_authorized": False, "equivalence_proven": False}
    return transformed, metadata


def _tool(name, arguments):
    executable = shutil.which(name)
    if not executable:
        raise ValueError(f"Missing required CPU tool: {name}")
    return subprocess.run([executable, *arguments], check=True, text=True, capture_output=True, timeout=45)


def prepare(source, output):
    source = Path(source)
    if source.is_symlink() or not source.is_file() or not 20 <= source.stat().st_size <= 2 * 1024 * 1024:
        raise ValueError("Expected bounded regular mesh SPIR-V input")
    with source.open("rb") as stream:
        data = stream.read(2 * 1024 * 1024 + 1)
    if not 20 <= len(data) <= 2 * 1024 * 1024 or len(data) % 4 or data[:4] != b"\x03\x02\x23\x07":
        raise ValueError("Invalid or changed bounded SPIR-V snapshot")
    digest = hashlib.sha256(data).hexdigest()
    kind = {ORIGINAL: "original", CONDITIONAL: "conditional"}.get(digest)
    if kind is None:
        raise ValueError("Mesh module digest is not one of the two reviewed d8 artifacts")
    return _prepare_bytes(data, output, kind)


def _prepare_bytes(data, output, source_kind):
    output = private_output(Path(output).resolve())
    if output.exists():
        raise ValueError("Choose a new private output directory")
    output.mkdir(parents=True)
    original, candidate = output / "source.spv", output / "instrumented.spv"
    original.write_bytes(data)
    _tool("spirv-val", ["--target-env", "vulkan1.3", "--uniform-buffer-standard-layout", str(original)])
    assembly = _tool("spirv-dis", ["--raw-id", str(original)]).stdout
    transformed, metadata = instrument_assembly(assembly, source_kind)
    (output / "instrumented.spvasm").write_text(transformed)
    _tool("spirv-as", ["--target-env", "spv1.6", str(output / "instrumented.spvasm"), "-o", str(candidate)])
    _tool("spirv-val", ["--target-env", "vulkan1.3", "--uniform-buffer-standard-layout", str(candidate)])
    report = {"schema": "open-ocarina.mesh-output-instrument/v1",
              "source_module_sha256": hashlib.sha256(data).hexdigest(),
              "instrumented_sha256": hashlib.sha256(candidate.read_bytes()).hexdigest(),
              "instrumenter_source_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
              "instrumented_filename": candidate.name, "source_filename": original.name,
              "structural_validation_passed": True, **metadata}
    (output / "report.json").write_text(json.dumps(report, indent=2) + "\n")
    return report


def synthetic_fixture_assembly():
    """Independently authored, origin-sensitive shader; contains no game bytes."""
    lines = ["OpCapability Shader", "OpCapability MeshShadingEXT", "OpCapability SignedZeroInfNanPreserve",
             'OpExtension "SPV_EXT_mesh_shader"', "OpMemoryModel Logical GLSL450"]
    interface = ["%wg", "%lid", "%position", "%triangles"] + [f"%generic{index}" for index in range(9)]
    interface += [f"%ubo{index}" for index in range(5)] + ["%ssbo0", "%ssbo1", "%ssbo2", "%ssbotable"]
    lines.append('OpEntryPoint MeshEXT %main "main" ' + " ".join(interface))
    lines += ["OpExecutionMode %main LocalSize 32 1 1", "OpExecutionMode %main OutputVertices 128",
              "OpExecutionMode %main OutputPrimitivesEXT 128", "OpExecutionMode %main OutputTrianglesEXT",
              "OpExecutionMode %main SignedZeroInfNanPreserve 32", "OpDecorate %wg BuiltIn WorkgroupId",
              "OpDecorate %lid BuiltIn LocalInvocationId", "OpDecorate %position BuiltIn Position",
              "OpDecorate %position Invariant", "OpDecorate %triangles BuiltIn PrimitiveTriangleIndicesEXT",
              "OpDecorate %uboarray ArrayStride 16", "OpDecorate %ubostruct Block",
              "OpMemberDecorate %ubostruct 0 Offset 0", "OpDecorate %ssboarray ArrayStride 4",
              "OpDecorate %ssbostruct Block", "OpMemberDecorate %ssbostruct 0 Offset 0"]
    for index, location in enumerate(LOCATIONS):
        lines.append(f"OpDecorate %generic{index} Location {location}")
    for binding, name in enumerate([f"%ubo{index}" for index in range(5)] + ["%ssbo0", "%ssbo1", "%ssbo2", "%ssbotable"]):
        lines.extend([f"OpDecorate {name} DescriptorSet 0", f"OpDecorate {name} Binding {binding}"])
    lines += ["%void = OpTypeVoid", "%bool = OpTypeBool", "%u32 = OpTypeInt 32 0", "%f32 = OpTypeFloat 32",
              "%uvec3 = OpTypeVector %u32 3", "%uvec4 = OpTypeVector %u32 4", "%fvec4 = OpTypeVector %f32 4",
              "%fn = OpTypeFunction %void", "%iptr = OpTypePointer Input %uvec3"]
    constants = {0, 1, 2, 3, 4, 6, 8, 16, 32, 64, 96, 127, 128, 129, 257, 1000, 65537}
    constants.update(range(40))
    for value in sorted(constants):
        lines.append(f"%k{value} = OpConstant %u32 {value}")
    lines += ["%varray = OpTypeArray %fvec4 %k128", "%tarray = OpTypeArray %uvec3 %k128",
              "%vptr = OpTypePointer Output %varray", "%tptr = OpTypePointer Output %tarray",
              "%fptr = OpTypePointer Output %f32", "%triptr = OpTypePointer Output %uvec3",
              "%uboarray = OpTypeArray %u32 %k4", "%ubostruct = OpTypeStruct %uboarray",
              "%uboptr = OpTypePointer Uniform %ubostruct", "%ssboarray = OpTypeRuntimeArray %u32",
              "%ssbostruct = OpTypeStruct %ssboarray", "%ssboptr = OpTypePointer StorageBuffer %ssbostruct",
              "%ssbotabletype = OpTypeArray %ssbostruct %k3", "%ssbotableptr = OpTypePointer StorageBuffer %ssbotabletype",
              "%wg = OpVariable %iptr Input", "%lid = OpVariable %iptr Input",
              "%position = OpVariable %vptr Output", "%triangles = OpVariable %tptr Output"]
    lines += [f"%generic{index} = OpVariable %vptr Output" for index in range(9)]
    lines += [f"%ubo{index} = OpVariable %uboptr Uniform" for index in range(5)]
    lines += [f"%ssbo{index} = OpVariable %ssboptr StorageBuffer" for index in range(3)]
    lines += ["%ssbotable = OpVariable %ssbotableptr StorageBuffer", "%main = OpFunction %void None %fn",
              "%entry = OpLabel", "%local = OpLoad %uvec3 %lid", "%x = OpCompositeExtract %u32 %local 0",
              "%group = OpLoad %uvec3 %wg", "%gx = OpCompositeExtract %u32 %group 0",
              "%gy = OpCompositeExtract %u32 %group 1", "%gz = OpCompositeExtract %u32 %group 2",
              "%tagy = OpIMul %u32 %gy %k257", "%tagz = OpIMul %u32 %gz %k65537",
              "%tagxy = OpIAdd %u32 %gx %tagy", "%tag = OpIAdd %u32 %tagxy %tagz",
              "%primitive_count = OpUMod %u32 %tag %k129",
              "OpControlBarrier %k2 %k2 %k264", "OpControlBarrier %k2 %k2 %k264",
              "OpSetMeshOutputsEXT %k128 %primitive_count"]
    # The memory-semantics constant must precede functions.
    first_function = lines.index("%main = OpFunction %void None %fn")
    lines.insert(first_function, "%k264 = OpConstant %u32 264")
    for block, first in enumerate((0, 32, 64, 96)):
        lines.append(f"%vertex{block} = OpIAdd %u32 %x %k{first}")
        lines += [f"%scaled{block} = OpIMul %u32 %vertex{block} %k8",
                  f"%base{block} = OpIAdd %u32 %tag %scaled{block}"]
        for field in range(10):
            variable = "%position" if field == 0 else f"%generic{field - 1}"
            for component in range(4):
                name = f"f{block}_{field}_{component}"
                lines += [f"%v_{name} = OpIAdd %u32 %base{block} %k{field * 4 + component}",
                          f"%f_{name} = OpConvertUToF %f32 %v_{name}",
                          f"%p_{name} = OpAccessChain %fptr {variable} %vertex{block} %k{component}",
                          f"OpStore %p_{name} %f_{name}"]
        lines += [f"%latebase{block} = OpIAdd %u32 %base{block} %k1000",
                  f"%late{block} = OpIAdd %u32 %latebase{block} %k2",
                  f"%latefloat{block} = OpConvertUToF %f32 %late{block}",
                  f"%lateptr{block} = OpAccessChain %fptr %position %vertex{block} %k2",
                  f"OpStore %lateptr{block} %latefloat{block}"]
    for block in range(4):
        lines += [f"%emit{block} = OpULessThan %bool %vertex{block} %primitive_count",
                  f"OpSelectionMerge %merge{block} None", f"OpBranchConditional %emit{block} %write{block} %merge{block}",
                  f"%write{block} = OpLabel", f"%tri0base{block} = OpIMul %u32 %vertex{block} %k3"]
        for component in range(3):
            lines += [f"%tric{block}_{component} = OpIAdd %u32 %tri0base{block} %k{component}",
                      f"%tri{block}_{component} = OpBitwiseAnd %u32 %tric{block}_{component} %k127"]
        lines += [f"%triangle{block} = OpCompositeConstruct %uvec3 %tri{block}_0 %tri{block}_1 %tri{block}_2",
                  f"%trip{block} = OpAccessChain %triptr %triangles %vertex{block}",
                  f"OpStore %trip{block} %triangle{block}", f"OpBranch %merge{block}", f"%merge{block} = OpLabel"]
    lines += ["OpReturn", "OpFunctionEnd"]
    return "\n".join(lines) + "\n"


def initial_capture(group_count):
    if type(group_count) is not int or not 1 <= group_count <= 32:
        raise ValueError("Capture must contain one through32 groups")
    words = [VALUE_SENTINEL] * (RECORD_WORDS * group_count)
    for group in range(group_count):
        base = group * RECORD_WORDS
        words[base:base + 16] = [INNER_GUARD] * 16
        words[base + 11216:base + RECORD_WORDS] = [INNER_GUARD] * 16
        words[base + 5712:base + 11216] = [0] * 5504
    return struct.pack(f"<{len(words)}I", *words)


def fixture_expected_capture(origin, groups):
    if (not isinstance(origin, (list, tuple)) or not isinstance(groups, (list, tuple))
            or len(origin) != 3 or len(groups) != 3 or any(type(value) is not int or not 0 <= value <= 0xFFFFFFFF for value in origin)
            or any(type(value) is not int or not 1 <= value <= 0xFFFFFFFF for value in groups)
            or any(start + count > 1 << 32 for start, count in zip(origin, groups))):
        raise ValueError("Expected nonnegative origin and positive3D window")
    total = groups[0] * groups[1] * groups[2]
    data = bytearray(initial_capture(total))

    def word(offset, value):
        struct.pack_into("<I", data, offset * 4, value)

    for z in range(groups[2]):
        for y in range(groups[1]):
            for x in range(groups[0]):
                relative = x + groups[0] * (y + groups[1] * z)
                base = relative * RECORD_WORDS
                tag = (origin[0] + x + 257 * (origin[1] + y) + 65537 * (origin[2] + z)) & 0xFFFFFFFF
                primitive_count = tag % 129
                for lane in range(32):
                    word(base + 16 + lane * 2, 128)
                    word(base + 17 + lane * 2, primitive_count)
                for vertex in range(128):
                    for field in range(10):
                        for component in range(4):
                            value = (tag + vertex * 8 + field * 4 + component + (1000 if field == 0 and component == 2 else 0)) & 0xFFFFFFFF
                            bits = struct.unpack("<I", struct.pack("<f", float(value)))[0]
                            offset = 80 + vertex * 4 + component if field == 0 else 592 + (field - 1) * 512 + vertex * 4 + component
                            word(base + offset, bits)
                            word(base + 5712 + field * 512 + vertex * 4 + component, 1)
                for primitive in range(primitive_count):
                    for component in range(3):
                        word(base + 5200 + primitive * 4 + component, (primitive * 3 + component) & 127)
                        word(base + 5712 + 5120 + primitive * 3 + component, 1)
    return bytes(data)


def prepare_fixture(output):
    output = private_output(Path(output).resolve())
    if output.exists():
        raise ValueError("Choose a new private output directory")
    # Assemble into a sibling private temporary directory; _prepare_bytes owns
    # the final output-directory creation and all report/validation work.
    import tempfile
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="mesh-fixture-", dir=output.parent) as directory:
        assembly, binary = Path(directory) / "fixture.spvasm", Path(directory) / "fixture.spv"
        assembly.write_text(synthetic_fixture_assembly())
        _tool("spirv-as", ["--target-env", "spv1.6", str(assembly), "-o", str(binary)])
        return _prepare_bytes(binary.read_bytes(), output, "synthetic-fixture")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path, nargs="?")
    parser.add_argument("--synthetic-fixture", action="store_true", help="Generate the independently authored fixture; no source argument")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    try:
        if args.synthetic_fixture:
            if args.source is not None:
                raise ValueError("Synthetic fixture generation does not accept a supplied module")
            report = prepare_fixture(args.output)
        elif args.source is not None:
            report = prepare(args.source, args.output)
        else:
            raise ValueError("Supply a reviewed source or --synthetic-fixture")
        print(json.dumps(report, indent=2))
    except (OSError, ValueError, KeyError, StopIteration, subprocess.SubprocessError) as exc:
        print(str(exc), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
