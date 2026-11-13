# SPDX-License-Identifier: GPL-3.0-or-later
from argparse import Namespace
import json
import os
from pathlib import Path
import shutil
import struct
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from tools import descriptor_array_runtime as runtime


class ArrayRuntimeBoundaryTests(unittest.TestCase):
    def test_changed_frozen_dependency_is_refused(self):
        with patch.object(runtime,'file_sha',return_value='0'*64):
            with self.assertRaises(ValueError):runtime.load_dependency('tools/descriptor_array_runtime_fixture.py')

    def test_unreviewed_plan_is_refused_before_any_child(self):
        (runtime.ROOT/'local').mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=runtime.ROOT/'local') as directory:
            path=Path(directory)/'plan.json';path.write_text('{}')
            with patch.object(runtime.subprocess,'Popen') as child:
                with self.assertRaises(ValueError):runtime.admit(path,'0'*64)
                child.assert_not_called()

    def test_owned_collector_does_not_limit_large_memfd(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            code="import os; f=os.memfd_create('CPU-array-fixture'); os.ftruncate(f,2<<30); os.write(1,b'ok')"
            terminal=runtime.collect([sys.executable,'-c',code],dict(os.environ),root/'out',root/'err')
            self.assertEqual(terminal['returncode'],0)
            self.assertEqual((root/'out').read_bytes(),b'ok')
            self.assertFalse(terminal['child_resource_limits_overridden'])
            self.assertTrue(terminal['child_reaped'])

    def test_native_execution_boundary_is_fixed_and_has_no_idle_wait(self):
        source=(runtime.ROOT/'src/probes/descriptor_array_runtime.c').read_text()
        self.assertEqual(source.count('vkCmdDispatch(command,1,1,1)'),1)
        self.assertEqual(source.count('vkQueueSubmit('),1)
        self.assertIn('UINT64_C(2000000000)',source)
        self.assertIn('alarm(25)',source)
        self.assertNotIn('vkDeviceWaitIdle',source)
        self.assertNotIn('vkQueueWaitIdle',source)
        self.assertLess(source.index('if(!execute){puts('),source.index('CHECK(vkCreateInstance'))
        self.assertIn('descriptor_info[1].buffer==descriptor_info[2].buffer',source)
        self.assertIn('O_CREAT|O_EXCL',source)


def native_offsets(control):
    at=20
    def skip_string():
        nonlocal at
        n=struct.unpack_from('<I',control,at)[0];at+=4+n
    def skip_artifact():
        nonlocal at
        skip_string();digest=at;at+=32;return digest
    skip_string();skip_string();at+=4
    code=skip_artifact();push=skip_artifact();allocations=[]
    for _ in range(10):
        at+=4;allocations.append((skip_artifact(),skip_artifact()))
    descriptors=at+4;at+=4+7*12
    relocations=at+4
    return {'code':code,'push':push,'allocations':allocations,'descriptors':descriptors,'relocations':relocations}


@unittest.skipUnless(os.environ.get('OPEN_OCARINA_ARRAY_RUNTIME_NATIVE_TESTS')=='1','Optional CPU native integration; needs the pinned open-source E5 driver and standard compiler tools')
class ArrayRuntimeNativeCPUTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        for tool in ('cc','pkg-config','glslangValidator','spirv-as','spirv-dis','spirv-val'):
            if not shutil.which(tool):raise unittest.SkipTest('Missing external CPU tool: '+tool)
        icd=runtime.ROOT/'local/mesa-cache-safe-v1/driver/intel_icd.json'
        if not icd.exists():raise unittest.SkipTest('Pinned open-source E5 build is unavailable')
        cls.temporary=tempfile.TemporaryDirectory(prefix='array-native-tests-',dir=runtime.ROOT/'local')
        cls.addClassCleanup(cls.temporary.cleanup)
        root=Path(cls.temporary.name);fixture=runtime.load_dependency('tools/descriptor_array_runtime_fixture.py')
        fixture.prepare(root/'fixtures')
        if any(runtime.file_sha(root/'fixtures'/(variant+'.spv'))!=digest for variant,digest in runtime.MODULES.items()):raise unittest.SkipTest('External assembler/compiler output differs from frozen reviewed modules')
        cls.report=runtime.prepare(Namespace(output=root/'prepared',fixtures=root/'fixtures',driver=icd,cc='cc',validator='spirv-val'))
        cls.root=root/'prepared';cls.case=cls.root/'follow-off-pointer';cls.trap=cls.root/'native-CPU-trap'

    def validate_native(self,data):
        path=self.root/'mutation-control.bin';path.write_bytes(data)
        return subprocess.run([str(self.trap),'--validate',str(path)],capture_output=True,text=True,timeout=5)

    def test_all_four_exact_plans_pass_CPU_validation_with_instance_trap(self):
        self.assertEqual(len(self.report['plans']),4)
        for item in self.report['plans']:
            plan=runtime.admit(Path(item['plan']),item['reviewed_plan_sha256'])
            self.assertEqual(len(plan['CPU_validations']),2)
            self.assertTrue(any(validation['linker_trap'] for validation in plan['CPU_validations']))
            self.assertFalse((Path(plan['directory'])/'control.bin.used').exists())
            self.assertFalse((Path(plan['directory'])/'reviewed.used').exists())

    def test_wrong_alias_descriptor_and_owned_relocation_fail_before_instance(self):
        original=(self.case/'control.bin').read_bytes();offsets=native_offsets(original)
        for offset,value in ((offsets['descriptors']+12,1),(offsets['descriptors']+8,8191),(offsets['relocations']+4,0),(offsets['relocations']+8,65)):
            control=bytearray(original);struct.pack_into('<I',control,offset,value)
            result=self.validate_native(control)
            self.assertEqual(result.returncode,2,(offset,result.stderr))
            self.assertNotEqual(result.returncode,99)

    def test_wrong_shader_even_with_matching_artifact_hash_is_refused(self):
        path=self.case/'module.spv';original=path.read_bytes();control=bytearray((self.case/'control.bin').read_bytes())
        changed=bytearray(original);changed[-1]^=1
        try:
            path.write_bytes(changed);at=native_offsets(control)['code'];control[at:at+32]=bytes.fromhex(runtime.sha(changed))
            self.assertEqual(self.validate_native(control).returncode,2)
        finally:path.write_bytes(original)

    def test_wrong_input_guard_query_and_golden_bytes_fail_after_rehash(self):
        original_control=(self.case/'control.bin').read_bytes();offsets=native_offsets(original_control)
        for index,mode,byte in ((0,'initial',0),(7,'initial',64),(8,'expected',64),(9,'initial',64+16*8)):
            plan=json.loads((self.case/'fixture/plan.json').read_text());allocation=plan['allocations'][index]
            path=self.case/'fixture'/allocation[mode+'_file'];original=path.read_bytes();changed=bytearray(original);changed[byte]^=1
            control=bytearray(original_control);digest_at=offsets['allocations'][index][int(mode=='expected')]
            try:
                path.write_bytes(changed);control[digest_at:digest_at+32]=bytes.fromhex(runtime.sha(changed))
                self.assertEqual(self.validate_native(control).returncode,2,(index,mode))
            finally:path.write_bytes(original)

    def test_complete_independent_upload_readback_comparison_detects_forgery(self):
        item=self.report['plans'][0];plan=runtime.admit(Path(item['plan']),item['reviewed_plan_sha256']);case=Path(plan['directory'])
        output=self.root/'CPU-fake-readback';output.mkdir()
        fixture=json.loads((case/'fixture/plan.json').read_text());addresses={name:0x100000+index*0x10000 for index,name in enumerate(runtime.NAMES)}
        initial={a['name']:(case/'fixture'/a['initial_file']).read_bytes() for a in fixture['allocations']}
        golden={a['name']:(case/'fixture'/a['expected_file']).read_bytes() for a in fixture['allocations']}
        for mapping in (initial,golden):
            table=bytearray(mapping['page_table'])
            for relocation in fixture['page_table_relocations']:struct.pack_into('<Q',table,relocation['write_offset'],addresses[relocation['target_allocation']]+relocation['target_offset'])
            mapping['page_table']=bytes(table)
        upload=[];allocations=[]
        for name,size in zip(runtime.NAMES,runtime.SIZES):
            (output/(name+'-uploaded.bin')).write_bytes(initial[name]);(output/(name+'-expected-relocated.bin')).write_bytes(golden[name]);(output/(name+'-actual.bin')).write_bytes(golden[name])
            upload.append({'name':name,'address':addresses[name],'bytes':size,'uploaded_sha256':runtime.sha(initial[name]),'expected_sha256':runtime.sha(golden[name])})
            allocations.append({'name':name,'address':addresses[name],'bytes':size,'first_difference':None})
        push=bytearray((case/'fixture/push-initial.bin').read_bytes());struct.pack_into('<Q',push,32,addresses['page_table']+64);(output/'push-uploaded.bin').write_bytes(push)
        features={key:True for key in ('variablePointersStorageBuffer','shaderStorageBufferArrayNonUniformIndexing','shaderInt64','bufferDeviceAddress','robustBufferAccess','robustBufferAccess2','subgroupSizeControl','computeFullSubgroups')}
        native={'dispatches':1,'actual_subgroup_size':32,'actual_stage_flags':32,'both_pointer_features_enabled':True,'enabled_features':features,'all_bytes_match':True,'compared_allocations':10,'compared_bytes':74458,'follow_stores':False,'allocations':allocations}
        (output/'native-result.json').write_bytes(runtime.canonical(native));(output/'upload.json').write_bytes(runtime.canonical({'push_sha256':runtime.sha(push),'allocations':upload}))
        self.assertTrue(runtime.compare_execution(plan,output)[1]['match'])
        for name,suffix,byte in (('shared','actual',64+256),('results','actual',0),('records','uploaded',64),('page_table','expected-relocated',64+16*8)):
            path=output/(name+'-'+suffix+'.bin');old=path.read_bytes();changed=bytearray(old);changed[byte]^=1
            try:
                path.write_bytes(changed)
                with self.assertRaises(ValueError):runtime.compare_execution(plan,output)
            finally:path.write_bytes(old)


if __name__=='__main__':unittest.main()
