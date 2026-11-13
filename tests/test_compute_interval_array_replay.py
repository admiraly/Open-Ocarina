# SPDX-License-Identifier: GPL-3.0-or-later
"""Independent CPU checks for owned-array framing, real-handle checks and full readback."""
import copy
import hashlib
import importlib.util
import json
from pathlib import Path
import shlex
import shutil
import subprocess
import tempfile
import unittest

from tools import compute_interval_array_replay as replay
from tools import compute_interval_array_compile as common

_fixture_spec=importlib.util.spec_from_file_location('array_compile_cpu_fixture',Path(__file__).with_name('test_compute_interval_array_compile.py'))
_fixture=importlib.util.module_from_spec(_fixture_spec);_fixture_spec.loader.exec_module(_fixture)


class ArrayReplayNativeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if any(not shutil.which(tool) for tool in ('cc','pkg-config','nm','clang-format')):raise unittest.SkipTest('Optional C/Vulkan tools unavailable')
        if subprocess.run(['pkg-config','--exists','vulkan']).returncode:raise unittest.SkipTest('Vulkan headers unavailable')
        cls.temp=tempfile.TemporaryDirectory();cls.addClassCleanup(cls.temp.cleanup);cls.root=Path(cls.temp.name)
        old,_=common.modules();base=common.BASE/'vulkan_compute_interval_replay.c'
        if base.exists():cls.original=base.read_text()
        else:
            text,_,_=old.derive_native((common.ROOT/'tools/vulkan_physical_replay.c').read_text())
            cls.original=subprocess.run(['clang-format','-style={BasedOnStyle: LLVM, IndentWidth: 4, ColumnLimit: 100, AllowShortFunctionsOnASingleLine: None, AllowShortIfStatementsOnASingleLine: Never}'],input=text,capture_output=True,text=True,check=True,timeout=15).stdout
            if hashlib.sha256(cls.original.encode()).hexdigest()!=common.SOURCE_SHA:raise unittest.SkipTest('Formatter differs from frozen source')
        cls.derived,cls.anchors=replay.derive_native(cls.original);cls.source=cls.root/'replay.c';cls.source.write_text(cls.derived)
        cls.flags=shlex.split(subprocess.run(['pkg-config','--cflags','--libs','vulkan'],capture_output=True,text=True,check=True).stdout)
        cls.helper=cls.root/'native';cls.trapped=cls.root/'trapped';cls.build([str(cls.source)],cls.helper)
        trap=cls.root/'trap.c';trap.write_text('#include <vulkan/vulkan.h>\n#include <stdlib.h>\nVkResult __wrap_vkCreateInstance(const VkInstanceCreateInfo*a,const VkAllocationCallbacks*b,VkInstance*c){(void)a;(void)b;(void)c;_Exit(99);}\n')
        cls.build([str(cls.source),str(trap),'-Wl,--wrap=vkCreateInstance'],cls.trapped)
        harness=cls.root/'handles.c';harness.write_text('#define main unused_replay_main\n#include "replay.c"\n#undef main\n'+r'''
int main(int argc,char **argv) {
 if(argc>1&&argv[1][0]=='c') {
  VkPhysicalDeviceLimits limits={.maxPushConstantsSize=128,.maxStorageBufferRange=592,.minStorageBufferOffsetAlignment=16,
   .maxPerStageDescriptorStorageBuffers=17,.maxDescriptorSetStorageBuffers=17,.maxPerStageResources=23,
   .maxPerStageDescriptorUniformBuffers=2,.maxDescriptorSetUniformBuffers=2,.maxPerStageDescriptorSampledImages=4,
   .maxDescriptorSetSampledImages=4,.maxPerStageDescriptorSamplers=4,.maxDescriptorSetSamplers=4};
  uint32_t push=23;VkBool32 nulls=VK_TRUE,nonuniform=VK_TRUE;
  switch(argv[1][1]) {
   case 'p':push=22;break;case 's':limits.maxPerStageDescriptorStorageBuffers=16;break;
   case 't':limits.maxDescriptorSetStorageBuffers=16;break;case 'r':limits.maxPerStageResources=22;break;
   case 'u':limits.maxPerStageDescriptorUniformBuffers=1;break;case 'b':limits.maxDescriptorSetUniformBuffers=1;break;
   case 'i':limits.maxPerStageDescriptorSampledImages=3;break;case 'I':limits.maxDescriptorSetSampledImages=3;break;
   case 'a':limits.maxPerStageDescriptorSamplers=3;break;case 'A':limits.maxDescriptorSetSamplers=3;break;
   case 'n':nulls=VK_FALSE;break;case 'f':nonuniform=VK_FALSE;break;case 'c':limits.maxPushConstantsSize=87;break;
  }
  return interval_device_caps(&limits,push,nulls,nonuniform);
 }
 Binding rows[23]={0};VkDescriptorBufferInfo infos[23]={0};VkWriteDescriptorSet writes[23]={0};
 unsigned source_indices[8]={2,3,4,5,6,7,8,9};
 for(unsigned i=0;i<15;i++)rows[i]=(Binding){.binding=i<8?i:i<10?7:i<14?i-2:12,.element=i==8?1:i==9?2:0};
 for(unsigned route=0;route<8;route++) {
  unsigned src=source_indices[route],dst=15+route;
  rows[src].type=7;rows[src].allocation=route;rows[src].offset=64*route;rows[src].size=256;
  if(route>=6){rows[src].null=1;rows[src].offset=rows[src].size=0;}
  rows[dst]=rows[src];rows[dst].binding=13;rows[dst].element=route;
  for(unsigned k=0;k<2;k++) {
   unsigned j=k?dst:src;infos[j]=(VkDescriptorBufferInfo){rows[j].null?VK_NULL_HANDLE:(VkBuffer)(uintptr_t)(0x1000+rows[j].allocation),rows[j].null?0:rows[j].offset,rows[j].null?VK_WHOLE_SIZE:rows[j].size};
   writes[j]=(VkWriteDescriptorSet){.dstBinding=rows[j].binding,.dstArrayElement=rows[j].element,.descriptorCount=1,.descriptorType=VK_DESCRIPTOR_TYPE_STORAGE_BUFFER,.pBufferInfo=&infos[j]};
  }
 }
 if(argc>1)switch(argv[1][0]) {
  case 'h':infos[19].buffer=(VkBuffer)(uintptr_t)0xfeed;break; /* binding6 direct-write alias */
  case 'o':infos[19].offset++;break;case 'r':infos[19].range--;break;
  case 'p':writes[19].pBufferInfo=&infos[6];break;case 'w':writes[19].dstArrayElement=3;break;
  case 'n':infos[21].buffer=(VkBuffer)(uintptr_t)0xface;break;case 'z':infos[21].range=0;break;
  case 'a':rows[19].allocation=55;break;
 }
 return array_bound_alias_contract(23,rows,infos,writes);
}
''')
        cls.handles=cls.root/'handles';cls.build([str(harness)],cls.handles)

    @classmethod
    def build(cls,args,output):
        subprocess.run(['cc',*common.FLAGS,*args,*cls.flags,'-o',str(output)],capture_output=True,text=True,check=True,timeout=30)

    def check(self,mutation=None,module='original',magic=None):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary);frame=common.append_aliases(_fixture.authored_frame(root));frame['module_sha256']=common.MODULES[module]
            if mutation:mutation(frame)
            control=root/'control.bin';data=replay.write_control(frame);control.write_bytes((magic+data[8:]) if magic else data)
            return subprocess.run([str(self.trapped),str(control),str(root),'--check-only'],capture_output=True,text=True,timeout=10)

    def test_original_and_array_CPU_headers_never_open_Vulkan(self):
        for module in ('original','array'):
            result=self.check(module=module);self.assertEqual(result.returncode,0,result.stderr)
            self.assertEqual(json.loads(result.stdout),{'native_plan_validated':True,'dispatch_executed':False,'allocations':60,'images':4})

    def test_old_ABIs_static_module_and_wrong_dispatch_refused(self):
        for magic in (common.MAGIC,b'OOILRP1\0'):
            self.assertEqual(self.check(magic=magic).returncode,2)
            with self.assertRaises(ValueError):replay.read_control(magic+b'garbage')
        self.assertEqual(self.check(module='static').returncode,2)
        for mutation in (lambda f:f.__setitem__('width',16),lambda f:f.__setitem__('groups',[1,1,1]),lambda f:f.__setitem__('root_offset',56)):
            self.assertEqual(self.check(mutation).returncode,2)

    def test_literal_alias_owner_range_null_and_record_mutations_refused(self):
        for mutation in (lambda f:f['descriptors'][19].__setitem__(4,1),lambda f:f['descriptors'][19].__setitem__(7,16),lambda f:f['descriptors'][19].__setitem__(8,128),lambda f:f['descriptors'][21].__setitem__(3,0),lambda f:f['allocations'][-1].__setitem__('expected','-'),lambda f:f['descriptors'].pop()):
            self.assertEqual(self.check(mutation).returncode,2)

    def test_actual_handles_offsets_ranges_and_write_targets_checked(self):
        self.assertEqual(subprocess.run([str(self.handles)],capture_output=True).returncode,0)
        for flag in ('h','o','r','p','w','n','z','a'):
            self.assertEqual(subprocess.run([str(self.handles),flag],capture_output=True).returncode,2,flag)

    def test_all_capacity_categories_and_required_features_fail_closed(self):
        self.assertEqual(subprocess.run([str(self.handles),'c'],capture_output=True).returncode,0)
        for flag in ('p','s','t','r','u','b','i','I','a','A','n','f','c'):
            self.assertEqual(subprocess.run([str(self.handles),'c'+flag],capture_output=True).returncode,2,flag)

    def test_source_and_optimized_binary_retain_only_one_bounded_dispatch(self):
        symbols=subprocess.run(['nm','-D',str(self.helper)],capture_output=True,text=True,check=True).stdout
        for symbol in ('vkCmdDispatch','vkQueueSubmit','vkWaitForFences','vkCreateBuffer'):self.assertIn(symbol,symbols)
        for symbol in ('vkDeviceWaitIdle','vkQueueWaitIdle'):self.assertNotIn(symbol,symbols)
        self.assertEqual(self.derived.count('vkCmdDispatch('),1);self.assertEqual(self.derived.count('vkQueueSubmit('),1)
        self.assertIn('UINT64_C(2000000000)',self.derived)
        self.assertLess(self.derived.index('array_bound_alias_contract(descriptor_count'),self.derived.index('VkCommandPoolCreateInfo pool_info'))
        self.assertIn('.shaderStorageBufferArrayNonUniformIndexing = VK_TRUE',self.derived);self.assertNotIn('variablePointers',self.derived)
        # Command recording, barriers, push, grid, queue/fence and export loop remain exact02.
        start='    VkCommandPoolCreateInfo pool_info =';end='    printf("{\\"interval_profile\\":1'
        self.assertEqual(self.derived[self.derived.index(start):self.derived.index('    printf("{\\"array_replay_profile\\":1',self.derived.index(start))],self.original[self.original.index(start):self.original.index(end,self.original.index(start))])

    def test_frozen_source_and_counted_anchor_mutation_fail_closed(self):
        with self.assertRaisesRegex(ValueError,'Frozen02 C source changed'):replay.derive_native(self.original+'\n')
        self.assertIn('check-actual-alias-before-command-pool',self.anchors)
        export='    for (uint32_t i = 0; i < allocation_count; i++) {\n        Allocation *a = &allocations[i];\n        char path[8192];'
        self.assertEqual(self.derived[self.derived.index(export):],self.original[self.original.index(export):])


class FullReadbackTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup);self.root=Path(self.temp.name)
        self.a=self.root/'a';self.b=self.root/'b';self.a.mkdir();self.b.mkdir()
        owners=[]
        for index in range(60):
            data=bytes((index,))* (17+index);sha=hashlib.sha256(data).hexdigest()
            for directory in (self.a,self.b):(directory/f'allocation-{index}.bin').write_bytes(data)
            owners.append({'index':index,'size':len(data),'output':f'allocation-{index}.bin','native_expected_sha256':sha if index==59 else None})
        self.profile={'owners':owners,'expected_complete_readback_bytes':sum(x['size'] for x in owners)}
        self.native={'array_replay_profile':1,'dispatch_executed':True,'dispatch_count':1,'actual_subgroup_size':32,'constructed_alias_pairs_verified':8,'constructed_null_alias_pairs_verified':2,'shaderStorageBufferArrayNonUniformIndexing_enabled':True,'VariablePointers_enabled':False,'union_descriptor_count':23,'union_storage_buffer_count':17,'comparison_passed':True,'allocations':[{'index':i,'size':x['size'],'first_difference':None} for i,x in enumerate(owners)],'experiment_manifest_sha256':hashlib.sha256(replay.canonical(self.profile)).hexdigest(),'device_uuid':'a','driver_uuid':'b','device_vendor':0x8086,'device_id':0x56a0,'module_sha256':common.MODULES['original']}

    def test_all60_owners_are_rehashed_and_compared(self):
        self.assertTrue(replay.compare_outputs(self.profile,self.a,self.native,self.b,self.native)['all60_complete_owners_match'])
        path=self.b/'allocation-58.bin';data=path.read_bytes();path.write_bytes(data[:-1]+b'\xff')
        with self.assertRaisesRegex(ValueError,'mismatch in owners'):replay.compare_outputs(self.profile,self.a,self.native,self.b,self.native)

    def test_missing_truncated_tail_or_changed_guard_refused(self):
        for index in (0,59):
            path=self.b/f'allocation-{index}.bin';data=path.read_bytes();path.write_bytes(data[:-1])
            with self.assertRaises(ValueError):replay.inspect_readback(self.b,self.profile,self.native)
            path.write_bytes(data)
        path=self.b/'allocation-59.bin';data=path.read_bytes();path.write_bytes(data[:-1]+b'\xfe')
        with self.assertRaisesRegex(ValueError,'guarded record'):replay.inspect_readback(self.b,self.profile,self.native)

    def test_receipt_identity_width_alias_count_and_output_module_refused(self):
        for key,value in [('constructed_alias_pairs_verified',7),('constructed_null_alias_pairs_verified',1),('actual_subgroup_size',16),('comparison_passed',False)]:
            mutated=copy.deepcopy(self.native);mutated[key]=value
            with self.assertRaises(ValueError):replay.inspect_readback(self.a,self.profile,mutated)
        for key,value in [('driver_uuid','other'),('experiment_manifest_sha256','0'*64),('module_sha256','0'*64)]:
            mutated=copy.deepcopy(self.native);mutated[key]=value
            with self.assertRaises(ValueError):replay.compare_outputs(self.profile,self.a,self.native,self.b,mutated)


if __name__=='__main__':unittest.main()
