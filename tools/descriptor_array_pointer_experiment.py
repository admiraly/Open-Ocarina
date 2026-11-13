#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Synthetic typed descriptor-array pointers: CPU model and SPIR-V validation only.

No Vulkan loader, instance, device, pipeline, command buffer or dispatch is used.
"""
from __future__ import annotations

import argparse
from bisect import bisect_right
from dataclasses import asdict, dataclass
import hashlib
import json
from pathlib import Path
import re
import struct
import subprocess

U32 = (1 << 32) - 1
U64 = (1 << 64) - 1
WIDTHS = (4, 8, 16)
W0, W0_SIZE = 0x1000000000, 8 << 30
W1, W1_SIZE = 0x7F00000000, 4 << 30
ROOT = Path(__file__).resolve().parents[1]


@dataclass(frozen=True)
class Descriptor:
    guest: int
    size_word: int
    bound_bytes: int
    binding: int
    element: int
    kind: str = "single"
    device: int = 0


@dataclass(frozen=True)
class Interval:
    low: int
    high: int
    base: int
    binding: int
    element: int


def validate_descriptor(view, alignment):
    if alignment <= 0 or alignment & (alignment - 1):
        raise ValueError("Alignment must be a positive power of two")
    if view.kind not in ("candidate", "single", "table"):
        raise ValueError("Descriptor class must already be known")
    if not 0 <= view.guest <= U64 or not 0 <= view.size_word <= U32 or view.bound_bytes < 0:
        raise ValueError("Invalid descriptor range")
    if view.binding not in (0, 1) or view.element not in (0, 1):
        raise ValueError("Synthetic descriptor roots are binding0/1, element0/1")
    if not 0 <= view.device <= U64:
        raise ValueError("Invalid synthetic device address")


def build_partitions(views, alignment=16):
    """Resolve each width independently, including source integer wrap rules.

    Inputs are already classified, in source traversal order. This does not
    derive native descriptor classes or assert GPU-valid original pointers.
    """
    for view in views:
        validate_descriptor(view, alignment)
    partitions = {}
    for width in WIDTHS:
        claims = []
        for order, view in enumerate(views):
            base = view.guest & ~(alignment - 1)
            count32 = (view.bound_bytes // width) & U32
            byte_extent32 = (count32 * width) & U32
            bound_end = (base + byte_extent32) & U64
            nvn_end = (view.guest + view.size_word) & U64
            end = bound_end if view.kind == "candidate" or (view.kind == "table" and view.size_word == 0) else min(bound_end, nvn_end)
            if base < end:
                claims.append((base, end, order, view))
        boundaries = sorted({end for claim in claims for end in claim[:2]})
        rows = []
        for low, high in zip(boundaries, boundaries[1:]):
            hits = [claim for claim in claims if claim[0] <= low < claim[1]]
            if not hits:
                continue
            # A nearer candidate wins; equal candidate bases retain source order.
            candidates = [claim for claim in hits if claim[3].kind == "candidate"]
            chosen = min(candidates, key=lambda claim: (-claim[0], claim[2])) if candidates else min(hits, key=lambda claim: claim[2])
            base, _, _, view = chosen
            row = Interval(low, high, base, view.binding, view.element)
            if rows and rows[-1].high == low and (rows[-1].base, rows[-1].binding, rows[-1].element) == (base, view.binding, view.element):
                previous = rows[-1]
                rows[-1] = Interval(previous.low, high, base, view.binding, view.element)
            else:
                rows.append(row)
        partitions[width] = rows
    return partitions


def walk_reference(views, address, width, alignment=16):
    """Separate direct walk, mirroring inspected operations rather than intervals."""
    if width not in WIDTHS or not 0 <= address <= U64:
        raise ValueError("Invalid typed query")
    winner, best_distance = None, U64
    for view in views:
        validate_descriptor(view, alignment)
        aligned = view.guest & -alignment
        length_result = view.bound_bytes // width % (1 << 32)
        endpoint = (aligned + ((length_result << (width.bit_length() - 1)) % (1 << 32))) % (1 << 64)
        if view.kind != "candidate" and not (view.kind == "table" and view.size_word == 0):
            endpoint = min(endpoint, (view.guest + view.size_word) % (1 << 64))
        if aligned <= address < endpoint:
            if view.kind == "candidate":
                distance = address - aligned
                if distance < best_distance:
                    winner, best_distance = view, distance
            elif winner is None:
                winner = view
    if winner is None:
        return None
    relative = (address - (winner.guest & -alignment)) % (1 << 32)
    return winner.binding, winner.element, (relative // width) * width


def interval_lookup(partitions, address, width):
    if width not in WIDTHS or not 0 <= address <= U64:
        raise ValueError("Invalid typed query")
    rows = partitions[width]
    position = bisect_right([row.low for row in rows], address) - 1
    if position < 0 or address >= rows[position].high:
        return None
    row = rows[position]
    return row.binding, row.element, (((address - row.base) & U32) // width) * width


def page_location(table, address, width):
    if address % 4096 + width > 4096:
        return None
    if W0 <= address < W0 + W0_SIZE:
        index = (address - W0) // 4096
    elif W1 <= address < W1 + W1_SIZE:
        index = W0_SIZE // 4096 + (address - W1) // 4096
    else:
        return None
    base = table.get(index, 0)
    return (base + address % 4096) & U64 if base else None


def access_location(views, partitions, table, address, width, *, transformed=False, store=False, follow_stores=False):
    hit = interval_lookup(partitions, address, width) if transformed else walk_reference(views, address, width)
    if hit is not None:
        return ("StorageBuffer", *hit)
    if store and not follow_stores:
        return None
    physical = page_location(table, address, width)
    return None if physical is None else ("PhysicalStorageBuffer", physical)


def glsl_source():
    parts = ["""#version 460
#extension GL_EXT_nonuniform_qualifier : require
#extension GL_EXT_buffer_reference : require
#extension GL_EXT_shader_explicit_arithmetic_types_int64 : require
layout(local_size_x=64) in;
struct IntervalRow { uint64_t low; uint64_t high; uint64_t base; uint root; uint descriptor; };
layout(set=0,binding=2,std430) readonly buffer Records { IntervalRow rows[]; } records;
layout(set=0,binding=3,std430) readonly buffer Queries { uint64_t addresses[]; } queries;
layout(set=0,binding=4,std430) buffer Results { uvec4 values[]; } results;
layout(push_constant,std430) uniform Parameters { uvec4 starts; uvec4 counts; uint64_t page_root; uint follow_stores; uint reserved; } parameters;
layout(buffer_reference,std430,buffer_reference_align=8) readonly buffer Page { uint64_t address; };
uint64_t physical_address(uint64_t address,uint width) {
    uint64_t a=address-uint64_t(0x1000000000ul);
    uint64_t b=address-uint64_t(0x7f00000000ul);
    bool in_a=a<uint64_t(0x200000000ul), in_b=b<uint64_t(0x100000000ul);
    if(parameters.page_root==uint64_t(0) || (!in_a&&!in_b) || (address&uint64_t(4095))>uint64_t(4096u-width)) return uint64_t(0);
    uint64_t index=in_a?(a>>12):((b>>12)+uint64_t(0x200000ul));
    uint64_t page=Page(parameters.page_root+index*uint64_t(8)).address;
    return page==uint64_t(0)?uint64_t(0):page+(address&uint64_t(4095));
}
uvec4 locate(uint64_t address,uint width_slot) {
    uint low=parameters.starts[width_slot], high=low+parameters.counts[width_slot];
    while(low<high) {
        uint middle=low+(high-low)/2u;
        IntervalRow row=records.rows[middle];
        if(address<row.low) high=middle;
        else if(address>=row.high) low=middle+1u;
        else return uvec4(row.root,row.descriptor,uint(address-row.base),1u);
    }
    return uvec4(0u);
}
"""]
    for slot, width in enumerate(WIDTHS):
        value_type = {4: "uint", 8: "uvec2", 16: "uvec4"}[width]
        shift = width.bit_length() - 1
        parts.append(f"""
layout(set=0,binding=0,std430) buffer A{width} {{ {value_type} values[]; }} a{width}[2];
layout(set=0,binding=1,std430) buffer B{width} {{ {value_type} values[]; }} b{width}[2];
layout(buffer_reference,std430,buffer_reference_align=4) buffer Physical{width} {{ {value_type} value; }};
uvec4 locate{width}(uint64_t address) {{ return locate(address,{slot}u); }}
uint length{width}(uint root,uint descriptor) {{
    if(root==0u) return a{width}[nonuniformEXT(descriptor)].values.length();
    return b{width}[nonuniformEXT(descriptor)].values.length();
}}
{value_type} fallback_load{width}(uint64_t address) {{
    uint64_t physical=physical_address(address,{width}u);
    return physical==uint64_t(0)?{value_type}(0u):Physical{width}(physical).value;
}}
void fallback_store{width}(uint64_t address,{value_type} value) {{
    if(parameters.follow_stores==0u) return;
    uint64_t physical=physical_address(address,{width}u);
    if(physical!=uint64_t(0)) Physical{width}(physical).value=value;
}}
{value_type} load{width}(uint64_t address) {{
    uvec4 hit=locate{width}(address);
    if(hit.w==0u) return fallback_load{width}(address);
    if(hit.x==0u) return a{width}[nonuniformEXT(hit.y)].values[hit.z>>{shift}];
    return b{width}[nonuniformEXT(hit.y)].values[hit.z>>{shift}];
}}
void store{width}(uint64_t address,{value_type} value) {{
    uvec4 hit=locate{width}(address);
    if(hit.w==0u) fallback_store{width}(address,value);
    else if(hit.x==0u) a{width}[nonuniformEXT(hit.y)].values[hit.z>>{shift}]=value;
    else b{width}[nonuniformEXT(hit.y)].values[hit.z>>{shift}]=value;
}}
""")
    parts.append("""void main() {
    uint invocation=gl_GlobalInvocationID.x;
    uint64_t address=queries.addresses[invocation]&~uint64_t(3);
    uint v4=load4(address); uvec2 v8=load8(address); uvec4 v16=load16(address);
    store4(address,v4); store8(address,v8); store16(address,v16);
    uvec4 hit=locate4(address);
    uint root=hit.x&1u, descriptor=hit.y&1u;
    results.values[invocation]=uvec4(v4^v8.x^v16.x,length4(root,descriptor),length8(root,descriptor),length16(root,descriptor));
}
""")
    return "".join(parts)


def named_id(assembly, name):
    matches = re.findall(r'^\s*OpName (%\S+) "' + re.escape(name) + r'[^"\n]*"$', assembly, re.MULTILINE)
    if len(matches) != 1:
        raise ValueError("Expected one synthetic symbol: " + name)
    return matches[0]


def type_id(assembly, declaration):
    matches = re.findall(r'^\s*(%\S+) = ' + declaration + r'$', assembly, re.MULTILINE)
    if len(matches) != 1:
        raise ValueError("Expected one synthetic type: " + declaration)
    return matches[0]


def pointer_assembly(assembly):
    """Rewrite only independently authored wrappers to typed array-pointer returns."""
    uint = type_id(assembly, "OpTypeInt 32 0")
    ulong = type_id(assembly, "OpTypeInt 64 0")
    boolean = type_id(assembly, "OpTypeBool")
    void = type_id(assembly, "OpTypeVoid")
    vector4 = type_id(assembly, "OpTypeVector " + re.escape(uint) + " 4")
    function_address = type_id(assembly, "OpTypePointer Function " + re.escape(ulong))
    assembly = assembly.replace("OpCapability Shader", "OpCapability Shader\n               OpCapability VariablePointersStorageBuffer", 1)
    annotations, declarations, helpers, removed_ids = [], [], [], set()
    for width in WIDTHS:
        value_type = uint if width == 4 else type_id(assembly, "OpTypeVector " + re.escape(uint) + f" {width//4}")
        pointer_type = type_id(assembly, "OpTypePointer StorageBuffer " + re.escape(value_type))
        lookup = named_id(assembly, f"locate{width}(")
        root_a, root_b = named_id(assembly, f"a{width}"), named_id(assembly, f"b{width}")
        prefix = f"%dap{width}_"
        for suffix in ("descriptor", "a", "b", "loaded_pointer", "stored_pointer"):
            annotations.append(f"OpDecorate {prefix}{suffix} NonUniform")
        declarations.append(f"""{prefix}zero = OpConstant {uint} 0
{prefix}one = OpConstant {uint} 1
{prefix}shift = OpConstant {uint} {width.bit_length()-1}
{prefix}null = OpConstantNull {pointer_type}
{prefix}type = OpTypeFunction {pointer_type} {function_address}
""")
        helpers.append(f"""{prefix}lookup = OpFunction {pointer_type} DontInline {prefix}type
{prefix}address = OpFunctionParameter {function_address}
{prefix}entry = OpLabel
{prefix}hit = OpFunctionCall {vector4} {lookup} {prefix}address
{prefix}valid = OpCompositeExtract {uint} {prefix}hit 3
{prefix}missing = OpIEqual {boolean} {prefix}valid {prefix}zero
OpSelectionMerge {prefix}present None
OpBranchConditional {prefix}missing {prefix}absent {prefix}present
{prefix}absent = OpLabel
OpReturnValue {prefix}null
{prefix}present = OpLabel
{prefix}root = OpCompositeExtract {uint} {prefix}hit 0
{prefix}descriptor = OpCompositeExtract {uint} {prefix}hit 1
{prefix}relative = OpCompositeExtract {uint} {prefix}hit 2
{prefix}index = OpShiftRightLogical {uint} {prefix}relative {prefix}shift
{prefix}is_a = OpIEqual {boolean} {prefix}root {prefix}zero
OpSelectionMerge {prefix}choose_b None
OpBranchConditional {prefix}is_a {prefix}choose_a {prefix}choose_b
{prefix}choose_a = OpLabel
{prefix}a = OpAccessChain {pointer_type} {root_a} {prefix}descriptor {prefix}zero {prefix}index
OpReturnValue {prefix}a
{prefix}choose_b = OpLabel
{prefix}b = OpAccessChain {pointer_type} {root_b} {prefix}descriptor {prefix}zero {prefix}index
OpReturnValue {prefix}b
OpFunctionEnd
""")
        for kind in ("load", "store"):
            identity = named_id(assembly, f"{kind}{width}(")
            fallback = named_id(assembly, f"fallback_{kind}{width}(")
            pattern = re.compile(r'^\s*' + re.escape(identity) + r' = OpFunction[^\n]*\n(?P<params>(?:\s*%\S+ = OpFunctionParameter[^\n]*\n)+).*?^\s*OpFunctionEnd\s*$', re.MULTILINE | re.DOTALL)
            match = pattern.search(assembly)
            if match is None:
                raise ValueError("Missing synthetic wrapper")
            header = next(line for line in match[0].splitlines() if " = OpFunction " in line)
            params = match["params"]
            removed_ids.update(re.findall(r'^\s*(%\S+) = ', match[0], re.MULTILINE))
            parameter_ids = re.findall(r'(%\S+) = OpFunctionParameter', params)
            if len(parameter_ids) != (1 if kind == "load" else 2):
                raise ValueError("Unexpected synthetic wrapper signature")
            address = parameter_ids[0]
            tag = prefix + kind + "_"
            returned_pointer = prefix + ("loaded_pointer" if kind == "load" else "stored_pointer")
            body = f"""{header}
{params}
{tag}entry = OpLabel
{returned_pointer} = OpFunctionCall {pointer_type} {prefix}lookup {address}
{tag}miss = OpPtrEqual {boolean} {returned_pointer} {prefix}null
OpSelectionMerge {tag}end None
OpBranchConditional {tag}miss {tag}fallback {tag}hit
{tag}hit = OpLabel
"""
            if kind == "load":
                body += f"{tag}value = OpLoad {value_type} {returned_pointer}\nOpReturnValue {tag}value\n"
            else:
                body += f"{tag}value = OpLoad {value_type} {parameter_ids[1]}\nOpStore {returned_pointer} {tag}value\nOpReturn\n"
            body += f"{tag}fallback = OpLabel\n"
            if kind == "load":
                body += f"{tag}physical = OpFunctionCall {value_type} {fallback} {address}\nOpReturnValue {tag}physical\n"
            else:
                body += f"{tag}physical = OpFunctionCall {void} {fallback} {address} {parameter_ids[1]}\nOpReturn\n"
            body += f"{tag}end = OpLabel\nOpUnreachable\nOpFunctionEnd\n"
            assembly = assembly[:match.start()] + "\n" + body + assembly[match.end():]
    # Drop debug IDs for removed wrapper-local variables before inserting globals.
    assembly = "\n".join(line for line in assembly.splitlines() if not re.match(r"\s*Op(?:Member)?Name ", line)) + "\n"
    defined_ids = set(re.findall(r'^\s*(%\S+) = ', assembly, re.MULTILINE))
    deleted = removed_ids - defined_ids
    assembly = "\n".join(line for line in assembly.splitlines()
                         if not (match := re.match(r"\s*OpDecorate (%\S+) ", line)) or match[1] not in deleted) + "\n"
    # Declarations must precede all global variables, but follow types they use.
    first_function = re.search(r'^\s*%\S+ = OpFunction ', assembly, re.MULTILINE).start()
    assembly = assembly[:first_function] + "\n" + "".join(declarations) + assembly[first_function:]
    first_type = re.search(r'^\s*%\S+ = OpType', assembly, re.MULTILINE).start()
    assembly = assembly[:first_type] + "\n" + "\n".join(annotations) + "\n" + assembly[first_type:]
    return assembly + "".join(helpers)


def example_plan():
    views = [Descriptor(W0+4092, 0, 10, 0, 0),
             Descriptor(W0+4080, 64, 64, 1, 1),
             Descriptor(W0+8192, 0, 32, 0, 1, "table")]
    partitions = build_partitions(views)
    rows, starts, counts = [], [], []
    for width in WIDTHS:
        starts.append(len(rows))
        counts.append(len(partitions[width]))
        rows.extend(asdict(row) for row in partitions[width])
    return {"schema": 1, "synthetic": True, "alignment": 16,
            "descriptor_bindings": [{"binding": binding, "count": 2, "typed_alias_widths": list(WIDTHS)} for binding in (0, 1)],
            "descriptors": [asdict(view) for view in views], "widths": list(WIDTHS),
            "starts": starts, "counts": counts, "rows": rows,
            "required_features": ["shaderInt64", "bufferDeviceAddress", "variablePointersStorageBuffer", "shaderStorageBufferArrayNonUniformIndexing"],
            "device_feature_support_queried": False, "pipeline_compile_authorized": False,
            "future_compile_requirements": ["Query and enable every declared optional feature.",
                                            "Match original robustBufferAccess/robustBufferAccess2 and reported robustness alignment.",
                                            "Bind every element of both fixed-count arrays; match all typed alias ranges.",
                                            "Verify uploaded per-width intervals against as-submitted descriptor ranges and words."],
            "limitations": ["Already-classified synthetic views, not native descriptor creation.",
                            "CPU integer-address agreement does not prove GPU access validity, robustness or alias behavior.",
                            "Feature support and descriptor limits must be queried only after the parent grants device access."]}


def canonical(data):
    return (json.dumps(data, sort_keys=True, indent=2) + "\n").encode()


def validate_only(output, glslang="glslangValidator", assembler="spirv-as", disassembler="spirv-dis", validator="spirv-val"):
    output = Path(output).resolve()
    if not any(output.is_relative_to(ROOT / part) for part in ("local", "work", "artifacts")) or output.exists():
        raise ValueError("Use a fresh ignored private output directory")
    output.mkdir(parents=True)
    source = output / "descriptor-array.comp"
    original = output / "descriptor-array-value.spv"
    assembly = output / "descriptor-array-pointer.spvasm"
    binary = output / "descriptor-array-pointer.spv"
    source.write_text(glsl_source())
    calls = [[glslang, "-V", "--target-env", "vulkan1.2", "-S", "comp", str(source), "-o", str(original)]]
    for call in calls:
        subprocess.run(call, check=True, capture_output=True, text=True, timeout=30)
    text = subprocess.run([disassembler, str(original)], check=True, capture_output=True, text=True, timeout=15).stdout
    assembly.write_text(pointer_assembly(text))
    subprocess.run([assembler, "--target-env", "vulkan1.2", str(assembly), "-o", str(binary)], check=True, capture_output=True, text=True, timeout=15)
    validation = subprocess.run([validator, "--target-env", "vulkan1.2", str(binary)], check=True, capture_output=True, text=True, timeout=15)
    disassembled = subprocess.run([disassembler, str(binary)], check=True, capture_output=True, text=True, timeout=15).stdout
    (output / "validated.spvasm").write_text(disassembled)
    plan_bytes = canonical(example_plan())
    (output / "plan.json").write_bytes(plan_bytes)
    row_bytes = b"".join(struct.pack("<QQQII", row["low"], row["high"], row["base"], row["binding"], row["element"]) for row in example_plan()["rows"])
    (output / "record.bin").write_bytes(row_bytes)
    versions = {name: subprocess.run([tool, "--version"], check=True, capture_output=True, text=True, timeout=15).stdout.splitlines()[0]
                for name, tool in (("glslang", glslang), ("assembler", assembler), ("disassembler", disassembler), ("validator", validator))}
    report = {"schema": 1, "assemble_validate_only": True, "vulkan_device_opened": False,
              "pipeline_compiled": False, "gpu_dispatched": False, "versions": versions,
              "tool_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
              "glsl_sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
              "assembly_sha256": hashlib.sha256(assembly.read_bytes()).hexdigest(),
              "spirv_sha256": hashlib.sha256(binary.read_bytes()).hexdigest(),
              "plan_sha256": hashlib.sha256(plan_bytes).hexdigest(),
              "record_sha256": hashlib.sha256(row_bytes).hexdigest(),
              "capabilities": re.findall(r'OpCapability (\S+)', disassembled),
              "storage_buffer_pointer_return_functions": len(re.findall(r'= OpFunction %_ptr_StorageBuffer_\S+ DontInline', disassembled)),
              "nonuniform_decorations": len(re.findall(r'OpDecorate %\S+ NonUniform', disassembled)),
              "typed_array_length_instructions": disassembled.count("OpArrayLength"),
              "validator_stdout": validation.stdout, "validator_stderr": validation.stderr}
    (output / "report.json").write_bytes(canonical(report))
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    try:
        print(json.dumps(validate_only(args.output), indent=2))
    except (OSError, ValueError, subprocess.SubprocessError) as error:
        print(str(error))
        if isinstance(error, subprocess.CalledProcessError):
            print(error.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
