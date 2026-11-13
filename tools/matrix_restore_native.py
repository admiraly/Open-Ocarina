#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Freeze synthetic matrix fixtures/native helper; execution is a separate reviewed action."""
import argparse
import hashlib
import json
from pathlib import Path
import struct
import subprocess
import sys

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tools import matrix_restore_probe as oracle
from tools.compile_probe import driver_identity
from tools.graphics_compile_probe import controlled_driver_environment
from tools.mesh_mapping_probe import environment, ensure_no_games, run_process

ROOT = Path(__file__).resolve().parents[1]
E5 = {"library_sha256": "e5a1305ea3b423a18fccbb945aaedd32f8add303926fcc39276ee01032cd456f",
      "icd_sha256": "14f4eda1b6fd6cbf07d61edeeffdaaede166f4c09d165fec9f661e9f90556ad3"}
DEPENDENCIES = ("tools/matrix_restore_native.py", "tools/matrix_restore_probe.py",
                "src/probes/matrix_restore_native.c", "tools/compile_probe.py",
                "tools/graphics_compile_probe.py", "tools/mesh_mapping_probe.py")
KINDS = {"base": 1, "checkpoint": 2}
FIXTURES = {"identity": 1, "layout": 2, "rounding": 3}


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def fixture(kind, width, case):
    if kind not in KINDS or width not in (8, 16, 32) or case not in FIXTURES:
        raise ValueError("Unknown bounded fixture")
    aa, bb, cc, dd = [], [], [], []
    for group in range(32 // width):
        rows = 8 if kind == "base" else 16
        if case == "rounding":
            a = []
            c = []
            for row in range(rows):
                if row % 4 == 0:
                    vector = [oracle.half(1), oracle.half(2 ** -11)] + [0] * 14
                    vector[8] = oracle.half(-1)
                elif row % 4 == 1:
                    vector = [0x8000] * 16
                elif row % 4 == 2:
                    vector = [oracle.half(65504), oracle.half(1), oracle.half(-65504)] + [0] * 13
                else:
                    vector = [0] * 16
                a.append(vector)
                c.append([0x8000 if row % 4 == 1 else 1 if row % 4 == 3 else 0] * 8)
            b = [[oracle.half(1)] * 8 for _ in range(16)]
        else:
            a = [[oracle.half(int(row == k) if case == "identity" else (row + k + group) % 3 + 1)
                  for k in range(16)] for row in range(rows)]
            b = [[oracle.half(k * 8 + col + 1 + group * 4 if case == "identity" else
                              (k + 2 * col + group) % 5 + 1) for col in range(8)] for k in range(16)]
            c = [[oracle.half(row * 4 + col + group * 100 if case == "identity" else
                              (row + col + group) % 7 + group * 100) for col in range(8)] for row in range(rows)]
        reference = [[oracle.dot16(a[row], [b[k][col] for k in range(16)], c[row][col])
                      for col in range(8)] for row in range(rows)]
        if kind == "checkpoint":
            tiled = oracle.tile_inputs(a, b, c)
            aa.extend(word for row in tiled["A_rows"] for word in row)
            bb.extend(word for row in tiled["B_rows"] for word in row)
        else:
            aa.extend(word for row in a for word in row)
            bb.extend(word for row in b for word in row)
        cc.extend(oracle.half_to_float32(word) for row in c for word in row)
        dd.extend(oracle.half_to_float32(word) for row in reference for word in row)
    header = struct.pack("<8s8I", b"OOMAT001", KINDS[kind], width, FIXTURES[case], 32 // width,
                         len(aa), len(bb), len(cc), len(dd))
    return header + struct.pack(f"<{len(aa) + len(bb)}H", *aa, *bb) + struct.pack(f"<{len(cc) + len(dd)}I", *cc, *dd)


def prepare(output):
    output = oracle.private_output(output)
    output.mkdir(parents=True)
    shader_report = oracle.prepare(output / "shaders", True)
    source = output / "helper.c"
    source.write_bytes((ROOT / "src/probes/matrix_restore_native.c").read_bytes())
    binary = output / "matrix-helper"
    flags = ["-std=c11", "-O2", "-DNDEBUG", "-Wall", "-Wextra", "-Werror"]
    result = subprocess.run(["cc", *flags, str(source), "-o", str(binary), "-lvulkan"],
                            capture_output=True, text=True, timeout=30)
    (output / "helper-build.log").write_text(result.stdout + result.stderr)
    if result.returncode:
        raise ValueError("Helper build failed; inspect private build log")
    cases = []
    for kind in KINDS:
        module = f"shaders/khr_{'m8n8k16' if kind == 'base' else 'm16_checkpoints_padded'}.spv"
        for width in (8, 16, 32):
            for case in FIXTURES:
                name = f"{kind}-{case}-sg{width}"
                bundle = output / (name + ".fixture")
                bundle.write_bytes(fixture(kind, width, case))
                result = subprocess.run([str(binary), "cpu", str(bundle)],
                                        capture_output=True, text=True, timeout=5)
                if result.returncode:
                    raise ValueError(f"Pre-instance CPU fixture validation failed: {name}: {result.stderr}")
                validation = json.loads(result.stdout)
                if validation != {"cpu_validated": True, "vulkan_instance_created": False}:
                    raise ValueError("CPU validation unexpectedly changed scope")
                cases.append({"name": name, "kind": kind, "fixture": case, "width": width,
                              "bundle": bundle.name, "module": module,
                              "exact_safe_reference": case != "rounding"})
    _, policy = controlled_driver_environment("native-safe", {})
    artifacts = [source, binary, *(output / case["bundle"] for case in cases),
                 *(output / case["module"] for case in cases)]
    plan = {"schema": "open-ocarina.matrix-restoration-prepared/v1", "synthetic_only": True,
            "gpu_executed": False, "pipeline_compiled": False, "source_build_flags": flags,
            "compiler_version": subprocess.run(["cc", "--version"], check=True, capture_output=True,
                                               text=True, timeout=10).stdout.splitlines()[0],
            "helper_binary_sha256": digest(binary), "helper_source_sha256": digest(source),
            "cpu_validation_count": len(cases), "shader_preparation": shader_report,
            "driver_profile": E5, "driver_policy": policy, "device_vendor": 0x8086, "device_id": 0x56A0,
            "workgroup": [32, 1, 1], "max_dispatches_per_invocation": 1,
            "fence_timeout_ns": 2_000_000_000, "process_timeout_seconds": 30,
            "cases": cases, "artifacts": {str(path.relative_to(output)): digest(path) for path in artifacts},
            "dependencies": {name: digest(ROOT / name) for name in DEPENDENCIES},
            "limits": ["No original shader, network weights or game inputs.",
                       "Required subgroup width must be confirmed from the compiled executable.",
                       "Rounding cases report differences from software checkpoints, not certified DPAS equivalence.",
                       "The original logical warp32 mapping is not integrated or narrowed."]}
    (output / "plan.json").write_text(json.dumps(plan, indent=2) + "\n")
    return {"plan_sha256": digest(output / "plan.json"), "helper_source_sha256": digest(source),
            "helper_binary_sha256": digest(binary), "cpu_validation_count": len(cases),
            "gpu_executed": False, "pipeline_compiled": False}


def reviewed(path, sha):
    output = Path(path).resolve()
    if digest(output / "plan.json") != sha:
        raise ValueError("Reviewed plan hash differs")
    plan = json.loads((output / "plan.json").read_text())
    if plan.get("schema") != "open-ocarina.matrix-restoration-prepared/v1" or plan.get("driver_profile") != E5:
        raise ValueError("Unknown matrix plan/profile")
    if set(plan["dependencies"]) != set(DEPENDENCIES):
        raise ValueError("Dependency inventory differs from review")
    for name, expected in plan["artifacts"].items():
        artifact = (output / name).resolve()
        if not artifact.is_relative_to(output) or digest(artifact) != expected:
            raise ValueError("Frozen artifact differs")
    for name, expected in plan["dependencies"].items():
        if name not in DEPENDENCIES or digest(ROOT / name) != expected:
            raise ValueError("Dependency differs from review")
    return output, plan


def execute(path, sha, driver, case_name, mode):
    output, plan = reviewed(path, sha)
    case = next((item for item in plan["cases"] if item["name"] == case_name), None)
    if case is None or mode not in ("properties", "compile", "dispatch"):
        raise ValueError("Choose one frozen case and one explicit runtime phase")
    if case["module"] not in plan["artifacts"] or case["bundle"] not in plan["artifacts"]:
        raise ValueError("Case artifacts were not frozen")
    identity = driver_identity(driver)
    if any(identity[key] != expected for key, expected in E5.items()):
        raise ValueError("Only exact reviewed E5 ICD/library are admitted")
    env, policy = controlled_driver_environment("native-safe", environment())
    if policy != plan["driver_policy"]:
        raise ValueError("Native-safe compiler policy changed")
    ensure_no_games()
    env.update(VK_ICD_FILENAMES=str(driver.resolve()), VK_DRIVER_FILES=str(driver.resolve()),
               MESA_SHADER_CACHE_DISABLE="true", MESA_SHADER_CACHE_DIR=str(output / "cache"))
    stem = f"{mode}-{case_name}"
    with (output / (stem + "-started.json")).open("x") as target:
        json.dump({"reviewed_plan_sha256": sha, "driver": identity, "case": case, "phase": mode}, target)
    result = json.loads(run_process([str(output / "matrix-helper"), mode, str(output / case["bundle"]),
                                     str(output / case["module"]), str(output / (stem + ".bin"))],
                                    env, output, stem, 30))
    if result.get("phase") != mode or result.get("requested_width") != case["width"]:
        raise ValueError("Runtime metadata disagrees with frozen request")
    if mode != "properties" and (result.get("actual_width") != case["width"] or
                                 result.get("width_source") != "pipeline_executable_properties"):
        raise ValueError("Compiled executable width differs")
    if mode == "dispatch":
        if result.get("dispatches") != 1 or not result.get("guards_and_inputs_unchanged") or not result.get("output_checked"):
            raise ValueError("Bounded dispatch contract failed")
        bundle = (output / case["bundle"]).read_bytes()
        _, _, _, _, _, na, nb, nc, nd = struct.unpack_from("<8s8I", bundle)
        expected = bundle[40 + (na + nb) * 2 + nc * 4:]
        actual = (output / (stem + ".bin")).read_bytes()
        if len(actual) != nd * 4 or len(expected) != len(actual) or result.get("full_output_word_count") != nd:
            raise ValueError("Full output extent differs from fixture")
        mismatches = sum(x != y for x, y in zip(struct.unpack(f"<{nd}I", actual), struct.unpack(f"<{nd}I", expected)))
        if mismatches != result.get("mismatches_with_software_reference") or (case["exact_safe_reference"] and mismatches):
            raise ValueError("Full output comparison differs from admitted fixture")
        result.update(output_sha256=hashlib.sha256(actual).hexdigest(),
                      software_reference_sha256=hashlib.sha256(expected).hexdigest())
    (output / (stem + "-report.json")).write_text(json.dumps(result, indent=2) + "\n")
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    prep = commands.add_parser("prepare")
    prep.add_argument("--out-dir", type=Path, required=True)
    run = commands.add_parser("execute")
    run.add_argument("--prepared", type=Path, required=True)
    run.add_argument("--reviewed-plan-sha256", required=True)
    run.add_argument("--driver", type=Path, required=True)
    run.add_argument("--case", required=True)
    run.add_argument("--phase", choices=("properties", "compile", "dispatch"), required=True)
    args = parser.parse_args()
    try:
        result = prepare(args.out_dir) if args.command == "prepare" else execute(
            args.prepared, args.reviewed_plan_sha256, args.driver, args.case, args.phase)
        print(json.dumps(result, indent=2))
    except (OSError, ValueError, KeyError, subprocess.SubprocessError) as exc:
        print(str(exc), file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
