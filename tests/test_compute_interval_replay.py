# SPDX-License-Identifier: GPL-3.0-or-later
"""Authored CPU framing fixtures only. No shader/device/pipeline execution."""
import copy
import json
from pathlib import Path
import shlex
import shutil
import struct
import subprocess
import tempfile
import unittest
from unittest.mock import patch
from tools import compute_interval_replay as replay


def authored_record():
    data = bytearray(struct.pack('<4I', 6, 6, 0, 6))
    for width in (4, 16):
        for i in range(6):
            binding, element = (i + 2, 0) if i < 5 else (7, 0)
            data.extend(struct.pack('<QQQ6I', 0x1000 + i * 0x1000, 0x1040 + i * 0x1000,
                                    0x1000 + i * 0x1000, binding, element, width, i, 0, 0))
    return bytes(data)


class IntervalReplayTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not shutil.which('cc') or not shutil.which('pkg-config') or not shutil.which('clang-format'):
            raise unittest.SkipTest('C/Vulkan/formatter development tools unavailable')
        if subprocess.run(['pkg-config', '--exists', 'vulkan']).returncode:
            raise unittest.SkipTest('Vulkan development headers unavailable')
        cls.temp = tempfile.TemporaryDirectory(); cls.root = Path(cls.temp.name)
        cls.receipt = replay.build_native(cls.root)
        cls.helper = cls.root / 'native'

    @classmethod
    def tearDownClass(cls): cls.temp.cleanup()

    def frame(self, root):
        # The SPIR-V contains only independent framing words and is never submitted.
        shader = root / 'framing.spv'; shader.write_bytes(struct.pack('<5I', 0x07230203, 0x00010600, 0, 64, 0))
        push = root / 'push.bin'; push.write_bytes(bytes(88))
        table = root / 'table.bin'; table.write_bytes(struct.pack('<Q', 0x100000))
        ordinary = root / 'ordinary.bin'; ordinary.write_bytes(b'\x97' * 4096)
        record = root / 'record.bin'; record.write_bytes(replay.guarded_record(authored_record()))
        image = root / 'pixels.bin'; image.write_bytes(bytes(256))
        allocations = [{'id':f'buffer-{i}', 'size':4096, 'initial':str(ordinary), 'expected':'-'} for i in range(59)]
        allocations.append({'id':replay.RECORD_ID,'size':replay.RECORD_OWNER_BYTES,'initial':str(record),'expected':str(record)})
        descriptors = [[i,0,6 if i<2 else 7,0,0,0,0,0,256] for i in range(7)]
        descriptors += [[7,0,7,0,0,0,0,0,256],[7,1,7,1,0,0,0,0,0],[7,2,7,1,0,0,0,0,0]]
        descriptors += [[i,0,1,0,0,i-8,i-8,0,0] for i in range(8,12)]
        descriptors.append([12,0,7,0,59,0,0,replay.GUARD_BYTES,replay.RECORD_BYTES])
        return {'width':32,'device':0,'vendor':0x8086,'device_id':0x56a0,'manifest_sha256':'0'*64,
            'module_sha256':replay.ORIGINAL,'record_sha256':replay.RECORD_SHA,'shader':str(shader),'push':str(push),
            'push_size':88,'root_offset':48,'groups':[2,1,1],'allocations':allocations,'table':str(table),
            'entries':1,'mappings':[(0,0,0)],'images':[{'words':[98,1,64,1,1,1,1],'subresources':[(0,0,256,str(image))]} for _ in range(4)],
            'image_views':[[i,1,98,0,0,0,0,0,1,0,1] for i in range(4)],
            'samplers':[[0,0,0,0,2,2,2,0,0,0x3f800000,0,7,0,0,0,0] for _ in range(4)],'descriptors':descriptors}

    def native_check(self, change=None):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary);frame=self.frame(root)
            if change: change(frame, root)
            control=root/'control.bin';control.write_bytes(replay.write_control(frame))
            child=subprocess.run([str(self.helper),str(control),str(root),'--check-only'],capture_output=True,text=True,timeout=10)
            return child

    def test_native_cpu_contract_accepts_authored_framing_without_vulkan(self):
        child=self.native_check();self.assertEqual(child.returncode,0,child.stderr)
        self.assertFalse(json.loads(child.stdout)['dispatch_executed'])

    def test_controls_roundtrip_preserves_images_and_every_descriptor(self):
        with tempfile.TemporaryDirectory() as temporary:
            frame=self.frame(Path(temporary));data=replay.write_control(frame)
            self.assertEqual(replay.read_control(data,experimental=True),frame)
            with self.assertRaisesRegex(ValueError,'Trailing'):
                replay.read_control(data+b'x',experimental=True)

    def test_record_header_reserved_width_and_interval_mutations_fail(self):
        for offset,value in [(0,65),(12,7),(16+40,1),(16+32,16),(16+48,0)]:
            with self.subTest(offset=offset):
                data=bytearray(authored_record());struct.pack_into('<I',data,offset,value)
                with self.assertRaises(ValueError):replay.check_record(data)

    def test_native_guard_or_payload_mutation_is_rejected_before_vulkan(self):
        for offset in (0,replay.GUARD_BYTES,replay.GUARD_BYTES+16+40,replay.RECORD_OWNER_BYTES-1):
            def change(frame, root):
                p=root/'record.bin';data=bytearray(p.read_bytes());data[offset]^=1;p.write_bytes(data)
            with self.subTest(offset=offset):self.assertEqual(self.native_check(change).returncode,2)

    def test_native_missing_or_wrong_extra_descriptor_is_rejected(self):
        for edit in (lambda f:f['descriptors'].pop(),lambda f:f['descriptors'][-1].__setitem__(0,13),
                     lambda f:f['descriptors'][-1].__setitem__(7,0),lambda f:f['descriptors'][-1].__setitem__(8,640)):
            with self.subTest(edit=edit):self.assertEqual(self.native_check(lambda f,r:edit(f)).returncode,2)

    def test_native_record_cannot_be_guest_mapped_or_an_existing_storage_root(self):
        for edit in (lambda f:f['mappings'].__setitem__(0,(0,59,0)),lambda f:f['descriptors'][2].__setitem__(4,59)):
            with self.subTest(edit=edit):self.assertEqual(self.native_check(lambda f,r:edit(f)).returncode,2)

    def test_native_subgroup_push_grid_or_wrapped_root_changes_are_rejected(self):
        for key,value in [('width',16),('push_size',80),('root_offset',0xfffffff8),('groups',[1,1,1])]:
            with self.subTest(key=key):self.assertEqual(self.native_check(lambda f,r:f.__setitem__(key,value)).returncode,2)

    def test_native_old_magic_cannot_enter_experimental_profile(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary);frame=self.frame(root);control=root/'control.bin'
            data=bytearray(replay.write_control(frame));data[:8]=b'OOPHYR1\0';control.write_bytes(data)
            result=subprocess.run([str(self.helper),str(control),str(root),'--check-only'],capture_output=True,timeout=10)
            self.assertEqual(result.returncode,2)

    def test_counted_anchors_do_not_accept_modified_original_native_source(self):
        source=(replay.ROOT/'tools/vulkan_physical_replay.c').read_text()
        with self.assertRaisesRegex(ValueError,'fingerprint'):
            replay.derive_native(source+'\n')

    def test_compile_only_boundary_precedes_buffer_allocations_and_queue_use(self):
        source=(self.root/'vulkan_compute_interval_replay.c').read_text()
        self.assertLess(source.index('if (!execute && !compile_only)'),source.index('vkCreateInstance('))
        self.assertLess(source.index('if (compile_only)'),source.index('int has_address'))
        self.assertLess(source.index('if (compile_only)'),source.index('vkGetDeviceQueue('))
        self.assertIn('vkGetPipelineExecutableStatisticsKHR',source)
        self.assertLess(source.index('interval_device_caps(&properties.properties.limits'),source.index('vkCreateDevice('))
        self.assertLess(source.index('vkGetPhysicalDeviceFeatures2('),source.index('interval_device_caps(&properties.properties.limits'))
        self.assertIn('supported_robustness.nullDescriptor',source)
        self.assertEqual(source.count('vkCmdDispatch('),1)
        self.assertNotIn('vkDeviceWaitIdle',source)
        self.assertNotIn('DISABLE_OPTIMIZATION',source)
        self.assertIn('UINT64_C(2000000000)',source)

    def test_native_union_caps_and_null_feature_are_required_without_vulkan(self):
        source=self.root/'caps-fixture.c';helper=self.root/'caps-fixture'
        source.write_text('#define main owned_profile_main\n#include "vulkan_compute_interval_replay.c"\n#undef main\n'
            'int main(int argc,char**argv){VkPhysicalDeviceLimits p={0};'
            'p.maxStorageBufferRange=592;p.minStorageBufferOffsetAlignment=4096;'
            'p.maxPerStageDescriptorStorageBuffers=9;p.maxDescriptorSetStorageBuffers=9;p.maxPerStageResources=15;'
            'p.maxPerStageDescriptorUniformBuffers=2;p.maxDescriptorSetUniformBuffers=2;'
            'p.maxPerStageDescriptorSampledImages=4;p.maxDescriptorSetSampledImages=4;'
            'p.maxPerStageDescriptorSamplers=4;p.maxDescriptorSetSamplers=4;'
            'int which=argc>1?atoi(argv[1]):0;'
            'if(which==1)p.maxPerStageDescriptorStorageBuffers=8;'
            'if(which==2)p.maxDescriptorSetStorageBuffers=8;'
            'if(which==3)p.maxPerStageResources=14;'
            'if(which==5)p.maxPerStageDescriptorSamplers=3;'
            'if(which==6)p.maxDescriptorSetSampledImages=3;'
            'if(which==7)p.maxDescriptorSetUniformBuffers=1;'
            'return interval_device_caps(&p,which==8?14:15,which==4?VK_FALSE:VK_TRUE);}')
        flags=shlex.split(subprocess.check_output(['pkg-config','--cflags','--libs','vulkan'],text=True))
        subprocess.run(['cc','-std=c11','-O2','-DNDEBUG','-Wall','-Wextra','-Werror',str(source),*flags,'-o',str(helper)],check=True,capture_output=True)
        self.assertEqual(subprocess.run([str(helper)],capture_output=True).returncode,0)
        for case in range(1,9):
            with self.subTest(cap=case):self.assertEqual(subprocess.run([str(helper),str(case)],capture_output=True).returncode,2)

    def test_production_statistics_serializer_is_structured_json(self):
        source=self.root/'stats-fixture.c';helper=self.root/'stats-fixture'
        source.write_text('#define main owned_profile_main\n#include "vulkan_compute_interval_replay.c"\n#undef main\n'
            'int main(void){VkPipelineExecutableStatisticKHR s={0};s.format=VK_PIPELINE_EXECUTABLE_STATISTIC_FORMAT_UINT64_KHR;'
            'strcpy(s.name,"Spills");strcpy(s.description,"quoted \\\"text\\\"");s.value.u64=123;putchar(\'{\');interval_stats_json(1,&s);puts("}");return 0;}\n')
        flags=shlex.split(subprocess.check_output(['pkg-config','--cflags','--libs','vulkan'],text=True))
        subprocess.run(['cc','-std=c11','-O2','-DNDEBUG','-Wall','-Wextra','-Werror',str(source),*flags,'-o',str(helper)],check=True,capture_output=True)
        row=json.loads(subprocess.check_output([str(helper)],text=True))['executable_statistics'][0]
        self.assertEqual(row['name'],'Spills');self.assertEqual(row['value'],123);self.assertEqual(row['description'],'quoted "text"')

    def test_selector_owner_rejects_storage_or_physical_aliases(self):
        state={'manifest':{'bindings':[{'binding':0,'element':0,'type':6,'range':2224,'offset':16,'view':'bank'},
              {'binding':6,'element':0,'type':7,'range':256,'offset':0,'view':'writable'}]},
              'views':{'bank':{'allocation':'c0'},'writable':{'allocation':'output'}},'mappings':[]}
        self.assertEqual(replay.prove_selector_owner(state)['allocation'],'c0')
        for change in (lambda x:x['mappings'].append({'allocation':'c0'}),lambda x:x['views']['writable'].update(allocation='c0')):
            changed=copy.deepcopy(state);change(changed)
            with self.assertRaises(ValueError):replay.prove_selector_owner(changed)

    def test_host_graph_extension_rejects_any_original_image_push_or_descriptor_change(self):
        with tempfile.TemporaryDirectory() as temporary:
            frame=self.frame(Path(temporary));original=copy.deepcopy(frame)
            original['allocations'].pop();original['descriptors'].pop()
            original.pop('module_sha256');original.pop('record_sha256')
            guarded=Path(frame['allocations'][-1]['initial'])
            replay.assert_graph_extension(original,frame,replay.ORIGINAL,guarded)
            edits=[lambda x:x['images'][0]['words'].__setitem__(0,37),
                   lambda x:x['descriptors'][2].__setitem__(8,128),
                   lambda x:x['mappings'].__setitem__(0,(0,59,0)),
                   lambda x:x['samplers'][0].__setitem__(1,1),
                   lambda x:x.__setitem__('root_offset',40)]
            for edit in edits:
                changed=copy.deepcopy(frame);edit(changed)
                with self.assertRaises(ValueError):replay.assert_graph_extension(original,changed,replay.ORIGINAL,guarded)

    def test_execution_rejects_bad_plan_before_starting_process(self):
        with tempfile.TemporaryDirectory() as temporary,patch.object(replay.subprocess,'Popen') as child:
            with self.assertRaises((ValueError,OSError)):
                replay.run(temporary,'candidate32',Path(temporary)/'out','missing','0'*64)
            child.assert_not_called()


if __name__=='__main__':unittest.main()
