#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Compile synthetic lookup-ID helpers; never select descriptors or execute shaders."""
from __future__ import annotations

import argparse
from bisect import bisect_right
from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
import struct
import subprocess
import sys

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tools import compile_probe


@dataclass(frozen=True)
class Claim:
    base: int
    end: int
    owner: int
    candidate: bool = False


def validate_claims(claims):
    if len(claims) > 128:
        raise ValueError("At most128 synthetic claims")
    for claim in claims:
        if not 0 <= claim.base <= claim.end <= 0xFFFFFFFF or not 0 <= claim.owner < 0xFFFFFFFF:
            raise ValueError("Invalid bounded32-bit synthetic claim")


def legacy_select(claims, address):
    """Return the chosen logical binding and original-relative offset, not bytes."""
    validate_claims(claims)
    candidates = [claim for claim in claims if claim.candidate and claim.base <= address < claim.end]
    if candidates:
        chosen = min(candidates, key=lambda claim: address - claim.base)
        return chosen.owner, address - chosen.base
    for claim in claims:
        if not claim.candidate and claim.base <= address < claim.end:
            return claim.owner, address - claim.base
    return None


def flatten_intervals(claims):
    """Sorted disjoint first-match intervals preserve the original binding base."""
    validate_claims(claims)
    singles = [claim for claim in claims if not claim.candidate and claim.base < claim.end]
    boundaries = sorted({boundary for claim in singles for boundary in (claim.base, claim.end)})
    result = []
    for low, high in zip(boundaries, boundaries[1:]):
        hit = next((claim for claim in singles if claim.base <= low < claim.end), None)
        if hit is None:
            continue
        if result and result[-1][1] == low and result[-1][2:] == (hit.owner, hit.base):
            result[-1] = (result[-1][0], high, hit.owner, hit.base)
        else:
            result.append((low, high, hit.owner, hit.base))
    return result


def interval_select(claims, intervals, address):
    """Prototype lookup semantics; caller constructs intervals from these claims."""
    validate_claims(claims)
    candidates = [claim for claim in claims if claim.candidate and claim.base <= address < claim.end]
    if candidates:
        chosen = min(candidates, key=lambda claim: address - claim.base)
        return chosen.owner, address - chosen.base
    index = bisect_right([row[0] for row in intervals], address) - 1
    if index >= 0:
        low, high, owner, original_base = intervals[index]
        if low <= address < high:
            return owner, address - original_base
    return None


def shader_source(kind, count, accesses=8, candidates=2, table_entries=0):
    if kind not in ("cascade", "interval") or count not in (4, 5, 8, 16, 32, 64):
        raise ValueError("Unsupported bounded lookup shape/single count")
    if not 1 <= accesses <= 16 or not 0 <= candidates <= 4 or not 0 <= table_entries <= 16:
        raise ValueError("Synthetic accesses/candidate count exceeds bounded experiment")
    header = """#version 450
layout(local_size_x=64,local_size_y=1,local_size_z=1) in;
layout(set=0,binding=0,std430) readonly buffer Claims { uvec4 claims[]; };
layout(set=0,binding=1,std430) readonly buffer Intervals { uvec4 intervals[]; };
layout(set=0,binding=2,std430) readonly buffer Queries { uint queries[]; };
layout(set=0,binding=3,std430) writeonly buffer Results { uvec2 results[]; };
layout(push_constant) uniform Parameters { uint interval_count; uint query_mask; uint salt; uint unused; } parameters;
uvec2 lookup_id(uint query) {
    uvec2 chosen=uvec2(0xffffffffu,0u);
    uint nearest=0xffffffffu;
"""
    lines = [header]
    for index in range(candidates):
        lines.append(f"    {{ uvec4 c=claims[{index}]; if(query>=c.x && query<c.y && query-c.x<nearest) {{ nearest=query-c.x; chosen=uvec2(c.z,query-c.w); }} }}\n")
    lines.append("    if(chosen.x!=0xffffffffu) return chosen;\n")
    if kind == "cascade":
        for index in range(candidates, candidates + count):
            lines.append(f"    {{ uvec4 c=claims[{index}]; if(query>=c.x && query<c.y) return uvec2(c.z,query-c.w); }}\n")
        if table_entries:
            lines.append(f"    for(uint i=0u;i<{table_entries}u;++i) {{ uvec4 c=claims[{candidates+count}u+i]; if(query>=c.x && query<c.y) return uvec2(c.z,query-c.w); }}\n")
    else:
        lines.append(f"""    uint low=0u;
    uint high=min(parameters.interval_count,{2 * (count + table_entries) - 1}u);
    while(low<high) {{
        uint middle=low+(high-low)/2u;
        uvec4 c=intervals[middle];
        if(query<c.x) high=middle;
        else if(query>=c.y) low=middle+1u;
        else return uvec2(c.z,query-c.w);
    }}
""")
    lines.append("    return uvec2(0xffffffffu,0u);\n}\nvoid main() {\n    uint invocation=gl_GlobalInvocationID.x;\n    uvec2 accumulator=uvec2(parameters.salt,invocation);\n")
    for index in range(accesses):
        lines.append(f"    accumulator=(accumulator*1664525u)^lookup_id(queries[(invocation*{accesses}u+{index}u)&parameters.query_mask]^parameters.salt);\n")
    lines.append("    results[invocation]=accumulator;\n}\n")
    return "".join(lines)


def retain_helpers(data):
    """Mark non-entry functions DontInline, matching the investigated helper hint."""
    if len(data) < 20 or len(data) % 4:
        raise ValueError("Invalid synthetic SPIR-V length")
    words = list(struct.unpack(f"<{len(data)//4}I", data))
    if len(words) < 5 or words[0] != 0x07230203:
        raise ValueError("Invalid synthetic SPIR-V")
    entry_ids, functions = set(), []
    cursor = 5
    while cursor < len(words):
        length, opcode = words[cursor] >> 16, words[cursor] & 0xFFFF
        if not length or cursor + length > len(words):
            raise ValueError("Malformed synthetic SPIR-V")
        if opcode == 15:
            entry_ids.add(words[cursor + 2])
        elif opcode == 246:
            if length != 4:
                raise ValueError("Unexpected parameterized loop control in synthetic module")
            words[cursor + 3] = (words[cursor + 3] & ~1) | 2
        elif opcode == 54:
            if length != 5:
                raise ValueError("Malformed function")
            functions.append((cursor, words[cursor + 2]))
        cursor += length
    changed = 0
    for position, identity in functions:
        if identity not in entry_ids:
            words[position + 3] = (words[position + 3] & ~1) | 2
            changed += 1
    if not changed:
        raise ValueError("Frontend eliminated all helpers; shape experiment would be invalid")
    return struct.pack(f"<{len(words)}I", *words), changed


def structure(data):
    """Metadata only: inspect the generated module's helper and loop controls."""
    if len(data) < 20 or len(data) % 4:
        raise ValueError("Invalid synthetic SPIR-V length")
    words = struct.unpack(f"<{len(data)//4}I", data)
    cursor = 5
    loops, selections, calls, helpers = 0, 0, 0, 0
    while cursor < len(words):
        length, opcode = words[cursor] >> 16, words[cursor] & 0xFFFF
        if not length or cursor + length > len(words):
            raise ValueError("Malformed synthetic SPIR-V")
        if opcode == 246:
            if length < 4 or not words[cursor + 3] & 2:
                raise ValueError("Synthetic binary-search loop lost DontUnroll")
            loops += 1
        elif opcode == 247:
            selections += 1
        elif opcode == 57:
            calls += 1
        elif opcode == 54 and length == 5 and words[cursor + 3] & 2:
            helpers += 1
        cursor += length
    return {"loop_merges": loops, "selection_merges": selections,
            "function_calls": calls, "dontinline_helpers": helpers}


def run(args):
    output = compile_probe.private_output(args.output)
    output.mkdir(parents=True)
    version = subprocess.run([args.glslang, "--version"], capture_output=True, text=True,
                             check=True, timeout=15).stdout.splitlines()[0]
    report = {"schema": 1, "scope": "synthetic lookup-identifier component only", "dispatch_executed": False,
              "frontend": version, "frontend_target": "vulkan1.2", "helper_hint": "DontInline; binary-search DontUnroll",
              "accesses_per_invocation": args.accesses, "candidate_claims": args.candidates,
              "looped_table_entries": args.table_entries, "cases": [],
              "limitations": ["No descriptor-pointer selection, PhysicalStorageBuffer operation or guest shader is represented.",
                              "No shader is executed; CPU reference checks are finite synthetic checks, not a runtime proof.",
                              "Fewer static instructions do not establish a GPU-time or frame-rate improvement.",
                              "The generator handles resolved32-bit synthetic claims, not original64-bit NVN table decoding."]}
    for count in args.counts:
        for shape in ("cascade", "interval"):
            source = output / f"{shape}-{count}.comp"
            source.write_text(shader_source(shape, count, args.accesses, args.candidates, args.table_entries))
            shader = source.with_suffix(".spv")
            subprocess.run([args.glslang, "-V", "--target-env", "vulkan1.2", "-S", "comp",
                            "-o", str(shader), str(source)], check=True, capture_output=True, text=True, timeout=30)
            binary, helper_count = retain_helpers(shader.read_bytes())
            shader.write_bytes(binary)
            namespace = argparse.Namespace(input=shader, sha256=hashlib.sha256(binary).hexdigest(), driver=args.driver,
                mesa_build=args.mesa_build, output=output/f"{shape}-{count}-compile", policy=args.policy, device=args.device,
                timeout=args.timeout, push_constant_bytes=16, cc=args.cc, validator=args.validator, env=args.env)
            compiled = compile_probe.run(namespace)
            case = {"shape": shape, "singles": count, "spirv_words": len(binary)//4,
                    "helper_functions": helper_count, "source_sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
                    "spirv_structure": structure(binary),
                    "compile_report": compiled}
            report["cases"].append(case)
            (output/"report.json").write_text(json.dumps(report, indent=2)+"\n")
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--driver", type=Path, required=True)
    parser.add_argument("--mesa-build", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--counts", type=int, nargs="+", choices=(4,5,8,16,32,64), default=[16,32,64])
    parser.add_argument("--accesses", type=int, default=8)
    parser.add_argument("--candidates", type=int, default=2)
    parser.add_argument("--table-entries", type=int, default=0, help="Legacy entries already scanned in a DontUnroll loop")
    parser.add_argument("--policy", choices=("pinned32","pinned16","unpinned"), default="pinned32")
    parser.add_argument("--timeout", type=int, default=90)
    parser.add_argument("--device", type=int, default=0)
    parser.add_argument("--glslang", default="glslangValidator")
    parser.add_argument("--validator", default="spirv-val")
    parser.add_argument("--cc", default="cc")
    parser.add_argument("--env", action="append", default=[])
    args = parser.parse_args()
    try:
        print(json.dumps(run(args), indent=2))
    except (OSError, ValueError, subprocess.SubprocessError) as exc:
        print(str(exc), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
