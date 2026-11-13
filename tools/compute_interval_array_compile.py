#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""CPU preparation only: three modules, one nonuniform-array compile layout.

The derived native host has no resource-allocation, queue or dispatch code.
Preparation invokes --check-only, never its separately reviewed Vulkan mode.
"""
import argparse
import copy
import difflib
import hashlib
import importlib
import json
import os
from pathlib import Path
import shlex
import subprocess
import sys

ROOT=Path(__file__).resolve().parents[1]
BASE=ROOT/'local/compute-interval-replay-owned-02'
MAGIC=b'OOACMP1\0'
ADAPTER_SHA='9a5e3835c251047f51ba23e9042ac9b821c9e6543adc9f979186d10afcdecb85'
SOURCE_SHA='9b67fbdec5487cd5138395455b58c7148114ee902f7bfa643bebc30cb741e55f'
BASE_PINS={'profile.json':'384a4064fc0aa593e4f9984122fe3f93dfaf089dfed608ee8fb0bacbace658e7',
    'prepared.json':'1bf7abdd3c37d571ced4bbe046c7e20c115f6121b169c55696bb8ffa679b23d4',
    'vulkan_compute_interval_replay.c':SOURCE_SHA,'native':'6bbf44eeb4f1521bc052d3a6473d5b2ba0656f986d091c9a19f97d39fb0c87dd',
    'baseline32.bin':'bc1e6c3a2788190bd16d922c6e5a33b038d6774af02df3325408c0c9d43c26a4',
    'candidate32.bin':'2f29fb6fc6104eccbd52137591119d2ecdbd6856ab3b4a4cd8d7732118e139c9'}
ARRAY_TOOL_SHA='d8c4a899e350408c52b6e1121ce74f244bc182f327b35007e4c7990ed214e13d'
ARRAY_SHA='7c6322e1a7b4a84206977fd0a0af165b7dedeb0eb9cbd8fb7c211ec1af77d7bd'
ALIAS_SHA='87556a394524d0e70acccbd9a9dd63c56ae6e40f97375e7d70af8a1ebb507c60'
MODULES={'original':'b9af52c0ba07f64eb8b0ca2905c432d094999fdf669aa95182ff7754b510e97b',
         'static':'d5148342b4517882b25a48e63700cbfb8695454cc05d131da718509ac2563dc0','array':ARRAY_SHA}
FLAGS=['-std=c11','-O2','-DNDEBUG','-Wall','-Wextra','-Werror']
CONTROLLED_OPTIONS=('HS_CS32_MAX_INSTS','HS_SPILL_BATCH','HS_PUSH_SHADOW','shader_spilling_rate','INTEL_DEBUG',
    'HS_SPILL_PCT','HS_RA_DEBUG','HS_FORCE_SIMD8','HS_LARGE_GRF','HS_SUBGROUP8','HS_LARGE_GRF_BIT','RX_VK_WARP64','RX_VK_NOOPT')


def need(value,message):
    if not value:raise ValueError(message)
def digest(path):
    with Path(path).open('rb') as stream:return hashlib.file_digest(stream,'sha256').hexdigest()
def canonical(data):return (json.dumps(data,sort_keys=True,indent=2)+'\n').encode()


def modules():
    need(digest(ROOT/'tools/compute_interval_replay.py')==ADAPTER_SHA,'Frozen02 adapter changed')
    need(digest(ROOT/'tools/compute_interval_array_lowering.py')==ARRAY_TOOL_SHA,'Array lowering tool changed')
    if str(ROOT) not in sys.path:sys.path.insert(0,str(ROOT))
    old=importlib.import_module('tools.compute_interval_replay');old.modules()
    array=importlib.import_module('tools.compute_interval_array_lowering');array.dependencies()
    return old,array


def append_aliases(frame):
    need(len(frame['descriptors'])==15 and len(frame['allocations'])==60,'Expected02 graph')
    result=copy.deepcopy(frame);lookup={(row[0],row[1]):row for row in frame['descriptors']}
    need(len(lookup)==15 and (13,0) not in lookup,'Original descriptor identities differ')
    for route,(binding,element) in enumerate([(binding,0) for binding in range(2,7)]+[(7,element) for element in range(3)]):
        source=lookup[binding,element];need(source[2]==7,'Array source is not storage');alias=source.copy();alias[0:2]=[13,route];result['descriptors'].append(alias)
    return result


def check_extension(original,extended):
    expected=append_aliases(original)
    for key in original:
        if key in ('shader','module_sha256','manifest_sha256'):continue
        need(extended[key]==expected[key],'Original graph or literal alias field changed: '+key)
    need(len(extended['descriptors'])==23 and extended['allocations']==original['allocations'],'Alias layout introduced memory')
    return {'original_allocations_unchanged':60,'extra_allocations':0,'descriptor_elements':23,'storage_buffer_elements':17,
            'same_owner_offset_range_null_for_all8_aliases':True}


ALIAS_C=r'''
static int array_alias_contract(uint32_t count,const Binding *descriptors) {
    REQUIRE(count==23);
    for(unsigned route=0;route<8;route++) {
        uint32_t binding=route<5?route+2:7,element=route<5?0:route-5;
        const Binding *source=NULL,*alias=NULL;
        for(unsigned i=0;i<count;i++) {
            if(descriptors[i].binding==binding&&descriptors[i].element==element) source=&descriptors[i];
            if(descriptors[i].binding==13&&descriptors[i].element==route) alias=&descriptors[i];
        }
        REQUIRE(source&&alias&&source->type==7&&alias->type==7&&source->null==alias->null&&
                source->allocation==alias->allocation&&source->offset==alias->offset&&source->size==alias->size&&
                source->view==alias->view&&source->sampler==alias->sampler);
    }
    return 0;
}
'''


def derive_native(original):
    need(hashlib.sha256(original.encode()).hexdigest()==SOURCE_SHA,'Frozen02 C source changed')
    source=original;anchors=[]
    def replace(before,after,name):
        nonlocal source
        need(source.count(before)==1,'Counted C anchor differs: '+name);source=source.replace(before,after,1);anchors.append(name)
    # Remove all executable resource/queue/dispatch paths and their helpers.
    for first,last,name in [('static float float_word(', 'typedef struct Buffer', 'remove-sampler-float-helper'),
                            ('static int make_buffer(', 'static uint32_t pixel_bytes(', 'remove-buffer-helpers'),
                            ('static int create_sampled_inputs(', '#define INTERVAL_RECORD_INDEX', 'remove-image-helpers')]:
        need(source.count(first)==source.count(last)==1,'Resource boundary differs')
        begin,end=source.index(first),source.index(last);need(begin<end,'Resource function order differs');replace(source[begin:end],'',name)
    begin=source.index('    VkBufferUsageFlags buffer_usage =')
    replace(source[begin:],'    return 2; /* compile branch above must have returned */\n}\n','remove-execution-tail')
    replace('"OOILRP1\\0"','"OOACMP1\\0"','new-compile-only-magic')
    replace('    int execute = !strcmp(argv[3], "--execute-approved-plan");','    int execute = !strcmp(argv[3], "--execute-approved-plan");\n    REQUIRE(!execute);','reject-execution-before-input')
    replace('    Sampler samplers[64] = {0};','    Sampler samplers[64] = {0};\n    (void)samplers; /* retained exact CPU frame; no sampler objects are created */','keep-sampler-frame-without-objects')
    replace('    VkApplicationInfo app =','    REQUIRE(compile_only && !execute);\n    VkApplicationInfo app =','compile-only-before-instance')
    replace('"d5148342b4517882b25a48e63700cbfb8695454cc05d131da718509ac2563dc0"));',
            '"d5148342b4517882b25a48e63700cbfb8695454cc05d131da718509ac2563dc0") ||\n        !strcmp(module_hex,"'+ARRAY_SHA+'"));','third-module-identity')
    replace('max_push_descriptors >= 15','max_push_descriptors >= 23','push-capacity23')
    replace('limits->maxPerStageDescriptorStorageBuffers >= 9','limits->maxPerStageDescriptorStorageBuffers >= 17','stage-SSBO17')
    replace('limits->maxDescriptorSetStorageBuffers >= 9','limits->maxDescriptorSetStorageBuffers >= 17','set-SSBO17')
    replace('limits->maxPerStageResources >= 15','limits->maxPerStageResources >= 23','stage-resources23')
    replace('VkBool32 null_descriptor) {','VkBool32 null_descriptor, VkBool32 nonuniform) {','nonuniform-gate-signature')
    replace('    REQUIRE(null_descriptor == VK_TRUE);','    REQUIRE(null_descriptor == VK_TRUE && nonuniform == VK_TRUE && limits->maxPushConstantsSize>=88);','null-and-nonuniform-feature-gate')
    replace('static int interval_cpu_contract(',ALIAS_C+'\nstatic int interval_cpu_contract(','exact-array-alias-contract')
    replace('allocation_count == 60 && descriptor_count == 15','allocation_count == 60 && descriptor_count == 23','same-memory23-descriptors')
    replace('d->binding <= 12 && d->element < (d->binding == 7 ? 3u : 1u)','d->binding <= 13 && d->element < (d->binding == 7 ? 3u : d->binding == 13 ? 8u : 1u)','binding13-array8')
    replace('d->binding < 8 || d->binding == 12','d->binding < 8 || d->binding == 12 || d->binding == 13','alias-storage-type')
    replace('(d->binding == 7 && d->element > 0));','((d->binding == 7 && d->element > 0) || (d->binding == 13 && d->element >= 6)));','alias-null-state')
    replace('    REQUIRE(found == 1);','    REQUIRE(found == 1);\n    int alias_status=array_alias_contract(descriptor_count,descriptors);\n    if(alias_status)return alias_status;','validate-literal-aliases')
    replace('supported_robustness.nullDescriptor);','supported_robustness.nullDescriptor, supported12.shaderStorageBufferArrayNonUniformIndexing);','query-nonuniform-before-device')
    replace('    if (!push_extension || total_descriptors > push_properties.maxPushDescriptors ||',
        '    if (!executable_extension || !executable.pipelineExecutableInfo || !push_extension || total_descriptors > push_properties.maxPushDescriptors ||','require-statistics-before-device')
    replace('        .bufferDeviceAddress = VK_TRUE};','        .bufferDeviceAddress = VK_TRUE,\n        .shaderStorageBufferArrayNonUniformIndexing = VK_TRUE};','enable-nonuniform-no-variable-pointers')
    replace('    REQUIRE(executable_properties.subgroupSize == required_width);','    REQUIRE(executable_properties.subgroupSize == required_width && executable_properties.stages == VK_SHADER_STAGE_COMPUTE_BIT);','actual-compute32')
    replace('\\"interval_profile\\":1','\\"array_compile_profile\\":1','separate-native-result-profile')
    replace('        interval_stats_json(statistics_count, statistics);',
        '        printf("\\"shaderStorageBufferArrayNonUniformIndexing_enabled\\":true,\\"VariablePointers_enabled\\":false,\\"union_descriptor_count\\":23,\\"union_storage_buffer_count\\":17,");\n        interval_stats_json(statistics_count, statistics);','all-structured-statistics-retained')
    replace('Open-Ocarina interval experimental replay','Open-Ocarina array union compile-only','application-identity')
    for forbidden in ('vkCmd','vkQueueSubmit','vkGetDeviceQueue(','vkCreateBuffer(','vkAllocateMemory(','vkCreateImage(','vkCreateCommandPool(','vkDeviceWaitIdle','vkQueueWaitIdle'):
        need(forbidden not in source,'Native compile-only source retained execution API: '+forbidden)
    need('variablePointers' not in source,'Unexpected variable-pointer feature')
    return source,anchors


def read_control(data):
    old,_=modules();need(data[:8]==MAGIC,'New compile-only control magic differs');return old.read_control(old.MAGIC+data[8:],experimental=True)
def write_control(frame):
    old,_=modules();data=old.write_control(frame,experimental=True);return MAGIC+data[8:]


def base_admission():
    old,array=modules()
    for name,expected in BASE_PINS.items():need(digest(BASE/name)==expected,'Pinned02 artifact changed: '+name)
    profile,left,_,_=old.validate_prepared(BASE,'baseline32',BASE_PINS['baseline32.bin'])
    _,right,_,_=old.validate_prepared(BASE,'candidate32',BASE_PINS['candidate32.bin'])
    normalized=copy.deepcopy(right)
    for key in ('shader','module_sha256'):normalized[key]=left[key]
    need(normalized==left,'02 baseline/static controls differ outside module')
    return old,array,profile,left


def prepare(args):
    old,array,base_profile,frame=base_admission()
    output=args.output.resolve();need(not output.exists() and output.is_relative_to(ROOT/'local'),'Use new private local output');output.mkdir(parents=True)
    bundle=args.array_bundle.resolve();need(digest(bundle/'candidate.spv')==ARRAY_SHA and digest(bundle/'records.bin')==old.RECORD_SHA and digest(bundle/'alias-plan.json')==ALIAS_SHA,'Array frozen artifacts differ')
    inspect=importlib.import_module('tools.compute_interval_lowering');reflect=importlib.import_module('tools.compile_probe').reflect
    def command(name,argv,timeout=30):
        result=subprocess.run(argv,capture_output=True,text=True,timeout=timeout);(output/(name+'.stdout')).write_text(result.stdout);(output/(name+'.stderr')).write_text(result.stderr);need(result.returncode==0,name+' failed');return result.stdout
    original=BASE/'proof/original.spv';original_text=command('original-dis',['spirv-dis','--raw-id','--no-indent',str(original)])
    _,helpers=inspect.discover_target(original_text);_,_,interfaces=array.transform(original_text)
    actual=command('array-dis',['spirv-dis','--raw-id','--no-indent',str(bundle/'candidate.spv')])
    report=json.loads((bundle/'report.json').read_text());need(report['tool_sha256']==ARRAY_TOOL_SHA and report['record_sha256']==old.RECORD_SHA,'Array report dependency differs')
    views=[inspect.View(**row) for row in json.loads((BASE/'proof/report.json').read_text())['views']];partitions=inspect.build_partitions(views)
    need(inspect.pack_records(partitions)==(bundle/'records.bin').read_bytes(),'Frozen source rows/record differ')
    preservation=array.verify_preservation(original.read_bytes(),(bundle/'candidate.spv').read_bytes(),helpers,interfaces)
    hits=array.audit_hit_shape(actual,helpers);finite=inspect.audit_lookup_shape(actual,helpers,partitions);selection=inspect.certify_partitions(views,partitions)
    static_text=command('static-dis',['spirv-dis','--raw-id','--no-indent',str(BASE/'proof/candidate.spv')])
    static_preservation=inspect.verify_preservation(original.read_bytes(),(BASE/'proof/candidate.spv').read_bytes(),helpers)
    static_finite=inspect.audit_lookup_shape(static_text,helpers,partitions)
    original_access=inspect.inspect_module(original.read_bytes())
    alias_descriptors=append_aliases(frame);graph=check_extension(frame,alias_descriptors)
    derived,anchors=derive_native((BASE/'vulkan_compute_interval_replay.c').read_text());source=output/'compile-only.c';source.write_text(derived)
    (output/'native.diff').write_text(''.join(difflib.unified_diff((BASE/'vulkan_compute_interval_replay.c').read_text().splitlines(True),derived.splitlines(True),fromfile='frozen02.c',tofile='array-compile-only.c')))
    helper=output/'native';flags=shlex.split(command('pkg-config',['pkg-config','--cflags','--libs','vulkan']).strip());command('native-build',[args.cc,*FLAGS,str(source),*flags,'-o',str(helper)])
    symbols=command('native-symbols',['nm','-D',str(helper)])
    need(not any(word in symbols for word in ('vkCmd','vkQueueSubmit','vkGetDeviceQueue','vkCreateBuffer','vkAllocateMemory','vkCreateImage','vkCreateCommandPool','vkDeviceWaitIdle','vkQueueWaitIdle')),'Compiled binary retained execution imports')
    union=copy.deepcopy(base_profile['union_layout']);union['bindings'].append({'set':0,'binding':13,'type':7,'count':8})
    inputs={name:output/(label+'.spv') for name,label in zip(MODULES,('original','static','array'))}
    paths={'original':BASE/'proof/original.spv','static':BASE/'proof/candidate.spv','array':bundle/'candidate.spv'}
    module_rows={}
    for label,path in paths.items():
        need(digest(path)==MODULES[label],'Exact module mismatch');inputs[label].write_bytes(path.read_bytes())
        command(label+'-val',['spirv-val','--target-env','vulkan1.3','--uniform-buffer-standard-layout',str(inputs[label])])
        reflection=reflect(inputs[label].read_bytes());need(reflection['workgroup']==[64,1,1] and reflection['push_constant_bytes']==56,'Shader local/push profile differs')
        expected=union if label=='array' else base_profile['union_layout'] if label=='static' else {**base_profile['union_layout'],'bindings':base_profile['union_layout']['bindings'][:-1]}
        need(reflection==expected,'Exact descriptor reflection differs')
        module_rows[label]={'module_sha256':MODULES[label],'reflection':reflection,'path':inputs[label].name}
    source_ledger={**old.DEPENDENCIES,'tools/compute_interval_replay.py':ADAPTER_SHA,'tools/compute_interval_array_lowering.py':ARRAY_TOOL_SHA,'tools/compute_interval_array_compile.py':digest(__file__)}
    profile={'schema':'open-ocarina.array-union-compile/v1','CPU_prepared':True,'Vulkan_opened':False,'pipeline_created':False,'dispatch_executed':False,'execution_admitted':False,
        'original02_pins':BASE_PINS,'source_ledger':source_ledger,'original02_input_inventory':base_profile['input_inventory'],'original02_proof_artifact_sha256':base_profile['proof_artifact_sha256'],
        'modules':module_rows,'union_layout':union,'push_bytes':88,'root_push_offset':48,'local_size':[64,1,1],'required_width':32,'require_full_subgroups':True,
        'additional_descriptors':alias_descriptors['descriptors'][-8:],'graph_extension_proof':graph,'original59_owner_graph':base_profile['original_allocation_graph'],'readonly_record_owner':base_profile['record'],
        'driver':base_profile['driver'],'compiler_environment':base_profile['compiler_environment'],'runtime_environment':base_profile['runtime_environment'],
        'controlled_compiler_options':{key:'1' if key in ('HS_SPILL_BATCH','shader_spilling_rate') else None for key in CONTROLLED_OPTIONS},
        'fresh_private_cache_per_case_required':True,
        'array_bundle_artifacts':{name:digest(bundle/name) for name in ('candidate.spv','records.bin','alias-plan.json','report.json')},
        'additional_feature':'shaderStorageBufferArrayNonUniformIndexing','VariablePointers_requested':False,'device_limits_queried':False,
        'native':{'source_sha256':digest(source),'binary_sha256':digest(helper),'diff_sha256':digest(output/'native.diff'),'flags':FLAGS,'counted_anchors':anchors,'execution_imports_absent':True},
        'actual_array_proof':{'preservation':preservation,'hit_shape':hits,'finite':finite,'selection':selection,'record_sha256':old.RECORD_SHA},
        'actual_static_proof':{'preservation':static_preservation,'finite':static_finite},'actual_original_access_contract':original_access,
        'scope':['Three compile-only pipelines under the same23/17 union layout and02 physical2-control policy; none created during preparation.',
                 'Compile-only native binary contains no resource allocation, queue retrieval, command or submission API imports.',
                 'No new replay executor/ABI, runtime identity/alias verification or live renderer capture admission.']}
    (output/'profile.json').write_bytes(canonical(profile));profile_sha=digest(output/'profile.json');plans={}
    for label in MODULES:
        control=copy.deepcopy(alias_descriptors);control.update(module_sha256=MODULES[label],shader=str(inputs[label]),manifest_sha256=profile_sha)
        check_extension(frame,control);path=output/(label+'32.bin');path.write_bytes(write_control(control))
        checked=json.loads(command(label+'-cpu-check',[str(helper),str(path),str(output),'--check-only']))
        need(checked=={'native_plan_validated':True,'dispatch_executed':False,'allocations':60,'images':4},'New native CPU contract differs')
        rejected=subprocess.run([str(helper),str(path),str(output),'--execute-approved-plan'],capture_output=True,text=True,timeout=5);need(rejected.returncode==2,'Execution mode was not rejected beforeinstance')
        plans[label]={'control':path.name,'control_sha256':digest(path),'module_sha256':MODULES[label],'native_CPU_check':checked,'execution_mode_rejected':True}
    receipt={'schema':'open-ocarina.array-union-compile-prepared/v1','profile_sha256':profile_sha,'native':profile['native'],'plans':plans,'Vulkan_opened':False,'pipeline_created':False,'dispatch_executed':False,'device_review_required':True}
    (output/'prepared.json').write_bytes(canonical(receipt));return receipt


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--array-bundle',type=Path,required=True);parser.add_argument('--output',type=Path,required=True);parser.add_argument('--cc',default='cc');args=parser.parse_args()
    try:print(json.dumps(prepare(args),indent=2))
    except (ValueError,OSError,KeyError,subprocess.SubprocessError) as error:print(str(error),file=sys.stderr);return 1
    return 0


if __name__=='__main__':raise SystemExit(main())
