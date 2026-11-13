#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Prepare or explicitly execute one hash-reviewed synthetic array-pointer case."""
import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import selectors
import shlex
import signal
import struct
import subprocess
import sys
import time

ROOT=Path(__file__).resolve().parents[1]
PROFILE='owned-typed-array-runtime/v1'
FIXTURE_SHA='658c8969cd0851ec6196441ce93b9206df9ceea6d9d8ab587ad10aa1bc362e25'
FROZEN={'tools/descriptor_array_runtime_fixture.py':FIXTURE_SHA,
        'tools/descriptor_array_pointer_experiment.py':'0e4fc9d15438f078bf206e40d8838052a91a9e16af696f44cbb77a7b8a94838e',
        'tools/descriptor_array_compile_host.py':'f9ad859f39cb6b81f06d119001d0872da0c911f6deb51a0a63dd553f4eaf25d0'}
CASE_SHA={False:'0ba9c2f84a756e3ba7d441825084fc3cd4476c078184c17c5e4ac81a81131110',True:'805867aeab27dcbb9a046098e29556231eb3d1778428ade16691587147a08d9d'}
MODULES={'pointer':'bf1c2e5a1379ced5b8c068b0b47498384322255bf2cab235c31ce9cc0f027cdc','value':'aa9816b41e458151632308a64d9df58aee0fcc5df287b70f47e6b067b1c76531'}
NAMES=('shared','short','zero_prefix','mapped_elsewhere','physical','ordinary_miss','records','queries','results','page_table')
SIZES=(8320,138,160,8320,8320,4224,480,5760,38080,656)
FLAGS=['-std=c11','-O2','-DNDEBUG','-Wall','-Wextra','-Werror']
EXPECTED_NATIVE={'native_cpu_validated':True,'Vulkan_instance_created':False,'allocations':10,'bytes':74458,'module_restricted':True}


def sha(data):return hashlib.sha256(data).hexdigest()
def file_sha(path):return sha(Path(path).read_bytes())
def canonical(data):return (json.dumps(data,sort_keys=True,indent=2)+'\n').encode()


def load_dependency(name):
    path=ROOT/name
    if file_sha(path)!=FROZEN[name]:raise ValueError('Frozen dependency changed: '+name)
    spec=importlib.util.spec_from_file_location('array_runtime_'+path.stem,path)
    module=importlib.util.module_from_spec(spec);sys.modules[spec.name]=module;spec.loader.exec_module(module)
    return module


def sources():
    ledger=dict(FROZEN)
    for name in ('tools/descriptor_array_runtime.py','src/probes/descriptor_array_runtime.c'):ledger[name]=file_sha(ROOT/name)
    for name,expected in FROZEN.items():
        if file_sha(ROOT/name)!=expected:raise ValueError('Frozen source changed')
    return ledger


def validate_fixture(directory,follow,variant,module):
    directory=Path(directory)
    plan_data=(directory/'plan.json').read_bytes()
    if type(follow) is not bool or sha(plan_data)!=CASE_SHA[follow]:raise ValueError('Unknown fixture case plan')
    plan=json.loads(plan_data)
    fixture=load_dependency('tools/descriptor_array_runtime_fixture.py')
    if plan['follow_stores'] is not follow or plan['descriptor_roots']!=fixture.memory_contract() or plan['ownership_proof']!=fixture.ownership_proof():raise ValueError('Alias/ownership graph differs')
    if variant not in MODULES or file_sha(module)!=MODULES[variant]:raise ValueError('Unknown exact fixture module')
    reflection=load_dependency('tools/descriptor_array_compile_host.py').reflect_profile(Path(module).read_bytes(),variant)
    if plan['fixture_tool_sha256']!=FIXTURE_SHA or plan['module_sha256']!=MODULES['pointer'] or [a['name'] for a in plan['allocations']]!=list(NAMES) or [a['bytes'] for a in plan['allocations']]!=list(SIZES):raise ValueError('Fixture allocation ABI differs')
    expected_initial,expected_final,results=fixture.oracle(follow)
    for allocation in plan['allocations']:
        for mode in ('initial','expected'):
            path=directory/allocation[mode+'_file']
            if path.is_symlink() or path.stat().st_size!=allocation['bytes'] or file_sha(path)!=allocation[mode+'_sha256']:raise ValueError('Fixture bytes changed')
            name=allocation['name'];data=path.read_bytes()
            if data[:64]!=bytes([0xBD])*64 or data[-64:]!=bytes([0xDB])*64:raise ValueError('Fixture guards differ')
            known=(expected_initial if mode=='initial' else expected_final).get(name)
            if name=='results':known=bytes([0xE5])*37952 if mode=='initial' else results+bytes([0xE5])*64
            if known is not None and data[64:-64]!=known:raise ValueError('Independent expected recipe differs')
    push=directory/'push-initial.bin'
    if push.stat().st_size!=48 or file_sha(push)!=plan['push_initial_sha256']:raise ValueError('Push ABI differs')
    return plan,reflection


def string(value):
    data=str(value).encode()
    if not 0<len(data)<=4095 or b'\0' in data:raise ValueError('Invalid native string')
    return struct.pack('<I',len(data))+data


def artifact(path):return string(path)+bytes.fromhex(file_sha(path))


def control_bytes(directory,variant,icd,library):
    plan=json.loads((directory/'fixture/plan.json').read_text())
    code=directory/'module.spv'
    data=b'DARUN1\0\0'+struct.pack('<3I',int(variant=='pointer'),int(plan['follow_stores']),10)+string(icd)+string(library)
    data+=struct.pack('<I',code.stat().st_size)+artifact(code)+artifact(directory/'fixture/push-initial.bin')
    for allocation in plan['allocations']:
        data+=struct.pack('<I',allocation['bytes'])+artifact(directory/'fixture'/allocation['initial_file'])+artifact(directory/'fixture'/allocation['expected_file'])
    data+=struct.pack('<I',7)
    for owner,size in zip((1,0,0,2,6,7,8),(10,8192,8192,32,352,5632,37952)):data+=struct.pack('<3I',owner,64,size)
    data+=struct.pack('<I',7)
    for relocation in plan['page_table_relocations']:
        data+=struct.pack('<3I',relocation['write_offset'],NAMES.index(relocation['target_allocation']),relocation['target_offset'])
    return data


def build(source,output,cc='cc',extra=()):
    flags=shlex.split(subprocess.run(['pkg-config','--cflags','--libs','vulkan','libcrypto'],check=True,capture_output=True,text=True,timeout=5).stdout)
    subprocess.run([cc,*FLAGS,str(source),*map(str,extra),*flags,'-o',str(output)],check=True,capture_output=True,text=True,timeout=30)


def prepare(args):
    output=args.output.resolve()
    if output.exists() or not output.is_relative_to(ROOT/'local'):raise ValueError('Use fresh private local output')
    ledger=sources();host=load_dependency('tools/descriptor_array_compile_host.py');identity=host.driver_identity(args.driver)
    icd=args.driver.resolve();library=Path(json.loads(icd.read_text())['ICD']['library_path'])
    if not library.is_absolute():library=icd.parent/library
    for follow in (False,True):
        for variant in MODULES:validate_fixture(args.fixtures/('follow-on' if follow else 'follow-off'),follow,variant,args.fixtures/(variant+'.spv'))
    output.mkdir(parents=True);helper=output/'native-runtime';build(ROOT/'src/probes/descriptor_array_runtime.c',helper,args.cc)
    trap=output/'instance-trap.c';trap.write_text('#include <vulkan/vulkan.h>\n#include <stdlib.h>\nVkResult __wrap_vkCreateInstance(const VkInstanceCreateInfo*a,const VkAllocationCallbacks*b,VkInstance*c){(void)a;(void)b;(void)c;_Exit(99);}\n')
    trapped=output/'native-CPU-trap';build(ROOT/'src/probes/descriptor_array_runtime.c',trapped,args.cc,(trap,'-Wl,--wrap=vkCreateInstance'))
    _,policy=host.native_safe_environment(os.environ);plans=[]
    for follow in (False,True):
        source=args.fixtures/('follow-on' if follow else 'follow-off')
        original=json.loads((source/'plan.json').read_text())
        for variant in ('value','pointer'):
            directory=output/(('follow-on-' if follow else 'follow-off-')+variant);directory.mkdir();copied=directory/'fixture';copied.mkdir()
            wanted=['plan.json','push-initial.bin']+[allocation[key] for allocation in original['allocations'] for key in ('initial_file','expected_file')]
            for name in wanted:(copied/name).write_bytes((source/name).read_bytes())
            module=directory/'module.spv';module.write_bytes((args.fixtures/(variant+'.spv')).read_bytes())
            _,reflection=validate_fixture(copied,follow,variant,module)
            subprocess.run([args.validator,'--target-env','vulkan1.2',str(module)],check=True,capture_output=True,text=True,timeout=15)
            control=directory/'control.bin';control.write_bytes(control_bytes(directory,variant,icd,library))
            validations=[]
            for executable in (helper,trapped):
                result=subprocess.run([str(executable),'--validate',str(control)],check=True,capture_output=True,text=True,timeout=5)
                if json.loads(result.stdout)!=EXPECTED_NATIVE:raise ValueError('Native pre-instance validation differs')
                validations.append({'helper_sha256':file_sha(executable),'validated_before_instance':True,'linker_trap':executable==trapped})
            files=[helper,control,module]+sorted(copied.iterdir())
            plan={'schema':1,'profile':PROFILE,'variant':variant,'follow_stores':follow,'preparation_root':str(output),'directory':str(directory),
                'sources':ledger,'module_sha256':MODULES[variant],'fixture_plan_sha256':CASE_SHA[follow],'files':[{'path':str(path),'bytes':path.stat().st_size,'sha256':file_sha(path)} for path in files],
                'reflection':reflection,'driver_identity':identity,'driver_icd':str(icd),'driver_library':str(library.resolve()),'driver_policy':policy,
                'native_helper':str(helper),'native_helper_sha256':file_sha(helper),'native_C_sha256':ledger['src/probes/descriptor_array_runtime.c'],
                'native_control_sha256':file_sha(control),'host_flags':FLAGS,'abi':'DARUN1/LE-v1','max_dispatches':1,'groups':[1,1,1],'local_size':[64,1,1],
                'required_subgroup_size':32,'full_subgroups':True,'fence_timeout_ns':2000000000,'native_alarm_seconds':25,'process_timeout_seconds':30,
                'combined_output_limit_bytes':64*1024*1024,'child_resource_limit_overrides':False,'disk_cache_disabled':True,
                'CPU_validations':validations,'Vulkan_opened':False,'runtime_executed':False,'single_use':True,'external_host_dependencies':['Vulkan development headers/loader','OpenSSL libcrypto development files'],
                'tool_versions':{'cc':subprocess.run([args.cc,'--version'],check=True,capture_output=True,text=True,timeout=5).stdout.splitlines()[0],
                                 'validator':subprocess.run([args.validator,'--version'],check=True,capture_output=True,text=True,timeout=5).stdout.splitlines()[0]}}
            plan_path=directory/'reviewed-plan.json';plan_path.write_bytes(canonical(plan));plans.append({'variant':variant,'follow_stores':follow,'plan':str(plan_path),'reviewed_plan_sha256':file_sha(plan_path)})
    report={'schema':1,'CPU_prepared':True,'Vulkan_opened':False,'helper_sha256':file_sha(helper),'C_source_sha256':ledger['src/probes/descriptor_array_runtime.c'],'wrapper_sha256':ledger['tools/descriptor_array_runtime.py'],'sources':ledger,'plans':plans}
    (output/'preparation.json').write_bytes(canonical(report));return report


def admit(path,expected):
    path=Path(path).resolve()
    if not path.is_relative_to(ROOT/'local') or file_sha(path)!=expected:raise ValueError('Explicit reviewed plan digest required')
    plan=json.loads(path.read_text())
    if plan['profile']!=PROFILE or plan['sources']!=sources() or plan['max_dispatches']!=1 or plan['groups']!=[1,1,1] or plan['fence_timeout_ns']!=2000000000 or plan['process_timeout_seconds']!=30 or plan['single_use'] is not True:raise ValueError('Reviewed ABI/source/limits differ')
    directory=Path(plan['directory']);root=Path(plan['preparation_root'])
    if path!=directory/'reviewed-plan.json' or not directory.is_relative_to(root) or not root.is_relative_to(ROOT/'local'):raise ValueError('Prepared paths differ')
    for row in plan['files']:
        artifact_path=Path(row['path'])
        if artifact_path.is_symlink() or not artifact_path.is_relative_to(root) or artifact_path.stat().st_size!=row['bytes'] or file_sha(artifact_path)!=row['sha256']:raise ValueError('Prepared file changed')
    _,reflection=validate_fixture(directory/'fixture',plan['follow_stores'],plan['variant'],directory/'module.spv')
    host=load_dependency('tools/descriptor_array_compile_host.py')
    if reflection!=plan['reflection'] or host.driver_identity(Path(plan['driver_icd']))!=plan['driver_identity']:raise ValueError('Profile or E5 identity changed')
    if control_bytes(directory,plan['variant'],Path(plan['driver_icd']),Path(plan['driver_library']))!=(directory/'control.bin').read_bytes():raise ValueError('Native control/relocation/alias differs')
    if file_sha(plan['native_helper'])!=plan['native_helper_sha256']:raise ValueError('Native helper changed')
    return plan


def collect(command,env,stdout_path,stderr_path):
    process=subprocess.Popen(command,env=env,stdout=subprocess.PIPE,stderr=subprocess.PIPE,start_new_session=True)
    selector=selectors.DefaultSelector();selector.register(process.stdout,selectors.EVENT_READ,'stdout');selector.register(process.stderr,selectors.EVENT_READ,'stderr')
    start=time.monotonic();sizes={'stdout':0,'stderr':0};reason=None
    def stop():
        if process.poll() is None:
            try:os.killpg(process.pid,signal.SIGTERM)
            except ProcessLookupError:pass
            try:process.wait(timeout=.5)
            except subprocess.TimeoutExpired:
                try:os.killpg(process.pid,signal.SIGKILL)
                except ProcessLookupError:pass
                process.wait(timeout=1)
    with Path(stdout_path).open('wb') as out,Path(stderr_path).open('wb') as err:
        try:
            streams={'stdout':out,'stderr':err}
            while selector.get_map():
                remaining=30-(time.monotonic()-start)
                if remaining<=0:reason='deadline';break
                for key,_ in selector.select(min(.05,remaining)):
                    block=os.read(key.fileobj.fileno(),65536)
                    if not block:selector.unregister(key.fileobj);key.fileobj.close();continue
                    available=64*1024*1024-sum(sizes.values());kept=block[:available];streams[key.data].write(kept);sizes[key.data]+=len(kept)
                    if len(block)>available:reason='output-limit';break
                if reason:break
            if reason:stop()
            else:
                try:process.wait(timeout=max(.001,30-(time.monotonic()-start)))
                except subprocess.TimeoutExpired:reason='deadline';stop()
        finally:
            stop()
            for key in list(selector.get_map().values()):key.fileobj.close()
            selector.close()
    return {'returncode':process.returncode,'stop_reason':reason,'captured_bytes':sizes,'child_reaped':process.returncode is not None,'child_resource_limits_overridden':False}


def compare_execution(plan,output):
    """Validate complete host-owned upload/golden/readback bytes independently."""
    directory=Path(plan['directory']);output=Path(output)
    native=json.loads((output/'native-result.json').read_text());upload=json.loads((output/'upload.json').read_text())
    required_features=('variablePointersStorageBuffer','shaderStorageBufferArrayNonUniformIndexing','shaderInt64','bufferDeviceAddress','robustBufferAccess','robustBufferAccess2','subgroupSizeControl','computeFullSubgroups')
    if native['dispatches']!=1 or native['actual_subgroup_size']!=32 or native['actual_stage_flags']!=32 or native['both_pointer_features_enabled'] is not True or any(native['enabled_features'][key] is not True for key in required_features) or native['all_bytes_match'] is not True or native['compared_allocations']!=10 or native['compared_bytes']!=74458:raise ValueError('Native profile or comparison failed')
    if [row['name'] for row in native['allocations']]!=list(NAMES) or [row['name'] for row in upload['allocations']]!=list(NAMES) or native['follow_stores'] is not plan['follow_stores']:raise ValueError('Native allocation/case identity differs')
    actual={};addresses={}
    for row,size in zip(native['allocations'],SIZES):
        path=output/(row['name']+'-actual.bin')
        if row['bytes']!=size or path.stat().st_size!=size or type(row['address']) is not int or row['address']<=0 or row['address']%16 or row['address']+size>=1<<64:raise ValueError('Incomplete/invalid whole allocation readback')
        actual[row['name']]=path.read_bytes();addresses[row['name']]=row['address']
    spans=sorted((addresses[name],addresses[name]+size) for name,size in zip(NAMES,SIZES))
    if any(previous[1]>current[0] for previous,current in zip(spans,spans[1:])):raise ValueError('Distinct allocations unexpectedly overlap')
    fixture_plan=json.loads((directory/'fixture/plan.json').read_text())
    expected_upload={a['name']:(directory/'fixture'/a['initial_file']).read_bytes() for a in fixture_plan['allocations']}
    expected_goldens={a['name']:(directory/'fixture'/a['expected_file']).read_bytes() for a in fixture_plan['allocations']}
    for destination in (expected_upload,expected_goldens):
        table=bytearray(destination['page_table'])
        for relocation in fixture_plan['page_table_relocations']:
            struct.pack_into('<Q',table,relocation['write_offset'],addresses[relocation['target_allocation']]+relocation['target_offset'])
        destination['page_table']=bytes(table)
    for row,size in zip(upload['allocations'],SIZES):
        name=row['name'];initial=(output/(name+'-uploaded.bin')).read_bytes();golden=(output/(name+'-expected-relocated.bin')).read_bytes()
        if row['address']!=addresses[name] or row['bytes']!=size or initial!=expected_upload[name] or golden!=expected_goldens[name] or sha(initial)!=row['uploaded_sha256'] or sha(golden)!=row['expected_sha256']:raise ValueError('Host frozen upload/golden differs from independent bytes')
    push=bytearray((directory/'fixture/push-initial.bin').read_bytes());struct.pack_into('<Q',push,32,addresses['page_table']+64)
    if (output/'push-uploaded.bin').read_bytes()!=push or sha(push)!=upload['push_sha256']:raise ValueError('Uploaded push root or fields differ')
    fixture=load_dependency('tools/descriptor_array_runtime_fixture.py');comparison=fixture.verify_readback(directory/'fixture',actual,addresses)
    if comparison['match'] is not True:raise ValueError('Independent whole-allocation oracle failed')
    return native,comparison


def execute(args):
    plan=admit(args.plan,args.reviewed_sha256);directory=Path(plan['directory']);output=directory/'execution';output.mkdir()
    used=directory/'reviewed.used';descriptor=os.open(used,os.O_CREAT|os.O_EXCL|os.O_WRONLY,0o600);os.write(descriptor,args.reviewed_sha256.encode());os.close(descriptor)
    host=load_dependency('tools/descriptor_array_compile_host.py');env,policy=host.native_safe_environment(os.environ)
    if policy!=plan['driver_policy']:raise ValueError('Native-safe policy differs')
    for key in ('VK_ICD_FILENAMES','VK_ADD_DRIVER_FILES','VK_LOADER_DRIVERS_SELECT','VK_LOADER_DRIVERS_DISABLE'):env.pop(key,None)
    cache=output/'cache';cache.mkdir();env.update(VK_DRIVER_FILES=plan['driver_icd'],MESA_SHADER_CACHE_DISABLE='true',MESA_SHADER_CACHE_DIR=str(cache))
    command=[plan['native_helper'],'--execute',str(directory/'control.bin'),str(output)]
    (output/'launch.json').write_bytes(canonical({'reviewed_plan_sha256':args.reviewed_sha256,'command':command,'driver_policy':policy,'disk_cache_disabled':True}))
    terminal=collect(command,env,output/'native-result.json',output/'native-stderr.txt');(output/'terminal.json').write_bytes(canonical(terminal))
    report={'schema':1,'reviewed_plan_sha256':args.reviewed_sha256,'variant':plan['variant'],'follow_stores':plan['follow_stores'],'terminal':terminal,'error':None}
    try:
        if terminal['returncode'] or terminal['stop_reason']:raise ValueError('Native runtime case failed; no retry permitted')
        native,comparison=compare_execution(plan,output)
        report.update(native=native,independent_CPU_comparison=comparison,result_sha256=file_sha(output/'native-result.json'),upload_manifest_sha256=file_sha(output/'upload.json'))
    except (OSError,ValueError,KeyError) as failure:report['error']=str(failure)
    finally:
        try:admit(args.plan,args.reviewed_sha256);report['sources_and_inputs_unchanged_after_terminal']=True
        except (OSError,ValueError,KeyError) as failure:report['sources_and_inputs_unchanged_after_terminal']=False;report['error']=str(failure)
        (output/'verified-result.json').write_bytes(canonical(report))
    if report['error']:raise ValueError(report['error'])
    return report


def main():
    parser=argparse.ArgumentParser(description=__doc__);commands=parser.add_subparsers(dest='command',required=True)
    prepare_parser=commands.add_parser('prepare');prepare_parser.add_argument('--fixtures',type=Path,required=True);prepare_parser.add_argument('--driver',type=Path,required=True);prepare_parser.add_argument('--output',type=Path,required=True);prepare_parser.add_argument('--cc',default='cc');prepare_parser.add_argument('--validator',default='spirv-val')
    execute_parser=commands.add_parser('execute-reviewed');execute_parser.add_argument('--plan',type=Path,required=True);execute_parser.add_argument('--reviewed-sha256',required=True)
    args=parser.parse_args()
    try:print(json.dumps(prepare(args) if args.command=='prepare' else execute(args),indent=2))
    except (OSError,ValueError,KeyError,subprocess.SubprocessError) as error:
        print(str(error),file=sys.stderr)
        if isinstance(error,subprocess.CalledProcessError):print(error.stderr,file=sys.stderr)
        return 1
    return 0


if __name__=='__main__':raise SystemExit(main())
