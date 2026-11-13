# SPDX-License-Identifier: GPL-3.0-or-later
"""Game-free CPU fixtures, production-function tests and Vulkan linker traps."""
import copy
import importlib.util
import json
from pathlib import Path
import shlex
import shutil
import struct
import subprocess
import tempfile
import unittest
from unittest.mock import patch
from tools import compute_interval_timing as timing
from tools import compute_interval_replay as replay

_SPEC=importlib.util.spec_from_file_location('interval_fixture',Path(__file__).with_name('test_compute_interval_replay.py'))
_FIXTURE=importlib.util.module_from_spec(_SPEC);_SPEC.loader.exec_module(_FIXTURE)


class PairedTimingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not all(shutil.which(p) for p in ('cc','pkg-config','clang-format')):
            raise unittest.SkipTest('native development tools unavailable')
        if subprocess.run(['pkg-config','--exists','vulkan']).returncode:
            raise unittest.SkipTest('Vulkan development headers unavailable')
        cls.temp=tempfile.TemporaryDirectory();cls.root=Path(cls.temp.name)
        cls.receipt=timing.build_native(cls.root)
        cls.flags=shlex.split(subprocess.check_output(['pkg-config','--cflags','--libs','vulkan'],text=True))
        cls.trap=cls.compile('trap',r'''
#define main timing_profile_main
#include "vulkan_compute_interval_timing.c"
#undef main
VKAPI_ATTR VkResult VKAPI_CALL __wrap_vkCreateInstance(const VkInstanceCreateInfo *i,
    const VkAllocationCallbacks *a,VkInstance *o) { (void)i;(void)a;(void)o;_Exit(97); }
int main(int argc,char **argv) { return timing_profile_main(argc,argv); }
''',['-Wl,--wrap=vkCreateInstance'])

    @classmethod
    def tearDownClass(cls):cls.temp.cleanup()

    @classmethod
    def compile(cls,name,source,extra=()):
        c=cls.root/(name+'.c');binary=cls.root/name;c.write_text(source)
        subprocess.run(['cc','-std=c11','-O2','-DNDEBUG','-Wall','-Wextra','-Werror',str(c),*cls.flags,'-lm',*extra,'-o',str(binary)],
                       check=True,capture_output=True,text=True,timeout=30)
        return binary

    def frame(self,root):
        frame=_FIXTURE.IntervalReplayTests.frame(None,root)
        p=root/'writable.bin';p.write_bytes(b'\x32'*timing.WRITABLE_BYTES)
        frame['allocations'][7]={'id':'buffer-16','size':timing.WRITABLE_BYTES,'initial':str(p),'expected':'-'}
        frame['descriptors'][6][4]=7
        candidate=root/'candidate.spv';candidate.write_bytes((root/'framing.spv').read_bytes())
        return frame,str(candidate),[a['initial'] for a in frame['allocations']]

    def check(self,change=None,raw=None,mode='--check-only'):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary);frame,candidate,oracles=self.frame(root)
            if change:change(frame,root,oracles)
            data=timing.encode_control(frame,candidate,oracles)
            if raw:data=raw(data)
            control=root/'paired.bin';control.write_bytes(data)
            return subprocess.run([str(self.trap),str(control),str(root),mode],capture_output=True,text=True,timeout=10)

    def test_positive_cpu_frame_never_reaches_vulkan_linker_trap(self):
        child=self.check();self.assertEqual(child.returncode,0,child.stderr)
        self.assertFalse(json.loads(child.stdout)['dispatch_executed'])
        # Demonstrate that the linked trap intercepts the first real API boundary.
        self.assertEqual(self.check(mode='--execute-approved-timing').returncode,97)

    def test_new_abi_roundtrip_preserves_old_graph_and_all_oracles(self):
        with tempfile.TemporaryDirectory() as temporary:
            frame,candidate,oracles=self.frame(Path(temporary))
            self.assertEqual(timing.decode_control(timing.encode_control(frame,candidate,oracles)),(frame,candidate,oracles))

    def test_old_abi_and_old_modes_are_rejected_before_vulkan(self):
        self.assertEqual(self.check(raw=lambda d:replay.MAGIC+d[8:]).returncode,2)
        for mode in ('--execute-approved-plan','--compile-approved-plan'):
            with self.subTest(mode=mode):self.assertEqual(self.check(mode=mode).returncode,2)

    def test_fixed_schedule_base_extent_and_trailing_mutations_rejected(self):
        def field(data,offset,value):
            b=bytearray(data);struct.pack_into('<I',b,offset,value);return bytes(b)
        for index,value in [(0,3),(1,5),(2,21),(3,6),(4,59)]:
            with self.subTest(field=index):
                self.assertEqual(self.check(raw=lambda d:field(d,struct.unpack_from('<Q',d,12)[0]+4*index,value)).returncode,2)
        self.assertEqual(self.check(raw=lambda d:d+b'x').returncode,2)
        self.assertEqual(self.check(raw=lambda d:d[:12]+struct.pack('<Q',20)+d[20:]).returncode,2)

    def test_readonly_oracle_extent_and_any_last_byte_mutation_rejected(self):
        def mutate(frame,root,oracles):
            p=root/'oracle.bin';p.write_bytes(b'\x97'*4095+b'\x96');oracles[0]=str(p)
        self.assertEqual(self.check(mutate).returncode,2)
        def truncate(frame,root,oracles):
            p=root/'oracle.bin';p.write_bytes(b'x');oracles[59]=str(p)
        self.assertEqual(self.check(truncate).returncode,2)

    def test_writable_owner_binding_and_size_are_native_admission_gates(self):
        mutations=[lambda f,r,o:f['descriptors'][6].__setitem__(4,0),
                   lambda f,r,o:f['allocations'][7].__setitem__('size',4096),
                   lambda f,r,o:f['allocations'][7].__setitem__('id','other')]
        for edit in mutations:
            with self.subTest(edit=edit):self.assertEqual(self.check(edit).returncode,2)

    def test_production_query_command_order_has_only_dispatch_between_timestamps(self):
        binary=self.compile('query-order',r'''
#define main timing_profile_main
#include "vulkan_compute_interval_timing.c"
#undef main
static unsigned trace=0;
VKAPI_ATTR void VKAPI_CALL __wrap_vkCmdResetQueryPool(VkCommandBuffer c,VkQueryPool p,uint32_t f,uint32_t n) {
    (void)c;(void)p;if(trace!=0||f||n!=2)_Exit(91);trace=1;
}
VKAPI_ATTR void VKAPI_CALL __wrap_vkCmdWriteTimestamp(VkCommandBuffer c,VkPipelineStageFlagBits s,VkQueryPool p,uint32_t i) {
    (void)c;(void)p;if((i==0&&s!=VK_PIPELINE_STAGE_TOP_OF_PIPE_BIT)||(i==1&&s!=VK_PIPELINE_STAGE_BOTTOM_OF_PIPE_BIT))_Exit(92);
    if(trace==1&&i==0)trace=2;else if(trace==3&&i==1)trace=4;else _Exit(93);
}
VKAPI_ATTR void VKAPI_CALL __wrap_vkCmdDispatch(VkCommandBuffer c,uint32_t x,uint32_t y,uint32_t z) {
    (void)c;if(x!=2||y!=1||z!=1)_Exit(94);if(trace==2)trace=3;else if(trace==0)trace=5;else _Exit(95);
}
int main(void) {
    uint32_t groups[3]={2,1,1}; timing_target_commands(VK_NULL_HANDLE,VK_NULL_HANDLE,1,groups);
    if(trace!=4) { return 1; }
    trace=0;timing_target_commands(VK_NULL_HANDLE,VK_NULL_HANDLE,0,groups);return trace!=5;
}
''',['-Wl,--wrap=vkCmdResetQueryPool','-Wl,--wrap=vkCmdWriteTimestamp','-Wl,--wrap=vkCmdDispatch'])
        child=subprocess.run([str(binary)],capture_output=True,text=True)
        self.assertEqual(child.returncode,0,child.stderr)

    def test_production_reset_rewrites_entire_owner_and_oracle_checks_last_byte(self):
        binary=self.compile('reset-oracle',r'''
#define main timing_profile_main
#include "vulkan_compute_interval_timing.c"
#undef main
int main(void) {
    Allocation a[2]={0};void *oracles[2];
    a[0].gpu.size=TIMING_WRITABLE_BYTES;a[0].gpu.mapped=malloc(TIMING_WRITABLE_BYTES);
    oracles[0]=malloc(TIMING_WRITABLE_BYTES);memset(oracles[0],0x6a,TIMING_WRITABLE_BYTES);
    memset(a[0].gpu.mapped,0xf7,TIMING_WRITABLE_BYTES);
    if(timing_reset_owner(&a[0],oracles[0])||timing_check_all(1,a,oracles))return 1;
    ((unsigned char*)a[0].gpu.mapped)[TIMING_WRITABLE_BYTES-1]^=1;
    if(timing_check_all(1,a,oracles)!=2)return 3;
    a[0].gpu.size--;return timing_reset_owner(&a[0],oracles[0])==2?0:4;
}
''')
        self.assertEqual(subprocess.run([str(binary)],capture_output=True).returncode,0)

    def test_production_timestamp_math_handles_wrap_and_rejects_unsupported_cases(self):
        binary=self.compile('timestamp-math',r'''
#define main timing_profile_main
#include "vulkan_compute_interval_timing.c"
#undef main
int main(void) {
    uint64_t ticks=0;double ns=0;
    if(timing_interval(UINT64_MAX-3,5,64,1,&ticks,&ns)||ticks!=9||ns!=9)return 1;
    if(timing_capabilities(0,1)!=2||timing_capabilities(65,1)!=2||timing_capabilities(64,NAN)!=2||
       timing_capabilities(64,0)!=2||timing_capabilities(32,1)!=2)return 3;
    if(timing_interval(5,5,64,1,&ticks,&ns)!=2||timing_interval(0,2000000000,64,1,&ticks,&ns)!=2)return 4;
    return 0;
}
''')
        self.assertEqual(subprocess.run([str(binary)],capture_output=True).returncode,0)
        self.assertEqual(timing.timestamp_delta((1<<64)-4,5,64,1),(9,9))
        for args in [(0,1,0,1),(0,1,65,1),(0,1,32,1),(0,1,64,float('nan')),(5,5,64,1),(0,2000000000,64,1)]:
            with self.subTest(args=args),self.assertRaises(ValueError):timing.timestamp_delta(*args)

    def test_production_schedule_is_exact_two_warmups_then_four_abba_blocks(self):
        binary=self.compile('schedule',r'''
#define main timing_profile_main
#include "vulkan_compute_interval_timing.c"
#undef main
int main(void) { for(unsigned i=0;i<TIMING_DISPATCHES;i++)printf("%u",timing_module(i));return 0; }
''')
        actual=subprocess.check_output([str(binary)],text=True)
        self.assertEqual(actual,'0011'+'0110'*4)
        self.assertEqual([m for m,timed in timing.protocol() if timed].count(0),8)
        self.assertEqual([m for m,timed in timing.protocol() if timed].count(1),8)

    def test_exact_source_anchors_and_pre_device_capabilities_boundaries(self):
        source=(self.root/'vulkan_compute_interval_timing.c').read_text()
        self.assertLess(source.index('timing_cpu_inputs(allocation_count'),source.index('vkCreateInstance('))
        self.assertLess(source.index('timing_capabilities(timestamp_bits'),source.index('vkCreateDevice('))
        self.assertIn('REQUIRE(properties.properties.limits.timestampComputeAndGraphics)',source)
        self.assertIn('host_barrier.dstAccessMask = VK_ACCESS_SHADER_READ_BIT | VK_ACCESS_SHADER_WRITE_BIT',source)
        self.assertIn('VK_PIPELINE_STAGE_ALL_COMMANDS_BIT',source)
        self.assertIn('for (unsigned trial = 0; trial < TIMING_DISPATCHES; trial++)',source)
        self.assertEqual(source.count('vkCmdDispatch('),1)
        self.assertNotIn('vkDeviceWaitIdle',source);self.assertNotIn('DISABLE_OPTIMIZATION',source)
        with self.assertRaisesRegex(ValueError,'source changed'):timing.derive_native(source)

    def test_reachable_store_audit_rejects_other_roots_and_memory_writes(self):
        source='''OpEntryPoint GLCompute %main "main"
OpDecorate %buffer DescriptorSet 0
OpDecorate %buffer Binding 6
%u = OpTypeInt 32 0
%p = OpTypePointer StorageBuffer %u
%buffer = OpVariable %p StorageBuffer
%main = OpFunction %u None %sig
%label = OpLabel
%ptr = OpAccessChain %p %buffer %zero
OpStore %ptr %zero
OpFunctionEnd
'''
        self.assertEqual(timing.audit_writes(source)['storage_store_sites'],1)
        for modified in [source.replace('Binding 6','Binding 5'),source.replace('OpStore %ptr %zero','OpAtomicStore %ptr %zero'),
                         source.replace('OpAccessChain %p %buffer %zero','OpConvertUToPtr %p %zero')]:
            with self.subTest(modified=modified),self.assertRaises(ValueError):timing.audit_writes(modified)

    def test_native_result_requires_complete_exact_trials_and_timestamp_math(self):
        native={'schema':'open-ocarina.compute-interval-paired-native/v1','profile_sha256':'a'*64,
            'actual_widths':[32,32],'module_sha256':[replay.ORIGINAL,replay.CANDIDATE],
            'record_sha256':replay.RECORD_SHA,'device_vendor':0x8086,'device_id':0x56a0,
            'dispatch_count':20,'full_comparisons':20,'complete_allocation_count':60,
            'timestamp_valid_bits':64,'timestamp_period_ns':1,'trials':[]}
        for i,(module,timed) in enumerate(timing.protocol()):
            native['trials'].append({'index':i,'module':module,'timed':timed,'all_60_complete_buffers_match':True,
                'timestamp_start':100 if timed else 0,'timestamp_end':150 if timed else 0,
                'ticks':50 if timed else 0,'nanoseconds':50 if timed else 0})
        profile={'total_dispatches':20,'timed_samples_per_module':8}
        timing.validate_native_result(native,profile,'a'*64)
        for edit in (lambda n:n['trials'].pop(),lambda n:n.__setitem__('actual_widths',[32,16]),
            lambda n:n['trials'][4].__setitem__('ticks',51),lambda n:n['trials'][5].__setitem__('module',0),
            lambda n:n['trials'][0].__setitem__('timestamp_start',1),lambda n:n['trials'][19].__setitem__('all_60_complete_buffers_match',False),
            lambda n:n.__setitem__('profile_sha256','b'*64)):
            changed=copy.deepcopy(native);edit(changed)
            with self.assertRaises(ValueError):timing.validate_native_result(changed,profile,'a'*64)

    def test_host_rejects_unreviewed_plan_without_process(self):
        with tempfile.TemporaryDirectory() as temporary,patch.object(timing.subprocess,'Popen') as child:
            with self.assertRaises((OSError,ValueError)):
                timing.run(temporary,Path(temporary)/'out','missing','0'*64)
            child.assert_not_called()


if __name__=='__main__':unittest.main()
