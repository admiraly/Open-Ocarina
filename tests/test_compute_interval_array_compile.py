# SPDX-License-Identifier: GPL-3.0-or-later
"""Independent CPU framing/capability/statistics tests; no Vulkan calls."""
import copy
import hashlib
import json
from pathlib import Path
import shlex
import shutil
import struct
import subprocess
import tempfile
import unittest

from tools import compute_interval_array_compile as compile_profile


def authored_record():
    payload=bytearray(struct.pack('<4I',6,6,0,6))
    for width in (4,16):
        for route in range(6):
            binding=route+2 if route<5 else 7
            payload.extend(struct.pack('<QQQ6I',0x1000+route*0x1000,0x1040+route*0x1000,0x1000+route*0x1000,binding,0,width,route,0,0))
    return bytes(payload)


def authored_frame(root):
    old,_=compile_profile.modules()
    shader=root/'framing.spv';shader.write_bytes(struct.pack('<5I',0x07230203,0x10600,0,64,0))
    push=root/'push.bin';push.write_bytes(bytes(88));table=root/'table.bin';table.write_bytes(struct.pack('<Q',0x100000))
    memory=root/'ordinary.bin';memory.write_bytes(bytes([0x97])*4096)
    record=root/'record.bin';record.write_bytes(old.guarded_record(authored_record()))
    pixels=root/'pixels.bin';pixels.write_bytes(bytes(256))
    allocations=[{'id':f'buffer-{index}','size':4096,'initial':str(memory),'expected':'-'} for index in range(59)]
    allocations.append({'id':old.RECORD_ID,'size':old.RECORD_OWNER_BYTES,'initial':str(record),'expected':str(record)})
    descriptors=[[binding,0,6 if binding<2 else 7,0,0,0,0,0,256] for binding in range(7)]
    descriptors.extend([[7,0,7,0,0,0,0,0,256],[7,1,7,1,0,0,0,0,0],[7,2,7,1,0,0,0,0,0]])
    descriptors.extend([[binding,0,1,0,0,binding-8,binding-8,0,0] for binding in range(8,12)])
    descriptors.append([12,0,7,0,59,0,0,4096,592])
    return {'width':32,'device':0,'vendor':0x8086,'device_id':0x56a0,'manifest_sha256':'0'*64,
        'module_sha256':old.ORIGINAL,'record_sha256':old.RECORD_SHA,'shader':str(shader),'push':str(push),
        'push_size':88,'root_offset':48,'groups':[2,1,1],'allocations':allocations,'table':str(table),'entries':1,'mappings':[(0,0,0)],
        'images':[{'words':[98,1,64,1,1,1,1],'subresources':[(0,0,256,str(pixels))]} for _ in range(4)],
        'image_views':[[index,1,98,0,0,0,0,0,1,0,1] for index in range(4)],
        'samplers':[[0,0,0,0,2,2,2,0,0,0x3f800000,0,7,0,0,0,0] for _ in range(4)],'descriptors':descriptors}


class ArrayCompileNativeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if any(not shutil.which(tool) for tool in ('cc','pkg-config','clang-format','nm')):raise unittest.SkipTest('Optional C/Vulkan/formatter CPU tools unavailable')
        if subprocess.run(['pkg-config','--exists','vulkan']).returncode:raise unittest.SkipTest('Vulkan development headers unavailable')
        cls.temporary=tempfile.TemporaryDirectory();cls.addClassCleanup(cls.temporary.cleanup);cls.root=Path(cls.temporary.name)
        old,_=compile_profile.modules()
        original=compile_profile.BASE/'vulkan_compute_interval_replay.c'
        if original.exists():source=original.read_text()
        else:
            text,_,_=old.derive_native((compile_profile.ROOT/'tools/vulkan_physical_replay.c').read_text())
            source=subprocess.run(['clang-format','-style={BasedOnStyle: LLVM, IndentWidth: 4, ColumnLimit: 100, AllowShortFunctionsOnASingleLine: None, AllowShortIfStatementsOnASingleLine: Never}'],input=text,capture_output=True,text=True,check=True,timeout=15).stdout
            if hashlib.sha256(source.encode()).hexdigest()!=compile_profile.SOURCE_SHA:raise unittest.SkipTest('Formatter output differs from frozen02 source')
        derived,cls.anchors=compile_profile.derive_native(source);cls.source=cls.root/'compile.c';cls.source.write_text(derived)
        cls.flags=shlex.split(subprocess.run(['pkg-config','--cflags','--libs','vulkan'],check=True,capture_output=True,text=True).stdout)
        cls.helper=cls.root/'native';cls.trapped=cls.root/'trapped'
        cls.build([str(cls.source)],cls.helper)
        trap=cls.root/'trap.c';trap.write_text('#include <vulkan/vulkan.h>\n#include <stdlib.h>\nVkResult __wrap_vkCreateInstance(const VkInstanceCreateInfo*a,const VkAllocationCallbacks*b,VkInstance*c){(void)a;(void)b;(void)c;_Exit(99);}\n')
        cls.build([str(cls.source),str(trap),'-Wl,--wrap=vkCreateInstance'],cls.trapped)
        fixture=cls.root/'caps-stats.c';fixture.write_text('#define main unused_compile_host_main\n#include "compile.c"\n#undef main\n'+r'''
int main(int argc,char **argv) {
 if(argc>1&&!strcmp(argv[1],"stats")) {
  VkPipelineExecutableStatisticKHR stats[16]={0};
  for(unsigned i=0;i<16;i++) {
   snprintf(stats[i].name,sizeof(stats[i].name),"field-%u",i);snprintf(stats[i].description,sizeof(stats[i].description),"description-%u",i);
   stats[i].format=(VkPipelineExecutableStatisticFormatKHR)(i%4);
   if(i%4==0)stats[i].value.b32=VK_TRUE;else if(i%4==1)stats[i].value.i64=-(int64_t)(i+1);else if(i%4==2)stats[i].value.u64=i+1;else stats[i].value.f64=i+.25;
  }
  putchar('{');interval_stats_json(16,stats);puts("}");return 0;
 }
 VkPhysicalDeviceLimits limits={.maxPushConstantsSize=128,.maxStorageBufferRange=592,.minStorageBufferOffsetAlignment=16,
  .maxPerStageDescriptorStorageBuffers=17,.maxDescriptorSetStorageBuffers=17,.maxPerStageResources=23,
  .maxPerStageDescriptorUniformBuffers=2,.maxDescriptorSetUniformBuffers=2,.maxPerStageDescriptorSampledImages=4,
  .maxDescriptorSetSampledImages=4,.maxPerStageDescriptorSamplers=4,.maxDescriptorSetSamplers=4};
 uint32_t push=23;VkBool32 nulls=VK_TRUE,nonuniform=VK_TRUE;
 if(argc>1)switch(argv[1][0]) {
  case 'p':push=22;break;case 's':limits.maxPerStageDescriptorStorageBuffers=16;break;case 't':limits.maxDescriptorSetStorageBuffers=16;break;
  case 'r':limits.maxPerStageResources=22;break;case 'u':limits.maxPerStageDescriptorUniformBuffers=1;break;case 'b':limits.maxDescriptorSetUniformBuffers=1;break;
  case 'i':limits.maxPerStageDescriptorSampledImages=3;break;case 'I':limits.maxDescriptorSetSampledImages=3;break;
  case 'a':limits.maxPerStageDescriptorSamplers=3;break;case 'A':limits.maxDescriptorSetSamplers=3;break;
  case 'n':nulls=VK_FALSE;break;case 'f':nonuniform=VK_FALSE;break;case 'c':limits.maxPushConstantsSize=87;break;
 }
 return interval_device_caps(&limits,push,nulls,nonuniform);
}
''')
        cls.caps=cls.root/'caps-stats';cls.build([str(fixture)],cls.caps)

    @classmethod
    def build(cls,arguments,output):
        subprocess.run(['cc',*compile_profile.FLAGS,*arguments,*cls.flags,'-o',str(output)],check=True,capture_output=True,text=True,timeout=30)

    def native_check(self,mutation=None,mode='--check-only',module='original'):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary);old=authored_frame(root);frame=compile_profile.append_aliases(old);frame['module_sha256']=compile_profile.MODULES[module]
            if mutation:mutation(frame)
            control=root/'control.bin';control.write_bytes(compile_profile.write_control(frame))
            return subprocess.run([str(self.trapped),str(control),str(root),mode],capture_output=True,text=True,timeout=10)

    def test_all_three_headers_accept_only_CPU_framing(self):
        for module in compile_profile.MODULES:
            result=self.native_check(module=module);self.assertEqual(result.returncode,0,result.stderr)
            self.assertEqual(json.loads(result.stdout),{'native_plan_validated':True,'dispatch_executed':False,'allocations':60,'images':4})

    def test_execution_mode_is_refused_before_instance(self):
        result=self.native_check(mode='--execute-approved-plan');self.assertEqual(result.returncode,2)

    def test_no_resource_queue_or_dispatch_imports(self):
        symbols=subprocess.run(['nm','-D',str(self.helper)],capture_output=True,text=True,check=True).stdout
        for forbidden in ('vkCmd','vkQueueSubmit','vkGetDeviceQueue','vkCreateBuffer','vkCreateImage','vkCreateSampler','vkAllocateMemory','vkDeviceWaitIdle'):
            self.assertNotIn(forbidden,symbols)
        text=self.source.read_text();self.assertLess(text.index('REQUIRE(compile_only && !execute)'),text.index('CHECK(vkCreateInstance'))
        self.assertIn('supported12.shaderStorageBufferArrayNonUniformIndexing',text)
        self.assertIn('.shaderStorageBufferArrayNonUniformIndexing = VK_TRUE',text)
        self.assertNotIn('variablePointers',text)

    def test_alias_owner_null_range_and_element_mutations_refused(self):
        mutations=[lambda f:f['descriptors'][19].__setitem__(4,1),lambda f:f['descriptors'][19].__setitem__(7,16),
                   lambda f:f['descriptors'][19].__setitem__(8,128),lambda f:f['descriptors'][21].__setitem__(3,0),
                   lambda f:f['descriptors'][22].__setitem__(1,6),lambda f:f['descriptors'].pop()]
        for mutation in mutations:
            result=self.native_check(mutation);self.assertEqual(result.returncode,2,result.stderr)

    def test_unknown_module_and_fixed_header_changes_refused(self):
        for mutation in (lambda f:f.__setitem__('module_sha256','0'*64),lambda f:f.__setitem__('width',16),lambda f:f.__setitem__('root_offset',56)):
            self.assertEqual(self.native_check(mutation).returncode,2)

    def test_capacity_null_and_nonuniform_gates_run_without_Vulkan(self):
        self.assertEqual(subprocess.run([str(self.caps)],capture_output=True).returncode,0)
        for flag in ('p','s','t','r','u','b','i','I','a','A','n','f','c'):
            self.assertEqual(subprocess.run([str(self.caps),flag],capture_output=True).returncode,2,flag)

    def test_all16_statistic_fields_and_types_survive_serialization(self):
        result=subprocess.run([str(self.caps),'stats'],capture_output=True,text=True,check=True)
        statistics=json.loads(result.stdout)['executable_statistics'];self.assertEqual(len(statistics),16)
        for index,row in enumerate(statistics):
            self.assertEqual(row['name'],f'field-{index}');self.assertEqual(row['description'],f'description-{index}');self.assertEqual(row['format'],index%4)
            wanted=True if index%4==0 else -(index+1) if index%4==1 else index+1 if index%4==2 else index+.25
            self.assertEqual(row['value'],wanted)

    def test_graph_extension_changes_only8_literal_descriptor_aliases(self):
        with tempfile.TemporaryDirectory() as temporary:
            frame=authored_frame(Path(temporary));copy_before=copy.deepcopy(frame);extended=compile_profile.append_aliases(frame)
            self.assertEqual(frame,copy_before);proof=compile_profile.check_extension(frame,extended)
            self.assertEqual(proof['extra_allocations'],0);self.assertEqual(len(extended['descriptors']),23)
            extended['descriptors'][-4][8]+=16
            with self.assertRaises(ValueError):compile_profile.check_extension(frame,extended)


if __name__=='__main__':unittest.main()
