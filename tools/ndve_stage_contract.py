#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""CPU-only resource and software-matrix contract for the exact NDVE export."""
import argparse
from collections import Counter, defaultdict
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
if __package__ in (None, ""):
    sys.path.insert(0, str(ROOT))
from tools.compile_probe import reflect
from tools.mesh_output_instrument import parse
from tools.spirv_contract import analyze_assembly
from tools.ndve_stage_export import PROGRAM, NATIVE_SHA, file_sha

MODULE_SHA = "169b30754016737e4b49bb633dc2e2656b62e6f40fbdccbd907a538393f28338"


def inspect_module(binary, assembly):
    reflected = reflect(binary)
    resources = analyze_assembly(assembly)
    items, definitions, functions, _ = parse(assembly)
    entry = resources["entry"]["function"]
    calls = Counter(item["args"][1] for item in functions[entry] if item["op"] == "OpFunctionCall")
    callees = []
    for callee, count in sorted(calls.items()):
        body = functions[callee]
        ops = Counter(item["op"] for item in body)
        ext = Counter(item["args"][2] for item in body if item["op"] == "OpExtInst")
        callees.append({"callee": callee, "static_main_call_sites": count, "instruction_counts": dict(ops),
                        "extended_instruction_counts": dict(ext)})
    decorations, offsets, constants = defaultdict(dict), {}, {}
    for item in items:
        args = item["args"]
        if item["op"] == "OpDecorate" and len(args) == 3:
            decorations[args[0]][args[1]] = args[2]
        elif item["op"] == "OpMemberDecorate" and len(args) == 4 and args[2] == "Offset":
            offsets[(args[0], int(args[1]))] = int(args[3])
        elif item["op"] == "OpConstant":
            try:
                constants[item["result"]] = int(args[-1])
            except ValueError:
                pass
    def size(identity):
        item = definitions[identity]
        args, op = item["args"], item["op"]
        if op in ("OpTypeInt", "OpTypeFloat"):
            return int(args[0]) // 8
        if op == "OpTypeVector":
            return size(args[0]) * int(args[1])
        if op == "OpTypeArray":
            return int(decorations[identity].get("ArrayStride", size(args[0]))) * constants[args[1]]
        if op == "OpTypeRuntimeArray":
            return 0
        if op == "OpTypeStruct":
            return max((offsets[(identity, index)] + size(member) for index, member in enumerate(args)), default=0)
        raise ValueError("Unsupported declared layout type")
    layouts, push = [], []
    for item in items:
        if item["op"] != "OpVariable":
            continue
        pointer, storage = item["args"][:2]
        pointee = definitions[pointer]["args"][1]
        if storage == "PushConstant":
            members = definitions[pointee]["args"]
            push = [{"member": index, "offset": offsets[(pointee, index)], "declared_bytes": size(member)}
                    for index, member in enumerate(members)]
        if storage not in ("Uniform", "StorageBuffer", "UniformConstant"):
            continue
        annotation = decorations[item["result"]]
        if "Binding" not in annotation or "DescriptorSet" not in annotation:
            continue
        row = {"set": int(annotation["DescriptorSet"]), "binding": int(annotation["Binding"]),
               "storage": storage, "variable": item["result"]}
        while definitions[pointee]["op"] == "OpTypeArray":
            pointee = definitions[pointee]["args"][0]
        shape = definitions[pointee]
        if shape["op"] == "OpTypeStruct":
            row["declared_block_bytes_before_runtime_tail"] = size(pointee)
            row["members"] = []
            for index, member in enumerate(shape["args"]):
                value = definitions[member]
                stride = decorations[member].get("ArrayStride")
                row["members"].append({"offset": offsets[(pointee, index)], "type": value["op"],
                                       "array_stride": int(stride) if stride is not None else None})
        else:
            image = definitions[shape["args"][0]] if shape["op"] == "OpTypeSampledImage" else shape
            if image["op"] != "OpTypeImage" or len(image["args"]) < 7:
                raise ValueError("Unsupported exact-stage image declaration")
            scalar, dimension, depth, arrayed, multisampled, sampled, image_format = image["args"][:7]
            row["image_type"] = {"scalar_type": definitions[scalar]["op"],
                                 "scalar_bits": int(definitions[scalar]["args"][0]),
                                 "dimension": dimension, "depth": int(depth), "arrayed": int(arrayed),
                                 "multisampled": int(multisampled), "sampled": int(sampled), "format": image_format}
        layouts.append(row)
    return {"reflection": reflected, "resource_summary": resources, "main_callees": callees,
            "declared_descriptor_layouts": layouts, "push_members": push}


def audit(exported, output):
    exported, output = Path(exported).resolve(), Path(output).resolve()
    if output.exists() or (output.is_relative_to(ROOT) and output.relative_to(ROOT).parts[:1] not in (("local",), ("work",), ("artifacts",))):
        raise ValueError("Choose a fresh private contract output")
    report_path = exported / "export-report.json"
    report = json.loads(report_path.read_text())
    if report.get("identity", {}).get("program") != PROGRAM or report["identity"].get("native_control_code_sha256") != NATIVE_SHA:
        raise ValueError("Not the exact known NDVE stage export")
    if report.get("wrapper_sha256") != file_sha(ROOT / "tools/ndve_stage_export.py") or report.get("conditional_on_offline_query_configuration") is not True:
        raise ValueError("Export source/configuration differs from reviewed conditional exporter")
    query_path = exported / "environment-queries.json"
    queries = json.loads(query_path.read_text())
    if file_sha(query_path) != report.get("environment_queries_sha256") or [row for row in queries if row["resolved"] == 0] != report.get("unresolved_compiler_queries"):
        raise ValueError("Environment query audit differs from export")
    module = exported / "translated/spv" / (PROGRAM + ".5.spv")
    if module.is_symlink() or not module.is_file() or module.stat().st_size > 1024**2:
        raise ValueError("Expected bounded regular module")
    data = module.read_bytes()
    if hashlib.sha256(data).hexdigest() != MODULE_SHA or report.get("module_sha256") != MODULE_SHA:
        raise ValueError("Conditional module digest differs from reviewed export")
    with tempfile.TemporaryDirectory(prefix="ndve-contract-") as directory:
        snapshot = Path(directory) / "module.spv"
        snapshot.write_bytes(data)
        assembly = subprocess.run(["spirv-dis", "--raw-id", str(snapshot)], capture_output=True, text=True, check=True, timeout=30).stdout
    contract = inspect_module(data, assembly)
    candidates = [row for row in contract["main_callees"] if row["static_main_call_sites"] == 72 and
                  row["extended_instruction_counts"].get("Fma") == 8 and row["instruction_counts"].get("OpLoopMerge") == 1]
    if len(candidates) != 1 or contract["reflection"]["workgroup"] != [32, 1, 1]:
        raise ValueError("Software matrix/helper work was not retained")
    contract.update(schema="open-ocarina.ndve-stage-conditional-contract/v1", CPU_only=True, Vulkan_calls=False,
                    module_sha256=MODULE_SHA, export_report_sha256=file_sha(report_path),
                    environment_queries_sha256=report["environment_queries_sha256"],
                    compiler_queries=report["unresolved_compiler_queries"],
                    matrix_function=candidates[0], tool_sha256=file_sha(Path(__file__)),
                    audit_sources={name: file_sha(ROOT / name) for name in
                                   ("tools/compile_probe.py", "tools/mesh_output_instrument.py", "tools/spirv_contract.py")},
                    live_compiler_variant_equality=False, complete_replay_inputs=False,
                    original_console_arithmetic_independently_certified=False,
                    scope="Conditional actual stage translation with explicit unresolved compiler answers; not a live variant or executed output reference")
    output.mkdir(parents=True)
    (output / "contract.json").write_text(json.dumps(contract, indent=2) + "\n")
    return contract


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--exported", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(audit(args.exported, args.output), indent=2))


if __name__ == "__main__":
    main()
