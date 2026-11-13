# SPDX-License-Identifier: GPL-3.0-or-later
import json
from pathlib import Path
import shutil
import struct
import subprocess
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from tools import compute_interval_array_lowering as alternative


def fixture(width):
    """Independently authored metadata/load helper, never game instructions."""
    base=alternative.dependencies();value='%7' if width==4 else '%10';pointer='%54' if width==4 else '%79'
    annotations=[f'OpDecorate %2011 ArrayStride {width}','OpDecorate %2012 Block','OpMemberDecorate %2012 0 Offset 0']
    roots=('%55','%56','%57','%58','%59','%63')
    for binding,root in enumerate(roots,2):annotations.extend((f'OpDecorate {root} DescriptorSet 0',f'OpDecorate {root} Binding {binding}'))
    globals_text=f'''%1 = OpTypeVoid
%2 = OpTypeBool
%7 = OpTypeInt 32 0
%23 = OpTypeInt 64 0
%10 = OpTypeVector %7 4
%26 = OpConstant %7 0
%60 = OpConstant %7 3
%54 = OpTypePointer StorageBuffer %7
%79 = OpTypePointer StorageBuffer %10
%110 = OpTypePointer Function %7
%106 = OpTypeFunction {value} %23
%1207 = OpTypeFunction %1
%2011 = OpTypeRuntimeArray {value}
%2012 = OpTypeStruct %2011
%2013 = OpTypePointer StorageBuffer %2012
%2014 = OpTypeArray %2012 %60
%2015 = OpTypePointer StorageBuffer %2014
%2016 = OpConstant %23 0
%2017 = OpConstantNull {value}
%55 = OpVariable %2013 StorageBuffer
%56 = OpVariable %2013 StorageBuffer
%57 = OpVariable %2013 StorageBuffer
%58 = OpVariable %2013 StorageBuffer
%59 = OpVariable %2013 StorageBuffer
%63 = OpVariable %2015 StorageBuffer
'''
    helper={'function':'%107','parameter':'%108','entry':'%109','width':width,'return_type':value,
        'function_header':f'%107 = OpFunction {value} DontInline %106','parameter_line':'%108 = OpFunctionParameter %23',
        'miss_label':'%247','miss_line':0,'end_line':3,'roots':[{'variable':root,'element_pointer_type':pointer} for root in roots]}
    original=SimpleNamespace(lines=['%247 = OpLabel','OpReturnValue %2017','OpFunctionEnd'])
    emitter=alternative.emitter_type(base)(base.Assembly(globals_text));emitter.layout();body=emitter.helper(original,helper)
    interfaces=[emitter.record_variable,emitter.array_variables[4],emitter.array_variables[16]]
    def module(candidate):
        entry='OpEntryPoint GLCompute %1209 "main" '+' '.join(roots+(tuple(interfaces) if candidate else ()))
        header=['OpCapability Shader','OpCapability Int64']
        if candidate:header+=['OpCapability ShaderNonUniform','OpCapability StorageBufferArrayNonUniformIndexing']
        header+=['OpMemoryModel Logical GLSL450',entry,'OpExecutionMode %1209 LocalSize 64 1 1',*annotations]
        if candidate:header+=emitter.annotations
        header+=[globals_text]
        if candidate:header+=emitter.globals+body
        else:header+=[helper['function_header'],helper['parameter_line'],'%109 = OpLabel','OpBranch %247',*original.lines]
        header+=['%1209 = OpFunction %1 None %1207','%1210 = OpLabel',f'%1211 = OpFunctionCall {value} %107 %2016','OpReturn','OpFunctionEnd']
        return '\n'.join(header)+'\n'
    return module(False),module(True),helper,interfaces


def source_bindings():
    return {'bindings':[{'binding':binding,'element':0,'type':7,'view':f'view-{binding}','offset':16*binding,'range':64} for binding in range(2,7)]+
        [{'binding':7,'element':element,'type':7,'view':'view-table' if element==0 else None,'offset':112 if element==0 else 0,'range':64 if element==0 else 0} for element in range(3)]}


class ArrayLoweringTests(unittest.TestCase):
    def test_alias_contract_keeps_exact_root_order_ranges_and_nulls(self):
        manifest=source_bindings();plan=alternative.alias_plan(manifest);proof=alternative.verify_alias_plan(manifest,plan)
        self.assertEqual([row['element'] for row in plan],list(range(8)))
        self.assertEqual(plan[4]['same_VkBuffer_as_original'],{'binding':6,'element':0})
        self.assertEqual((plan[4]['view'],plan[4]['offset'],plan[4]['range']),('view-6',96,64))
        self.assertTrue(plan[6]['null']);self.assertTrue(plan[7]['null'])
        self.assertFalse(proof['VkBuffer_handle_equality_checked'])
        for key,value in (('view','equal-byte-copy'),('offset',112),('range',63)):
            bad=[dict(row) for row in plan];bad[4][key]=value
            with self.assertRaises(ValueError):alternative.verify_alias_plan(manifest,bad)

    def test_route_cannot_silently_change_descriptor_class(self):
        self.assertEqual([alternative.array_route(binding,0) for binding in range(2,7)],list(range(5)))
        self.assertEqual([alternative.array_route(7,element) for element in range(3)],[5,6,7])
        for values in ((6,1),(7,3),(13,0),(True,0)):
            with self.assertRaises(ValueError):alternative.array_route(*values)

    def test_original_store_and_array_load_use_same_backing_and_typed_floor(self):
        base=alternative.dependencies();manifest=source_bindings();aliases=alternative.alias_plan(manifest)
        backing={f'view-{binding}':bytearray(256) for binding in range(2,7)}
        views=[base.View(0x1000,64,10,2,0,'single'),base.View(0x1000,64,64,6,0,'single')]
        partitions=base.build_partitions(views);base.certify_partitions(views,partitions)
        selected,_=base.binary_identity(partitions[16],0x100f)
        self.assertEqual(selected,(6,0,0x1000))
        route=alternative.array_route(selected[0],selected[1]);self.assertEqual(route,4)
        relative=(((0x100f-selected[2])&0xffffffff)>>4)*16
        old=next(row for row in manifest['bindings'] if (row['binding'],row['element'])==(6,0));new=aliases[route]
        backing[old['view']][old['offset']+relative:old['offset']+relative+16]=b'changed-16-bytes'
        self.assertEqual(bytes(backing[new['view']][new['offset']+relative:new['offset']+relative+16]),b'changed-16-bytes')
        scalar,_=base.binary_identity(partitions[4],0x1004);self.assertEqual(scalar[0],2)

    def test_null_source_cannot_gain_nonzero_range(self):
        manifest=source_bindings();manifest['bindings'][-1]['range']=4
        with self.assertRaises(ValueError):alternative.alias_plan(manifest)

    def test_authored_hit_is_value_return_not_switch_or_variable_pointer(self):
        base=alternative.dependencies()
        for width in (4,16):
            _,text,helper,_=fixture(width);proof=alternative.audit_hit_shape(text,[helper])
            self.assertTrue(proof[0]['NonUniform_route_access_load'])
            self.assertNotIn('OpSwitch',text);self.assertNotIn('OpPhi',text);self.assertNotIn('VariablePointers',text)
            rows={4:[],16:[]};rows[width]=[base.Row(0x1000,0x1040,0x1000,2,0)]
            self.assertEqual(base.audit_lookup_shape(text,[helper],rows)[0]['initial_high'],1)

    def test_missing_nonuniform_and_bad_record_route_or_floor_are_rejected(self):
        base=alternative.dependencies();_,text,helper,_=fixture(16);module=base.Assembly(text)
        prefix=[row for row in module.functions['%107'] if row[3]<module.definitions['%247'][3]]
        returned=next(row[2][0] for row in prefix if row[1]=='OpReturnValue');pointer=module.args(returned,'OpLoad')[1];access=module.args(pointer,'OpAccessChain');route=access[2]
        for identity in (route,pointer,returned):
            changed=text.replace(f'OpDecorate {identity} NonUniform\n','')
            with self.assertRaises(ValueError):alternative.audit_hit_shape(changed,[helper])
        changed=text.replace(f'OpDecorate {access[1]} Binding 13',f'OpDecorate {access[1]} Binding 13\nOpDecorate {access[1]} NonWritable')
        with self.assertRaises(ValueError):alternative.audit_hit_shape(changed,[helper])
        _,array=module.args(module.args(access[1],'OpVariable')[0],'OpTypePointer');block,_=module.args(array,'OpTypeArray')
        changed=text.replace(f'OpDecorate {block} Block',f'OpDecorate {block} Block\nOpMemberDecorate {block} 0 NonWritable')
        with self.assertRaises(ValueError):alternative.audit_hit_shape(changed,[helper])
        runtime=module.args(block,'OpTypeStruct')[0]
        changed=text.replace(f'OpDecorate {runtime} ArrayStride 16',f'OpDecorate {runtime} ArrayStride 8')
        with self.assertRaises(ValueError):alternative.audit_hit_shape(changed,[helper])
        changed=text.replace(f'OpMemberDecorate {block} 0 Offset 0',f'OpMemberDecorate {block} 0 Offset 16')
        with self.assertRaises(ValueError):alternative.audit_hit_shape(changed,[helper])
        route_line=module.lines[module.definitions[route][3]]
        changed=text.replace(route_line,f'{route} = OpCopyObject %7 %26')
        with self.assertRaises(ValueError):alternative.audit_hit_shape(changed,[helper])
        shift_id=access[-1];shift_line=module.lines[module.definitions[shift_id][3]]
        changed=text.replace(shift_line,shift_line.rsplit(' ',1)[0]+' %26')
        with self.assertRaises(ValueError):alternative.audit_hit_shape(changed,[helper])

    def test_search_update_mutation_is_rejected(self):
        base=alternative.dependencies();_,text,helper,_=fixture(4);module=base.Assembly(text)
        rows=module.functions['%107'];bound=next(row for row in rows if row[1]=='OpULessThan');lower=module.args(bound[2][1],'OpLoad')[1]
        update=next(row for row in rows if row[1]=='OpStore' and row[2][0]==lower and module.definitions[row[2][1]][1]=='OpIAdd')
        line=module.lines[module.definitions[update[2][1]][3]];changed=text.replace(line,line.replace('OpIAdd','OpISub'))
        with self.assertRaises(ValueError):base.audit_lookup_shape(changed,[helper],{4:[base.Row(0,16,0,2,0)],16:[]})

    def test_dependency_digest_is_checked_before_import(self):
        with patch.object(alternative,'sha',return_value='0'*64):
            with self.assertRaises(ValueError):alternative.dependencies()

    @unittest.skipUnless(shutil.which('spirv-as') and shutil.which('spirv-val'),'Optional authored SPIRV-Tools validation')
    def test_authored_modules_validate_and_binary_preservation_mutations_fail(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory=Path(temporary)
            for width in (4,16):
                original,candidate,helper,interfaces=fixture(width)
                binaries=[]
                for name,text in (('original',original),('candidate',candidate)):
                    source=directory/(name+'.spvasm');binary=directory/(name+'.spv');source.write_text(text)
                    subprocess.run(['spirv-as','--target-env','vulkan1.3','--preserve-numeric-ids',str(source),'-o',str(binary)],check=True,capture_output=True,text=True,timeout=15)
                    subprocess.run(['spirv-val','--target-env','vulkan1.3',str(binary)],check=True,capture_output=True,text=True,timeout=15)
                    binaries.append(binary.read_bytes())
                report=alternative.verify_preservation(*binaries,[helper],interfaces)
                self.assertTrue(report['original_store_and_main_preserved'])
                self.assertEqual(report['added_capabilities'],[5301,5308])
                reflect=__import__('tools.compile_probe',fromlist=['reflect']).reflect(binaries[1])
                self.assertEqual(sum(row['count'] for row in reflect['bindings']),17)
                self.assertEqual(reflect['bindings'][-1],{'set':0,'binding':13,'type':7,'count':8})
                for victim in ('main','miss','global'):
                    words=list(struct.unpack(f'<{len(binaries[1])//4}I',binaries[1]));at=5;function=None;block=None
                    while at<len(words):
                        count,opcode=words[at]>>16,words[at]&65535
                        if opcode==54:function=words[at+2]
                        if opcode==248:block=words[at+1]
                        if victim=='main' and function==1209 and opcode==253:words[at]=(count<<16)|255;break
                        if victim=='miss' and function==107 and block==247 and opcode==254:words[at+1]=26;break
                        if victim=='global' and opcode==43 and words[at+2]==60:words[at+3]=2;break
                        at+=count
                    with self.assertRaises(ValueError):alternative.verify_preservation(binaries[0],struct.pack(f'<{len(words)}I',*words),[helper],interfaces)


if __name__=='__main__':unittest.main()
