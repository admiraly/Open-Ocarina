#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Synthetic StorageBuffer-pointer returns plus physical miss path; compile only."""
from __future__ import annotations

import argparse
from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import re
import shlex
import subprocess
import sys

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tools import compile_probe
from tools.interval_compile_experiment import retain_helpers, shader_source

ROOT = Path(__file__).resolve().parents[1]
CONTROLLED_DRIVER_OPTIONS = ("HS_CS32_MAX_INSTS", "HS_SPILL_BATCH", "HS_PUSH_SHADOW", "shader_spilling_rate",
                             "INTEL_DEBUG", "HS_SPILL_PCT", "HS_RA_DEBUG", "HS_FORCE_SIMD8", "HS_LARGE_GRF")


def typed_bound_bytes(byte_length, width):
    """Reject source U32-shift wrapping rather than quietly model a U64 bound."""
    if byte_length < 0 or width not in (4, 8, 16):
        raise ValueError("Invalid typed extent")
    extent = byte_length // width * width
    if extent > 0xFFFFFFFF:
        raise ValueError("U32 typed bound-size wrapping is outside this bounded oracle")
    return extent


def controlled_environment(ambient, requested):
    environment = dict(ambient)
    for key in CONTROLLED_DRIVER_OPTIONS:
        environment.pop(key, None)
    environment.update(compile_probe.parse_environment(requested))
    return environment, {key: environment.get(key) for key in CONTROLLED_DRIVER_OPTIONS}


@dataclass
class View:
    guest: int
    size_word: int
    payload: memoryview
    kind: str = "single"


def resolved_claims(views, width, alignment=16):
    """Bounded CPU model of typed OpArrayLength caps; not a native admission gate."""
    if width not in (4, 8, 16) or alignment <= 0 or alignment & (alignment - 1):
        raise ValueError("Unsupported synthetic access shape")
    rows = []
    order = [i for i, view in enumerate(views) if view.kind == "candidate"]
    order += [i for i, view in enumerate(views) if view.kind == "single"]
    order += [i for i, view in enumerate(views) if view.kind == "table"]
    if len(order) != len(views):
        raise ValueError("Unknown view kind")
    for owner in order:
        view = views[owner]
        if view.payload.ndim != 1 or view.payload.itemsize != 1:
            raise ValueError("Oracle payload must be a byte view")
        if not 0 <= view.guest <= 0xFFFFFFFFFFFFFFFF or not 0 <= view.size_word <= 0xFFFFFFFF:
            raise ValueError("Invalid synthetic guest range")
        base = view.guest & ~(alignment - 1)
        bound = base + typed_bound_bytes(len(view.payload), width)
        if max(bound, view.guest + view.size_word) > 0xFFFFFFFFFFFFFFFF:
            raise ValueError("Overflowing ranges are outside this bounded oracle")
        end = bound if view.kind == "candidate" or (view.kind == "table" and not view.size_word) else min(bound, view.guest + view.size_word)
        if base < end:
            rows.append((base, end, owner, view.kind == "candidate"))
    return rows


def select_view(views, address, width, interval=False):
    if not 0 <= address <= 0xFFFFFFFFFFFFFFFF:
        raise ValueError("Invalid synthetic query address")
    rows = resolved_claims(views, width)
    candidates = [row for row in rows if row[3] and row[0] <= address < row[1]]
    if candidates:
        selected = min(candidates, key=lambda row: address - row[0])
    else:
        singles = [row for row in rows if not row[3]]
        if interval:
            boundaries = sorted({value for row in singles for value in row[:2]})
            disjoint = []
            for low, high in zip(boundaries, boundaries[1:]):
                hit = next((row for row in singles if row[0] <= low < row[1]), None)
                if hit:
                    disjoint.append((low, high, hit))
            selected = next((row for low, high, row in disjoint if low <= address < high), None)
        else:
            selected = next((row for row in singles if row[0] <= address < row[1]), None)
    if selected is None:
        return None
    base, _, owner, _ = selected
    # Match source's UConvert(U32, addr-base), shift, and typed access chain.
    offset = ((address - base) & 0xFFFFFFFF) // width * width
    return owner, offset


def model_access(views, pages, address, width, *, interval=False, value=None, follow_stores=False):
    """Keep bound view identity/aliasing; use a one-window page miss model."""
    selected = select_view(views, address, width, interval)
    if selected is not None:
        owner, offset = selected
        data = views[owner].payload
    else:
        if value is not None and not follow_stores:
            return None
        page, offset = divmod(address, 4096)
        data = pages.get(page)
        if data is None or offset + width > 4096:
            return bytes(width) if value is None else None
    if offset + width > len(data):
        raise ValueError("Synthetic backing data does not satisfy its declared range")
    if value is None:
        return bytes(data[offset:offset + width])
    if len(value) != width:
        raise ValueError("Stored value has the wrong width")
    data[offset:offset + width] = value
    return None


def source(shape, count=5, table_entries=3, accesses=4):
    text = shader_source(shape, count, accesses, candidates=0, table_entries=table_entries)
    text = text.replace("#version 450\n", """#version 450
#extension GL_EXT_buffer_reference : require
#extension GL_EXT_shader_explicit_arithmetic_types_int64 : require
layout(buffer_reference,std430,buffer_reference_align=8) readonly buffer PageReference { uint64_t address; };
layout(buffer_reference,std430,buffer_reference_align=4) buffer ValueReference { uint value; };
layout(set=0,binding=4,std430) buffer PayloadA { uint words[]; } data_a;
layout(set=0,binding=5,std430) buffer PayloadB { uint words[]; } data_b;
""", 1)
    text = text.replace("uint salt; uint unused;", "uint salt; uint unused; uint64_t page_root; uint follow_stores; uint window_bytes;", 1)
    text = text[:text.index("void main()")]
    text += """uint fallback_load(uint address) {
    if(parameters.page_root==uint64_t(0) || address>=parameters.window_bytes || (address&4095u)>4092u) return 0u;
    uint64_t page=PageReference(parameters.page_root+uint64_t(address>>12)*uint64_t(8)).address;
    if(page==uint64_t(0)) return 0u;
    return ValueReference(page+uint64_t(address&4095u)).value;
}
void fallback_store(uint address,uint value) {
    if(parameters.follow_stores==0u || parameters.page_root==uint64_t(0) || address>=parameters.window_bytes || (address&4095u)>4092u) return;
    uint64_t page=PageReference(parameters.page_root+uint64_t(address>>12)*uint64_t(8)).address;
    if(page!=uint64_t(0)) ValueReference(page+uint64_t(address&4095u)).value=value;
}
uint global_load(uint address) {
    uvec2 location=lookup_id(address);
    if(location.x==0u) return data_a.words[location.y>>2];
    if(location.x==1u) return data_b.words[location.y>>2];
    return fallback_load(address);
}
void global_store(uint address,uint value) {
    uvec2 location=lookup_id(address);
    if(location.x==0u) data_a.words[location.y>>2]=value;
    else if(location.x==1u) data_b.words[location.y>>2]=value;
    else fallback_store(address,value);
}
void main() {
    uint invocation=gl_GlobalInvocationID.x;
    uint accumulator=parameters.salt;
"""
    for index in range(accesses):
        text += f"    {{ uint query=(queries[(invocation*{accesses}u+{index}u)&parameters.query_mask]^parameters.salt)&~3u; uint value=global_load(query); global_store(query,value^parameters.salt); accumulator=(accumulator*1664525u)^value; }}\n"
    return text + "    results[invocation]=uvec2(accumulator,invocation);\n}\n"


def _named_id(assembly, prefix):
    matches = re.findall(r'^\s*OpName\s+(%\S+)\s+"' + re.escape(prefix) + r'[^"\n]*"\s*$', assembly, re.MULTILINE)
    if len(matches) != 1:
        raise ValueError(f"Expected one synthetic {prefix} symbol")
    return matches[0]


def _type_id(assembly, declaration):
    match = re.search(r'^\s*(%\S+) = ' + declaration + r'\s*$', assembly, re.MULTILINE)
    if match is None:
        raise ValueError("Required synthetic pointer/type declaration is absent")
    return match[1]


def pointer_transform(assembly):
    """Use two static descriptor roots; never dynamically index a descriptor array."""
    uint = _type_id(assembly, 'OpTypeInt 32 0')
    boolean = _type_id(assembly, 'OpTypeBool')
    void = _type_id(assembly, 'OpTypeVoid')
    vector = _type_id(assembly, 'OpTypeVector ' + re.escape(uint) + ' 2')
    storage_ptr = _type_id(assembly, 'OpTypePointer StorageBuffer ' + re.escape(uint))
    function_ptr = _type_id(assembly, 'OpTypePointer Function ' + re.escape(uint))
    lookup = _named_id(assembly, "lookup_id(")
    load = _named_id(assembly, "global_load(")
    store = _named_id(assembly, "global_store(")
    fallback_load = _named_id(assembly, "fallback_load(")
    fallback_store = _named_id(assembly, "fallback_store(")
    data_a, data_b = _named_id(assembly, "data_a"), _named_id(assembly, "data_b")
    assembly = assembly.replace("OpCapability Shader", "OpCapability Shader\n               OpCapability VariablePointersStorageBuffer", 1)
    marker = re.search(r'^\s*%\S+ = OpFunction ', assembly, re.MULTILINE)
    if marker is None:
        raise ValueError("No synthetic functions")
    declarations = f"""
%oo_zero = OpConstant {uint} 0
%oo_one = OpConstant {uint} 1
%oo_two = OpConstant {uint} 2
%oo_missing = OpConstant {uint} 4294967295
%oo_null = OpConstantNull {storage_ptr}
%oo_make_type = OpTypeFunction {storage_ptr} {uint} {uint}
%oo_lookup_type = OpTypeFunction {storage_ptr} {function_ptr}
"""
    helper = f"""
%oo_make = OpFunction {storage_ptr} DontInline %oo_make_type
%oo_owner = OpFunctionParameter {uint}
%oo_index = OpFunctionParameter {uint}
%oo_make_entry = OpLabel
%oo_isa = OpIEqual {boolean} %oo_owner %oo_zero
OpSelectionMerge %oo_nota None
OpBranchConditional %oo_isa %oo_hita %oo_nota
%oo_hita = OpLabel
%oo_pa = OpAccessChain {storage_ptr} {data_a} %oo_zero %oo_index
OpReturnValue %oo_pa
%oo_nota = OpLabel
%oo_isb = OpIEqual {boolean} %oo_owner %oo_one
OpSelectionMerge %oo_notb None
OpBranchConditional %oo_isb %oo_hitb %oo_notb
%oo_hitb = OpLabel
%oo_pb = OpAccessChain {storage_ptr} {data_b} %oo_zero %oo_index
OpReturnValue %oo_pb
%oo_notb = OpLabel
OpReturnValue %oo_null
OpFunctionEnd
%oo_lookup = OpFunction {storage_ptr} DontInline %oo_lookup_type
%oo_address = OpFunctionParameter {function_ptr}
%oo_lookup_entry = OpLabel
%oo_location = OpFunctionCall {vector} {lookup} %oo_address
%oo_binding = OpCompositeExtract {uint} %oo_location 0
%oo_offset = OpCompositeExtract {uint} %oo_location 1
%oo_word = OpShiftRightLogical {uint} %oo_offset %oo_two
%oo_chosen = OpFunctionCall {storage_ptr} %oo_make %oo_binding %oo_word
OpReturnValue %oo_chosen
OpFunctionEnd
"""
    assembly = assembly[:marker.start()] + declarations + assembly[marker.start():] + helper
    for identity, tag, parameter_count in ((load, "load", 1), (store, "store", 2)):
        pattern = re.compile(r'^\s*' + re.escape(identity) + r' = OpFunction[^\n]*\n(?P<params>(?:\s*%\S+ = OpFunctionParameter[^\n]*\n)+).*?^\s*OpFunctionEnd\s*$', re.MULTILINE | re.DOTALL)
        match = pattern.search(assembly)
        if match is None:
            raise ValueError("Synthetic wrapper function not found")
        header = assembly[match.start():assembly.index("\n", match.start()) + 1]
        # A leading empty line is included by \s*: locate the actual function line.
        header = next(line for line in match[0].splitlines() if " = OpFunction " in line) + "\n"
        params = match["params"]
        names = re.findall(r'(%\S+) = OpFunctionParameter', params)
        if len(names) != parameter_count:
            raise ValueError("Unexpected synthetic wrapper parameters")
        address = names[0]
        prefix = f"%oo_{tag}_"
        body = f"""{header}{params}
{prefix}entry = OpLabel
{prefix}ptr = OpFunctionCall {storage_ptr} %oo_lookup {address}
{prefix}miss = OpPtrEqual {boolean} {prefix}ptr %oo_null
OpSelectionMerge {prefix}end None
OpBranchConditional {prefix}miss {prefix}fallback {prefix}hit
{prefix}hit = OpLabel
"""
        if tag == "load":
            body += f"{prefix}value = OpLoad {uint} {prefix}ptr\nOpReturnValue {prefix}value\n"
        else:
            body += f"{prefix}value = OpLoad {uint} {names[1]}\nOpStore {prefix}ptr {prefix}value\nOpReturn\n"
        body += f"{prefix}fallback = OpLabel\n"
        if tag == "load":
            body += f"{prefix}physical = OpFunctionCall {uint} {fallback_load} {address}\nOpReturnValue {prefix}physical\n"
        else:
            body += f"{prefix}physical = OpFunctionCall {void} {fallback_store} {address} {names[1]}\nOpReturn\n"
        body += f"{prefix}end = OpLabel\nOpUnreachable\nOpFunctionEnd\n"
        assembly = assembly[:match.start()] + "\n" + body + assembly[match.end():]
    # Removed GLSL wrapper locals still have debug names. They are not semantic
    # interface decorations and must not leave undefined IDs after replacement.
    return "\n".join(line for line in assembly.splitlines()
                      if not re.match(r"\s*Op(?:Member)?Name\s", line)) + "\n"


def adapted_helper(original):
    """Private exact-anchor adaptation, leaving the shared helper source unchanged."""
    replacements = [
        ("VkPhysicalDeviceFeatures2 supported = {.sType = VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_FEATURES_2, .pNext = &supported12};",
         "VkPhysicalDeviceVulkan11Features supported11 = {.sType = VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_VULKAN_1_1_FEATURES, .pNext = &supported12};\n    VkPhysicalDeviceFeatures2 supported = {.sType = VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_FEATURES_2, .pNext = &supported11};"),
        ("if (!push_extension || total_descriptors", "if (!supported11.variablePointersStorageBuffer || !push_extension || total_descriptors"),
        ("VkPhysicalDeviceFeatures2 enabled = {.sType = VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_FEATURES_2, .pNext = &enabled12,",
         "VkPhysicalDeviceVulkan11Features enabled11 = {.sType = VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_VULKAN_1_1_FEATURES, .pNext = &enabled12, .variablePointersStorageBuffer = VK_TRUE};\n    VkPhysicalDeviceFeatures2 enabled = {.sType = VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_FEATURES_2, .pNext = &enabled11,"),
    ]
    for old, new in replacements:
        if original.count(old) != 1:
            raise ValueError("Frozen helper source changed; review the private feature adaptation")
        original = original.replace(old, new, 1)
    return original


def run(args):
    if not 1 <= args.timeout <= 120:
        raise ValueError("Each synthetic compilation timeout must be from1to120seconds")
    output = compile_probe.private_output(args.output)
    output.mkdir(parents=True)
    helper_text = (ROOT / "tools/vulkan_compile_probe.c").read_text()
    modified = adapted_helper(helper_text)
    helper_source = output / "compile-variable-pointers.c"
    helper_source.write_text(modified)
    executable = output / "compile-helper"
    flags = shlex.split(subprocess.run(["pkg-config", "--cflags", "--libs", "vulkan"], check=True, capture_output=True, text=True, timeout=15).stdout)
    subprocess.run([args.cc, "-std=c11", "-O2", "-DNDEBUG", "-Wall", "-Wextra", "-Werror", str(helper_source), *flags, "-o", str(executable)], check=True, capture_output=True, text=True, timeout=60)
    driver = compile_probe.driver_identity(args.driver, args.mesa_build)
    environment, controlled_options = controlled_environment(os.environ, args.env)
    versions = {}
    for name, executable_name in (("glslang", args.glslang), ("assembler", args.assembler),
                                  ("validator", args.validator), ("host_compiler", args.cc)):
        versions[name] = subprocess.run([executable_name, "--version"], capture_output=True,
                                        text=True, check=True, timeout=15).stdout.splitlines()[0]
    report = {"schema": 1, "compile_only": True, "dispatch_executed": False,
              "versions": versions, "helper_build_flags": ["-O2", "-DNDEBUG", "-Wall", "-Wextra", "-Werror"],
              "scope": "alignedU32 synthetic views; no game shader, resource replay or native semantics proof",
              "helper_original_sha256": hashlib.sha256(helper_text.encode()).hexdigest(),
              "helper_adapted_sha256": hashlib.sha256(modified.encode()).hexdigest(),
              "variable_pointers_storage_buffer_requested": True, "driver": driver, "cases": [],
              "controlled_driver_options": controlled_options,
              "limitations": ["No dispatch, output comparison or GPU execution timing.",
                              "Only two static StorageBuffer roots; no descriptor-array nonuniform indexing.",
                              "Physical fallback models one32-bit window; original guest windows and full64-bit arithmetic not integrated.",
                              "Wide access and candidate rules are CPU-model tests only; compiled helper is alignedU32."]}
    for variant in ("cascade-value", "interval-value", "interval-pointer"):
        stem = output / variant
        glsl = stem.with_suffix(".comp")
        binary = stem.with_suffix(".spv")
        glsl.write_text(source("cascade" if variant.startswith("cascade") else "interval", args.singles, args.table_entries, args.accesses))
        subprocess.run([args.glslang, "-V", "--target-env", "vulkan1.2", "-S", "comp", str(glsl), "-o", str(binary)], check=True, capture_output=True, text=True, timeout=30)
        if variant.endswith("pointer"):
            disassembled = subprocess.run([args.disassembler, str(binary)], check=True, capture_output=True, text=True, timeout=15).stdout
            assembly = stem.with_suffix(".spvasm")
            assembly.write_text(pointer_transform(disassembled))
            subprocess.run([args.assembler, "--target-env", "vulkan1.2", str(assembly), "-o", str(binary)], check=True, capture_output=True, text=True, timeout=15)
        retained, _ = retain_helpers(binary.read_bytes())
        binary.write_bytes(retained)
        subprocess.run([args.validator, "--target-env", "vulkan1.3", "--uniform-buffer-standard-layout", str(binary)], check=True, capture_output=True, text=True, timeout=15)
        reflection = compile_probe.reflect(retained)
        configuration = stem.with_suffix(".layout")
        configuration.write_text(f"32 {len(reflection['bindings'])}\n" + "".join(f"{row['set']} {row['binding']} {row['type']} {row['count']}\n" for row in reflection["bindings"]))
        env = environment.copy()
        env["VK_DRIVER_FILES"] = str(args.driver.resolve())
        env["MESA_SHADER_CACHE_DIR"] = str(output / (variant + "-cache"))
        result = compile_probe.run_child([str(executable), str(binary), str(configuration), "pinned32", "0"], env, stem.with_suffix(".json"), stem.with_suffix(".stderr"), args.timeout)
        report["cases"].append({"variant": variant, "spirv_sha256": hashlib.sha256(retained).hexdigest(), "spirv_words": len(retained)//4, "reflection": reflection, "result": result})
        (output / "report.json").write_text(json.dumps(report, indent=2) + "\n")
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--driver", type=Path, required=True)
    parser.add_argument("--mesa-build", type=Path)
    parser.add_argument("--singles", type=int, choices=(4,5,8,16), default=5)
    parser.add_argument("--table-entries", type=int, default=3)
    parser.add_argument("--accesses", type=int, default=4)
    parser.add_argument("--timeout", type=int, default=90)
    parser.add_argument("--cc", default="cc")
    parser.add_argument("--glslang", default="glslangValidator")
    parser.add_argument("--assembler", default="spirv-as")
    parser.add_argument("--disassembler", default="spirv-dis")
    parser.add_argument("--validator", default="spirv-val")
    parser.add_argument("--env", action="append", default=[])
    args = parser.parse_args()
    try:
        print(json.dumps(run(args), indent=2))
    except (OSError, ValueError, subprocess.SubprocessError, TypeError) as exc:
        print(str(exc), file=sys.stderr)
        if isinstance(exc, subprocess.CalledProcessError):
            print(exc.stderr, file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
