#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Check bounded native-mesh input capture without executing Vulkan or game code."""
import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import shutil
import struct
import subprocess
import tempfile

HERE = Path(__file__).resolve().parent
PATCH = HERE / "mesh-input-profile96.patch"
SPEC = importlib.util.spec_from_file_location("mesh_dispatch_checker", HERE / "check_dispatch_parameters.py")
DP = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(DP)
BASE = (*DP.BASE, "dispatch-parameters.patch", "binding-provenance.patch", "mesh-input-capture.patch", "mesh-warp32-lowering.patch")
PROGRAM = "68642bb810ee3e7c"
VARIANT = "3f3f5b29054673da"
DIGEST = "999c8511db93d486543421dde7350e3d0034faa834ef3edaf3553dcdc9b55d93"
D8_HEADER_SHA256 = "e129b121f6a77673852780da3d0d4510f26672912d6dd5f96bf64376203022a6"
NATIVE_V2_SHA256 = "8d0cc053103ccf90b51e6c55c6e7ee39292636f03b85e16d74bd8d8d1221854b"
require = DP.require


def source_audit(before, after, memory):
    pattern = r"\bvkCmdDrawMeshTasks(?:IndirectCount|Indirect)?EXT\([^;]+;"
    require(re.findall(pattern, before) == re.findall(pattern, after) and len(re.findall(pattern, after)) == 5,
            "Actual native mesh commands or arguments changed")
    require(DP.body(before, "void pin_subgroup(") == DP.body(after, "void pin_subgroup("), "Subgroup policy changed")
    draw = DP.body(after, "void draw(")
    selected = draw.index("memory_mesh96_inputs_wanted(")
    split = draw.index("end_pass(cmd);", selected)
    copy = draw.index("memory_pt_snapshot_record(", split)
    begin = draw.index("begin_pass(cmd, at);", copy)
    query = draw.index("StatsQuery stats_query(", begin)
    require(selected < split < copy < begin < query, "Copies must precede original rendering and current StatsQuery")
    pre = draw[draw.rindex("auto event=oo_mesh_event(",0,selected):begin]
    require("!same_desc&&dw().prog==&p" in pre, "Fresh actual descriptor ownership is not checked")
    require(all(token in pre for token in ("!at.has_block_images", "!dump_on()", "!probe_on()", "!gprof_on()", "!stats_frame_now()")),
            "Unsupported rendering/debug boundary is not explicit")
    require("mesh_conv_buf,mesh_conv_off" in pre and "d.groups" not in pre, "Launch must name actual converted buffer, not CPU grid")
    observer = DP.body(after, "void oo_graphics_identity_record(")
    require("memory_mesh96_inputs_confirm(cmd,oo_mesh_event(" in observer, "Actual command site confirmation missing")
    bind = DP.body(after, "bool bind_descriptors(")
    require(not re.search(r"vkCmd(?:Copy|Fill|Dispatch|Draw)", bind), "Descriptor binding gained resource-writing commands")
    # These command categories must not appear on the ordinary path between preparation and actual native mesh calls.
    critical = draw[begin:draw.index("const auto oo_record_mesh =", begin)]
    require(not re.search(r"vkCmd(?:Copy|Fill|Dispatch|Update|Clear)", critical), "Intervening resource-writing command in confirmed draw path")
    ring = DP.body(after, "bool mesh_indirect_convert(")
    require("((oo_mesh::enabled()||oo_mesh96::enabled())?VK_BUFFER_USAGE_TRANSFER_SRC_BIT:0)" in ring, "Launch owner lacks opt-in transfer-source usage")
    allocation = DP.body(memory, "void oo_allocation_record(VkCommandBuffer cmd) {")
    require('"mesh6864-launch-input"' in allocation and "memory_allocation_retain_buffer(buffer,&owner)" in allocation,
            "Launch must retain and copy its true VMA owner as a separate catalog row")
    extra = allocation[allocation.index("if(oo_mesh96_pending.selected"):allocation.index("oo_image_prepare(cmd)")]
    require("state.descriptors.push" not in extra, "Launch input fabricated as a descriptor")
    require("oo_page_pending.request.mesh" in DP.body(memory, "void memory_allocation_after("), "Later compute could manufacture unrelated mesh AFTER payload")
    collect = DP.body(memory, "void oo_allocation_collect(const oo_pt::Request& request")
    require(collect.index("const bool before_ok=invalidate_mapped_checked") < collect.index("oo_mesh96::write_completed("), "Mesh snapshots exported before mapped-memory invalidation")
    require("state.descriptors,lifetimes,dev.null_descriptor)" in collect, "Actual enabled nullDescriptor feature is not retained")
    require('namespace mkwvk::oo_mesh96' in DP.header_from_patch(PATCH,"mesh_input_profile96.h"),"Separate explicit profile helper missing")
    require('p.key==oo_mesh::kProgram && p.variant==oo_mesh::kVariant' in after and 'p.key==oo_mesh96::kProgram && p.variant==oo_mesh96::kVariant' in after,"D8 hardfilter or separate target selector changed")
    return {"d8_default_hardfilter_retained":True,"five_native_calls_unchanged": True, "outside_rendering_before_query": True, "fresh_descriptor_ownership_required": True,
            "actual_site_confirmation": True, "no_intervening_buffer_write_commands": True, "launch_owner_retained_separately": True,
            "later_compute_after_copy_blocked": True}


def check_source(checkout):
    checkout = Path(checkout).resolve()
    require(subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=checkout, text=True).strip() == DP.PIN, "Pinned source required")
    with tempfile.TemporaryDirectory() as td:
        env = dict(os.environ, GIT_INDEX_FILE=str(Path(td) / "index"))
        subprocess.run(["git", "read-tree", DP.PIN], cwd=checkout, env=env, check=True)
        hashes = []
        for name in BASE:
            patch = HERE / name;hashes.append({"patch": name, "sha256": hashlib.sha256(patch.read_bytes()).hexdigest()})
            subprocess.run(["git", "apply", "--cached", str(patch)], cwd=checkout, env=env, check=True)
        require(hashlib.sha256((HERE/"mesh-warp32-lowering.patch").read_bytes()).hexdigest()==NATIVE_V2_SHA256,"Native V2 dependency changed")
        d8_before=subprocess.check_output(["git","show",":gpu/vk/mesh_input_capture.h"],cwd=checkout,env=env)
        require(hashlib.sha256(d8_before).hexdigest()==D8_HEADER_SHA256,"Frozen d8 metadata dependency changed")
        before = subprocess.check_output(["git", "show", ":gpu/vk/vk_draw.cpp"], cwd=checkout, env=env, text=True)
        subprocess.run(["git", "apply", "--cached", "--check", str(PATCH)], cwd=checkout, env=env, check=True)
        subprocess.run(["git", "apply", "--cached", str(PATCH)], cwd=checkout, env=env, check=True)
        require(subprocess.check_output(["git","show",":gpu/vk/mesh_input_capture.h"],cwd=checkout,env=env)==d8_before,"D8 helper changed")
        after = subprocess.check_output(["git", "show", ":gpu/vk/vk_draw.cpp"], cwd=checkout, env=env, text=True)
        memory = subprocess.check_output(["git", "show", ":gpu/vk/vk_memory.cpp"], cwd=checkout, env=env, text=True)
    return {"eleven_patch_chain_apply_check": True, "base_patches": hashes, **source_audit(before, after, memory)}


def check_helper(compiler=None):
    compiler = compiler or shutil.which("clang++") or shutil.which("g++")
    require(bool(compiler), "C++20 compiler required")
    with tempfile.TemporaryDirectory(prefix="oo-mesh-input-") as td:
        work = Path(td);headers = work / "gpu/vk";headers.mkdir(parents=True)
        for patch, name in (("compute-identity.patch", "compute_identity.h"), ("page-table-snapshot.patch", "page_table_capture.h"),
                            ("allocation-capture.patch", "allocation_capture.h"), ("graphics-identity.patch", "graphics_identity.h"),
                            ("dispatch-parameters.patch", "dispatch_parameters.h")):
            (headers / name).write_text(DP.header_from_patch(HERE / patch, name))
        d8=DP.header_from_patch(HERE/"mesh-input-capture.patch", "mesh_input_capture.h")
        require(hashlib.sha256(d8.encode()).hexdigest()==D8_HEADER_SHA256,"Frozen d8 metadata dependency changed")
        (headers / "mesh_input_capture.h").write_text(d8)
        (headers / "mesh_input_profile96.h").write_text(DP.header_from_patch(PATCH,"mesh_input_profile96.h"))
        subprocess.run(["git", "apply", "--include=gpu/vk/page_table_capture.h", str(HERE/"mesh-input-capture.patch")], cwd=work, check=True)
        source = work / "test.cpp"
        source.write_text(r'''#include "gpu/vk/mesh_input_profile96.h"
int main(int argc,char**argv) {
    using namespace mkwvk;using namespace oo_mesh96;
    auto demand=[](bool value){if(!value)std::exit(2);};
    const std::string mode=argc>1?argv[1]:"valid";if(mode=="disabled")return enabled()?2:0;if(mode=="enabled")return enabled()?0:2;
    Event e;e.command=1;e.key=kProgram;e.variant=kVariant;e.layout=9;e.part=7;e.logical=8;
    e.argument_buffer=3;e.argument_offset=12;e.count=1;e.stride=12;e.required_size=32;
    e.indirect=true;e.converted=true;e.fresh_descriptors=true;e.safe_boundary=true;
    e.stage_index=0;e.nvn_stage=7;e.vk_stage=128;e.stage_count=2;e.digest=kDigest;
    Layout l;l.handle=9;for(uint32_t i=0;i<9;++i)l.bindings.push_back({i,i<4?6u:7u,i==8?3u:1u,128});oo_mesh96::note_layout(l,kProgram,kVariant);
    oo_pt::Request r;r.key=kProgram;r.variant=kVariant;r.shader_digest=kDigest;r.recording_part=7;r.logical_value=8;r.indirect=true;r.mesh=true;
    oo_mesh96::prepare(e);auto c=select(1,r,64);demand(c.selected&&c.layout_valid);oo_mesh96::confirm(c,e);demand(c.confirmed);
    auto changed=e;changed.argument_offset++;oo_mesh96::confirm(c,changed);demand(!c.confirmed);oo_mesh96::confirm(c,e);
    if(mode=="reuse") {e.fresh_descriptors=false;oo_mesh96::prepare(e);c=select(1,r,64);oo_mesh96::confirm(c,e);}
    if(mode=="count") {e.counter=1;oo_mesh96::prepare(e);c=select(1,r,64);oo_mesh96::confirm(c,e);}
    if(mode=="multi") {e.count=2;oo_mesh96::prepare(e);c=select(1,r,64);oo_mesh96::confirm(c,e);}
    if(mode=="unconfirmed")c.confirmed=false;
    std::array<uint8_t,256> input{};for(unsigned i=0;i<input.size();++i)input[i]=uint8_t(i*7);
    std::array<uint8_t,64> launch{};const uint32_t grid[3]{10,2,3};
    for(unsigned i=0;i<3;++i)for(unsigned j=0;j<4;++j)launch[12+4*i+j]=uint8_t(grid[i]>>(8*j));
    if(mode=="zero")for(unsigned j=0;j<4;++j)launch[12+j]=0;
    std::vector<oo_alloc::Row> rows(2);rows[0].buffer=2;rows[0].size=256;rows[1].buffer=3;rows[1].size=64;
    std::vector<Payload> payloads{{input.data(),256,true},{launch.data(),64,true}};
    if(mode=="invalidated")payloads[0].invalidated=false;
    std::vector<oo_alloc::Descriptor> descriptors;
    for(unsigned b=0;b<9;++b)for(unsigned element=0;element<(b==8?3u:1u);++element)
        descriptors.push_back({b,element,b<4?6u:7u,2,uint64_t(b*4+element),32});
    const bool has_nulls=mode.rfind("null",0)==0;
    if(has_nulls)for(size_t i=9;i<11;++i){descriptors[i].buffer=0;descriptors[i].offset=0;descriptors[i].range=UINT64_MAX;}
    if(mode=="null_offset")descriptors[10].offset=4;
    if(mode=="null_range")descriptors[10].range=32;
    if(mode=="null_position"){descriptors[0].buffer=0;descriptors[0].offset=0;descriptors[0].range=UINT64_MAX;}
    const bool null_feature=has_nulls&&mode!="null_feature_off";
    if(mode=="missing")descriptors.pop_back();
    if(mode=="range")descriptors[0].range=300;
    if(mode=="whole")descriptors[0].range=UINT64_MAX;
    demand(!write_completed(c,r,99,rows,payloads,descriptors,{},null_feature));r.submitted_sequence=15;
    demand(!write_completed(c,r,14,rows,payloads,descriptors,{},null_feature));
    demand(write_completed(c,r,15,rows,payloads,descriptors,{{2,100},{3,101}},null_feature));
    demand(!write_completed(c,r,15,rows,payloads,descriptors,{},null_feature));return 0;
}
''')
        executable = work / "test"
        subprocess.run([compiler, "-std=c++20", "-O2", "-DNDEBUG", "-Wall", "-Wextra", "-Werror", "-pthread", str(source), "-o", str(executable)], check=True, timeout=60)
        base = {k:v for k,v in os.environ.items() if not k.startswith("RX_VK_OO_")}
        for mode in ("valid", "whole", "zero", "reuse", "count", "multi", "unconfirmed", "invalidated", "missing", "range", "null", "null_feature_off", "null_offset", "null_range", "null_position"):
            output=work/mode;output.mkdir()
            env=dict(base,RX_VK_OO_MESH_PROFILE="6864-position10-96/v1",RX_VK_OO_MESH_INPUTS="1",RX_VK_OO_ALLOC_CAPTURE="1",RX_VK_OO_IDENTITY_FILE=str(work/"identity"),
                     RX_VK_OO_IDENTITY_PROGRAM=PROGRAM,RX_VK_OO_IDENTITY_VARIANT=VARIANT,RX_VK_OO_PT_SNAPSHOT_DIR=str(output),
                     RX_VK_OO_PT_SNAPSHOT_SHA256=DIGEST,RX_VK_OO_GRAPHICS_IDENTITY_FILE=str(work/"graphics"),RX_VK_OO_GRAPHICS_PROGRAMS=PROGRAM)
            subprocess.run([str(executable),mode],env=env,check=True,timeout=10)
            d=json.loads((output/"mesh-inputs-6864.json").read_text())
            require(d.get("profile")=="6864-position10-96/v1" and d.get("runtime_execution_authorized") is False and d.get("equivalence_proven") is False,"Profile or unchecked execution/equivalence claim differs")
            valid=mode in ("valid","whole","zero","null")
            require(d["mesh_stage_inputs_present"] == valid and d["replay_complete"] is False, "Unsupported/incomplete mesh input marked complete")
            require(d["event"]["recording_sequence"]==15 and d["event"]["logical_value"]==8 and d["event"]["selected_occurrence"]==64, "Completion/occurrence confused")
            require(not any(d["coverage"].values()), "Rendering/CPU/alias coverage fabricated")
            if valid:
                launch=(output/"mesh6864-indirect.bin").read_bytes();require(len(launch)==12 and hashlib.sha256(launch).hexdigest()==d["launch"]["snapshot"]["sha256"], "GPU launch payload differs")
                require(d["launch"]["group_count"]==list(struct.unpack("<3I",launch)), "Launch grid reconstructed from CPU metadata")
                require(d["launch"]["zero_grid_no_execution"] == (mode=="zero"), "Zero native launch not explicit")
                require(len(d["bindings"])==11, "Required mesh descriptor slot missing")
                require(d["features"]["nullDescriptor"] == mode.startswith("null"),"Actual nullDescriptor feature not retained")
                for slot in d["bindings"]:
                    if slot["null_descriptor"]:
                        require(mode=="null" and slot["binding"]==8 and slot["element"] in (1,2),"Unexpected null slot")
                        require(all(slot[field] is None for field in ("snapshot","range_bytes","allocation_row","buffer_lifetime","backing_generation")),"Null slot fabricated payload/owner")
                        require(slot["buffer_handle"]=="0000000000000000" and slot["original_buffer_offset"]==0 and slot["descriptor_range"]==(1<<64)-1,"Null framing changed")
                        require(not (output/f"mesh6864-binding-8-{slot['element']}.bin").exists(),"Null slot wrote fake bytes")
                        continue
                    blob=(output/slot["snapshot"]["path"]).read_bytes();offset=slot["original_buffer_offset"]
                    expected=bytes(i*7%256 for i in range(256))[offset:offset+slot["range_bytes"]]
                    require(blob==expected and hashlib.sha256(blob).hexdigest()==slot["snapshot"]["sha256"], "Exact descriptor slice differs")
                    require(slot["buffer_lifetime"]==100 and slot["backing_generation"] is None, "Ownership/generation evidence invented")
        subprocess.run([str(executable),"disabled"],env=base,check=True,timeout=10)
        counter=0
        def fresh_env():
            nonlocal counter
            counter+=1;directory=work/("negative-"+str(counter));directory.mkdir()
            result=env.copy();result["RX_VK_OO_PT_SNAPSHOT_DIR"]=str(directory)
            subprocess.run([str(executable),"enabled"],env=result,check=True,timeout=10)
            return result
        for value in (None,"","6864","6864-position10-96/v2","d8-position9-128/v1"):
            bad=fresh_env()
            if value is None:bad.pop("RX_VK_OO_MESH_PROFILE",None)
            else:bad["RX_VK_OO_MESH_PROFILE"]=value
            subprocess.run([str(executable),"disabled"],env=bad,check=True,timeout=10)
        for key,value in (("RX_VK_OO_IDENTITY_PROGRAM","d8e59341cf4f629c"),("RX_VK_OO_IDENTITY_VARIANT","0c8f08626a9d98a4"),("RX_VK_OO_PT_SNAPSHOT_SHA256","835aa59799c6ecb621034d946e2fa4eadb1aa2407d293321ae3e3052882bc9b3")):
            bad=fresh_env();bad[key]=value;subprocess.run([str(executable),"disabled"],env=bad,check=True,timeout=10)
    return {"optimized_helper_compile":True,"eleven_exact_descriptor_slices":True,"gpu_launch_twelve_bytes":True,"exact_opt_in_and_identity_negatives_checked":True,
            "same_recording_completion_checked":True,"defined_null_positions_preserved_without_bytes":True,"invalid_null_feature_framing_positions_refused":True,"unsupported_reuse_count_multidraw_missing_ranges_refused":True,"zero_grid_explicit":True,"gpu_execution":False}


def validate_capture(directory):
    directory=Path(directory)
    require(not directory.is_symlink() and directory.is_dir(), "Regular capture directory required")
    document=json.loads(DP.regular(directory/"mesh-inputs-6864.json",131072))
    require(document.get("schema")=="open-ocarina.mesh-input-profile96/v1" and document.get("replay_complete") is False,
            "Unsupported or overstated mesh capture")
    require(document.get("mesh_stage_inputs_present") is True and document.get("failures")==[], "Required mesh input snapshots incomplete")
    require(document.get("profile")=="6864-position10-96/v1" and document.get("runtime_execution_authorized") is False and document.get("equivalence_proven") is False,"Explicit profile or unchecked legacy execution/equivalence state differs")
    event=document["event"]
    require([event.get(k) for k in ("program","variant","spirv_sha256")]==[PROGRAM,VARIANT,DIGEST] and
            [event.get(k) for k in ("stage_index","nvn_stage","vk_stage")]==[0,7,128] and event.get("actual_site_confirmed") is True,
            "Exact native mesh stage/command identity missing")
    sequence=DP.integer(event.get("recording_sequence"),"recording sequence",1)
    DP.integer(event.get("completed_recording_sequence"),"completed sequence",sequence)
    DP.integer(event.get("selected_occurrence"),"occurrence",1,65536)
    DP.integer(event.get("recording_part"),"recording part",1);DP.integer(event.get("logical_value"),"logical submission value",1)
    require(document.get("push_constants_not_used") is True, "Pinned mesh push-constant contract differs")
    launch=document["launch"]
    require(launch.get("command")=="vkCmdDrawMeshTasksIndirectEXT" and type(launch.get("draw_count")) is int and launch["draw_count"]==1 and
            type(launch.get("stride")) is int and launch["stride"]==12 and launch.get("converted") is True,
            "Only one converted indirect record is supported")
    def snapshot(record,name,maximum):
        require(isinstance(record,dict) and record.get("path")==name,"Noncanonical snapshot path")
        data=DP.regular(directory/name,maximum)
        require(type(record.get("size")) is int and record["size"]==len(data) and hashlib.sha256(data).hexdigest()==record.get("sha256"),
                "Snapshot size or digest differs")
        return data
    raw=snapshot(launch["snapshot"],"mesh6864-indirect.bin",12)
    require(len(raw)==12,"Indirect GPU snapshot must contain exactly12bytes")
    grid=list(struct.unpack("<3I",raw));require(launch.get("group_count")==grid,"GPU launch words differ from declared grid")
    require(launch.get("zero_grid_no_execution")== (0 in grid),"Zero native grid must explicitly forbid execution")
    layouts=document["descriptor_layout"]
    require(isinstance(layouts,list) and 9<=len(layouts)<=256,"Actual mesh descriptor layout missing")
    for binding in range(9):
        require(layouts[binding]=={"set":0,"binding":binding,"type":6 if binding<4 else 7,"count":3 if binding==8 else 1,"stage_flags":128},
                "Required descriptor layout/count/stage differs")
    slots=document["bindings"]
    expected=[(b,e) for b in range(9) for e in range(3 if b==8 else 1)]
    require(isinstance(slots,list) and [(s.get("binding"),s.get("element")) for s in slots]==expected,"All eleven exact mesh slots required")
    null_feature=document.get("features",{}).get("nullDescriptor")
    require(type(null_feature) is bool,"Actual enabled nullDescriptor feature state required")
    total=12;null_count=0
    for slot in slots:
        b,e=slot["binding"],slot["element"]
        require(type(slot.get("set")) is int and slot["set"]==0 and slot.get("type")== (6 if b<4 else 7),"Descriptor identity/type differs")
        require(type(slot.get("null_descriptor")) is bool,"Explicit descriptor nullness required")
        if slot["null_descriptor"]:
            require((b,e) in ((8,1),(8,2)) and null_feature is True,"Unsupported or feature-disabled null descriptor")
            require(slot.get("buffer_handle")=="0000000000000000" and type(slot.get("original_buffer_offset")) is int and slot["original_buffer_offset"]==0 and
                    type(slot.get("descriptor_range")) is int and slot["descriptor_range"]==(1<<64)-1,"Null descriptor framing differs from Vulkan requirements")
            require(all(field in slot and slot[field] is None for field in ("range_bytes","snapshot","allocation_row","buffer_lifetime","backing_generation")),"Null descriptor fabricated payload or ownership")
            path=directory/f"mesh6864-binding-{b}-{e}.bin"
            require(not path.exists() and not path.is_symlink(),"Null descriptor must not have a payload artifact")
            null_count+=1;continue
        require(isinstance(slot.get("buffer_handle"),str) and re.fullmatch("[0-9a-f]{16}",slot["buffer_handle"]) and int(slot["buffer_handle"],16)!=0,"Nonnull descriptor has null or malformed buffer")
        size=DP.integer(slot.get("range_bytes"),"descriptor range",1,1<<30);total+=size
        require(total<=1<<30,"Exact mesh slice budget exceeded")
        data=snapshot(slot["snapshot"],f"mesh6864-binding-{b}-{e}.bin",size)
        require(len(data)==size,"Exact descriptor range is truncated")
        DP.integer(slot.get("original_buffer_offset"),"original descriptor offset")
        if slot.get("buffer_lifetime") is not None: DP.integer(slot["buffer_lifetime"],"buffer lifetime",1)
        require(slot.get("backing_generation") is None,"Generation must be resolved from linked provenance, never fabricated")
    require(document.get("coverage")=={"cpu_writers_quiesced":False,"import_aliases_proven":False,"fragment_resources":False,
                                        "draw_equivalent_inputs_proven":False,"whole_rendering_equivalence":False},"Coverage overstated")
    associations=document["associations"]
    require(isinstance(associations,list) and [r.get("path") for r in associations]==["manifest.json","allocations.json","binding-provenance.json"],"Same-recording associations required")
    associated={}
    for row in associations:
        data=DP.regular(directory/row["path"],16<<20)
        require(hashlib.sha256(data).hexdigest()==row.get("sha256"),"Associated manifest integrity differs")
        associated[row["path"]]=json.loads(data)
    allocations=associated["allocations.json"];provenance=associated["binding-provenance.json"];table=associated["manifest.json"]
    require(allocations.get("schema")=="open-ocarina.gpu-allocations/v2" and allocations.get("after_recorded") is False,
            "Mesh BEFORE-only capture must not adopt a later compute AFTER")
    require(provenance.get("schema")=="open-ocarina.binding-provenance/v1","Binding provenance schema differs")
    require(type(allocations.get("null_descriptor_enabled")) is bool and allocations["null_descriptor_enabled"] is null_feature,
            "Null feature differs from authoritative captured device state")
    for manifest in (allocations,provenance):
        for field in ("program","variant","spirv_sha256","recording_part","recording_sequence","completed_recording_sequence","selected_occurrence"):
            require(manifest.get(field)==event[field],"Associated stage/recording/occurrence differs")
    require(table["pipeline"]=={k:event[k] for k in ("program","variant","spirv_sha256")} and
            table["submission"]=={k:event[k] for k in ("recording_part","logical_value","recording_sequence","completed_recording_sequence")},"Table association differs")
    rows=allocations["buffers"];descriptors=allocations["descriptors"]
    checked_files={}
    def view_digest(owner,index,offset,size):
        record=owner.get("before")
        name=f"buffer-{index}-before.bin"
        require(isinstance(record,dict) and record.get("artifact")==name,"Authoritative GPU BEFORE payload missing")
        path=directory/name
        require(not path.is_symlink() and path.is_file() and path.stat().st_size==owner["size"],"GPU BEFORE owner extent differs")
        require(0<=offset<=owner["size"] and 0<size<=owner["size"]-offset,"Snapshot view exceeds owner")
        if name not in checked_files:
            digest=hashlib.sha256()
            with path.open("rb") as stream:
                for chunk in iter(lambda:stream.read(1<<20),b""):digest.update(chunk)
            require(digest.hexdigest()==record.get("sha256"),"GPU BEFORE payload digest differs")
            checked_files[name]=True
        digest=hashlib.sha256()
        with path.open("rb") as stream:
            stream.seek(offset);remaining=size
            while remaining:
                chunk=stream.read(min(remaining,1<<20));require(bool(chunk),"Truncated authoritative BEFORE view")
                digest.update(chunk);remaining-=len(chunk)
        return digest.hexdigest()
    launch_rows=[(i,row) for i,row in enumerate(rows) if row.get("buffer")==launch.get("original_buffer")]
    require(len(launch_rows)==1,"Unique true launch owner missing")
    launch_index,launch_owner=launch_rows[0]
    launch_offset=DP.integer(launch.get("original_buffer_offset"),"launch owner offset")
    require(view_digest(launch_owner,launch_index,launch_offset,12)==launch["snapshot"]["sha256"],"Launch bytes differ from authoritative GPU BEFORE copy")
    for slot in slots:
        matches=[d for d in descriptors if d.get("binding")==slot["binding"] and d.get("element")==slot["element"]]
        if slot["null_descriptor"]:
            require(len(matches)==1 and matches[0].get("type")==slot["type"] and matches[0].get("buffer")=="0000000000000000" and
                    type(matches[0].get("offset")) is int and matches[0]["offset"]==0 and type(matches[0].get("range")) is int and matches[0]["range"]==(1<<64)-1,
                    "Null descriptor differs from authoritative submitted write")
            require(not any(row.get("buffer")=="0000000000000000" for row in rows) and
                    not any(obj.get("kind")=="buffer" and obj.get("handle")=="0000000000000000" for obj in provenance["objects"]),
                    "Null descriptor fabricated an allocation or provenance owner")
            continue
        index=DP.integer(slot.get("allocation_row"),"allocation row",0,len(rows)-1);owner=rows[index]
        require(owner.get("id")==index and owner.get("buffer")==slot.get("buffer_handle"),"Mesh input owner differs")
        require(len(matches)==1 and matches[0].get("type")==slot["type"] and matches[0].get("buffer")==slot["buffer_handle"] and
                matches[0].get("offset")==slot["original_buffer_offset"] and matches[0].get("range")==slot.get("descriptor_range"),"Captured descriptor differs from actual write")
        expected_range=owner["size"]-slot["original_buffer_offset"] if slot["descriptor_range"]==(1<<64)-1 else slot["descriptor_range"]
        require(slot["range_bytes"]==expected_range,"Exact Vulkan range/ArrayLength differs")
        require(view_digest(owner,index,slot["original_buffer_offset"],slot["range_bytes"])==slot["snapshot"]["sha256"],"Descriptor slice differs from authoritative GPU BEFORE copy")
        objects=[o for o in provenance["objects"] if o.get("kind")=="buffer" and o.get("handle")==slot["buffer_handle"] and o.get("catalog_id")==index]
        require(len(objects)==1 and objects[0].get("tracked") is True and objects[0].get("lifetime_id")==slot.get("buffer_lifetime"),"Resource lifetime missing or differs")
    return {"mesh_stage_inputs_present":True,"slots":11,"snapshot_slots":11-null_count,"null_slots":null_count,"group_count":grid,"nonzero_grid_guard_passed":0 not in grid,"runtime_execution_authorized":False,"equivalence_proven":False,"replay_complete":False,
            "unresolved":["backing_generation_and_alias_normalization","cpu_writers_quiesced","draw_equivalent_input_proof","whole_graphics_resources_and_outputs"]}


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument("--checkout",type=Path);parser.add_argument("--compiler");parser.add_argument("--capture-dir",type=Path)
    args=parser.parse_args()
    try:
        report=validate_capture(args.capture_dir) if args.capture_dir else check_helper(args.compiler)
        if args.checkout:report.update(check_source(args.checkout))
        print(json.dumps(report,indent=2))
    except (OSError,ValueError,KeyError,TypeError,subprocess.SubprocessError) as error:parser.exit(1,str(error)+"\n")


if __name__=="__main__":main()
