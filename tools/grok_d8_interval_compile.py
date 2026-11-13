#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""CPU preparation for a paired D8 compile under one 33-element descriptor set.

MaxPushDescriptors counts descriptor elements, not binding numbers. The original
union is 30 bindings and 32 elements. The candidate adds one storage element and
is 31 bindings and 33 elements. A 33-element push layout is rejected against the
prior A770 limit of 32. This tool builds a sets-only helper and does not run it.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shlex
import struct
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
HELPER_SOURCE = ROOT / "src" / "probes" / "grok_d8_interval_compile.c"
LOWERING_TOOL = ROOT / "tools" / "grok-d8-interval.py"
BUILD_FLAGS = ["-std=c11", "-O2", "-DNDEBUG", "-Wall", "-Wextra", "-Werror"]
KNOWN_PRIOR_MAX_PUSH_DESCRIPTORS = 32
COMMON_BINDINGS = 31
COMMON_ELEMENTS = 33
PUSH_BYTES = 88
MESH_STAGE = 128
FRAGMENT_STAGE = 16
RECORD_BINDING = 30
ENTRY_FUNCTION = 851
PRESERVED_ENTRY_LOADS = {5: 4, 6: 5, 7: 12}
UNTOUCHED_FUNCTIONS = (277, 390, 507, 738, 851)
REWRITTEN_FUNCTIONS = (149, 620)
PINS = {
    "original_mesh_sha256": "835aa59799c6ecb621034d946e2fa4eadb1aa2407d293321ae3e3052882bc9b3",
    "candidate_mesh_sha256": "c2ae946e5b4706d220e3adb940ee00e4b382f326ca4f63586d043b2990650129",
    "fragment_sha256": "569b378a012388d711a115030bd1f41af66d38aebb645118baacd1e90a8f5a3a",
    "records_sha256": "40c0e1d71a67998d9da47b9aec592616d3e9cb7de1ad512887835f5b087cd09a",
    "lowering_tool_sha256": "f21f84024417ab2896202857c6493481ef5a9b99a84efaae12c6a75ff5044650",
}
DIAGNOSTIC_STATE = {
    "color_format": 37, "depth_format": 126, "samples": 1, "extent": [1536, 864],
    "depth_test": True, "depth_write": True, "depth_compare_op": 3, "cull_mode": 0,
    "front_face": 0, "alpha_to_coverage": False, "color_write_mask": 15,
}
LIMIT_THRESHOLDS = {
    "maxPerStageDescriptorUniformBuffers": 9,
    "maxPerStageDescriptorStorageBuffers": 7,
    "maxPerStageDescriptorSamplers": 10,
    "maxPerStageDescriptorSampledImages": 10,
    "maxDescriptorSetUniformBuffers": 14,
    "maxDescriptorSetStorageBuffers": 9,
    "maxDescriptorSetSamplers": 10,
    "maxDescriptorSetSampledImages": 10,
    "maxPerStageResources": 21,
    "maxBoundDescriptorSets": 1,
    "maxPushConstantsSize": PUSH_BYTES,
}
ELEMENT_TOTALS = {"uniform": 14, "storage": 9, "combined_image_sampler": 10}
STAGE_ELEMENT_TOTALS = {
    "mesh": {"uniform": 5, "storage": 7, "combined_image_sampler": 0},
    "fragment": {"uniform": 9, "storage": 2, "combined_image_sampler": 10},
}
FEATURES = [
    "robustBufferAccess", "robustBufferAccess2", "nullDescriptor", "shaderInt16", "shaderInt64",
    "shaderStorageBufferArrayDynamicIndexing", "shaderImageGatherExtended", "fragmentStoresAndAtomics",
    "uniformBufferStandardLayout", "bufferDeviceAddress", "shaderFloat16", "shaderInt8",
    "subgroupSizeControl", "dynamicRendering", "meshShader",
]
FORBIDDEN_HELPER_APIS = (
    "vkGetDeviceQueue", "vkCmd", "vkQueueSubmit", "vkCreateBuffer", "vkAllocateMemory", "vkCreateImage",
    "vkAllocateDescriptorSets", "vkUpdateDescriptorSets", "vkCreateCommandPool", "vkCmdDraw", "vkDrawMeshTasks",
)
DRIVER_CLEARS = (
    "HS_CS32_MAX_INSTS", "HS_SPILL_BATCH", "HS_PUSH_SHADOW", "shader_spilling_rate", "INTEL_DEBUG",
    "HS_SPILL_PCT", "HS_RA_DEBUG", "HS_FORCE_SIMD8", "HS_LARGE_GRF", "HS_SUBGROUP8", "HS_LARGE_GRF_BIT",
    "RX_VK_WARP64", "RX_VK_NOOPT",
)
NATIVE_SAFE = {"HS_SPILL_BATCH": "1", "HS_PUSH_SHADOW": "1", "shader_spilling_rate": "1"}
TYPE_NAMES = {6: "uniform", 7: "storage", 1: "combined_image_sampler"}


def need(value, message):
    if not value:
        raise ValueError(message)


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def words_of(data):
    need(len(data) >= 20 and len(data) % 4 == 0, "Invalid SPIR-V length")
    words = struct.unpack(f"<{len(data) // 4}I", data)
    need(words[0] == 0x07230203, "Invalid SPIR-V magic")
    return words


def instructions(words):
    offset = 5
    while offset < len(words):
        count = words[offset] >> 16
        need(count and offset + count <= len(words), "Malformed SPIR-V instruction")
        yield words[offset:offset + count]
        offset += count


def descriptor_elements(bindings):
    """Return the sum of descriptor counts. Binding numbers are not the push limit."""
    return sum(row["count"] for row in bindings)


def push_layout_allowed(bindings, limit=KNOWN_PRIOR_MAX_PUSH_DESCRIPTORS):
    return descriptor_elements(bindings) <= limit


def reject_push_layout(bindings, limit=KNOWN_PRIOR_MAX_PUSH_DESCRIPTORS):
    elements = descriptor_elements(bindings)
    need(not push_layout_allowed(bindings, limit), "This common layout is expected to exceed the known push limit")
    raise ValueError(
        f"Refusing push layout: {elements} descriptor elements exceed known maxPushDescriptors {limit}; "
        f"binding count {len(bindings)} is not the limit")


def function_bodies(data):
    bodies, current, ident = {}, [], None
    for inst in instructions(words_of(data)):
        opcode = inst[0] & 0xFFFF
        if opcode == 54:
            need(ident is None, "Nested function")
            ident = inst[2]
            current = [tuple(inst)]
        elif ident is not None:
            current.append(tuple(inst))
            if opcode == 56:
                bodies[ident] = tuple(current)
                ident = None
    need(ident is None, "Unclosed function")
    return bodies


def binding_variables(words):
    binding, descriptor_set, nonwritable = {}, {}, set()
    for inst in instructions(words):
        if (inst[0] & 0xFFFF) != 71 or len(inst) < 3:
            continue
        if inst[2] == 33 and len(inst) >= 4:
            binding[inst[1]] = inst[3]
        elif inst[2] == 34 and len(inst) >= 4:
            descriptor_set[inst[1]] = inst[3]
        elif inst[2] == 24:
            nonwritable.add(inst[1])
    return binding, descriptor_set, nonwritable


def classify_entry_loads(data):
    """Count preserved direct loads in mesh entry 851. This is not a whole-shader rewrite claim."""
    parsed = words_of(data)
    binding, descriptor_set, _nonwritable = binding_variables(parsed)
    bodies = function_bodies(data)
    need(ENTRY_FUNCTION in bodies, "Mesh entry function 851 is absent")
    produced = {}
    counts = {5: 0, 6: 0, 7: 0}
    for inst in bodies[ENTRY_FUNCTION]:
        opcode = inst[0] & 0xFFFF
        if opcode in (65, 66, 67, 83) and len(inst) > 3:
            produced[inst[2]] = inst
        if opcode != 61 or len(inst) < 4:
            continue
        root = inst[3]
        seen = set()
        chain = None
        while root not in seen:
            seen.add(root)
            source = produced.get(root)
            if source is None:
                break
            source_opcode = source[0] & 0xFFFF
            if source_opcode in (65, 66, 67, 83) and len(source) > 3:
                if source_opcode in (65, 66):
                    chain = source
                root = source[3]
            else:
                break
        number = binding.get(root)
        if number not in counts:
            continue
        need(descriptor_set.get(root) == 0, "Entry load is not set 0")
        need(chain is not None and (chain[0] & 0xFFFF) == 65 and len(chain) - 4 == 2, "Entry load is not a two-index access chain")
        counts[number] += 1
    need(counts == PRESERVED_ENTRY_LOADS, "Preserved entry load counts differ")
    return {
        "function": ENTRY_FUNCTION,
        "classification": "preserved_direct_entry_loads",
        "loads_by_binding": counts,
        "load_count": sum(counts.values()),
        "whole_shader_rewrite": False,
        "claim_cascade_proved": False,
        "rewritten_functions": list(REWRITTEN_FUNCTIONS),
    }


def record_binding(data):
    parsed = words_of(data)
    binding, descriptor_set, nonwritable = binding_variables(parsed)
    matches = [ident for ident, number in binding.items() if number == RECORD_BINDING and descriptor_set.get(ident) == 0]
    need(len(matches) == 1 and matches[0] in nonwritable, "Binding 30 is not one NonWritable set-0 variable")
    return {"set": 0, "binding": RECORD_BINDING, "nonwritable": True, "storage": "StorageBuffer"}


def reflect_graphics(data, stage):
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))
    return __import__("tools.graphics_compile_probe", fromlist=["reflect_stage"]).reflect_stage(data, stage)


def with_stage(rows, stage_flags):
    return [dict(row, stage_flags=stage_flags) for row in rows]


def union_bindings(mesh_rows, fragment_rows, record_row):
    bindings = {}
    for row in with_stage(mesh_rows, MESH_STAGE) + with_stage(fragment_rows, FRAGMENT_STAGE) + [record_row]:
        current = bindings.get(row["binding"])
        if current and (current["type"], current["count"], current["set"]) != (row["type"], row["count"], row["set"]):
            raise ValueError("Incompatible descriptor union")
        if current:
            current["stage_flags"] |= row["stage_flags"]
        else:
            bindings[row["binding"]] = dict(row)
    ordered = [bindings[key] for key in sorted(bindings)]
    record = bindings[RECORD_BINDING]
    need(record["stage_flags"] == MESH_STAGE and record["type"] == 7 and record["count"] == 1, "Binding 30 is not mesh-only storage")
    need(len(ordered) == COMMON_BINDINGS, "Common binding count is not 31")
    need(descriptor_elements(ordered) == COMMON_ELEMENTS, "Common descriptor-element sum is not 33")
    return ordered


def element_totals(bindings):
    totals = {"uniform": 0, "storage": 0, "combined_image_sampler": 0}
    per_stage = {"mesh": dict(totals), "fragment": dict(totals)}
    for row in bindings:
        name = TYPE_NAMES[row["type"]]
        totals[name] += row["count"]
        if row["stage_flags"] & MESH_STAGE:
            per_stage["mesh"][name] += row["count"]
        if row["stage_flags"] & FRAGMENT_STAGE:
            per_stage["fragment"][name] += row["count"]
    need(totals == ELEMENT_TOTALS and per_stage == STAGE_ELEMENT_TOTALS, "Descriptor class totals differ")
    return totals, per_stage


def layout_text(bindings):
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))
    probe = __import__("tools.graphics_compile_probe", fromlist=["layout_text"])
    config = {
        "push_constant_bytes": PUSH_BYTES,
        "descriptor_mode": "sets",
        "diagnostic_state": DIAGNOSTIC_STATE,
        "bindings": bindings,
        "stages": [
            {"stage_flags": MESH_STAGE, "required_subgroup_size": 32},
            {"stage_flags": FRAGMENT_STAGE, "required_subgroup_size": 0},
        ],
    }
    return probe.layout_text(config)


def preservation(original, candidate):
    old, new = function_bodies(original), function_bodies(candidate)
    need(set(old) == set(new), "Function ids differ")
    for ident in UNTOUCHED_FUNCTIONS:
        need(old[ident] == new[ident], f"Function {ident} changed")
    for ident in REWRITTEN_FUNCTIONS:
        need(old[ident][-3:] == new[ident][-3:], f"Function {ident} miss suffix changed")
        need(old[ident] != new[ident], f"Function {ident} was not rewritten")
    return {"untouched_functions": list(UNTOUCHED_FUNCTIONS), "rewritten_functions": list(REWRITTEN_FUNCTIONS), "miss_suffix_preserved": True}


def native_safe_environment():
    environment = {"HS_SPILL_BATCH": "1", "HS_PUSH_SHADOW": "1", "shader_spilling_rate": "1"}
    return {
        "name": "native-safe",
        "max_absent": "HS_CS32_MAX_INSTS" not in environment,
        "values": environment,
        "cleared": [key for key in DRIVER_CLEARS if key not in NATIVE_SAFE],
        "profile": "E5 safe 3; MAX absent",
    }


def runner_text(case, plan_sha):
    return f"""#!/bin/sh
# Parent GPU admission runner for one D8 compile. Preparation must not execute this file.
# No child resource limit is applied. Authorization is a separate parent step.
set -eu
if [ "${{GROK_D8_COMPILE_ADMIT:-}}" != "{plan_sha}" ]; then
  echo "GPU admission is not authorized by this preparation" >&2
  exit 2
fi
PLAN=$(CDPATH= cd -- "$(dirname "$0")/.." && pwd)
CACHE="$PLAN/fresh-cache-{case}"
if [ -e "$CACHE" ]; then
  echo "Fresh cache required; existing evidence is preserved" >&2
  exit 2
fi
if [ -z "${{GROK_D8_DRIVER_ICD:-}}" ]; then
  echo "An explicitly reviewed driver ICD is required" >&2
  exit 2
fi
mkdir -m 700 "$CACHE"
export MESA_SHADER_CACHE_DIR="$CACHE"
export HS_SPILL_BATCH=1
export HS_PUSH_SHADOW=1
export shader_spilling_rate=1
unset HS_CS32_MAX_INSTS INTEL_DEBUG HS_SPILL_PCT HS_RA_DEBUG HS_FORCE_SIMD8 HS_LARGE_GRF HS_SUBGROUP8 HS_LARGE_GRF_BIT RX_VK_WARP64 RX_VK_NOOPT
if [ -n "${{GROK_D8_DRIVER_ICD:-}}" ]; then
  export VK_DRIVER_FILES="$GROK_D8_DRIVER_ICD"
fi
timeout 180 "$PLAN/compile-helper" "$PLAN/layout.txt" "${{GROK_D8_DEVICE_INDEX:-0}}" "$PLAN/{case}.spv" "$PLAN/fragment.spv"
"""


def assert_helper_source():
    source = HELPER_SOURCE.read_text()
    for name in FORBIDDEN_HELPER_APIS:
        need(name not in source, "Helper source contains a forbidden API: " + name)
    need("vkCreateGraphicsPipelines" in source and "COMMON_ELEMENTS" in source, "Helper is missing its compile-only pipeline path")
    return hashlib.sha256(source.encode()).hexdigest()


def build_helper(output, cc):
    source_sha = assert_helper_source()
    flags = shlex.split(subprocess.run(["pkg-config", "--cflags", "--libs", "vulkan"], check=True, capture_output=True, text=True, timeout=15).stdout)
    executable = output / "compile-helper"
    subprocess.run([cc, *BUILD_FLAGS, str(HELPER_SOURCE), *flags, "-o", str(executable)], check=True, capture_output=True, text=True, timeout=60)
    symbols = subprocess.run(["nm", "-D", str(executable)], check=True, capture_output=True, text=True, timeout=15).stdout
    for name in FORBIDDEN_HELPER_APIS:
        need(name not in symbols, "Built helper imports a forbidden API: " + name)
    return {"helper_source_sha256": source_sha, "helper_sha256": digest(executable), "helper_build_flags": BUILD_FLAGS, "helper_executed": False}


def prepare(output, original, candidate, fragment, records, cc="cc"):
    output = Path(output).resolve()
    need(output.is_relative_to(ROOT / "local") and not output.exists(), "Choose a new output directory under local/")
    pins = dict(PINS)
    paths = {"original": Path(original), "candidate": Path(candidate), "fragment": Path(fragment), "records": Path(records)}
    blobs = {name: path.read_bytes() for name, path in paths.items()}
    for name, key in (("original", "original_mesh_sha256"), ("candidate", "candidate_mesh_sha256"), ("fragment", "fragment_sha256"), ("records", "records_sha256")):
        need(hashlib.sha256(blobs[name]).hexdigest() == pins[key], name + " digest differs from the pinned artifact")
    need(digest(LOWERING_TOOL) == pins["lowering_tool_sha256"], "Lowering tool digest differs")
    need(len(blobs["records"]) == 400, "Record is not the pinned 400-byte capture")
    original_mesh = reflect_graphics(blobs["original"], "mesh")
    candidate_mesh = reflect_graphics(blobs["candidate"], "mesh")
    fragment_stage = reflect_graphics(blobs["fragment"], "fragment")
    record = record_binding(blobs["candidate"])
    need(all(row["binding"] != RECORD_BINDING for row in original_mesh["bindings"]), "Original mesh already uses binding 30")
    record_row = dict(next(row for row in candidate_mesh["bindings"] if row["binding"] == RECORD_BINDING), stage_flags=MESH_STAGE)
    common = union_bindings(candidate_mesh["bindings"], fragment_stage["bindings"], record_row)
    try:
        reject_push_layout(common)
        raise AssertionError("push rejection must raise")
    except ValueError as error:
        need("33 descriptor elements" in str(error), "Push rejection did not cite the element sum")
    totals, per_stage = element_totals(common)
    original_union = union_bindings(original_mesh["bindings"], fragment_stage["bindings"], dict(record_row))
    need(original_union == common, "Original and candidate common layouts differ")
    loads = classify_entry_loads(blobs["original"])
    need(classify_entry_loads(blobs["candidate"]) == loads, "Candidate entry loads differ")
    kept = preservation(blobs["original"], blobs["candidate"])
    output.mkdir(parents=True)
    for name in ("original", "candidate", "fragment", "records"):
        suffix = ".bin" if name == "records" else ".spv"
        (output / f"{name}{suffix}").write_bytes(blobs[name])
    (output / "layout.txt").write_text(layout_text(common))
    helper = build_helper(output, cc)
    plan = {
        "schema": "open-ocarina.grok-d8-interval-compile/v1",
        "cpu_preparation": True,
        "helper_executed": False,
        "vulkan_opened": False,
        "pipeline_created": False,
        "execution_admitted": False,
        "live_certificate": False,
        "one_capture_record": True,
        "renderer_integration": False,
        "native_quality_or_real60_achieved": False,
        "descriptor_mode": "sets",
        "push_layout_rejected": True,
        "push_rejection_reason": "33 descriptor elements exceed the prior A770 maxPushDescriptors 32; the limit is the sum of counts, not the binding count 31",
        "known_prior_max_push_descriptors": KNOWN_PRIOR_MAX_PUSH_DESCRIPTORS,
        "descriptor_binding_count": COMMON_BINDINGS,
        "descriptor_element_count": COMMON_ELEMENTS,
        "binding_30": {"set": 0, "binding": 30, "type": 7, "count": 1, "stage_flags": MESH_STAGE, "nonwritable": True, "used_by_original_mesh": False, "mesh_only": True},
        "push_constant_bytes": PUSH_BYTES,
        "required_subgroup_size": {"mesh": 32, "fragment": 0},
        "require_full_subgroups": False,
        "diagnostic_state": DIAGNOSTIC_STATE,
        "features": FEATURES,
        "null_descriptor_required": True,
        "robustness_required": ["robustBufferAccess", "robustBufferAccess2"],
        "limit_thresholds": LIMIT_THRESHOLDS,
        "element_totals": totals,
        "stage_element_totals": per_stage,
        "driver_policy": native_safe_environment(),
        "pins": pins,
        "entry_loads": loads,
        "preservation": kept,
        "record_binding_declaration": record,
        "common_layout": common,
        "only_spirv_differs": ["original.spv", "candidate.spv"],
        "fragment_unchanged": pins["fragment_sha256"],
        "future_abi": "This common set is a diagnostic compile profile. A later renderer still needs a second-set, buffer-device-address, or other ABI if a benefit is measured.",
        "admission": "Separate parent GPU admission only. Runners require GROK_D8_COMPILE_ADMIT equal to plan_sha256. This preparation does not create that authorization.",
        **helper,
    }
    encoded = (json.dumps(plan, indent=2, sort_keys=True) + "\n").encode()
    plan_sha = hashlib.sha256(encoded).hexdigest()
    plan["plan_sha256"] = plan_sha
    encoded = (json.dumps(plan, indent=2, sort_keys=True) + "\n").encode()
    # The authorization token covers every field except the self-hash.
    body = dict(plan)
    body.pop("plan_sha256")
    plan_sha = hashlib.sha256((json.dumps(body, indent=2, sort_keys=True) + "\n").encode()).hexdigest()
    plan["plan_sha256"] = plan_sha
    (output / "plan.json").write_text(json.dumps(plan, indent=2, sort_keys=True) + "\n")
    runners = output / "runners"
    runners.mkdir()
    for case in ("original", "candidate"):
        path = runners / f"{case}.sh"
        path.write_text(runner_text(case, plan_sha))
        path.chmod(0o755)
    return plan


def check_plan(path):
    plan = json.loads(Path(path).read_text())
    body = dict(plan)
    plan_sha = body.pop("plan_sha256")
    need(hashlib.sha256((json.dumps(body, indent=2, sort_keys=True) + "\n").encode()).hexdigest() == plan_sha, "Plan hash differs")
    need(plan["descriptor_element_count"] == COMMON_ELEMENTS and plan["descriptor_binding_count"] == COMMON_BINDINGS, "Plan counts differ")
    need(plan["push_layout_rejected"] and plan["descriptor_mode"] == "sets", "Plan does not reject push")
    need(plan["helper_executed"] is False and plan["execution_admitted"] is False, "Plan admits execution")
    return plan


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    prepare_parser = sub.add_parser("prepare")
    prepare_parser.add_argument("--output", type=Path, required=True)
    prepare_parser.add_argument("--original", type=Path, required=True)
    prepare_parser.add_argument("--candidate", type=Path, required=True)
    prepare_parser.add_argument("--fragment", type=Path, required=True)
    prepare_parser.add_argument("--records", type=Path, required=True)
    prepare_parser.add_argument("--cc", default="cc")
    check_parser = sub.add_parser("check-plan")
    check_parser.add_argument("--plan", type=Path, required=True)
    args = parser.parse_args()
    if args.command == "prepare":
        plan = prepare(args.output, args.original, args.candidate, args.fragment, args.records, args.cc)
        print(json.dumps({"plan_sha256": plan["plan_sha256"], "helper_executed": False, "descriptor_element_count": 33}, indent=2))
    else:
        check_plan(args.plan)
        print("plan ok")


if __name__ == "__main__":
    main()
