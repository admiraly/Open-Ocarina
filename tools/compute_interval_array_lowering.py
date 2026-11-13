#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""CPU-only descriptor-array alternative for two exact compute load prefixes.

No Vulkan, compiler pipeline, replay ABI or vendor code is modified. Transformed
game-derived SPIR-V and disassembly remain private analysis outputs.
"""
from collections import Counter
import argparse
import hashlib
import importlib
import json
from pathlib import Path
import re
import struct
import subprocess
import sys

ROOT=Path(__file__).resolve().parents[1]
DEPENDENCIES={'tools/compute_interval_lowering.py':'3176a7e0130b5d722a88fdb2d5ab9512fff4afecf78e10828eb6747b38ca00ca',
              'tools/compile_probe.py':'3f545e63c23ff2f3e8b24c77941fb6e2519a2371e913be3df76b87f1667d72af',
              'tools/physical_span_contract.py':'f60dabf565b21b5b883d2784541682c27b88df8520bc16fffe5117d115d6ac33'}
TARGET='b9af52c0ba07f64eb8b0ca2905c432d094999fdf669aa95182ff7754b510e97b'
MANIFEST='4c4b5fcaf73318b8edc08406a58e39a2702544f86183e7d894455e7d363206b9'
RECORD='49da4bdc0c4542b4fef7b5dcbb631d41fb030d80803cde3bb37185722daf9770'
ARRAY_BINDING=13
NEW_CAPABILITIES={5301,5308}


def sha(data):return hashlib.sha256(data).hexdigest()
def need(value,message):
    if not value:raise ValueError(message)


def dependencies():
    for name,expected in DEPENDENCIES.items():need(sha((ROOT/name).read_bytes())==expected,'Frozen dependency changed: '+name)
    if str(ROOT) not in sys.path:sys.path.insert(0,str(ROOT))
    return importlib.import_module('tools.compute_interval_lowering')


def array_route(binding,element):
    need(type(binding) is int and type(element) is int,'Descriptor identity must be integral')
    need((2<=binding<=6 and element==0) or (binding==7 and 0<=element<3),'Unexpected original root')
    return binding-2 if binding<7 else 5+element


def alias_plan(manifest):
    """Require literal original descriptor identity, not equal copied bytes."""
    bindings={(row['binding'],row['element']):row for row in manifest['bindings']}
    need(len(bindings)==len(manifest['bindings']),'Duplicate source binding identity')
    need(not any(binding in (12,13) for binding,_ in bindings),'Additional bindings conflict')
    result=[]
    for binding,element in [(binding,0) for binding in range(2,7)]+[(7,element) for element in range(3)]:
        source=bindings[binding,element]
        need(source['type']==7 and type(source['offset']) is int and type(source['range']) is int,'Expected exact storage descriptor')
        need(source['view'] is not None or (source['offset']==0 and source['range']==0),'Null source range differs')
        result.append({'binding':13,'element':array_route(binding,element),'type':7,'view':source['view'],'offset':source['offset'],'range':source['range'],
                       'same_VkBuffer_as_original':{'binding':binding,'element':element},'null':source['view'] is None})
    return result


def verify_alias_plan(original,supplied):
    expected=alias_plan(original)
    need(supplied==expected,'Array alias must retain original VkBuffer/view, offset, range, null and route order')
    return {'entries':8,'source_roots':[[row['same_VkBuffer_as_original']['binding'],row['same_VkBuffer_as_original']['element']] for row in expected],
            'direct_binding6_store_alias_route':4,'VkBuffer_handle_equality_checked':False,
            'future_host_obligation':'Bind each array element to exactly the original VkBuffer/offset/range/null; comparing allocation bytes is insufficient.'}


def emitter_type(base):
    class ArrayEmitter(base.Emitter):
        def layout(self):
            super().layout();self.array_variables={};count=self.constant(8)
            for width,value in ((4,'%7'),(16,'%10')):
                runtime,block,array,pointer,variable=[self.new() for _ in range(5)]
                self.globals.extend([f'{runtime} = OpTypeRuntimeArray {value}',f'{block} = OpTypeStruct {runtime}',
                    f'{array} = OpTypeArray {block} {count}',f'{pointer} = OpTypePointer StorageBuffer {array}',f'{variable} = OpVariable {pointer} StorageBuffer'])
                self.annotations.extend([f'OpDecorate {runtime} ArrayStride {width}',f'OpDecorate {block} Block',
                    f'OpMemberDecorate {block} 0 Offset 0',f'OpDecorate {variable} DescriptorSet 0',f'OpDecorate {variable} Binding {ARRAY_BINDING}'])
                self.array_variables[width]=variable

        def helper(self,original,helper):
            lines=super().helper(original,helper)
            parsed=base.Assembly('\n'.join(lines))
            switches=[row for row in parsed.rows if row[1]=='OpSwitch']
            need(len(switches)==1,'Expected exactly one generated eight-way hit switch')
            _,_,args,at=switches[0];selector,merge=args[:2]
            need(lines[at-1]==f'OpSelectionMerge {merge} None','Hit selection shape changed')
            end=next(index for index in range(at+1,len(lines)) if lines[index]==f'{merge} = OpLabel')
            need(lines[end+1].startswith('OpBranch '),'Static hit merge shape differs')
            accesses=[row for row in parsed.rows if at<row[3]<end and row[1]=='OpAccessChain']
            need(len(accesses)==8,'Expected one static access per original route')
            index=accesses[0][2][-1]
            need(all(row[2][-1]==index for row in accesses),'Typed indices differ across original routes')
            route,pointer,value=[self.new() for _ in range(3)]
            self.annotations.extend(f'OpDecorate {identity} NonUniform' for identity in (route,pointer,value))
            replacement=[f'{route} = OpCopyObject %7 {selector}',
                f'{pointer} = OpAccessChain {helper["roots"][0]["element_pointer_type"]} {self.array_variables[helper["width"]]} {route} {self.constant(0)} {index}',
                f'{value} = OpLoad {helper["return_type"]} {pointer}',f'OpReturnValue {value}']
            return lines[:at-1]+replacement+lines[end+2:]
    return ArrayEmitter


def transform(text):
    base=dependencies();module,helpers=base.discover_target(text)
    need(not any(module.binding(identity)==ARRAY_BINDING for identity,opcode,_,_ in module.rows if opcode=='OpVariable' and 'Binding' in module.decorations.get(identity,{})),'Binding13 already exists')
    emitter=emitter_type(base)(module);emitter.layout()
    replacements={helper['start_line']:(helper['end_line'],emitter.helper(module,helper)) for helper in helpers}
    removed=set()
    for helper in helpers:
        keep={helper['function'],helper['parameter'],helper['entry']}
        removed.update(result for result,_,_,line in module.functions[helper['function']] if helper['start_line']<=line<helper['miss_line'] and result not in keep and result is not None)
    annotations=next(number for _,opcode,_,number in module.rows if opcode.startswith('OpType'))
    globals_at=min(row[3] for rows in module.functions.values() for row in rows)
    interfaces=[emitter.record_variable,emitter.array_variables[4],emitter.array_variables[16]]
    lines=['OpCapability ShaderNonUniform','OpCapability StorageBufferArrayNonUniformIndexing'];at=0
    while at<len(module.lines):
        if at==annotations:lines.extend(emitter.annotations)
        if at==globals_at:lines.extend(emitter.globals)
        if at in replacements:at,replacement=replacements[at];lines.extend(replacement);continue
        line=module.lines[at];identities=set(re.findall(r'%\d+',line))
        if identities&removed:need(line.startswith(('OpName ','OpDecorate ')),'Removed helper value escapes function')
        else:
            if line.startswith('OpEntryPoint GLCompute '):line+=' '+' '.join(interfaces)
            lines.append(line)
        at+=1
    return '\n'.join(lines)+'\n',helpers,interfaces


def binary_rows(data):
    need(len(data)>=20 and len(data)%4==0,'Malformed binary size')
    words=struct.unpack(f'<{len(data)//4}I',data);rows=[];at=5
    while at<len(words):
        count,opcode=words[at]>>16,words[at]&65535
        need(count and at+count<=len(words),'Malformed binary framing')
        rows.append((opcode,tuple(words[at:at+count])));at+=count
    return words[:5],rows


def verify_preservation(original,candidate,helpers,interfaces):
    base=dependencies();old_header,old_rows=binary_rows(original);new_header,new_rows=binary_rows(candidate)
    need(old_header[:2]==new_header[:2] and old_header[4]==new_header[4],'Original version/magic/schema changed')
    left,right=base.function_words(original),base.function_words(candidate);changed={int(helper['function'][1:]) for helper in helpers}
    need(set(left)==set(right),'Function inventory changed')
    untouched=[]
    for function,words in left.items():
        if function not in changed:
            need(words==right[function],'Original main/store/work or untouched helper changed')
            untouched.append({'function_id':function,'sha256':sha(struct.pack(f'<{len(words)}I',*words))})
    misses=[]
    for helper in helpers:
        identity=int(helper['function'][1:]);label=int(helper['miss_label'][1:])
        def suffix(words):
            at=0
            while at<len(words):
                count,opcode=words[at]>>16,words[at]&65535
                if opcode==248 and words[at+1]==label:return words[at:]
                at+=count
            raise ValueError('Physical MISS label disappeared')
        old,new=suffix(left[identity]),suffix(right[identity]);need(old==new,'Original physical MISS suffix changed')
        misses.append({'width':helper['width'],'unchanged_binary_words':len(old)})
    def globals_only(rows):
        result=[]
        for opcode,row in rows:
            if opcode==54:break
            result.append((opcode,row))
        return result
    original_globals=globals_only(old_rows);retained=[];extra=[];caps=[]
    for opcode,row in globals_only(new_rows):
        if opcode==17 and row[1] in NEW_CAPABILITIES:caps.append(row[1]);continue
        result=row[1] if 19<=opcode<=38 else row[2] if opcode in (41,42,43,44,59) else None
        target=row[1] if opcode in (5,71,72) else None
        if (result is not None and result>=old_header[3]) or (target is not None and target>=old_header[3]):extra.append(opcode)
        else:retained.append((opcode,row))
    need(sorted(caps)==sorted(NEW_CAPABILITIES),'Added capabilities differ')
    need(len(retained)==len(original_globals),'Original global instruction count changed')
    entry_count=0;added=tuple(int(identity[1:]) for identity in interfaces)
    for (op,old),(newop,new) in zip(original_globals,retained):
        if old==new:continue
        need(op==newop==15 and old[1:]==new[1:-3] and new[-3:]==added,'Original capability/type/decoration/global changed')
        entry_count+=1
    need(entry_count==1,'Expected only the extended main entry interface')
    return {'all_other_function_instruction_words_identical':True,'untouched_functions':untouched,'physical_miss_suffixes':misses,
        'modified_functions':sorted(changed),'original_globals_preserved':True,'added_capabilities':sorted(caps),'added_global_opcodes':dict(Counter(extra)),
        'entry_interface_additions':list(added),'original_store_and_main_preserved':True}


def audit_hit_shape(text,helpers):
    base=dependencies();module=base.Assembly(text);reports=[]
    forbidden={'NonWritable','Restrict','RestrictPointer'}
    for helper in helpers:
        prefix=[row for row in module.functions[helper['function']] if row[3]<module.definitions[helper['miss_label']][3]]
        need(not any(row[1] in ('OpSwitch','OpPhi','OpConvertUToPtr','OpPtrAccessChain') for row in prefix),'Hit prefix contains switch/pointer selection/physical conversion')
        returns=[row for row in prefix if row[1]=='OpReturnValue'];need(len(returns)==1,'Expected exactly one typed value hit return')
        load_id=returns[0][2][0];load=module.args(load_id,'OpLoad');need(load[0]==helper['return_type'],'Hit return type differs')
        pointer_id=load[1];pointer=module.args(pointer_id,'OpAccessChain');root=pointer[1]
        need(module.binding(root)==ARRAY_BINDING and len(pointer)==5 and pointer[0]==helper['roots'][0]['element_pointer_type'],'Hit does not use reviewed typed descriptor array')
        route_id=pointer[2];route=module.args(route_id,'OpCopyObject');need(route[0]=='%7','Descriptor route is not uint32')
        selected=module.args(route[1],'OpLoad');field=module.args(selected[1],'OpAccessChain')
        need(module.binding(field[1])==12 and module.constant(field[2])==4 and module.constant(field[-1])==6,'Descriptor route did not come from original record route field')
        need(module.constant(pointer[3])==0,'Typed block member differs')
        index=module.args(pointer[4],'OpShiftRightLogical');converted=module.args(index[1],'OpUConvert');delta=module.args(converted[1],'OpISub')
        need(index[0]=='%7' and module.constant(index[2])==helper['width'].bit_length()-1 and converted[0]=='%7' and delta[0:2]==['%23',helper['parameter']],'Typed U32 conversion/floor rule changed')
        base_load=module.args(delta[2],'OpLoad');base_field=module.args(base_load[1],'OpAccessChain')
        need(base_field[1]==field[1] and base_field[3]==field[3] and module.constant(base_field[-1])==2,'Typed offset uses another interval/base')
        decorations={target:{args[1] for _,op,args,_ in module.rows if op=='OpDecorate' and args[0]==target} for target in (route_id,pointer_id,load_id,root)}
        need(all('NonUniform' in decorations[target] for target in (route_id,pointer_id,load_id)),'Route/access/load lacks NonUniform')
        need(not any(decorations[target]&forbidden for target in decorations),'Alias path acquired a readonly/restrict promise')
        pointer_storage,array=module.args(module.args(root,'OpVariable')[0],'OpTypePointer')
        block,count=module.args(array,'OpTypeArray');members=module.args(block,'OpTypeStruct')
        need(pointer_storage=='StorageBuffer' and module.constant(count)==8 and len(members)==1 and module.args(members[0],'OpTypeRuntimeArray')==[helper['return_type']],'Typed array root shape differs')
        need(module.decorations.get(members[0],{}).get('ArrayStride')==str(helper['width']) and
            any(op=='OpMemberDecorate' and args==[block,'0','Offset','0'] for _,op,args,_ in module.rows),'Typed array stride/member offset changed')
        need(module.args(pointer[0],'OpTypePointer')==['StorageBuffer',helper['return_type']],'Typed load pointer storage/value type changed')
        alias_types={module.args(root,'OpVariable')[0],array,block,members[0]}
        need(not any(op=='OpDecorate' and args[0] in alias_types and args[1] in forbidden for _,op,args,_ in module.rows),'Alias type acquired readonly/restrict decoration')
        need(not any(op=='OpMemberDecorate' and args[0]==block and args[2] in forbidden for _,op,args,_ in module.rows),'Alias member acquired readonly/restrict decoration')
        reports.append({'width':helper['width'],'array_binding':13,'descriptor_count':8,'typed_storage_value_return':True,
            'NonUniform_route_access_load':True,'pointer_return_or_phi_added':False,'alias_readonly_or_restrict_added':False})
    return reports


def build(spirv,manifest,output):
    base=dependencies();need(sha(spirv.read_bytes())==TARGET,'Unknown original module');need(sha(manifest.read_bytes())==MANIFEST,'Unknown owned graph')
    output=output.resolve();need(not output.exists() and output.is_relative_to(ROOT/'local'),'Use fresh private local output');output.mkdir(parents=True)
    def command(name,args):
        result=subprocess.run(args,capture_output=True,text=True,timeout=30);(output/(name+'.stdout')).write_text(result.stdout);(output/(name+'.stderr')).write_text(result.stderr)
        need(result.returncode==0,name+' failed; inspect private diagnostics');return result.stdout
    command('original-val',['spirv-val','--target-env','vulkan1.3','--uniform-buffer-standard-layout',str(spirv)])
    text=command('original-dis',['spirv-dis','--raw-id','--no-indent',str(spirv)]);candidate,helpers,interfaces=transform(text)
    views,frozen=base.frozen_views(manifest,helpers[0]['selectors']);partitions=base.build_partitions(views);selection=base.certify_partitions(views,partitions)
    records=base.pack_records(partitions);need(sha(records)==RECORD,'Original interval record differs')
    aliases=alias_plan(json.loads(manifest.read_text()));alias_proof=verify_alias_plan(json.loads(manifest.read_text()),aliases)
    (output/'records.bin').write_bytes(records);(output/'original.spv').write_bytes(spirv.read_bytes());(output/'candidate.spvasm').write_text(candidate)
    command('candidate-as',['spirv-as','--target-env','vulkan1.3','--preserve-numeric-ids',str(output/'candidate.spvasm'),'-o',str(output/'candidate.spv')])
    command('candidate-val',['spirv-val','--target-env','vulkan1.3','--uniform-buffer-standard-layout',str(output/'candidate.spv')])
    data=(output/'candidate.spv').read_bytes();preservation=verify_preservation(spirv.read_bytes(),data,helpers,interfaces)
    hits=audit_hit_shape(candidate,helpers);finite=base.audit_lookup_shape(candidate,helpers,partitions)
    reflection=importlib.import_module('tools.compile_probe').reflect(data)
    need(sum(row['count'] for row in reflection['bindings'])==23 and sum(row['count'] for row in reflection['bindings'] if row['type']==7)==17,'Descriptor reflection capacity differs')
    need(reflection['bindings'][-2:]==[{'set':0,'binding':12,'type':7,'count':1},{'set':0,'binding':13,'type':7,'count':8}],'Extra binding layout differs')
    (output/'alias-plan.json').write_text(json.dumps(aliases,indent=2)+'\n')
    module=base.Assembly(text);stores=[]
    for _,op,args,_ in module.functions['%1209']:
        if op=='OpStore':
            access=module.args(args[0],'OpAccessChain');need(module.binding(access[1])==6,'Unexpected original direct store binding');stores.append({'binding':6,'root_variable':access[1],'pointer_type':access[0]})
    report={'schema':'open-ocarina.compute-interval-array-lowering/v1','CPU_only':True,'Vulkan_opened':False,'pipeline_compiled':False,'dispatch_executed':False,'execution_admitted':False,
        'original_sha256':TARGET,'candidate_sha256':sha(data),'candidate_bytes':len(data),'tool_sha256':sha(Path(__file__).read_bytes()),'dependencies':DEPENDENCIES,
        'manifest_sha256':MANIFEST,'record_sha256':sha(records),'record_bytes':len(records),'alias_plan_sha256':sha((output/'alias-plan.json').read_bytes()),
        'reflection':reflection,'preservation':preservation,'hit_path_audit':hits,'finite_lookup_audit':finite,'selection_proof':selection,'alias_proof':alias_proof,
        'original_main_direct_store_sites':stores,'required_additional_features':['shaderStorageBufferArrayNonUniformIndexing'],
        'new_capabilities':['ShaderNonUniform','StorageBufferArrayNonUniformIndexing'],'VariablePointers_added':False,
        'descriptor_capacity':{'total':23,'storage_buffers':17,'device_limits_queried':False},'frozen_inputs':frozen,
        'limitations':['Owned frozen records only; actual VkBuffer alias identity remains a future host obligation.',
            'No Vulkan or pipeline compilation. Descriptor/push/null limits and nonuniform feature capacity have not been queried for this23-resource layout.',
            'Existing replay ABI cannot admit binding13 or these aliases; no new replay ABI is implemented here.',
            'Prior synthetic alias fixture supports bounded feasibility, not proof of this module or full native rendering.',
            'Code generation, spills, instruction count, alias correctness and execution results remain unknown for this new candidate.']}
    (output/'report.json').write_text(json.dumps(report,indent=2)+'\n');return report


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--spirv',type=Path,required=True);parser.add_argument('--manifest',type=Path,required=True);parser.add_argument('--output',type=Path,required=True);args=parser.parse_args()
    try:
        report=build(args.spirv,args.manifest,args.output);print(json.dumps({key:report[key] for key in ('candidate_sha256','record_sha256','descriptor_capacity','execution_admitted')},indent=2))
    except (OSError,ValueError,KeyError,subprocess.SubprocessError) as error:print(str(error),file=sys.stderr);return 1
    return 0


if __name__=='__main__':raise SystemExit(main())
