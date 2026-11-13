#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Prepare one logical-warp32 matrix bridge; native execution is separately reviewed."""
import argparse
import hashlib
import json
from pathlib import Path
import struct
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
ORACLE_SHA256 = "80a97bd7289c85d1ceaf5c145d948caf216cc75af556dd3e246399cc8e3e2e37"
if hashlib.sha256((ROOT / "tools/matrix_restore_probe.py").read_bytes()).hexdigest() != ORACLE_SHA256:
    raise ValueError("The independently reviewed integer oracle changed")
if __package__ in (None, ""):
    sys.path.insert(0, str(ROOT))
from tools import matrix_restore_probe as oracle
from tools.compile_probe import driver_identity
from tools.graphics_compile_probe import controlled_driver_environment
from tools.mesh_mapping_probe import environment, ensure_no_games, run_process

E5 = {"library_sha256": "e5a1305ea3b423a18fccbb945aaedd32f8add303926fcc39276ee01032cd456f",
      "icd_sha256": "14f4eda1b6fd6cbf07d61edeeffdaaede166f4c09d165fec9f661e9f90556ad3"}
DEPENDENCIES = ("tools/matrix_warp_bridge.py", "src/probes/matrix_warp_bridge.c",
                "tools/matrix_restore_probe.py", "tools/compile_probe.py",
                "tools/graphics_compile_probe.py", "tools/mesh_mapping_probe.py")
PROFILE = "open-ocarina.matrix-original-warp32/v1"
CASES = {"identity": 1, "layout": 2, "rounding": 3}
SCHEMA = "open-ocarina.prepared-matrix-original-warp32/v1"


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def matrices(case):
    if case not in CASES:
        raise ValueError("Unknown synthetic matrix bridge case")
    if case == "rounding":
        a, c = [], []
        for row in range(16):
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
        a = [[oracle.half(int(row == k) if case == "identity" else (row + k) % 3 + 1)
              for k in range(16)] for row in range(16)]
        b = [[oracle.half(k * 8 + col + 1 if case == "identity" else (k + 2 * col) % 5 + 1)
              for col in range(8)] for k in range(16)]
        c = [[oracle.half(row * 4 + col if case == "identity" else (row + col) % 7)
              for col in range(8)] for row in range(16)]
    return a, b, c


def fixture(case):
    a, b, c = matrices(case)
    packed = oracle.pack_fragments(a, b, c)
    inputs = [word for lane in range(32) for name in ("A", "B", "C") for word in packed[name][lane]]
    expected_d = oracle.logical_hmma(a, b, c)
    expected = [word for lane in oracle.pack_fragments(a, b, expected_d)["C"] for word in lane]
    return struct.pack("<8s4I", b"OOMATW32", 32, CASES[case], 256, 64) + struct.pack("<320I", *inputs, *expected)


def layout_proof():
    owners = {"A": {}, "B": {}, "C": {}}
    for lane in range(32):
        g, i = divmod(lane, 4)
        points = {"A": [(row, k + half) for row, k in ((g, 2*i), (g+8, 2*i), (g, 8+2*i), (g+8, 8+2*i)) for half in (0, 1)],
                  "B": [(k + half, g) for k in (2*i, 8+2*i) for half in (0, 1)],
                  "C": [(row, 2*i + half) for row in (g, g+8) for half in (0, 1)]}
        for name, coordinates in points.items():
            for coordinate in coordinates:
                if coordinate in owners[name]:
                    raise ValueError("Shared reconstruction has overlapping lane writes")
                owners[name][coordinate] = lane
    for name, (rows, columns) in {"A": (16,16), "B": (16,8), "C": (16,8)}.items():
        if set(owners[name]) != {(row, column) for row in range(rows) for column in range(columns)}:
            raise ValueError("Shared reconstruction has missing elements")
    return {"logical_lanes": 32, "required_physical_subgroup_size": 32,
            "workgroup": [32, 1, 1], "logical_warps": 1,
            "disjoint_shared_elements": {name: len(entries) for name, entries in owners.items()},
            "tile_elements_per_uniform_phase": 128, "tile_writes_per_lane": 4,
            "tile_owner_rule": "index modulo 32", "m_iterations": 2, "k_chunk_iterations": 2,
            "matrix_calls_per_dispatch": 4,
            "uniformity_scope": "Only this independently authored fixture's constant-bounded collective path"}


def classify(data, expected):
    if len(data) != 256 or len(expected) != 256:
        raise ValueError("Expected complete 64-word packed-D buffers")
    def counts(blob):
        result = {name: 0 for name in ("positive_zero", "negative_zero", "subnormal", "normal", "infinity", "nan")}
        for word in struct.unpack("<128H", blob):
            exponent, fraction = (word >> 10) & 31, word & 1023
            name = ("nan" if fraction else "infinity") if exponent == 31 else "normal" if exponent else "subnormal" if fraction else "negative_zero" if word >> 15 else "positive_zero"
            result[name] += 1
        return result
    mismatched_words = sum(a != b for a,b in zip(struct.unpack("<64I", data), struct.unpack("<64I", expected)))
    mismatched_halves = sum(a != b for a,b in zip(struct.unpack("<128H", data), struct.unpack("<128H", expected)))
    zero_sign_only = sum(a != b and (a & 0x7fff) == (b & 0x7fff) == 0
                         for a,b in zip(struct.unpack("<128H", data), struct.unpack("<128H", expected)))
    return {"mismatched_packed_words": mismatched_words, "mismatched_half_elements": mismatched_halves,
            "zero_sign_only_half_mismatches": zero_sign_only,
            "actual_half_classes": counts(data), "software_reference_half_classes": counts(expected),
            "actual_sha256": hashlib.sha256(data).hexdigest(),
            "software_reference_sha256": hashlib.sha256(expected).hexdigest(),
            "nan_payload_or_signaling_equivalence_claim": False}


def shader_source():
    checkpoint = oracle.CHECKPOINT_GLSL.split("uint rne_shift",1)[1].split("void main()",1)[0]
    return """#version 460
#extension GL_KHR_cooperative_matrix : require
#extension GL_KHR_memory_scope_semantics : require
#extension GL_EXT_shader_explicit_arithmetic_types_float16 : require
#extension GL_KHR_shader_subgroup_basic : require
layout(local_size_x=32,local_size_y=1,local_size_z=1) in;
layout(set=0,binding=0,std430) readonly buffer PackedInput { uint words[]; } source;
layout(set=0,binding=1,std430) writeonly buffer PackedOutput { uint words[]; } destination;
shared float16_t originalA[256];
shared float16_t originalB[128];
shared float originalC[128];
shared float originalD[128];
shared float16_t tileA[128];
shared float16_t tileB[128];
uint rne_shift""" + checkpoint + """
void shared_phase() {
    subgroupMemoryBarrierShared();
    subgroupBarrier();
}
void main() {
    uint lane = gl_LocalInvocationIndex;
    uint g = lane >> 2u;
    uint i = lane & 3u;
    for (uint word = 0u; word < 4u; word++) {
        vec2 values = unpackHalf2x16(source.words[lane * 8u + word]);
        uint row = g + (word & 1u) * 8u;
        uint k = i * 2u + (word >> 1u) * 8u;
        originalA[row * 16u + k] = float16_t(values.x);
        originalA[row * 16u + k + 1u] = float16_t(values.y);
    }
    for (uint word = 0u; word < 2u; word++) {
        vec2 bv = unpackHalf2x16(source.words[lane * 8u + 4u + word]);
        uint k = i * 2u + word * 8u;
        originalB[k * 8u + g] = float16_t(bv.x);
        originalB[(k + 1u) * 8u + g] = float16_t(bv.y);
        vec2 cv = unpackHalf2x16(source.words[lane * 8u + 6u + word]);
        uint row = g + word * 8u;
        originalC[row * 8u + i * 2u] = cv.x;
        originalC[row * 8u + i * 2u + 1u] = cv.y;
    }
    shared_phase();
    coopmat<float16_t,gl_ScopeSubgroup,8,16,gl_MatrixUseA> ma;
    coopmat<float16_t,gl_ScopeSubgroup,16,8,gl_MatrixUseB> mb;
    coopmat<float,gl_ScopeSubgroup,8,8,gl_MatrixUseAccumulator> mc;
    for (uint m = 0u; m < 2u; m++) {
        coopMatLoad(mc,originalC,m * 64u,8u,gl_CooperativeMatrixLayoutRowMajor);
        for (uint chunk = 0u; chunk < 2u; chunk++) {
            for (uint j = lane; j < 128u; j += 32u) {
                uint ar = j / 16u, ak = j % 16u;
                uint bk = j / 8u, bc = j % 8u;
                tileA[j] = ak < 8u ? originalA[(m * 8u + ar) * 16u + chunk * 8u + (ak & 7u)] : float16_t(0.0);
                tileB[j] = bk < 8u ? originalB[(chunk * 8u + (bk & 7u)) * 8u + bc] : float16_t(0.0);
            }
            shared_phase();
            coopMatLoad(ma,tileA,0u,16u,gl_CooperativeMatrixLayoutRowMajor);
            coopMatLoad(mb,tileB,0u,8u,gl_CooperativeMatrixLayoutRowMajor);
            mc = coopMatMulAdd(ma,mb,mc);
            for (int element = 0; element < mc.length(); element++) mc[element] = half_checkpoint(mc[element]);
            shared_phase();
        }
        coopMatStore(mc,originalD,m * 64u,8u,gl_CooperativeMatrixLayoutRowMajor);
    }
    shared_phase();
    for (uint word = 0u; word < 2u; word++) {
        uint row = g + word * 8u;
        uint position = row * 8u + i * 2u;
        destination.words[lane * 2u + word] = packHalf2x16(vec2(originalD[position],originalD[position + 1u]));
    }
}
"""


def prepare(output):
    output = oracle.private_output(output)
    output.mkdir(parents=True)
    shader = output / "warp32-bridge.comp"
    shader.write_text(shader_source())
    module = output / "warp32-bridge.spv"
    env = environment()
    shader_flags = ["-V","--target-env","vulkan1.3","-Os"]
    run_process(["glslangValidator",*shader_flags,"-o",str(module),str(shader)],env,output,"compile-shader",30)
    run_process(["spirv-val","--target-env","vulkan1.3",str(module)],env,output,"validate-shader",15)
    source = output / "helper.c"
    source.write_bytes((ROOT / "src/probes/matrix_warp_bridge.c").read_bytes())
    binary = output / "warp32-bridge-helper"
    flags = ["-std=c11","-O2","-DNDEBUG","-Wall","-Wextra","-Werror"]
    run_process(["cc",*flags,str(source),"-o",str(binary),"-lvulkan"],env,output,"build-helper",30)
    cases=[]
    for name in CASES:
        path=output/(name+".fixture")
        path.write_bytes(fixture(name))
        result=json.loads(run_process([str(binary),"cpu",str(path)],env,output,"cpu-"+name,5))
        if result!={"profile":PROFILE,"cpu_validated":True,"vulkan_instance_created":False}:
            raise ValueError("Unexpected CPU-validation scope")
        cases.append({"name":name,"bundle":path.name,"exact_safe_reference":name!="rounding",
                      "expected_classification":classify(path.read_bytes()[-256:],path.read_bytes()[-256:])})
    _,policy=controlled_driver_environment("native-safe",{})
    artifacts=[shader,module,source,binary,*(output/(name+".fixture") for name in CASES)]
    plan={"schema":SCHEMA,"profile":PROFILE,"header_magic":"OOMATW32","synthetic_only":True,
          "oracle_sha256":ORACLE_SHA256,"helper_source_sha256":digest(source),"helper_binary_sha256":digest(binary),
          "shader_source_sha256":digest(shader),"shader_module_sha256":digest(module),
          "driver_profile":E5,"driver_policy":policy,"source_build_flags":flags,"shader_build_flags":shader_flags,
          "layout_proof":layout_proof(),"input_words_per_lane":8,"output_words_per_lane":2,
          "full_input_bytes":1024,"full_output_bytes":256,"guard_bytes_each_end":256,
          "cases":cases,"artifacts":{str(p.relative_to(output)):digest(p) for p in artifacts},
          "dependencies":{name:digest(ROOT/name) for name in DEPENDENCIES},
          "compiler_versions":{tool:subprocess.run([tool,"--version"],check=True,capture_output=True,text=True,timeout=10).stdout.splitlines()[0]
                               for tool in ("cc","glslangValidator","spirv-val")},
          "cpu_validated_cases":3,"vulkan_instance_created":False,"pipeline_compiled":False,"gpu_dispatched":False,
          "max_dispatch_calls":1,"dispatch":[1,1,1],"workgroup":[32,1,1],"required_width":32,
          "fence_timeout_ns":2000000000,"process_timeout_seconds":30,
          "limits":["Padded KHR matrix arithmetic is not proven equivalent to original software or console arithmetic.",
                    "Only one uniform, local32, required-SG32 logical warp is supported by this fixture.",
                    "No general 32/64/128-invocation workgroup, control-flow or production shader bridge is established.",
                    "NaN payload/signaling behavior is not an oracle contract.",
                    "No game program, weights, assets or renderer policy changed."]}
    (output/"plan.json").write_text(json.dumps(plan,indent=2)+"\n")
    return {"plan_sha256":digest(output/"plan.json"),"helper_source_sha256":digest(source),
            "helper_binary_sha256":digest(binary),"shader_module_sha256":digest(module),
            "cpu_validated_cases":3,"vulkan_instance_created":False,"pipeline_compiled":False,"gpu_dispatched":False}


def reviewed(path,sha):
    output=Path(path).resolve()
    if digest(output/"plan.json")!=sha:
        raise ValueError("Reviewed bridge plan hash differs")
    plan=json.loads((output/"plan.json").read_text())
    if plan.get("schema")!=SCHEMA or plan.get("profile")!=PROFILE or plan.get("driver_profile")!=E5 or plan.get("oracle_sha256")!=ORACLE_SHA256:
        raise ValueError("Unrecognized bridge profile/oracle/driver")
    if set(plan["dependencies"])!=set(DEPENDENCIES):
        raise ValueError("Bridge dependency inventory changed")
    required_artifacts={"warp32-bridge.comp","warp32-bridge.spv","helper.c","warp32-bridge-helper",
                        *(name+".fixture" for name in CASES)}
    if set(plan["artifacts"])!=required_artifacts or plan.get("required_width")!=32 or plan.get("workgroup")!=[32,1,1]:
        raise ValueError("Bridge artifact inventory or single-warp bounds changed")
    for name,expected in plan["dependencies"].items():
        if digest(ROOT/name)!=expected:
            raise ValueError("Frozen bridge dependency changed")
    for name,expected in plan["artifacts"].items():
        path=(output/name).resolve()
        if not path.is_relative_to(output) or digest(path)!=expected:
            raise ValueError("Frozen bridge artifact changed")
    return output,plan


def execute(path,sha,driver,name,phase):
    output,plan=reviewed(path,sha)
    case=next((case for case in plan["cases"] if case["name"]==name),None)
    if case is None or phase not in ("properties","compile","dispatch"):
        raise ValueError("Select one reviewed synthetic case and runtime phase")
    if name not in CASES or case["bundle"]!=name+".fixture" or case["bundle"] not in plan["artifacts"]:
        raise ValueError("Bridge case bundle differs from review")
    identity=driver_identity(driver)
    if any(identity[key]!=value for key,value in E5.items()):
        raise ValueError("Bridge requires exact E5 ICD/library")
    env,policy=controlled_driver_environment("native-safe",environment())
    if policy!=plan["driver_policy"]:
        raise ValueError("Native-safe policy changed")
    ensure_no_games()
    env.update(VK_ICD_FILENAMES=str(driver.resolve()),VK_DRIVER_FILES=str(driver.resolve()),
               MESA_SHADER_CACHE_DISABLE="true",MESA_SHADER_CACHE_DIR=str(output/"cache"))
    stem=phase+"-"+name
    with (output/(stem+"-started.json")).open("x") as target:
        json.dump({"reviewed_plan_sha256":sha,"profile":PROFILE,"driver":identity,"case":case,"phase":phase},target)
    result=json.loads(run_process([str(output/"warp32-bridge-helper"),phase,str(output/case["bundle"]),
                                   str(output/"warp32-bridge.spv"),str(output/(stem+".bin"))],env,output,stem,30))
    if result.get("profile")!=PROFILE or result.get("phase")!=phase or result.get("requested_width")!=32:
        raise ValueError("Runtime bridge profile differs")
    if phase!="properties" and (result.get("actual_width")!=32 or result.get("width_source")!="pipeline_executable_properties"):
        raise ValueError("Bridge requires actual subgroup32")
    if phase=="dispatch":
        if result.get("dispatches")!=1 or not result.get("guards_and_inputs_unchanged") or not result.get("output_checked"):
            raise ValueError("Bridge bounded output/guard contract failed")
        actual=(output/(stem+".bin")).read_bytes()
        reference=(output/case["bundle"]).read_bytes()[-256:]
        check=classify(actual,reference)
        if check["mismatched_packed_words"]!=result.get("mismatches_with_software_reference") or result.get("full_output_word_count")!=64:
            raise ValueError("Native/CPU complete packed output comparisons disagree")
        if case["exact_safe_reference"] and check["mismatched_packed_words"]:
            raise ValueError("Safe exact packed bridge result differs")
        result["classification"]=check
    (output/(stem+"-report.json")).write_text(json.dumps(result,indent=2)+"\n")
    return result


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    commands=parser.add_subparsers(dest="command",required=True)
    prep=commands.add_parser("prepare")
    prep.add_argument("--out-dir",type=Path,required=True)
    run=commands.add_parser("execute")
    run.add_argument("--prepared",type=Path,required=True)
    run.add_argument("--reviewed-plan-sha256",required=True)
    run.add_argument("--driver",type=Path,required=True)
    run.add_argument("--case",choices=tuple(CASES),required=True)
    run.add_argument("--phase",choices=("properties","compile","dispatch"),required=True)
    args=parser.parse_args()
    try:
        result=prepare(args.out_dir) if args.command=="prepare" else execute(args.prepared,args.reviewed_plan_sha256,args.driver,args.case,args.phase)
        print(json.dumps(result,indent=2))
    except (OSError,ValueError,KeyError,subprocess.SubprocessError) as exc:
        print(str(exc),file=sys.stderr)
        return 2
    return 0


if __name__=="__main__":
    raise SystemExit(main())
