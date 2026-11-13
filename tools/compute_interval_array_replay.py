#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""CPU-only preparation for a separately reviewed common23 owned replay ABI.

This command builds and checks plans. It never opens Vulkan. Runtime execution
requires a separate reviewed runner and an exclusive grant for each single case.
"""
import argparse
import copy
import difflib
import hashlib
import importlib
import json
from pathlib import Path
import shlex
import subprocess
import sys

ROOT=Path(__file__).resolve().parents[1]
MAGIC=b'OOARPL1\0'
COMPILE_SHA='e25f93a7ddba3cfc2a2a5887beabbe2de030f8c5843574b2171945377b94d5a3'
COMPILE_PROFILE_SHA='d6a343cade4fec41cc005fe3b2eb7fe1ceb333f28fda7c5ae1866a43a4983982'
COMPILE_PREPARED_SHA='79d0032821e5722da59919f849c08c2d59f31ba295dee05327bdc6f5c8d32f24'
COMPILE_BASE=ROOT/'local/compute-interval-array-compile/final-review'
CASES={'baseline-once':'original','baseline-repeat':'original','array-once':'array'}


def need(value,message):
    if not value:raise ValueError(message)
def digest(path):
    with Path(path).open('rb') as stream:return hashlib.file_digest(stream,'sha256').hexdigest()
def canonical(data):return (json.dumps(data,sort_keys=True,indent=2)+'\n').encode()
def dependencies():
    need(digest(ROOT/'tools/compute_interval_array_compile.py')==COMPILE_SHA,'Frozen compile adapter changed')
    if str(ROOT) not in sys.path:sys.path.insert(0,str(ROOT))
    common=importlib.import_module('tools.compute_interval_array_compile');old,array=common.modules()
    return common,old,array


# Checks constructed Vulkan descriptor infos, not hashes or copied payload bytes.
# Source ordering is irrelevant: binding and element identities locate each pair.
HANDLE_ALIAS_C=r'''
static int array_bound_alias_contract(uint32_t count,const Binding *descriptors,
                                      const VkDescriptorBufferInfo *infos,
                                      const VkWriteDescriptorSet *writes) {
    REQUIRE(array_alias_contract(count,descriptors)==0);
    unsigned null_pairs=0;
    for(unsigned route=0;route<8;route++) {
        uint32_t binding=route<5?route+2:7,element=route<5?0:route-5;
        uint32_t source=UINT32_MAX,alias=UINT32_MAX;
        for(unsigned i=0;i<count;i++) {
            if(descriptors[i].binding==binding&&descriptors[i].element==element)source=i;
            if(descriptors[i].binding==13&&descriptors[i].element==route)alias=i;
        }
        REQUIRE(source<count&&alias<count);
        const VkDescriptorBufferInfo *a=&infos[source],*b=&infos[alias];
        REQUIRE(writes[source].pBufferInfo==a&&writes[alias].pBufferInfo==b&&
                writes[source].descriptorType==VK_DESCRIPTOR_TYPE_STORAGE_BUFFER&&
                writes[alias].descriptorType==VK_DESCRIPTOR_TYPE_STORAGE_BUFFER&&
                writes[source].dstBinding==binding&&writes[source].dstArrayElement==element&&
                writes[alias].dstBinding==13&&writes[alias].dstArrayElement==route&&
                writes[source].descriptorCount==1&&writes[alias].descriptorCount==1&&
                a->buffer==b->buffer&&a->offset==b->offset&&a->range==b->range);
        if(descriptors[source].null) {
            null_pairs++;
            REQUIRE(a->buffer==VK_NULL_HANDLE&&a->offset==0&&a->range==VK_WHOLE_SIZE);
        } else REQUIRE(a->buffer!=VK_NULL_HANDLE&&a->offset==descriptors[source].offset&&
                       a->range==descriptors[source].size);
    }
    REQUIRE(null_pairs==2);
    return 0;
}
'''


def derive_native(original):
    common,_,_=dependencies()
    source,anchors=common.derive_native(original)
    anchors=['common23:'+name for name in anchors]
    def replace(before,after,name):
        nonlocal source
        need(source.count(before)==1,'Counted replay anchor differs: '+name)
        source=source.replace(before,after,1);anchors.append(name)
    # Restore only byte-identical reviewed02 resource helpers and execution tail.
    for first,last,name in [('static float float_word(', 'typedef struct Buffer','sampler-float'),
                            ('static int make_buffer(', 'static uint32_t pixel_bytes(','buffer'),
                            ('static int create_sampled_inputs(', '#define INTERVAL_RECORD_INDEX','images')]:
        begin,end=original.index(first),original.index(last)
        replace(last,original[begin:end]+last,'restore02-'+name)
    begin=original.index('    VkBufferUsageFlags buffer_usage =')
    replace('    return 2; /* compile branch above must have returned */\n}\n',original[begin:],'restore02-dispatch-tail')
    replace('"OOACMP1\\0"','"OOARPL1\\0"','new-replay-only-ABI')
    replace('    REQUIRE(!execute);\n','','restore-explicit-execute-mode')
    replace('    REQUIRE(compile_only && !execute);\n','','restore-explicit-compile-or-execute-branch')
    replace('    (void)samplers; /* retained exact CPU frame; no sampler objects are created */\n','','restore-sampler-objects')
    replace('"'+common.MODULES['static']+'") ||\n        !strcmp(module_hex,"'+common.ARRAY_SHA+'"));',
            '"'+common.ARRAY_SHA+'"));','only-original-and-array-modules')
    replace('static int interval_cpu_contract(',HANDLE_ALIAS_C+'\nstatic int interval_cpu_contract(','constructed-handle-alias-check')
    replace('    VkCommandPoolCreateInfo pool_info =',
            '    REQUIRE(array_bound_alias_contract(descriptor_count,descriptors,buffer_infos,writes)==0);\n    VkCommandPoolCreateInfo pool_info =','check-actual-alias-before-command-pool')
    # Both output modes use the new ABI identity; runtime output also reports alias checks.
    source=source.replace('\\"array_compile_profile\\":1','\\"array_replay_profile\\":1').replace('\\"interval_profile\\":1','\\"array_replay_profile\\":1')
    replace('    int compared = 0, matches = 1;',
            '    printf("\\"constructed_alias_pairs_verified\\":8,\\"constructed_null_alias_pairs_verified\\":2,\\"shaderStorageBufferArrayNonUniformIndexing_enabled\\":true,\\"VariablePointers_enabled\\":false,\\"union_descriptor_count\\":23,\\"union_storage_buffer_count\\":17,");\n    int compared = 0, matches = 1;','runtime-alias-check-receipt')
    replace('Open-Ocarina array union compile-only','Open-Ocarina array union owned replay','new-application-identity')
    for word in ('vkCmdDispatch(','vkQueueSubmit(','vkWaitForFences(','vkCreateBuffer('):
        need(source.count(word)==original.count(word)==1,'Reviewed02 execution multiplicity differs: '+word)
    need('UINT64_C(2000000000)' in source and 'variablePointers' not in source,'Fence/feature contract differs')
    need('vkDeviceWaitIdle' not in source and 'vkQueueWaitIdle' not in source,'Failure path acquired unbounded idle')
    return source,anchors


def read_control(data):
    common,old,_=dependencies();need(data[:8]==MAGIC,'New owned-array replay ABI required')
    return old.read_control(old.MAGIC+data[8:],experimental=True)
def write_control(frame):
    _,old,_=dependencies();data=old.write_control(frame,experimental=True);return MAGIC+data[8:]


def validate_prepared(directory,label,authorization):
    common,old,_=dependencies();directory=Path(directory).resolve()
    need(label in CASES,'Unknown single-dispatch case')
    profile=json.loads((directory/'profile.json').read_text());receipt=json.loads((directory/'prepared.json').read_text())
    need(receipt['profile_sha256']==digest(directory/'profile.json') and profile['adapter_sha256']==digest(__file__),'Replay profile/source changed')
    need(digest(COMPILE_BASE/'profile.json')==COMPILE_PROFILE_SHA and digest(COMPILE_BASE/'prepared.json')==COMPILE_PREPARED_SHA,'Frozen common23 preparation changed')
    _,_,_,base=common.base_admission()
    frozen=json.loads((COMPILE_BASE/'profile.json').read_text());need(profile['common_compile_profile']==frozen,'Common23 proof/profile changed')
    for name,sha in profile['source_ledger'].items():need(digest(ROOT/name)==sha,'Replay dependency changed: '+name)
    native=profile['native']
    for name,key in [('replay.c','source_sha256'),('native','binary_sha256'),('native.diff','diff_sha256')]:need(digest(directory/name)==native[key],'Replay native artifact changed: '+name)
    plan=receipt['plans'][label];control=directory/plan['control']
    need(plan['control']==label+'.bin' and digest(control)==plan['control_sha256']==authorization,'Exact reviewed replay plan required')
    frame=read_control(control.read_bytes());common.check_extension(base,frame)
    need(frame['manifest_sha256']==receipt['profile_sha256'] and frame['module_sha256']==common.MODULES[CASES[label]],'Replay case/module identity differs')
    module=directory/(CASES[label]+'.spv');need(frame['shader']==str(module) and digest(module)==frame['module_sha256'],'Replay module changed')
    need(frame['width']==32 and frame['groups']==[2,1,1] and len(frame['allocations'])==60,'Replay dispatch shape differs')
    return profile,frame,directory/'native',control


def prepare(args):
    common,old,_=dependencies()
    need(digest(COMPILE_BASE/'profile.json')==COMPILE_PROFILE_SHA and digest(COMPILE_BASE/'prepared.json')==COMPILE_PREPARED_SHA,'Frozen common23 preparation changed')
    common_profile=json.loads((COMPILE_BASE/'profile.json').read_text());common_receipt=json.loads((COMPILE_BASE/'prepared.json').read_text())
    _,_,base_profile,base_frame=common.base_admission()
    need(common_profile['source_ledger']['tools/compute_interval_array_compile.py']==COMPILE_SHA,'Common23 source ledger changed')
    for name,sha in common_profile['source_ledger'].items():need(digest(ROOT/name)==sha,'Common23 dependency changed: '+name)
    for name,key in [('compile-only.c','source_sha256'),('native','binary_sha256'),('native.diff','diff_sha256')]:need(digest(COMPILE_BASE/name)==common_profile['native'][key],'Common23 native artifacts changed')
    output=Path(args.output).resolve();need(not output.exists() and output.is_relative_to(ROOT/'local'),'Choose new private local output');output.mkdir(parents=True)
    def command(name,argv):
        result=subprocess.run(argv,capture_output=True,text=True,timeout=30);(output/(name+'.stdout')).write_text(result.stdout);(output/(name+'.stderr')).write_text(result.stderr);need(result.returncode==0,name+' failed: '+result.stderr[-1000:]);return result.stdout
    source,anchors=derive_native((common.BASE/'vulkan_compute_interval_replay.c').read_text());(output/'replay.c').write_text(source)
    (output/'native.diff').write_text(''.join(difflib.unified_diff((common.BASE/'vulkan_compute_interval_replay.c').read_text().splitlines(True),source.splitlines(True),fromfile='frozen02.c',tofile='array-owned-replay.c')))
    flags=shlex.split(command('pkg-config',['pkg-config','--cflags','--libs','vulkan']).strip())
    command('native-build',[args.cc,*common.FLAGS,str(output/'replay.c'),*flags,'-o',str(output/'native')])
    symbols=command('native-symbols',['nm','-D',str(output/'native')])
    for symbol in ('vkCmdDispatch','vkQueueSubmit','vkWaitForFences','vkCreateBuffer'):need(symbol in symbols,'Replay tail missing: '+symbol)
    for symbol in ('vkDeviceWaitIdle','vkQueueWaitIdle'):need(symbol not in symbols,'Unbounded idle imported')
    frames={}
    for label in ('original','array'):
        module=COMPILE_BASE/(label+'.spv');need(digest(module)==common.MODULES[label],'Module changed')
        (output/(label+'.spv')).write_bytes(module.read_bytes())
        command(label+'-val',['spirv-val','--target-env','vulkan1.3','--uniform-buffer-standard-layout',str(output/(label+'.spv'))])
        control=COMPILE_BASE/(label+'32.bin');need(digest(control)==common_receipt['plans'][label]['control_sha256'],'Compile control changed')
        frames[label]=common.read_control(control.read_bytes());common.check_extension(base_frame,frames[label])
        need(frames[label]['manifest_sha256']==COMPILE_PROFILE_SHA and frames[label]['module_sha256']==common.MODULES[label],'Compile control identity differs')
    source_ledger={**common_profile['source_ledger'],'tools/compute_interval_array_replay.py':digest(__file__)}
    owners=[{'index':index,'id':row['id'],'size':row['size'],'output':f'allocation-{index}.bin','initial_sha256':digest(row['initial']),'native_expected_sha256':None if row['expected']=='-' else digest(row['expected'])} for index,row in enumerate(base_frame['allocations'])]
    profile={'schema':'open-ocarina.array-union-owned-replay/v1','CPU_prepared':True,'Vulkan_opened':False,'dispatch_executed':False,'runtime_grant_required_per_case':True,'adapter_sha256':digest(__file__),'source_ledger':source_ledger,'common_compile_profile':common_profile,'common_compile_profile_sha256':COMPILE_PROFILE_SHA,'common_compile_prepared_sha256':COMPILE_PREPARED_SHA,
        'native':{'source_sha256':digest(output/'replay.c'),'binary_sha256':digest(output/'native'),'diff_sha256':digest(output/'native.diff'),'flags':common.FLAGS,'counted_anchors':anchors,'execution_tail_retained':True},
        'owners':owners,'expected_complete_readback_bytes':sum(row['size'] for row in owners),'alias_contract':'8 exact owner/offset/range/null clones; actual constructed VkBuffer+offset+range checked before command creation; binding6 included; two null pairs.',
        'dispatch':{'count_per_case':1,'grid':[2,1,1],'local':[64,1,1],'actual_subgroup_size_required':32,'full_subgroups':True,'fence_bound_ns':2000000000,'process_bound_seconds':30,'parent_pipe_bound_bytes':67108864,'child_rlimit_overrides':False},
        'case_order':list(CASES),'oracle':'Baseline once and repeat must match all60 complete buffers; array must match both. Native compares independent guarded record owner; Python rehashes all original59 complete owners plus record. No tolerance or subset acceptance.',
        'scope':['Owned frozen diagnostic inputs, not as-submitted renderer descriptors or live integration.','No dispatch/pipeline/device during preparation. No runtime correctness or speed established.','A future runner must admit/recheck all source, input, proof, plan, module and driver pins before/after one reviewed case; this tool exposes preparation/comparison only.']}
    (output/'profile.json').write_bytes(canonical(profile));profile_sha=digest(output/'profile.json');plans={}
    for case,label in CASES.items():
        frame=copy.deepcopy(frames[label]);frame.update(shader=str(output/(label+'.spv')),manifest_sha256=profile_sha)
        path=output/(case+'.bin');path.write_bytes(write_control(frame))
        checked=json.loads(command(case+'-CPU-check',[str(output/'native'),str(path),str(output),'--check-only']))
        need(checked=={'native_plan_validated':True,'dispatch_executed':False,'allocations':60,'images':4},'CPU framing contract differs')
        plans[case]={'control':path.name,'control_sha256':digest(path),'module_sha256':common.MODULES[label],'CPU_check':checked,'single_use_runtime_grant_required':True}
    receipt={'schema':'open-ocarina.array-union-owned-replay-prepared/v1','profile_sha256':profile_sha,'native':profile['native'],'plans':plans,'Vulkan_opened':False,'dispatch_executed':False}
    (output/'prepared.json').write_bytes(canonical(receipt))
    for label,row in plans.items():validate_prepared(output,label,row['control_sha256'])
    return receipt


def inspect_readback(directory,profile,native):
    """Full output integrity/guard check; baseline equality remains separate."""
    directory=Path(directory)
    need(native.get('array_replay_profile')==1 and native.get('dispatch_executed') is True and native.get('dispatch_count')==1 and
         native.get('actual_subgroup_size')==32 and native.get('constructed_alias_pairs_verified')==8 and native.get('constructed_null_alias_pairs_verified')==2 and
         native.get('shaderStorageBufferArrayNonUniformIndexing_enabled') is True and native.get('VariablePointers_enabled') is False and
         native.get('union_descriptor_count')==23 and native.get('union_storage_buffer_count')==17 and native.get('comparison_passed') is True,'Native replay receipt incomplete')
    rows=native.get('allocations',[]);owners=profile['owners'];need(len(rows)==len(owners)==60,'Complete60 owner outputs required')
    outputs=[]
    for index,(row,owner) in enumerate(zip(rows,owners)):
        path=directory/owner['output'];need(row['index']==index and row['size']==owner['size'] and row['first_difference'] is None,'Native owner/guard comparison differs')
        need(path.is_file() and not path.is_symlink() and path.stat().st_size==owner['size'],'Complete owner bytes missing')
        sha=digest(path)
        if owner['native_expected_sha256'] is not None:need(sha==owner['native_expected_sha256'],'Independent guarded record changed')
        outputs.append({'index':index,'size':owner['size'],'sha256':sha,'path':owner['output']})
    need(sum(row['size'] for row in outputs)==profile['expected_complete_readback_bytes'],'Incomplete readback graph')
    return outputs


def compare_outputs(profile,left,left_native,right,right_native):
    a=inspect_readback(left,profile,left_native);b=inspect_readback(right,profile,right_native)
    for key in ('experiment_manifest_sha256','device_uuid','driver_uuid','device_vendor','device_id'):
        need(left_native[key]==right_native[key],'Comparison identity differs: '+key)
    expected_profile=hashlib.sha256(canonical(profile)).hexdigest()
    need(left_native['experiment_manifest_sha256']==expected_profile,'Output profile identity differs')
    common,_,_=dependencies();need(left_native['module_sha256'] in (common.MODULES['original'],common.MODULES['array']) and right_native['module_sha256'] in (common.MODULES['original'],common.MODULES['array']),'Unreviewed output module')
    differences=[x['index'] for x,y in zip(a,b) if x['sha256']!=y['sha256']]
    need(not differences,'Complete output mismatch in owners '+str(differences))
    return {'all60_complete_owners_match':True,'total_bytes':sum(row['size'] for row in a),'record_and_guard_bytes':8784,'different_owners':[],'historical_live_equivalence_established':False,'left':a,'right':b}


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--output',type=Path,required=True);p.add_argument('--cc',default='cc');args=p.parse_args()
    try:print(json.dumps(prepare(args),indent=2))
    except (ValueError,OSError,KeyError,subprocess.SubprocessError) as error:print(str(error),file=sys.stderr);return 1
    return 0


if __name__=='__main__':raise SystemExit(main())
