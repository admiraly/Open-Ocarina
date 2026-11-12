#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""CPU-only, digest-pinned structural audit and conditional mesh ballot prototype.

Candidate emission is exclusively for later compilation inspection. Mesh lane
mapping and real output equivalence remain unverified; no Vulkan API is called.
"""
import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
import shlex
import subprocess
import sys

try:
    from .compile_probe import private_output
except ImportError:
    from compile_probe import private_output

TARGET_SHA256 = "835aa59799c6ecb621034d946e2fa4eadb1aa2407d293321ae3e3052882bc9b3"
MASKS = {"SubgroupEqMask", "SubgroupLtMask", "SubgroupLeMask", "SubgroupGtMask", "SubgroupGeMask"}
ALLOWED_BUILTINS = MASKS | {"SubgroupLocalInvocationId"}


def parse(assembly):
    if len(assembly) > 16 * 1024 * 1024:
        raise ValueError("Assembly exceeds bound")
    instructions, definitions, functions = [], {}, {}
    current = None
    for raw in assembly.splitlines():
        if raw.lstrip().startswith(";"):
            continue
        parts = shlex.split(raw, comments=True)
        if not parts:
            continue
        result = parts[0] if len(parts) > 2 and parts[1] == "=" else None
        op, args = (parts[2], parts[3:]) if result else (parts[0], parts[1:])
        if not op.startswith("Op") or any(token.startswith("%ow32_") for token in parts):
            raise ValueError("Unsupported syntax or reserved prototype ID")
        instruction = (result, op, args, raw)
        if result:
            if result in definitions:
                raise ValueError("Duplicate result ID")
            definitions[result] = instruction
        if op == "OpFunction":
            if current is not None:
                raise ValueError("Nested function")
            current = result
            functions[current] = []
        if current is not None:
            functions[current].append(instruction)
        if op == "OpFunctionEnd":
            if current is None:
                raise ValueError("Unexpected function end")
            current = None
        instructions.append(instruction)
    if current is not None:
        raise ValueError("Truncated function")
    return instructions, definitions, functions


def audit(assembly):
    instructions, definitions, functions = parse(assembly)
    entries = [i for i in instructions if i[1] == "OpEntryPoint"]
    if len(entries) != 1 or entries[0][2][:3:2] != ["MeshEXT", "main"]:
        raise ValueError("Exactly one MeshEXT main is required")
    entry = entries[0][2][1]
    if entry not in functions:
        raise ValueError("Missing main function")
    models = [i[2] for i in instructions if i[1] == "OpMemoryModel"]
    if models != [["Logical", "GLSL450"]]:
        raise ValueError("Only Logical GLSL450 memory model is supported")
    local_sizes = [i[2] for i in instructions if i[1] in ("OpExecutionMode", "OpExecutionModeId")
                   and len(i[2]) > 1 and i[2][1] in ("LocalSize", "LocalSizeId")]
    if local_sizes != [[entry, "LocalSize", "32", "1", "1"]]:
        raise ValueError("Exactly one literal 32x1x1 local size is required")
    if any(i[1] == "OpMemberDecorate" and "BuiltIn" in i[2] for i in instructions):
        raise ValueError("Member builtins are unsupported")
    builtins = {}
    for _, op, args, _ in instructions:
        if op == "OpDecorate" and len(args) == 3 and args[1] == "BuiltIn":
            if args[0] in builtins:
                raise ValueError("Duplicate builtin decoration")
            builtins[args[0]] = args[2]
    subgroup = {identity: name for identity, name in builtins.items() if "Subgroup" in name}
    if set(subgroup.values()) != ALLOWED_BUILTINS or len(subgroup) != len(ALLOWED_BUILTINS):
        raise ValueError("Unexpected subgroup builtin inventory")
    locals_ = [identity for identity, name in builtins.items() if name == "LocalInvocationId"]
    if len(locals_) != 1 or locals_[0] not in entries[0][2][3:]:
        raise ValueError("Unique local invocation builtin must be in entry interface")
    local = locals_[0]

    def pointee(identity):
        variable = definitions.get(identity)
        if not variable or variable[1] != "OpVariable" or variable[2][1:] != ["Input"]:
            raise ValueError("Builtin must be a plain Input variable")
        pointer = definitions.get(variable[2][0])
        if not pointer or pointer[1] != "OpTypePointer" or pointer[2][0] != "Input":
            raise ValueError("Unexpected builtin pointer type")
        return pointer[2][1]

    local_type = pointee(local)
    if definitions[local_type][1] != "OpTypeVector" or definitions[local_type][2][1:] != ["3"]:
        raise ValueError("Local invocation ID must be a three-vector")
    uint = definitions[local_type][2][0]
    if definitions[uint][1:3] != ("OpTypeInt", ["32", "0"]):
        raise ValueError("Unsigned 32-bit lane type required")
    vectors = {pointee(identity) for identity, name in subgroup.items() if name in MASKS}
    if len(vectors) != 1:
        raise ValueError("Mask types differ")
    vector = vectors.pop()
    if definitions[vector][1:3] != ("OpTypeVector", [uint, "4"]):
        raise ValueError("Masks must use uint32 four-vectors")
    loads = Counter()
    for identity, name in subgroup.items():
        expected = uint if name == "SubgroupLocalInvocationId" else vector
        if pointee(identity) != expected or identity not in entries[0][2][3:]:
            raise ValueError("Subgroup builtin type/interface mismatch")
        for result, op, args, _ in instructions:
            if identity not in args:
                continue
            if op in ("OpName", "OpEntryPoint") or (op == "OpDecorate" and args == [identity, "BuiltIn", name]):
                continue
            if op != "OpLoad" or args != [expected, identity]:
                raise ValueError("Aliased or unsupported subgroup builtin use")
            loads[name] += 1

    reachable, pending = set(), [entry]
    while pending:
        function = pending.pop()
        if function in reachable:
            continue
        if function not in functions:
            raise ValueError("Unresolved function call")
        reachable.add(function)
        for _, op, args, _ in functions[function]:
            if op == "OpFunctionCall":
                pending.append(args[1])
            if "FunctionPointer" in op:
                raise ValueError("Indirect functions unsupported")
    active = [i for f in reachable for i in functions[f]]
    group = [i for i in instructions if i[1].startswith("OpGroup") or "Subgroup" in i[1]]
    ballots = [i for i in group if i[1] == "OpGroupNonUniformBallot"]
    if len(group) != 2 or len(ballots) != 2 or any(i not in functions[entry] for i in ballots):
        raise ValueError("Only two main-function ballots are supported")
    for result, _, args, _ in ballots:
        if len(args) != 3 or args[0] != vector:
            raise ValueError("Unexpected ballot type/operands")
        scope = definitions.get(args[1])
        if not scope or scope[1:3] != ("OpConstant", [uint, "3"]):
            raise ValueError("Ballot scope must be constant Subgroup")
        predicate = definitions.get(args[2])
        if not predicate or not predicate[2] or definitions.get(predicate[2][0], (None, None))[1] != "OpTypeBool":
            raise ValueError("Ballot predicate must have a declared bool type")
        uses = [i for i in instructions if result in i[2]]
        if len(uses) != 1 or uses[0][1:3] != ("OpCompositeExtract", [uint, result, "0"]):
            raise ValueError("Ballot result must have one component-zero consumer")
    blocks, order = {}, []
    current = None
    for instruction in functions[entry]:
        if instruction[1] == "OpLabel":
            current = instruction[0]
            blocks[current] = []
            order.append(current)
        elif current is not None and instruction[1] != "OpFunctionEnd":
            blocks[current].append(instruction)
    if not order:
        raise ValueError("Empty main")
    ballot_blocks = [label for label, body in blocks.items() if any(i in ballots for i in body)]
    if len(ballot_blocks) != 1:
        raise ValueError("Ballots must share one block")
    target = ballot_blocks[0]
    edges = {}
    for label, body in blocks.items():
        if not body:
            raise ValueError("Empty basic block")
        terminator, args = body[-1][1:3]
        if terminator == "OpBranch":
            successors = args
        elif terminator == "OpBranchConditional" and len(args) == 3:
            successors = args[1:]
        elif terminator in ("OpReturn", "OpReturnValue", "OpUnreachable"):
            successors = []
        else:
            raise ValueError("Unsupported main terminator")
        if any(label not in blocks for label in successors):
            raise ValueError("Unknown branch target")
        edges[label] = successors
    visiting, visited = set(), set()

    def prefix(label):
        if label == target:
            return
        if label in visiting:
            raise ValueError("Cycle before ballot")
        if label in visited:
            return
        visiting.add(label)
        if not edges[label]:
            raise ValueError("Entry path exits before ballots")
        if any(i[1] in ("OpFunctionCall", "OpLoopMerge", "OpKill", "OpTerminateInvocation",
                       "OpDemoteToHelperInvocation", "OpEmitMeshTasksEXT") for i in blocks[label]):
            raise ValueError("Unsupported call/termination/loop before ballots")
        for successor in edges[label]:
            prefix(successor)
        visiting.remove(label)
        visited.add(label)

    prefix(order[0])
    # A later back edge would permit only a subset to execute a new barrier.
    seen, pending = set(), list(edges[target])
    while pending:
        label = pending.pop()
        if label == target:
            raise ValueError("Ballot block can be revisited")
        if label not in seen:
            seen.add(label)
            pending.extend(edges[label])
    last_ballot = max(blocks[target].index(i) for i in ballots)
    if any(i[1] in ("OpFunctionCall", "OpKill", "OpTerminateInvocation", "OpDemoteToHelperInvocation")
           for i in blocks[target][:last_ballot]):
        raise ValueError("Unsupported call/termination at ballot prefix")
    report = {"entry_model": "MeshEXT", "local_size": [32, 1, 1], "reachable_functions": len(reachable),
              "reachable_subgroup_ops": dict(Counter(i[1] for i in active if i in group)),
              "subgroup_builtin_declarations": sorted(subgroup.values()), "subgroup_builtin_loads": dict(loads),
              "all_entry_paths_reach_ballots": True, "prefix_blocks_excluding_ballot": len(visited),
              "ballot_block_revisited": False, "ballot_component_zero_consumers": 2,
              "memory_model": "Logical GLSL450", "added_workgroup_bytes": 256,
              "added_uniform_barriers": 2, "lane_mapping_verified": False,
              "source_runtime_required_width_verified": False,
              "equivalence_proven": False, "runtime_execution_authorized": False,
              "condition": "Original mesh subgroup32 lane ID must equal LocalInvocationId.x; unverified"}
    return report, (instructions, subgroup, local, local_type, uint, vector, ballots)


def transform_assembly(assembly):
    report, context = audit(assembly)
    instructions, subgroup, local, local_type, uint, vector, ballots = context
    globals_ = [f"%ow32_c{i} = OpConstant {uint} {i}" for i in range(33)]
    globals_ += [f"%ow32_sem = OpConstant {uint} 264",
                 f"%ow32_array = OpTypeArray {uint} %ow32_c32",
                 f"%ow32_ptr_array = OpTypePointer Workgroup %ow32_array",
                 f"%ow32_ptr_uint = OpTypePointer Workgroup {uint}"]
    globals_ += [f"%ow32_scratch{i} = OpVariable %ow32_ptr_array Workgroup" for i in range(2)]
    output, serial, declared = [], 0, False

    def lane(prefix):
        return [f"{prefix}_local = OpLoad {local_type} {local}",
                f"{prefix}_lane = OpCompositeExtract {uint} {prefix}_local 0"]

    for result, op, args, raw in instructions:
        if op == "OpFunction" and not declared:
            output.extend(globals_)
            declared = True
        if op == "OpCapability" and args[0].startswith("GroupNonUniform"):
            continue
        if result in subgroup or (op in ("OpDecorate", "OpName") and args[0] in subgroup):
            continue
        if op == "OpEntryPoint":
            interface = [identity for identity in args[3:] if identity not in subgroup]
            interface += ["%ow32_scratch0", "%ow32_scratch1"]
            output.append(f'OpEntryPoint {args[0]} {args[1]} "main" ' + " ".join(interface))
        elif op == "OpLoad" and args[1] in subgroup:
            prefix = f"%ow32_builtin{serial}"
            serial += 1
            output.extend(lane(prefix))
            name = subgroup[args[1]]
            if name == "SubgroupLocalInvocationId":
                output.append(f"{result} = OpCopyObject {uint} {prefix}_lane")
            else:
                output += [f"{prefix}_eq = OpShiftLeftLogical {uint} %ow32_c1 {prefix}_lane",
                           f"{prefix}_lt = OpISub {uint} {prefix}_eq %ow32_c1",
                           f"{prefix}_le = OpBitwiseOr {uint} {prefix}_eq {prefix}_lt",
                           f"{prefix}_gt = OpNot {uint} {prefix}_le",
                           f"{prefix}_ge = OpNot {uint} {prefix}_lt"]
                suffix = {"SubgroupEqMask": "eq", "SubgroupLtMask": "lt", "SubgroupLeMask": "le",
                          "SubgroupGtMask": "gt", "SubgroupGeMask": "ge"}[name]
                output.append(f"{result} = OpCompositeConstruct {vector} {prefix}_{suffix} %ow32_c0 %ow32_c0 %ow32_c0")
        elif op == "OpGroupNonUniformBallot":
            index = next(index for index, item in enumerate(ballots) if item[0] == result)
            prefix = f"%ow32_ballot{index}"
            output.extend(lane(prefix))
            output += [f"{prefix}_bit = OpShiftLeftLogical {uint} %ow32_c1 {prefix}_lane",
                       f"{prefix}_value = OpSelect {uint} {args[2]} {prefix}_bit %ow32_c0",
                       f"{prefix}_pointer = OpAccessChain %ow32_ptr_uint %ow32_scratch{index} {prefix}_lane",
                       f"OpStore {prefix}_pointer {prefix}_value",
                       "OpControlBarrier %ow32_c2 %ow32_c2 %ow32_sem"]
            previous = "%ow32_c0"
            for lane_index in range(32):
                output += [f"{prefix}_p{lane_index} = OpAccessChain %ow32_ptr_uint %ow32_scratch{index} %ow32_c{lane_index}",
                           f"{prefix}_v{lane_index} = OpLoad {uint} {prefix}_p{lane_index}",
                           f"{prefix}_or{lane_index} = OpBitwiseOr {uint} {previous} {prefix}_v{lane_index}"]
                previous = f"{prefix}_or{lane_index}"
            output.append(f"{result} = OpCompositeConstruct {vector} {previous} %ow32_c0 %ow32_c0 %ow32_c0")
        else:
            output.append(raw)
    return "\n".join(output) + "\n", report


def checked_source(path):
    if path.is_symlink() or not path.is_file() or path.stat().st_size > 2 * 1024 * 1024:
        raise ValueError("Input must be a bounded regular module")
    data = path.read_bytes()
    if hashlib.sha256(data).hexdigest() != TARGET_SHA256:
        raise ValueError("Input differs from the exact reviewed nightly mesh digest")
    return data


def run(args):
    data = checked_source(args.input)
    output = private_output(args.output)
    output.mkdir(parents=True)
    source = output / "source.spv"
    source.write_bytes(data)

    def tool(command, stem):
        result = subprocess.run(command, capture_output=True, text=True, timeout=30)
        (output / f"{stem}.stdout").write_text(result.stdout)
        (output / f"{stem}.stderr").write_text(result.stderr)
        if result.returncode:
            raise ValueError(f"{stem} failed; inspect private log")
        return result.stdout

    validator = ["spirv-val", "--target-env", "vulkan1.3", "--uniform-buffer-standard-layout"]
    tool([*validator, str(source)], "validate-source")
    assembly = tool(["spirv-dis", "--raw-id", str(source)], "disassemble-source")
    report, _ = audit(assembly)
    report.update(schema=1, source_sha256=TARGET_SHA256, cpu_only=True, candidate_emitted=False,
                  transformer_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest())
    if args.compile_only_candidate:
        candidate, _ = transform_assembly(assembly)
        textual = output / "candidate.spvasm"
        textual.write_text(candidate)
        binary = output / "candidate.spv"
        tool(["spirv-as", "--target-env", "vulkan1.3", "--preserve-numeric-ids", str(textual), "-o", str(binary)], "assemble-candidate")
        tool([*validator, str(binary)], "validate-candidate")
        result = tool(["spirv-dis", "--raw-id", str(binary)], "disassemble-candidate")
        parsed, _, _ = parse(result)
        if any(op.startswith("OpGroup") or "Subgroup" in op or
               (op == "OpDecorate" and "BuiltIn" in arguments and any("Subgroup" in a for a in arguments))
               for _, op, arguments, _ in parsed):
            raise ValueError("Residual subgroup dependence in candidate")
        report.update(candidate_emitted=True, candidate_validated=True,
                      candidate_sha256=hashlib.sha256(binary.read_bytes()).hexdigest(),
                      candidate_words=len(binary.read_bytes()) // 4,
                      candidate_scope="Compile-only conditional prototype; no runtime equivalence")
    (output / "report.json").write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report, indent=2))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--compile-only-candidate", action="store_true",
                        help="Emit a private conditional candidate for later compilation inspection only")
    args = parser.parse_args()
    try:
        run(args)
    except (OSError, ValueError, subprocess.SubprocessError) as error:
        print(str(error), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
