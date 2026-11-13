#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Resource contract for the sampled what-if NDVE module.

Declares descriptor shapes and the software-matrix helper. It does not infer
buffer or image payloads, and it does not compare the module with the older
offline export.
"""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tools.ndve_sampled_stage_export import PROGRAM, file_sha
from tools.ndve_stage_contract import inspect_module
from tools.ndve_stage_export import NATIVE_SHA

ROOT = Path(__file__).resolve().parents[1]


def audit(exported, output):
    exported, output = Path(exported).resolve(), Path(output).resolve()
    if output.exists():
        raise ValueError("Choose a fresh private contract output")
    if output.is_relative_to(ROOT) and output.relative_to(ROOT).parts[:1] not in (("local",), ("work",), ("artifacts",)):
        raise ValueError("Private output must stay under local, work, or artifacts")
    report = json.loads((exported / "export-report.json").read_text())
    if report.get("schema") != "open-ocarina.ndve-sampled-stage-export/v1":
        raise ValueError("Not a sampled what-if export")
    if report.get("identity", {}).get("program") != PROGRAM or report["identity"].get("native_control_code_sha256") != NATIVE_SHA:
        raise ValueError("Stage identity differs")
    if report.get("actual_profile_consumption_observed") is not False or report.get("production_network_exclusion_changed") is not False:
        raise ValueError("Export report overclaims profile use or changes the renderer exclusion")
    module = exported / "translated/spv" / (PROGRAM + ".5.spv")
    data = module.read_bytes()
    if hashlib.sha256(data).hexdigest() != report.get("module_sha256") or len(data) > 1024**2 or len(data) % 4:
        raise ValueError("Module pin differs")
    with tempfile.TemporaryDirectory(prefix="ndve-sampled-contract-") as directory:
        snapshot = Path(directory) / "module.spv"
        snapshot.write_bytes(data)
        assembly = subprocess.run(["spirv-dis", "--raw-id", str(snapshot)], capture_output=True, text=True, check=True, timeout=30).stdout
    contract = inspect_module(data, assembly)
    helpers = [row for row in contract["main_callees"] if row["static_main_call_sites"] == 72 and
               row["extended_instruction_counts"].get("Fma") == 8 and row["instruction_counts"].get("OpLoopMerge") == 1]
    if len(helpers) != 1 or contract["reflection"]["workgroup"] != [32, 1, 1]:
        raise ValueError("Software matrix helper was not retained")
    if any(row.get("image_type", {}).get("format") not in (None, "Unknown") for row in contract["declared_descriptor_layouts"]):
        raise ValueError("A concrete image format was declared; this contract expected an untyped sample")
    public = {key: contract[key] for key in ("reflection", "declared_descriptor_layouts", "push_members")}
    public.update(schema="open-ocarina.ndve-sampled-stage-contract/v1", CPU_only=True, Vulkan_calls=False,
                  module_sha256=report["module_sha256"], module_words=report["module_words"],
                  matrix_helper_static_main_call_sites=72,
                  software_helper={"loop_merges": 1, "Fma": 8, "subgroup_shuffles": helpers[0]["instruction_counts"].get("OpGroupNonUniformShuffle", 0)},
                  input_classes={
                      "observed_consumed": "47 recorded compiler queries, including c0 byte 88 and the noninteger answer",
                      "conditional_sampled": "pinned 77-field profile and any texture-type answer taken from the bound registration",
                      "unresolved": "descriptor bytes, image pixels, guest page backing, and any compiler query this export denied",
                  },
                  payloads_inferred=False, complete_replay_inputs=False, native_network_restored=False,
                  arithmetic_equivalence_certified=False, tool_sha256=file_sha(Path(__file__)))
    output.mkdir(parents=True)
    (output / "contract.json").write_text(json.dumps(public, indent=2) + "\n")
    return public


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--exported", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(audit(args.exported, args.output), indent=2))


if __name__ == "__main__":
    main()
