#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""CPU-only optional profile96 overlay checks; no Vulkan loader or game execution."""
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
import sys
import tempfile

HERE = Path(__file__).resolve().parent
PATCH = HERE / 'mesh-warp32-profile96.patch'
SPEC = importlib.util.spec_from_file_location('frozen_native_mesh_checker', HERE / 'check_mesh_warp32_lowering.py')
FROZEN = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(FROZEN)
BASE = (*FROZEN.BASE, 'mesh-warp32-lowering.patch', 'mesh-input-profile96.patch')
PINNED = {'mesh-warp32-lowering.patch': '8d0cc053103ccf90b51e6c55c6e7ee39292636f03b85e16d74bd8d8d1221854b',
          'mesh-input-profile96.patch': 'd87391e4c8ff1130b2c918da4d6e38c45420acd0f43b12b6ea47a33a3b27f51b'}
FILES = ('gpu/vk/mesh_warp32_profiles.h', 'gpu/vk/vk_shader.cpp', 'gpu/vk/vk_shader.h',
         'gpu/vk/vk_progcache.cpp', 'gpu/vk/vk_draw.cpp', 'nx2/build.py')
require = FROZEN.require


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def body(source, marker):
    start = source.index(marker)
    brace = source.index('{', start)
    depth, end = 1, brace + 1
    while depth:
        depth += (source[end] == '{') - (source[end] == '}')
        end += 1
    return source[start:end]


def header():
    return FROZEN.added_file('gpu/vk/mesh_warp32_profiles.h', PATCH.read_text())


HEADER_TEST = r'''#include "mesh_warp32_profiles.h"
#include <array>
#include <cstdint>
using namespace mkwvk::oo_mesh_warp32_profiles;
struct Subgroup {uint32_t requiredSubgroupSizeStages=128,minSubgroupSize=8,maxSubgroupSize=32;};
struct Mesh {
 uint32_t maxMeshWorkGroupInvocations=32,maxMeshWorkGroupSize[3]={32,1,1};
 uint32_t maxMeshOutputVertices=128,maxMeshOutputPrimitives=128,maxMeshOutputComponents=128;
 uint32_t maxMeshSharedMemorySize=14612,maxMeshPayloadAndSharedMemorySize=14740;
};
int main() {
 if(kRevision!=3 || kScratchBytes!=256 || kD8.shared_bytes()!=14612 || kProfile96.shared_bytes()!=11412)return 1;
 if(kD8.payload_shared_bytes()!=14740 || kProfile96.payload_shared_bytes()!=11540)return 2;
 for(const char* value:{static_cast<const char*>(nullptr),"","0","false","no","01","true","1"}) {
  bool on=value&&std::string(value)=="1";if(parse_profile96(value)!=on)return 3;
  for(uint32_t width:{0u,8u,16u,32u}) {
   bool width_ok=width==8||width==16;
   if(requested(kD8.program,width,on)!=width_ok)return 4;
   if(requested(kProfile96.program,width,on)!=(width_ok&&on))return 5;
   if(requested(0,width,on)||target(0,on))return 6;
   if(cache_policy(width,on)!=std::array<uint32_t,3>{3,width,on?1u:0u})return 7;
  }
 }
 if(cache_policy(8,false)==cache_policy(8,true)||cache_policy(8,true)==cache_policy(16,true))return 8;
 if(pipeline_tag(0)||pipeline_tag(32)||pipeline_tag(8)!=0x4f4f573332030008ull||pipeline_tag(16)!=0x4f4f573332030010ull)return 9;
 if(pipeline_tag(8)==0x4f4f573332020008ull)return 10;
 for(const Target* t:{&kD8,&kProfile96}) {
  if(!identity_matches(*t,t->program,t->variant,7,128,1,16,t->original,t->fragment))return 11;
  if(identity_matches(*t,t->program+1,t->variant,7,128,1,16,t->original,t->fragment))return 12;
  if(identity_matches(*t,t->program,t->variant+1,7,128,1,16,t->original,t->fragment))return 13;
  if(identity_matches(*t,t->program,t->variant,6,128,1,16,t->original,t->fragment))return 14;
  if(identity_matches(*t,t->program,t->variant,7,64,1,16,t->original,t->fragment))return 15;
  if(identity_matches(*t,t->program,t->variant,7,128,0,16,t->original,t->fragment))return 16;
  if(identity_matches(*t,t->program,t->variant,7,128,1,128,t->original,t->fragment))return 17;
  if(identity_matches(*t,t->program,t->variant,7,128,1,16,"changed",t->fragment))return 18;
  if(identity_matches(*t,t->program,t->variant,7,128,1,16,t->original,"changed"))return 19;
  Subgroup s;Mesh m;
  for(uint32_t width:{8u,16u})if(!limits_match(width,*t,s,m))return 20;
  for(uint32_t width:{0u,32u})if(limits_match(width,*t,s,m))return 21;
  s.requiredSubgroupSizeStages=64;if(limits_match(8,*t,s,m))return 22;s.requiredSubgroupSizeStages=128;
  s.minSubgroupSize=16;if(limits_match(8,*t,s,m))return 23;s.minSubgroupSize=8;
  s.maxSubgroupSize=8;if(limits_match(16,*t,s,m))return 24;s.maxSubgroupSize=32;
  uint32_t* fields[]={&m.maxMeshWorkGroupInvocations,&m.maxMeshWorkGroupSize[0],&m.maxMeshOutputVertices,
   &m.maxMeshOutputPrimitives,&m.maxMeshOutputComponents,&m.maxMeshSharedMemorySize,&m.maxMeshPayloadAndSharedMemorySize};
  const uint32_t minima[]={32,32,t->max_vertices,t->max_primitives,t->vertex_components,t->shared_bytes(),t->payload_shared_bytes()};
  for(unsigned i=0;i<7;++i){auto old=*fields[i];*fields[i]=minima[i]-1;if(limits_match(8,*t,s,m))return 25;*fields[i]=old;}
 }
 Subgroup s;Mesh m;m.maxMeshSharedMemorySize=11412;m.maxMeshPayloadAndSharedMemorySize=11540;
 if(!limits_match(8,kProfile96,s,m)||limits_match(8,kD8,s,m))return 26;
 if(std::string(kD8.candidate)!="8754612a7395d1f804d4e56a7c18cb5a6f23ed2e02f01879cbc51bd108682de0")return 27;
 if(std::string(kProfile96.candidate)!="43434b023ff17ee4b92ed8e49f19c937c86f15cf31a7034161dd11878f944099")return 28;
 return 0;
}
'''


def check_header(compiler=None):
    compiler = compiler or shutil.which('clang++') or shutil.which('g++')
    require(compiler, 'C++20 compiler required')
    with tempfile.TemporaryDirectory(prefix='oo-native96-header-') as directory:
        work = Path(directory)
        (work / 'mesh_warp32_lowering.h').write_text(FROZEN.added_file('gpu/vk/mesh_warp32_lowering.h'))
        (work / 'mesh_warp32_profiles.h').write_text(header())
        (work / 'test.cpp').write_text(HEADER_TEST)
        subprocess.run([compiler, '-std=c++20', '-O2', '-DNDEBUG', '-Wall', '-Wextra', '-Werror',
                        str(work/'test.cpp'), '-o', str(work/'test')], check=True, capture_output=True, text=True, timeout=60)
        subprocess.run([str(work/'test')], check=True, timeout=10)
    return {'production_registry_and_limit_checks': True, 'strict_profile96_default_off': True,
            'both_target_identity_negatives': True, 'per_target_shared_and_output_limits': True,
            'revision3_cache_and_pipeline_tags': True, 'cpu_only_no_vulkan_link': True}


def check_source(checkout):
    checkout = Path(checkout).resolve()
    require(subprocess.check_output(['git','rev-parse','HEAD'], cwd=checkout, text=True).strip() == FROZEN.PIN, 'Wrong source pin')
    changed = re.findall(r'^diff --git a/(\S+) b/\S+$', PATCH.read_text(), re.M)
    require(sorted(changed) == sorted(FILES), 'Overlay changed unexpected files')
    with tempfile.TemporaryDirectory(prefix='oo-native96-index-') as directory:
        env = dict(os.environ, GIT_INDEX_FILE=str(Path(directory)/'index'))
        subprocess.run(['git','read-tree',FROZEN.PIN], cwd=checkout, env=env, check=True)
        hashes = []
        for name in BASE:
            path = HERE/name
            require(name not in PINNED or digest(path) == PINNED[name], 'Frozen overlay dependency changed')
            hashes.append({'patch':name, 'sha256':digest(path)})
            subprocess.run(['git','apply','--cached',str(path)], cwd=checkout, env=env, check=True)
        def content(name):
            return subprocess.check_output(['git','show',':'+name], cwd=checkout, env=env, text=True)
        names = (*FILES[1:], 'gpu/vk/mesh_warp32_lowering.h', 'gpu/vk/mesh_warp32_lowering.cpp',
                 'gpu/vk/mesh_input_capture.h', 'gpu/vk/mesh_input_profile96.h')
        before = {n:content(n) for n in names}
        subprocess.run(['git','apply','--cached','--check',str(PATCH)], cwd=checkout, env=env, check=True)
        subprocess.run(['git','apply','--cached',str(PATCH)], cwd=checkout, env=env, check=True)
        after = {n:content(n) for n in names}
        for name in names[-4:]:
            require(before[name] == after[name], 'Frozen helper source changed')
        shader, cache, draw, build = [after[n] for n in ('gpu/vk/vk_shader.cpp','gpu/vk/vk_progcache.cpp','gpu/vk/vk_draw.cpp','nx2/build.py')]
        old_shader, old_cache, old_draw = [before[n] for n in ('gpu/vk/vk_shader.cpp','gpu/vk/vk_progcache.cpp','gpu/vk/vk_draw.cpp')]
        require(body(shader,'bool mesh_warp32_runtime_policy(') == body(old_shader,'bool mesh_warp32_runtime_policy('), 'Strict compiler policy changed')
        old_device = body(old_shader,'bool mesh_warp32_device(')
        new_device = body(shader,'bool mesh_warp32_device(')
        require(old_device[old_device.index('{'):old_device.index('    return (subgroup')] ==
                new_device[new_device.index('{'):new_device.index('    return oo_mesh_warp32_profiles::limits_match')], 'Device identity/query gate changed')
        require('oo_mesh_warp32_profiles::limits_match(width, target, subgroup, mesh)' in new_device,'Production device misses tested limits')
        require('static const bool enabled = oo_mesh_warp32_profiles::parse_profile96(std::getenv("RX_VK_OO_MESH_WARP32_PROFILE96"))' in shader,'Immutable explicit opt-in missing')
        require('oo_mesh_warp32_profiles::requested(program, mesh_warp32_requested_width(), mesh_warp32_profile96_enabled())' in shader,'Full-cache target selection differs from tested registry')
        require('p->mesh && !p->fallback && mesh_warp32_target_requested(p->key)' in cache,'Full-cache path misses selection')
        require('mesh_warp32_apply(p, kept)' in shader and 'mesh_warp32_apply(*p, oo_original)' in cache,'JIT/cache parity missing')
        apply = body(shader,'void mesh_warp32_apply(')
        for token in ('p.variant != target->variant','p.stages.size() != 2','original.size() != 2',
                      'oo_mesh_warp32_profiles::identity_matches(*target','mesh_warp32_digest(candidate.words) != target->candidate',
                      'stage.oo_identity = actual_identity','target->shared_bytes()','target->original, target->candidate'):
            require(token in apply,'Production apply lacks exact target or identity condition: '+token)
        require(body(cache,'void program_cache_save(') == body(old_cache,'void program_cache_save('),'Canonical cache serialization changed')
        require('put_list(o, spirv[k])' in cache and 'if (!p.stages[k].mesh_warp32_width)' in cache,'Canonical bytes or actual identity lost')
        for marker in ('void pin_subgroup(', 'void pin_program_stage('):
            require(body(draw,marker) == body(old_draw,marker),'Baseline or selected-stage policy changed')
        commands = r'\bvkCmdDrawMeshTasks(?:IndirectCount|Indirect)?EXT\([^;]+;'
        require(re.findall(commands,draw) == re.findall(commands,old_draw),'Native commands/arguments changed')
        for token in ('pin_program_stage(si, st)','pin_program_stage(info, source)','pin_program_stage(info,stage)',
                      'mesh_warp32_pipeline_tag(p)','oo_mesh_warp32_profiles::pipeline_tag(stage.mesh_warp32_width)'):
            require(token in draw,'Full/GPL/observer parity absent: '+token)
        require('oo_mesh_warp32_profiles::cache_policy(mesh_warp32_requested_width(), mesh_warp32_profile96_enabled())' in shader,'Cache misses immutable profile96 selection')
        require('"mesh_warp32_profiles.h"' in build[build.index('for f in sorted(translator)'):build.index('shader_stamp =')],'Translator stamp misses target registry')
        require('kProgramCacheFormat = 1' in cache,'Program serialization unexpectedly changed')
    return {'twelve_overlay_chain_checked':True,'base_patches':hashes,'frozen_helpers_unchanged':True,
            'strict_policy_and_device_identity_preserved':True,'canonical_original_words_preserved':True,
            'jit_fullcache_fullpipeline_gpl_prewarm_policy_parity':True,'five_native_draw_commands_unchanged':True}


def check_authored_goldens(compiler=None):
    """Compare production core against Python for two independent fixture shapes."""
    sys.path.insert(0, str(HERE.parents[1]))
    from tools.mesh_warp32_transform import transform_assembly
    compiler = compiler or shutil.which('clang++') or shutil.which('g++')
    require(compiler and all(shutil.which(t) for t in ('spirv-as','spirv-dis','spirv-val')), 'C++ and SPIR-V Tools required')
    with tempfile.TemporaryDirectory(prefix='oo-native96-golden-') as directory:
        work=Path(directory)
        for name in ('mesh_warp32_lowering.h','mesh_warp32_lowering.cpp'):
            (work/name).write_text(FROZEN.added_file('gpu/vk/'+name))
        (work/'main.cpp').write_text(r'''#include "mesh_warp32_lowering.h"
#include <fstream>
int main(int argc,char**argv){if(argc!=3)return 2;std::ifstream f(argv[1],std::ios::binary|std::ios::ate);auto n=f.tellg();if(n<20||n%4)return 3;f.seekg(0);std::vector<uint32_t>w(size_t(n)/4);f.read((char*)w.data(),n);auto r=mkwvk::oo_mesh_warp32::rewrite(w);if(!r.ok)return 1;std::ofstream o(argv[2],std::ios::binary);o.write((char*)r.words.data(),r.words.size()*4);return !o;}
''')
        subprocess.run([compiler,'-std=c++20','-O2','-DNDEBUG',str(work/'main.cpp'),str(work/'mesh_warp32_lowering.cpp'),'-o',str(work/'run')],check=True,capture_output=True,text=True,timeout=60)
        for maximum in (3,96):
            words=FROZEN.replace_instruction(FROZEN.fixture(),16,[20,26,maximum],1)
            words=FROZEN.replace_instruction(words,16,[20,5270,maximum],2)
            src=work/'source.spv';src.write_bytes(FROZEN.binary(words))
            asm=subprocess.run(['spirv-dis','--raw-id',str(src)],check=True,capture_output=True,text=True).stdout
            transformed,_=transform_assembly(asm);(work/'python.spvasm').write_text(transformed)
            subprocess.run(['spirv-as','--preserve-numeric-ids','--target-env','spv1.6',str(work/'python.spvasm'),'-o',str(work/'python.spv')],check=True,capture_output=True,text=True)
            subprocess.run([str(work/'run'),str(src),str(work/'native.spv')],check=True,timeout=10)
            native=(work/'native.spv').read_bytes();expected=(work/'python.spv').read_bytes()
            require(native[:8]==expected[:8] and native[12:]==expected[12:],'Authored C++/Python word/ID golden differs')
            subprocess.run(['spirv-val','--target-env','vulkan1.3',str(work/'native.spv')],check=True,capture_output=True,text=True)
    return {'two_authored_shapes_cpp_python_word_id_goldens':True,'authored_candidates_spirv_validated':True}


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--checkout',type=Path)
    parser.add_argument('--compiler')
    args=parser.parse_args()
    try:
        report=check_header(args.compiler)
        report.update(FROZEN.check_helper(args.compiler))
        report.update(FROZEN.check_policy(args.compiler))
        report.update(check_authored_goldens(args.compiler))
        if args.checkout:report.update(check_source(args.checkout))
        report['patch_sha256']=digest(PATCH)
        print(json.dumps(report,indent=2))
    except (OSError,ValueError,subprocess.SubprocessError) as exc:
        parser.exit(1,str(exc)+'\n')


if __name__=='__main__':main()
