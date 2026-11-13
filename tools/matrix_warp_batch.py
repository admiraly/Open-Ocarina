#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Prepare deterministic finite-half coverage across independent warp32 workgroups."""
import argparse
import hashlib
import json
from pathlib import Path
import struct
import subprocess
import sys

ROOT=Path(__file__).resolve().parents[1]
BRIDGE_SHA256="28f35436b7ef7e7b3076da773c054b62b32b73790ea109872f956c4b1c753cf1"
if hashlib.sha256((ROOT/"tools/matrix_warp_bridge.py").read_bytes()).hexdigest()!=BRIDGE_SHA256:
    raise ValueError("Reviewed single-warp bridge source changed")
if __package__ in (None,""):
    sys.path.insert(0,str(ROOT))
from tools import matrix_warp_bridge as bridge
from tools.compile_probe import driver_identity
from tools.graphics_compile_probe import controlled_driver_environment
from tools.mesh_mapping_probe import environment,ensure_no_games,run_process

o=bridge.oracle
PROFILE="open-ocarina.matrix-warp32-finite-batch/v2"
SCHEMA="open-ocarina.prepared-matrix-warp32-finite-batch/v2"
MAGIC=b"OOMATB04"
GROUPS=64
INPUT_STRIDE=288
OUTPUT_STRIDE=96
INNER_WORDS=16
INNER_GUARD=0xA11CE55A
SENTINEL=0xCDCDCDCD
MARKER_BASE=0x57420000
SEED=0x4F4F5442
DEPENDENCIES=("tools/matrix_warp_batch.py","src/probes/matrix_warp_batch.c",
              "tools/matrix_warp_bridge.py","tools/matrix_restore_probe.py",
              "tools/compile_probe.py","tools/graphics_compile_probe.py","tools/mesh_mapping_probe.py")


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def sample(group,matrix,index):
    return int.from_bytes(hashlib.sha256(struct.pack("<4I",SEED,group,matrix,index)).digest()[:8],"little")


def finite_word(value):
    word=value&65535
    return ((word&0x83ff)|0x7800) if word&0x7c00==0x7c00 else word


def empty():
    return [[0]*16 for _ in range(16)],[[0]*8 for _ in range(16)],[[0]*8 for _ in range(16)]


def case(group):
    if not isinstance(group,int) or not 0<=group<GROUPS:
        raise ValueError("Batch group must be in 0..63")
    if group<2:
        name=("identity","layout")[group]
        a,b,c=bridge.matrices(name)
        return name,True,a,b,c
    a,b,c=empty()
    if group<16:
        name="positive-zero" if group==2 else f"small-integer-{group-3:02d}"
        if group!=2:
            a=[[o.half(sample(group,0,r*16+k)%4) for k in range(16)] for r in range(16)]
            b=[[o.half(sample(group,1,k*8+col)%5) for col in range(8)] for k in range(16)]
            c=[[o.half(sample(group,2,r*8+col)%9) for col in range(8)] for r in range(16)]
        return name,True,a,b,c
    if group==16:
        name="midpoint-k8-checkpoint"
        vector=[o.half(1),o.half(2**-11)]+[0]*14
        vector[8]=o.half(-1)
        a=[list(vector) for _ in range(16)]
        b=[[o.half(1)]*8 for _ in range(16)]
    elif group==17:
        name="positive-zero-padding-negative-zero"
        a=[[0x8000]*16 for _ in range(16)]
        b=[[o.half(1)]*8 for _ in range(16)]
        c=[[0x8000]*8 for _ in range(16)]
    elif group==18:
        name="cancellation-reduction-order"
        vector=[o.half(65504),o.half(1),o.half(-65504)]+[0]*13
        weights=[o.half(65504),o.half(1),o.half(65504)]+[0]*13
        a=[list(vector) for _ in range(16)]
        b=[[weights[k]]*8 for k in range(16)]
    elif group==19:
        name="half-subnormal-preservation"
        c=[[(1,2,0x03fe,0x03ff)[(r+col)%4]|(0x8000 if r&1 else 0) for col in range(8)] for r in range(16)]
    elif group==20:
        name="half-underflow-midpoint"
        for r in range(16):a[r][0]=(0x37ff,0x3800,0x3801,0xb800)[r%4]
        b[0]=[1,1,1,1,2,2,2,2]
    elif group==21:
        name="half-normal-subnormal-boundary"
        for r in range(16):a[r][0]=o.half(1)
        b[0]=[1,0x8001]*4
        c=[[(0x03ff,0x0400,0x0401)[(r+col)%3]|(0x8000 if r&1 else 0) for col in range(8)] for r in range(16)]
    elif group==22:
        name="half-overflow-midpoint"
        for r in range(16):a[r][0]=o.half((15,16,17,-16)[r%4])
        b[0]=[o.half(1)]*8
        c=[[0xfbff if r%4==3 else 0x7bff]*8 for r in range(16)]
    elif group==23:
        name="half-midpoint-even-odd"
        for r in range(16):a[r][0]=o.half(-1 if r&2 else 1)
        b[0]=[o.half(2**-11)]*8
        c=[[(0x3c00|(r&1))|(0x8000 if r&2 else 0)]*8 for r in range(16)]
    elif group<32:
        name=f"exponent-pair-{group-24:02d}"
        exponents=(0,1,2,5,10,15,25,30)
        e=exponents[group-24]
        a=[[(e<<10)|((r*17+k*31)&1023)|(0x8000 if (r+k)&1 else 0) for k in range(16)] for r in range(16)]
        b=[[((30-e)<<10)|((k*13+col*29)&1023)|(0x8000 if (k+col)&2 else 0) for col in range(8)] for k in range(16)]
        c=[[0]*8 for _ in range(16)]
    elif group<40:
        name=f"finite-exponent-mantissa-{group-32:02d}"
        def grid(matrix,index):
            value=sample(group,matrix,index)
            return ((value%31)<<10)|((0,1,511,512,1022,1023)[(value>>8)%6])|((value>>24)&0x8000)
        a=[[grid(0,r*16+k) for k in range(16)] for r in range(16)]
        b=[[grid(1,k*8+col) for col in range(8)] for k in range(16)]
        c=[[grid(2,r*8+col) for col in range(8)] for r in range(16)]
    elif group<48:
        name=f"balanced-finite-products-{group-40:02d}"
        exponents=[1+sample(group,3,k)%29 for k in range(16)]
        a=[[(exponents[k]<<10)|(sample(group,0,r*16+k)&0x83ff) for k in range(16)] for r in range(16)]
        b=[[((30-exponents[k])<<10)|(sample(group,1,k*8+col)&0x83ff) for col in range(8)] for k in range(16)]
        c=[[((sample(group,2,r*8+col)%17)<<10)|(sample(group,2,r*8+col)&0x83ff) for col in range(8)] for r in range(16)]
    else:
        name=f"finite-half-bits-{group-48:02d}" if group<56 else f"low-exponent-finite-bits-{group-56:02d}"
        def bits(matrix,index):
            word=finite_word(sample(group,matrix,index))
            return word if group<56 else (word&0x83ff)|(((word>>10)%16)<<10)
        a=[[bits(0,r*16+k) for k in range(16)] for r in range(16)]
        b=[[bits(1,k*8+col) for col in range(8)] for k in range(16)]
        c=[[bits(2,r*8+col) for col in range(8)] for r in range(16)]
    return name,False,a,b,c


def corpus():
    inputs=[]
    expected=[]
    markers=[]
    cases=[]
    for group in range(GROUPS):
        name,safe,a,b,c=case(group)
        if any(word&0x7c00==0x7c00 for matrix in (a,b,c) for row in matrix for word in row):
            raise ValueError("Corpus input is not finite half")
        packed=o.pack_fragments(a,b,c)
        words=[word for lane in range(32) for key in ("A","B","C") for word in packed[key][lane]]
        d=o.logical_hmma(a,b,c)
        output=[word for lane in o.pack_fragments(a,b,d)["C"] for word in lane]
        inputs.extend([INNER_GUARD]*INNER_WORDS+words+[INNER_GUARD]*INNER_WORDS)
        expected.extend([INNER_GUARD]*INNER_WORDS+output+[INNER_GUARD]*INNER_WORDS)
        markers.extend([INNER_GUARD]*INNER_WORDS+[marker_token(group,word) for word in range(64)]+[INNER_GUARD]*INNER_WORDS)
        payload=struct.pack("<64I",*output)
        classification=bridge.classify(payload,payload)
        cases.append({"group":group,"name":name,"safe_exact":safe,
                      "input_payload_sha256":hashlib.sha256(struct.pack("<256I",*words)).hexdigest(),
                      "reference_payload_sha256":hashlib.sha256(payload).hexdigest(),
                      "reference_half_classes":classification["software_reference_half_classes"]})
    header=struct.pack("<8s10I",MAGIC,2,32,GROUPS,INPUT_STRIDE,OUTPUT_STRIDE,OUTPUT_STRIDE,len(inputs),len(expected),len(markers),len(cases))
    records=struct.pack("<128I",*(word for entry in cases for word in (entry["group"],int(entry["safe_exact"]))))
    bundle=header+records+struct.pack(f"<{len(inputs)+len(expected)+len(markers)}I",*inputs,*expected,*markers)
    return bundle,cases


def marker_token(group,word):
    if not 0<=group<64 or not 0<=word<64:
        raise ValueError("Marker owner is outside the bounded batch")
    return MARKER_BASE|(group*64+word+1)


def shader_source():
    source=bridge.shader_source()
    if source.count("uint lane = gl_LocalInvocationIndex;")!=1 or source.count("source.words[lane * 8u +")!=3 or source.count("destination.words[lane * 2u + word]")!=1:
        raise ValueError("Frozen parent shader shape changed")
    source=source.replace("uint lane = gl_LocalInvocationIndex;","uint lane = gl_LocalInvocationIndex;\n    uint group = gl_WorkGroupID.x;")
    source=source.replace("source.words[lane * 8u +","source.words[group * 288u + 16u + lane * 8u +")
    source=source.replace("destination.words[lane * 2u + word]","destination.words[group * 96u + 16u + lane * 2u + word]")
    source=source.replace("shared float16_t originalA[256];","layout(set=0,binding=2,std430) writeonly buffer Writtenness { uint words[]; } completion;\nshared float16_t originalA[256];")
    output_store="destination.words[group * 96u + 16u + lane * 2u + word] = packHalf2x16(vec2(originalD[position],originalD[position + 1u]));"
    if source.count(output_store)!=1:
        raise ValueError("Frozen parent payload store shape changed")
    return source.replace(output_store,output_store+"\n        subgroupMemoryBarrierBuffer();\n        completion.words[group * 96u + 16u + lane * 2u + word] = 0x57420000u | (group * 64u + lane * 2u + word + 1u);")


def layout_proof():
    inputs={g*INPUT_STRIDE+INNER_WORDS+lane*8+word for g in range(64) for lane in range(32) for word in range(8)}
    outputs={g*OUTPUT_STRIDE+INNER_WORDS+lane*2+word for g in range(64) for lane in range(32) for word in range(2)}
    if len(inputs)!=64*256 or len(outputs)!=64*64 or not all(16<=index%288<272 for index in inputs) or not all(16<=index%96<80 for index in outputs):
        raise ValueError("Batch workgroup payload indexing is not disjoint and complete")
    return {"parent_single_warp":bridge.layout_proof(),"independent_workgroups":64,
            "unique_input_payload_words":len(inputs),"unique_output_payload_words":len(outputs),
            "input_inner_guard_words":64*32,"output_inner_guard_words":64*32,
            "marker_unique_tokens":len({marker_token(g,w) for g in range(64) for w in range(64)}),
            "marker_inner_guard_words":64*32,"marker_shader_indices_equal_payload_indices_in_separate_writable_buffer":True,
            "all_payload_slots_disjoint_and_complete":True,"all_shader_storage_accesses_skip_inner_guards":True,
            "shared_arrays_private_to_each_workgroup":True,"declared_shared_bytes_per_workgroup":2304}


def unpack_bundle(bundle):
    if len(bundle)<48:raise ValueError("Truncated batch header")
    magic,version,width,groups,ins,outs,marks,ni,no,nm,nr=struct.unpack_from("<8s10I",bundle)
    if (magic,version,width,ins,outs,marks)!=(MAGIC,2,32,INPUT_STRIDE,OUTPUT_STRIDE,OUTPUT_STRIDE) or not 1<=groups<=64 or nr!=groups or ni!=groups*ins or no!=groups*outs or nm!=groups*marks:
        raise ValueError("Invalid batch ABI/bounds")
    offset=48+groups*8
    if len(bundle)!=offset+(ni+no+nm)*4:raise ValueError("Invalid batch extent")
    return groups,bundle[offset:offset+ni*4],bundle[offset+ni*4:offset+(ni+no)*4],bundle[offset+(ni+no)*4:]


def classify_output(data,markers,bundle,cases):
    groups,_,reference,reference_markers=unpack_bundle(bundle)
    if len(data)!=groups*OUTPUT_STRIDE*4 or len(markers)!=len(data) or len(cases)!=groups:
        raise ValueError("Complete guarded batch output extent differs")
    guard=struct.pack("<16I",*([INNER_GUARD]*16))
    # Validate ALL guards and completion tokens before classifying any payload.
    for group in range(groups):
        start=group*OUTPUT_STRIDE*4
        actual_slot=data[start:start+OUTPUT_STRIDE*4]
        marker_slot=markers[start:start+OUTPUT_STRIDE*4]
        if actual_slot[:64]!=guard or actual_slot[-64:]!=guard or marker_slot[:64]!=guard or marker_slot[-64:]!=guard:
            raise ValueError("Per-workgroup output/marker guard changed")
        expected_tokens=struct.pack("<64I",*(marker_token(group,word) for word in range(64)))
        if marker_slot[64:-64]!=expected_tokens or reference_markers[start+64:start+320]!=expected_tokens:
            raise ValueError("Missing, partial or wrong-owner output writtenness marker")
    results=[]
    safe_bad=diagnostic_bad=0
    for group,entry in enumerate(cases):
        start=group*OUTPUT_STRIDE*4
        actual_slot=data[start:start+OUTPUT_STRIDE*4]
        expected_slot=reference[start:start+OUTPUT_STRIDE*4]
        result=bridge.classify(actual_slot[64:-64],expected_slot[64:-64])
        if entry["safe_exact"]:safe_bad+=result["mismatched_packed_words"]
        else:diagnostic_bad+=result["mismatched_packed_words"]
        results.append({"group":group,"name":entry["name"],"safe_exact":entry["safe_exact"],**result})
    return {"groups":groups,"complete_payload_packed_words":groups*64,"complete_payload_half_elements":groups*128,
            "safe_mismatched_packed_words":safe_bad,"diagnostic_mismatched_packed_words":diagnostic_bad,
            "all_inner_guards_unchanged":True,"groups_classified":results,
            "all_payload_words_marked":True,"verified_writtenness_markers":groups*64,
            "complete_guarded_marker_sha256":hashlib.sha256(markers).hexdigest(),
            "complete_guarded_output_sha256":hashlib.sha256(data).hexdigest()}


def validate_readback(input_frame,output_frame,marker_frame,bundle,cases):
    groups,inputs,_,_=unpack_bundle(bundle)
    outer=bytes([0xa5])*256
    for frame,size in ((input_frame,groups*INPUT_STRIDE*4),(output_frame,groups*OUTPUT_STRIDE*4),(marker_frame,groups*OUTPUT_STRIDE*4)):
        if len(frame)!=size+512 or frame[:256]!=outer or frame[-256:]!=outer:
            raise ValueError("Readback allocation outer guard/extent changed")
    captured_input=input_frame[256:-256]
    if captured_input!=inputs:
        raise ValueError("Readonly input readback changed")
    guard=struct.pack("<16I",*([INNER_GUARD]*16))
    for group in range(groups):
        slot=captured_input[group*INPUT_STRIDE*4:(group+1)*INPUT_STRIDE*4]
        if slot[:64]!=guard or slot[-64:]!=guard:
            raise ValueError("Readonly input inner guard changed")
    result=classify_output(output_frame[256:-256],marker_frame[256:-256],bundle,cases)
    result.update(all_three_allocation_outer_guards_unchanged=True,all_input_bytes_unchanged=True,
                  input_frame_sha256=hashlib.sha256(input_frame).hexdigest(),
                  output_frame_sha256=hashlib.sha256(output_frame).hexdigest(),
                  marker_frame_sha256=hashlib.sha256(marker_frame).hexdigest())
    return result


def prepare(output):
    output=o.private_output(output)
    output.mkdir(parents=True)
    bundle,cases=corpus()
    fixture=output/"finite-corpus.fixture"
    fixture.write_bytes(bundle)
    header=output/"fixture_binding.h"
    values=",".join(f"0x{byte:02x}" for byte in hashlib.sha256(bundle).digest())
    header.write_text("/* SPDX-License-Identifier: GPL-3.0-or-later */\n#define APPROVED_GROUP_COUNT 64u\nstatic const uint8_t approved_bundle_sha256[32]={"+values+"};\n")
    shader=output/"warp32-batch.comp"
    shader.write_text(shader_source())
    module=output/"warp32-batch.spv"
    source=output/"helper.c"
    source.write_bytes((ROOT/"src/probes/matrix_warp_batch.c").read_bytes())
    binary=output/"warp32-batch-helper"
    env=environment()
    sflags=["-V","--target-env","vulkan1.3","-Os"]
    flags=["-std=c11","-O2","-DNDEBUG","-Wall","-Wextra","-Werror"]
    run_process(["glslangValidator",*sflags,"-o",str(module),str(shader)],env,output,"compile-shader",30)
    run_process(["spirv-val","--target-env","vulkan1.3",str(module)],env,output,"validate-shader",15)
    run_process(["cc",*flags,str(source),"-o",str(binary),"-lvulkan"],env,output,"build-helper",30)
    selftest=json.loads(run_process([str(binary),"selftest"],env,output,"sha256-selftest",5))
    validation=json.loads(run_process([str(binary),"cpu",str(fixture)],env,output,"cpu-validation",10))
    if selftest!={"sha256_selftest":True,"vulkan_instance_created":False} or validation!={"profile":PROFILE,"cpu_validated":True,"groups":64,"safe_groups_independently_integer_validated":16,"vulkan_instance_created":False}:
        raise ValueError("Unexpected pre-instance CPU validation")
    _,_,reference,reference_markers=unpack_bundle(bundle)
    cpu_values=output/"cpu-reference-output.bin"
    cpu_markers=output/"cpu-reference-markers.bin"
    cpu_values.write_bytes(reference)
    cpu_markers.write_bytes(reference_markers)
    checked=json.loads(run_process([str(binary),"check",str(fixture),str(cpu_values),str(cpu_markers)],env,output,"cpu-output-validation",10))
    if checked!={"profile":PROFILE,"cpu_output_validated":True,"groups":64,"all_payload_words_marked":True,"verified_writtenness_markers":4096,"safe_mismatched_packed_words":0,"diagnostic_mismatched_packed_words":0,"vulkan_instance_created":False}:
        raise ValueError("Unexpected CPU writtenness validation")
    classify_output(reference,reference_markers,bundle,cases)
    _,inputs,_,_=unpack_bundle(bundle)
    outer=bytes([0xa5])*256
    input_frame=outer+inputs+outer
    output_frame=outer+reference+outer
    marker_frame=outer+reference_markers+outer
    for name,data in (("cpu-input-frame.bin",input_frame),("cpu-output-frame.bin",output_frame),("cpu-marker-frame.bin",marker_frame)):
        (output/name).write_bytes(data)
    framed=json.loads(run_process([str(binary),"frames",str(fixture),str(output/"cpu-input-frame.bin"),str(output/"cpu-output-frame.bin"),str(output/"cpu-marker-frame.bin")],env,output,"cpu-frame-validation",10))
    if framed.get("cpu_frame_validated") is not True or framed.get("all_payload_words_marked") is not True or framed.get("vulkan_instance_created") is not False:
        raise ValueError("Unexpected complete-frame CPU validation")
    validate_readback(input_frame,output_frame,marker_frame,bundle,cases)
    _,policy=controlled_driver_environment("native-safe",{})
    artifacts=(fixture,header,shader,module,source,binary)
    classes={key:sum(entry["reference_half_classes"][key] for entry in cases) for key in cases[0]["reference_half_classes"]}
    plan={"schema":SCHEMA,"profile":PROFILE,"header_magic":"OOMATB04","synthetic_only":True,
          "group_count":64,"safe_groups":16,"diagnostic_groups":48,"required_width":32,"workgroup":[32,1,1],"dispatch":[64,1,1],
          "maximum_workgroups":64,"logical_warps_per_workgroup":1,"input_stride_words":INPUT_STRIDE,"output_stride_words":OUTPUT_STRIDE,
          "layout_proof":layout_proof(),
          "input_guarded_bytes":64*INPUT_STRIDE*4,"output_guarded_bytes":64*OUTPUT_STRIDE*4,"outer_guard_bytes_each_end":256,"inner_guard_bytes_each_end_per_group":64,
          "marker_guarded_bytes":64*OUTPUT_STRIDE*4,"marker_stride_words":OUTPUT_STRIDE,
          "writtenness":{"writable_storage_binding":2,"buffer_count":3,"payload_word_markers":4096,"initial_payload_marker":0,"unique_owner_token_base":MARKER_BASE,"token_rule":"base OR (group*64 + lane*2 + word + 1)","payload_store_before_marker_with_subgroup_buffer_barrier":True,"all_markers_and_all_guards_required_before_any_numerical_classification":True},
          "readback_evidence":{"all_three_complete_guarded_buffer_frames_captured":True,"frame_span":"256-byte prefix + full descriptor range + 256-byte suffix","input_frame_bytes":74240,"output_frame_bytes":25088,"marker_frame_bytes":25088,"independent_python_outer_inner_guards_and_full_input_check_before_classification":True},
          "source_build_flags":flags,"shader_build_flags":sflags,"helper_source_sha256":digest(source),"helper_binary_sha256":digest(binary),
          "shader_source_sha256":digest(shader),"shader_module_sha256":digest(module),"fixture_binding_header_sha256":digest(header),"bundle_sha256":digest(fixture),
          "oracle_sha256":bridge.ORACLE_SHA256,"parent_bridge_sha256":BRIDGE_SHA256,"driver_profile":bridge.E5,"driver_policy":policy,
          "corpus_seed":SEED,"sampling":"SHA-256 of little-endian seed/group/matrix/element tuple; finite exponent remapping documented in source",
          "cases":cases,"reference_aggregate_half_classes":classes,"named_counterexample_reference":o.fixture_report()["counterexamples"],
          "artifacts":{str(path.relative_to(output)):digest(path) for path in artifacts},"dependencies":{name:digest(ROOT/name) for name in DEPENDENCIES},
          "compiler_versions":{tool:subprocess.run([tool,"--version"],check=True,capture_output=True,text=True,timeout=10).stdout.splitlines()[0] for tool in ("cc","glslangValidator","spirv-val")},
          "cpu_sha256_selftest":True,"native_safe_integer_oracles_independently_validated":16,"all_bundle_bytes_bound_to_compiled_sha256":True,
          "cpu_output_writtenness_acceptance_verified":True,
          "predecessor03":{"plan_sha256":"596eb1d0adb88a2b5c8998ac7d320f15ef5ce4be36b504c36dd3b8bf471eccd7","source_snapshot_sha256":"3f8746d956b98c1e27ec13f0e57e4e0a67078ed94dcda84b3538a54608068b82","properties_report_sha256":"58120281216a579eb6d3db7f53030f7dd4e2e7bbf2c4a397bb8aae92dc53a58c","compile_report_sha256":"eab3e06c5a922d4a81e689948dce56b0f70e576b50e642ed4c2be02b9f06673a","dispatch_executed":False,"arithmetic_acceptance":False,"known_gap":"Diagnostic differences did not require per-word completion evidence"},
          "gpu_admission_requirements":{"api_version":"1.3","vendor_id":32902,"device_id":22176,
              "minimum_compute_shared_bytes":2304,"minimum_compute_workgroup_count_x":64,"minimum_compute_invocations_and_x_size":32,
              "minimum_storage_buffer_range_bytes":73728,"minimum_storage_descriptors_per_stage_and_set":3,
              "storage_offset_alignment_must_divide_bytes":256,"required_compute_subgroup_size":32,
              "features":["robustBufferAccess","shaderFloat16","vulkanMemoryModel","subgroupSizeControl","computeFullSubgroups","cooperativeMatrix","pipelineExecutableInfo"],
              "shape":{"M":8,"N":8,"K":16,"A":"float16","B":"float16","C":"float32","result":"float32","scope":"subgroup","saturating":False}},
          "vulkan_instance_created":False,"pipeline_compiled":False,"gpu_dispatched":False,"max_dispatch_calls":1,"fence_timeout_ns":2000000000,"process_timeout_seconds":30,
          "limits":["Finite input classifications are computed by the pinned oracle; no NaN or infinity outcome is presumed.","Padded matrix arithmetic has no general original-console equivalence claim.","Every workgroup is an independent local32/physical32 single warp; no multi-warp workgroup or original game control flow is covered.","Safe cases require exact equality; diagnostics report bit/class differences without a production tolerance or NaN-payload contract.","No production shader, weights, assets, driver or renderer policy changed."]}
    (output/"plan.json").write_text(json.dumps(plan,indent=2)+"\n")
    return {"plan_sha256":digest(output/"plan.json"),"helper_source_sha256":digest(source),"helper_binary_sha256":digest(binary),"shader_module_sha256":digest(module),"bundle_sha256":digest(fixture),"groups":64,"safe_groups":16,"diagnostic_groups":48,"reference_aggregate_half_classes":classes,"vulkan_instance_created":False,"pipeline_compiled":False,"gpu_dispatched":False}


def reviewed(path,sha):
    output=Path(path).resolve()
    if digest(output/"plan.json")!=sha:raise ValueError("Reviewed batch plan hash differs")
    plan=json.loads((output/"plan.json").read_text())
    if plan.get("schema")!=SCHEMA or plan.get("profile")!=PROFILE or plan.get("driver_profile")!=bridge.E5 or plan.get("oracle_sha256")!=bridge.ORACLE_SHA256 or plan.get("parent_bridge_sha256")!=BRIDGE_SHA256:
        raise ValueError("Unrecognized batch profile or pinned reference")
    names={"finite-corpus.fixture","fixture_binding.h","warp32-batch.comp","warp32-batch.spv","helper.c","warp32-batch-helper"}
    if set(plan["artifacts"])!=names or set(plan["dependencies"])!=set(DEPENDENCIES) or plan.get("group_count")!=64 or plan.get("workgroup")!=[32,1,1] or plan.get("required_width")!=32:
        raise ValueError("Batch artifact/dependency inventory or bounds changed")
    for name,expected in plan["artifacts"].items():
        item=(output/name).resolve()
        if not item.is_relative_to(output) or digest(item)!=expected:raise ValueError("Frozen batch artifact changed")
    for name,expected in plan["dependencies"].items():
        if digest(ROOT/name)!=expected:raise ValueError("Frozen batch dependency changed")
    return output,plan


def execute(path,sha,driver,phase):
    output,plan=reviewed(path,sha)
    if phase not in ("properties","compile","dispatch"):raise ValueError("Select one reviewed runtime phase")
    identity=driver_identity(driver)
    if any(identity[key]!=value for key,value in bridge.E5.items()):raise ValueError("Batch requires exact E5 ICD/library")
    env,policy=controlled_driver_environment("native-safe",environment())
    if policy!=plan["driver_policy"]:raise ValueError("Native-safe policy changed")
    ensure_no_games()
    env.update(VK_ICD_FILENAMES=str(driver.resolve()),VK_DRIVER_FILES=str(driver.resolve()),MESA_SHADER_CACHE_DISABLE="true",MESA_SHADER_CACHE_DIR=str(output/"cache"))
    with (output/(phase+"-started.json")).open("x") as target:json.dump({"reviewed_plan_sha256":sha,"profile":PROFILE,"driver":identity,"phase":phase},target)
    result=json.loads(run_process([str(output/"warp32-batch-helper"),phase,str(output/"finite-corpus.fixture"),str(output/"warp32-batch.spv"),str(output/(phase+".bin")),str(output/(phase+"-markers.bin")),str(output/(phase+"-input.bin"))],env,output,phase,30))
    if result.get("profile")!=PROFILE or result.get("phase")!=phase or result.get("requested_width")!=32 or result.get("groups")!=64:raise ValueError("Runtime batch metadata differs")
    if phase!="properties" and (result.get("actual_width")!=32 or result.get("width_source")!="pipeline_executable_properties"):raise ValueError("Batch requires actual subgroup32")
    if phase=="dispatch":
        if result.get("dispatches")!=1 or not result.get("guards_and_inputs_unchanged") or not result.get("output_checked") or result.get("full_payload_packed_word_count")!=4096 or result.get("full_payload_half_element_count")!=8192 or result.get("guarded_output_word_count")!=6144 or not result.get("writtenness_checked") or result.get("all_payload_words_marked") is not True or result.get("verified_writtenness_markers")!=4096:raise ValueError("Batch guard/input/writtenness/dispatch contract failed")
        check=validate_readback((output/"dispatch-input.bin").read_bytes(),(output/"dispatch.bin").read_bytes(),(output/"dispatch-markers.bin").read_bytes(),(output/"finite-corpus.fixture").read_bytes(),plan["cases"])
        if check["safe_mismatched_packed_words"] or check["safe_mismatched_packed_words"]!=result.get("safe_mismatched_packed_words") or check["diagnostic_mismatched_packed_words"]!=result.get("diagnostic_mismatched_packed_words"):raise ValueError("Complete batch output comparisons disagree")
        result["classification"]=check
    (output/(phase+"-report.json")).write_text(json.dumps(result,indent=2)+"\n")
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
    run.add_argument("--phase",choices=("properties","compile","dispatch"),required=True)
    args=parser.parse_args()
    try:
        result=prepare(args.out_dir) if args.command=="prepare" else execute(args.prepared,args.reviewed_plan_sha256,args.driver,args.phase)
        print(json.dumps(result,indent=2))
    except (OSError,ValueError,KeyError,subprocess.SubprocessError) as exc:
        print(str(exc),file=sys.stderr)
        return 2
    return 0


if __name__=="__main__":raise SystemExit(main())
