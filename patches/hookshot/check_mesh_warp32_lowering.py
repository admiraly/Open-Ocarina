#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""CPU-only production mesh-warp32 checks using independently authored inputs."""
import argparse
import hashlib
import json
import os
import re
from pathlib import Path
import shutil
import struct
import subprocess
import tempfile

HERE = Path(__file__).resolve().parent
PATCH = HERE / 'mesh-warp32-lowering.patch'
BASE = ('compute-identity.patch', 'page-table-snapshot.patch', 'allocation-capture.patch',
        'occurrence-capture.patch', 'graphics-identity.patch', 'sampled-image-capture.patch',
        'dispatch-parameters.patch', 'binding-provenance.patch', 'mesh-input-capture.patch')
PIN = '4cf785a0b143bb34026506c9a3fe4f4bb93c797d'


def require(condition, message):
    if not condition:
        raise ValueError(message)


def added_file(name, patch=None):
    active, lines = False, []
    for line in (PATCH.read_text() if patch is None else patch).splitlines(keepends=True):
        if line.startswith('diff --git '):
            active = line.rstrip().endswith(' b/' + name)
        elif active and line.startswith('+') and not line.startswith('+++'):
            lines.append(line[1:])
    result = ''.join(lines)
    require(result and 'SPDX-License-Identifier' in result, 'Authored helper missing from patch')
    return result


def instruction(op, *a):
    return [(len(a) + 1) << 16 | op, *a]


def fixture():
    # An authored SPIR-V mesh fixture. All IDs, control flow and expressions are synthetic.
    w = [0x07230203, 0x00010600, 0, 64, 0]
    w += instruction(17, 1) + instruction(17, 5283) + instruction(17, 61) + instruction(17, 64)
    text = b'SPV_EXT_mesh_shader\0';text += b'\0' * (-len(text) % 4)
    w += instruction(10, *struct.unpack('<%dI' % (len(text)//4), text))
    w += instruction(14, 0, 1) + instruction(15, 5365, 20, 0x6e69616d, 0, *range(13, 20))
    w += instruction(16, 20, 17, 32, 1, 1) + instruction(16, 20, 26, 3)
    w += instruction(16, 20, 5270, 1) + instruction(16, 20, 5298)
    for identity, builtin in [(13, 27), (14, 41), *[(15+i, 4416+i) for i in range(5)]]:
        w += instruction(71, identity, 11, builtin)
    w += instruction(19, 1) + instruction(20, 2) + instruction(21, 3, 32, 0)
    w += instruction(23, 4, 3, 3) + instruction(23, 5, 3, 4)
    w += instruction(32, 6, 1, 4) + instruction(32, 7, 1, 5) + instruction(32, 8, 1, 3)
    w += instruction(33, 9, 1) + instruction(43, 3, 10, 0) + instruction(43, 3, 11, 3) + instruction(41, 2, 12)
    w += instruction(59, 6, 13, 1) + instruction(59, 8, 14, 1)
    for identity in range(15, 20):
        w += instruction(59, 7, identity, 1)
    w += instruction(54, 1, 20, 0, 9) + instruction(248, 21) + instruction(61, 3, 22, 14)
    w += instruction(247, 23, 0) + instruction(250, 12, 24, 23)
    w += instruction(248, 24) + instruction(249, 23) + instruction(248, 23)
    w += instruction(339, 5, 25, 11, 12) + instruction(81, 3, 26, 25, 0)
    w += instruction(339, 5, 27, 11, 12) + instruction(81, 3, 28, 27, 0)
    w += instruction(61, 5, 29, 16) + instruction(5295, 10, 10) + instruction(253) + instruction(56)
    return w


def binary(words):
    return struct.pack('<%dI' % len(words), *words)


def replace_instruction(words, opcode, operands, occurrence=0):
    out, at, seen = words[:5], 5, 0
    while at < len(words):
        n, op = words[at] >> 16, words[at] & 65535
        if op == opcode and seen == occurrence:
            out += instruction(opcode, *operands)
        else:
            out += words[at:at+n]
        if op == opcode:
            seen += 1
        at += n
    require(seen > occurrence, 'Fixture opcode not found')
    return out


def check_helper(compiler=None, checkout=None):
    compiler = compiler or shutil.which('clang++') or shutil.which('g++')
    require(bool(compiler), 'C++20 compiler and public SPIRV-Headers are required')
    with tempfile.TemporaryDirectory(prefix='oo-mesh-warp32-') as temporary:
        work = Path(temporary)
        for filename in ('mesh_warp32_lowering.h', 'mesh_warp32_lowering.cpp'):
            text = (Path(checkout) / 'gpu/vk' / filename).read_text() if checkout else added_file('gpu/vk/' + filename)
            (work / filename).write_text(text)
        source = work / 'main.cpp'
        source.write_text(r'''#include "mesh_warp32_lowering.h"
#include <fstream>
#include <iostream>
#include <cstring>
int main(int argc,char**argv) {
    using namespace mkwvk::oo_mesh_warp32;
    if(argc==2 && !std::strcmp(argv[1],"identity")) {
        if(!identity_matches(kProgram,kVariant,7,128,1,16,kOriginal,kFragment))return 3;
        if(identity_matches(kProgram+1,kVariant,7,128,1,16,kOriginal,kFragment))return 4;
        if(identity_matches(kProgram,kVariant+1,7,128,1,16,kOriginal,kFragment))return 5;
        if(identity_matches(kProgram,kVariant,6,64,1,16,kOriginal,kFragment))return 6;
        if(identity_matches(kProgram,kVariant,7,128,1,16,"changed",kFragment))return 7;
        if(identity_matches(kProgram,kVariant,7,128,1,16,kOriginal,"changed"))return 8;
        if(parse_width(nullptr)||parse_width("")||parse_width("0")||parse_width("32")||parse_width("8x"))return 9;
        return parse_width("8")==8 && parse_width("16")==16 ? 0 : 10;
    }
    if(argc!=3)return 2;
    std::ifstream file(argv[1],std::ios::binary|std::ios::ate);if(!file)return 2;
    auto size=file.tellg();if(size<20||size>2097152||size%4)return 2;file.seekg(0);
    std::vector<uint32_t> words(size_t(size)/4);file.read(reinterpret_cast<char*>(words.data()),size);
    const auto result=rewrite(words);std::cout<<result.ok<<" "<<result.reason<<"\n";
    if(!result.ok)return 1;
    std::ofstream out(argv[2],std::ios::binary);out.write(reinterpret_cast<const char*>(result.words.data()),result.words.size()*4);
    return out ? 0 : 2;
}
''')
        executable = work / 'helper'
        subprocess.run([compiler, '-std=c++20', '-O2', '-DNDEBUG', '-Wall', '-Wextra', '-Werror',
                        str(source), str(work/'mesh_warp32_lowering.cpp'), '-o', str(executable)],
                       check=True, capture_output=True, text=True, timeout=60)
        subprocess.run([str(executable), 'identity'], check=True, timeout=10)
        original = fixture();input_path = work/'input.spv';output_path = work/'output.spv'
        def probe(words, accepted):
            input_path.write_bytes(binary(words));output_path.unlink(missing_ok=True)
            result = subprocess.run([str(executable), str(input_path), str(output_path)],
                                    capture_output=True, text=True, timeout=10)
            require(result.returncode == (0 if accepted else 1), 'Production core acceptance/crash mismatch: '+result.stdout+result.stderr)
            require(output_path.exists() == accepted, 'Rejected input emitted a candidate')
        probe(original, True)
        candidate = output_path.read_bytes()
        require(len(candidate)>len(binary(original)), 'Synthetic lowering did not expand ballots')
        negatives = [replace_instruction(original, 16, [20,17,16,1,1]),
                     replace_instruction(original, 14, [0,3]),
                     replace_instruction(original, 81, [3,26,25,1]),
                     replace_instruction(original, 249, [24])]
        # Known, recognized opcodes with insufficient operands must reject, never index out of bounds.
        for opcode, operands in [(17,[]),(54,[1]),(249,[]),(61,[3,22]),(250,[12]),(71,[13]),(339,[5,25])]:
            negatives.append(replace_instruction(original,opcode,operands))
        for words in negatives:
            probe(words, False)
        return {'production_cpp_optimized_compile': True, 'synthetic_positive_lowering': True,
                'negative_cases': len(negatives), 'malformed_known_opcodes_fail_closed': True,
                'exact_identity_and_width_guards_checked': True,
                'scope': 'CPU-only authored fixtures; no Vulkan/device/real shader execution'}


MOCK_POLICY = r'''#include <cstdint>
#include <cstring>
#include <cstdlib>
#include <utility>
#include <initializer_list>
constexpr uint32_t VK_DRIVER_ID_INTEL_OPEN_SOURCE_MESA=6,VK_SHADER_STAGE_MESH_BIT_EXT=128;
constexpr uint32_t VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_MESH_SHADER_PROPERTIES_EXT=1;
constexpr uint32_t VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_SUBGROUP_SIZE_CONTROL_PROPERTIES=2;
constexpr uint32_t VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_PROPERTIES_2=3;
constexpr uint32_t VK_STRUCTURE_TYPE_PIPELINE_SHADER_STAGE_REQUIRED_SUBGROUP_SIZE_CREATE_INFO=4;
constexpr uint32_t VK_PIPELINE_SHADER_STAGE_CREATE_REQUIRE_FULL_SUBGROUPS_BIT=2;
struct VkPhysicalDeviceMeshShaderPropertiesEXT {
 uint32_t sType;void* pNext=nullptr;uint32_t maxMeshWorkGroupInvocations=0,maxMeshWorkGroupSize[3]={0,0,0};
 uint32_t maxMeshOutputVertices=0,maxMeshOutputPrimitives=0,maxMeshSharedMemorySize=0,maxMeshPayloadAndSharedMemorySize=0;
};
struct VkPhysicalDeviceSubgroupSizeControlProperties {uint32_t sType;void* pNext=nullptr;uint32_t requiredSubgroupSizeStages=0,minSubgroupSize=0,maxSubgroupSize=0;};
struct VkPhysicalDeviceProperties2 {uint32_t sType;void* pNext=nullptr;};
struct Device {
 uint64_t physical=1,device=1;bool mesh_shader=true;
 struct {bool subgroupSizeControl=true;}vk13;
 struct {uint32_t vendorID=0x8086,deviceID=0x56a0,driverVersion=0x06802004;}props;
 uint32_t driver_id=VK_DRIVER_ID_INTEL_OPEN_SOURCE_MESA,sg32_stages=128;
}dev;
uint32_t mock_min=8,mock_max=32,mock_stage=128,mock_shared=65536;
// Pure CPU mock: these functions do not link Vulkan or open a device.
void vkGetPhysicalDeviceProperties2(uint64_t,VkPhysicalDeviceProperties2* p) {
 auto* s=static_cast<VkPhysicalDeviceSubgroupSizeControlProperties*>(p->pNext);
 s->requiredSubgroupSizeStages=mock_stage;s->minSubgroupSize=mock_min;s->maxSubgroupSize=mock_max;
 auto* m=static_cast<VkPhysicalDeviceMeshShaderPropertiesEXT*>(s->pNext);
 m->maxMeshWorkGroupInvocations=1024;m->maxMeshWorkGroupSize[0]=1024;
 m->maxMeshOutputVertices=m->maxMeshOutputPrimitives=256;m->maxMeshSharedMemorySize=mock_shared;m->maxMeshPayloadAndSharedMemorySize=131040;
}
struct VkPipelineShaderStageRequiredSubgroupSizeCreateInfo {uint32_t sType;const void* pNext;uint32_t requiredSubgroupSize;};
struct VkPipelineShaderStageCreateInfo {uint32_t stage=128,flags=2;const void* pNext=nullptr;};
struct Stage {uint32_t mesh_warp32_width=0;int nvn_stage=7;uint32_t vk_stage=128;};
void pin_subgroup(VkPipelineShaderStageCreateInfo& info) {
 static const VkPipelineShaderStageRequiredSubgroupSizeCreateInfo baseline{4,nullptr,32};info.pNext=&baseline;
}
bool env_on(const char* key) { const char* v=std::getenv(key);return v&&*v&&std::strcmp(v,"0"); }
// Production functions are inserted here by the checker.
@PRODUCTION@
int main() {
 if(!mesh_warp32_runtime_policy()||!mesh_warp32_device(8)||!mesh_warp32_device(16))return 1;
 dev.props.vendorID=0x1002;if(mesh_warp32_device(8))return 2;dev.props.vendorID=0x8086;
 dev.props.deviceID=0x1234;if(mesh_warp32_device(8))return 3;dev.props.deviceID=0x56a0;
 dev.props.driverVersion++;if(mesh_warp32_device(8))return 4;dev.props.driverVersion--;
 mock_min=16;if(mesh_warp32_device(8))return 5;mock_min=8;
 mock_stage=0;if(mesh_warp32_device(8))return 6;mock_stage=128;
 mock_shared=14611;if(mesh_warp32_device(8))return 7;mock_shared=65536;
 setenv("HS_SPILL_BATCH","0",1);if(mesh_warp32_runtime_policy())return 8;setenv("HS_SPILL_BATCH","1",1);
 // Every forbidden variable is presence-based: empty and zero must also refuse.
 for(const char* key:{"HS_CS32_MAX_INSTS","HS_FORCE_SIMD8","HS_SUBGROUP8","HS_SPILL_PCT","HS_LARGE_GRF","HS_LARGE_GRF_BIT","HS_RA_DEBUG","INTEL_DEBUG","RX_VK_WARP64"}) {
  for(const char* value:{"","0","1"}) {setenv(key,value,1);if(mesh_warp32_runtime_policy())return 18;}
  unsetenv(key);
 }
 setenv("HS_CS32_MAX_INSTS","100000",1);if(mesh_warp32_runtime_policy())return 19;unsetenv("HS_CS32_MAX_INSTS");
 setenv("RX_VK_NOOPT","1",1);if(mesh_warp32_runtime_policy())return 16;setenv("RX_VK_NOOPT","0",1);if(!mesh_warp32_runtime_policy())return 17;unsetenv("RX_VK_NOOPT");
 Stage source;VkPipelineShaderStageCreateInfo info;
 pin_program_stage(info,source);if(static_cast<const VkPipelineShaderStageRequiredSubgroupSizeCreateInfo*>(info.pNext)->requiredSubgroupSize!=32)return 10;
 source.mesh_warp32_width=8;pin_program_stage(info,source);
 if(static_cast<const VkPipelineShaderStageRequiredSubgroupSizeCreateInfo*>(info.pNext)->requiredSubgroupSize!=8||info.flags&2)return 11;
 source.mesh_warp32_width=16;pin_program_stage(info,source);
 if(static_cast<const VkPipelineShaderStageRequiredSubgroupSizeCreateInfo*>(info.pNext)->requiredSubgroupSize!=16)return 12;
 info.stage=16;pin_program_stage(info,source);
 if(static_cast<const VkPipelineShaderStageRequiredSubgroupSizeCreateInfo*>(info.pNext)->requiredSubgroupSize!=32)return 13;
 source.vk_stage=16;source.nvn_stage=1;pin_program_stage(info,source);
 if(static_cast<const VkPipelineShaderStageRequiredSubgroupSizeCreateInfo*>(info.pNext)->requiredSubgroupSize!=32)return 14;
 return 0;
}
'''


def check_policy(compiler=None):
    compiler = compiler or shutil.which('clang++') or shutil.which('g++')
    require(bool(compiler), 'C++20 compiler required')
    def additions(name):
        active, lines = False, []
        for line in PATCH.read_text().splitlines(keepends=True):
            if line.startswith('diff --git '): active = line.rstrip().endswith(' b/' + name)
            elif active and line.startswith('+') and not line.startswith('+++'): lines.append(line[1:])
        return ''.join(lines)
    shader, draw = additions('gpu/vk/vk_shader.cpp'), additions('gpu/vk/vk_draw.cpp')
    runtime = shader[shader.index('bool mesh_warp32_runtime_policy()'):shader.index('std::string mesh_warp32_digest(')]
    stage = draw[draw.index('void pin_program_stage('):draw.index('uint64_t mesh_warp32_pipeline_tag(')]
    source = MOCK_POLICY.replace('@PRODUCTION@', runtime + stage)
    with tempfile.TemporaryDirectory(prefix='oo-mesh-warp32-policy-') as temporary:
        work=Path(temporary);(work/'mock.cpp').write_text(source)
        subprocess.run([compiler,'-std=c++20','-O2','-DNDEBUG','-Wall','-Wextra','-Werror',str(work/'mock.cpp'),'-o',str(work/'mock')],
                       check=True,capture_output=True,text=True,timeout=60)
        env=os.environ.copy()
        for key in ('HS_CS32_MAX_INSTS','HS_FORCE_SIMD8','HS_SUBGROUP8','HS_SPILL_PCT','HS_LARGE_GRF','HS_LARGE_GRF_BIT','HS_RA_DEBUG','INTEL_DEBUG','RX_VK_WARP64','RX_VK_NOOPT'):env.pop(key,None)
        env.update(HS_SPILL_BATCH='1',HS_PUSH_SHADOW='1',shader_spilling_rate='1')
        subprocess.run([str(work/'mock')],env=env,check=True,timeout=10)
    return {'production_device_stage_and_compiler_policy_mocks_checked':True,
            'mock_only_no_vulkan_link_or_device_calls':True,
            'max_presence_empty_zero_and_legacy_value_refused':True,
            'all_nine_forbidden_controls_refused_for_empty_zero_and_one':True}


def check_source(checkout):
    checkout = Path(checkout).resolve()
    require(subprocess.check_output(['git','rev-parse','HEAD'],cwd=checkout,text=True).strip()==PIN, 'Wrong source pin')
    with tempfile.TemporaryDirectory(prefix='oo-mesh-warp32-index-') as temporary:
        env=os.environ.copy();env['GIT_INDEX_FILE']=str(Path(temporary)/'index')
        subprocess.run(['git','read-tree',PIN],cwd=checkout,env=env,check=True)
        for name in BASE:
            subprocess.run(['git','apply','--cached','--whitespace=nowarn',str((HERE/name).resolve())],cwd=checkout,env=env,check=True)
        before_draw = subprocess.check_output(['git','show',':gpu/vk/vk_draw.cpp'],cwd=checkout,env=env,text=True)
        subprocess.run(['git','apply','--cached','--check',str(PATCH.resolve())],cwd=checkout,env=env,check=True)
        subprocess.run(['git','apply','--cached',str(PATCH.resolve())],cwd=checkout,env=env,check=True)
        def content(name):
            return subprocess.check_output(['git','show',':'+name],cwd=checkout,env=env,text=True)
        shader,cache,draw,build=[content(n) for n in ('gpu/vk/vk_shader.cpp','gpu/vk/vk_progcache.cpp','gpu/vk/vk_draw.cpp','nx2/build.py')]
        command_pattern = r'\bvkCmdDrawMeshTasks(?:IndirectCount|Indirect)?EXT\([^;]+;'
        require(re.findall(command_pattern,before_draw)==re.findall(command_pattern,draw), 'Native mesh commands or arguments changed')
        require('mesh_warp32_apply(p, kept)' in shader and 'mesh_warp32_apply(*p, oo_original)' in cache, 'JIT/cache parity absent')
        require('if (!p.stages[k].mesh_warp32_width)' in cache, 'Cache save overwrites actual selected identity')
        require('dev.props.vendorID != 0x8086' in shader and 'dev.props.deviceID != 0x56a0' in shader, 'Device gate absent')
        require('dev.driver_id != VK_DRIVER_ID_INTEL_OPEN_SOURCE_MESA' in shader, 'Driver gate absent')
        require('mesh.maxMeshSharedMemorySize >= 14612' in shader, 'Added shared storage not checked')
        require('pin_program_stage(si, st)' in draw and 'pin_program_stage(info, source)' in draw, 'Pipeline/observer parity absent')
        require('mesh_warp32_lowering.h' in build and 'mesh_warp32_lowering.cpp' in build, 'Translator stamp misses helper source')
        require('mesh_warp32_requested_width()' in shader[shader.index('uint64_t translator_stamp()'):], 'Policy missing from cache stamp')
        require('kProgramCacheFormat = 1' in cache, 'Unnecessary cache serialization change')
    return {'nine_patch_chain_apply_checked': True, 'cache_keeps_canonical_originals': True,
            'jit_cache_identity_policy_and_build_stamp_hooks_checked': True}


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--checkout',type=Path)
    parser.add_argument('--compiler')
    args=parser.parse_args()
    try:
        report=check_helper(args.compiler)
        report.update(check_policy(args.compiler))
        if args.checkout:report.update(check_source(args.checkout))
        report['patch_sha256']=hashlib.sha256(PATCH.read_bytes()).hexdigest()
        print(json.dumps(report,indent=2))
    except (OSError,ValueError,subprocess.SubprocessError) as error:
        parser.exit(1,str(error)+'\n')


if __name__=='__main__':main()
