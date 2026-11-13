#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Compile the selector prototype against exact private renderer struct shapes.

This tests source-member compatibility with authored objects and CPU mock owners.
It never calls the renderer, VMA, Vulkan, host import APIs or a shader.
"""
import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile

try:
    from .compile_probe import private_output
except ImportError:
    from compile_probe import private_output

ROOT=Path(__file__).resolve().parents[1]
CORE_SHA='eec1960f1c3a8df33d0c9ccbb70f8a01037713c912317bb705e87b8aae7bc88b'
PIN='4cf785a0b143bb34026506c9a3fe4f4bb93c797d'
SOURCE_SHA={
 'host/capture_backing.h':'04d09046d4b1175a19e68b0ef500421afc19f40b9b0568aca22463af20347e21',
 'gpu/vk/vk_draw.cpp':'1f0243d368973d6969cb04c766fb1fa713b578cc4932c0af14f7310f48475b49',
 'gpu/vk/vk_vma.cpp':'f377d14faea178ed3cf2f2ff4e50eb202291370cd263f808c1c00dc2790fd67c',
 'gpu/vk/vk_internal.h':'5e582b44784962d97edd475fbd314b8e19359d3378facfa1bb87ec2051f215c2',
 'gpu/vk/vk_memory.cpp':'798c732c88ac9413cb762c5aa32d814c76774527600123da3cf13bde2f9f9ec7',
 'gpu/vk/vk_replay.cpp':'32455c2f47ae68cb5335065f3d4ea42badb6ea3d12920fdf2a5de61b11d62492',
}
PATCH=ROOT/'patches/hookshot/compute-selector-extract-prototype.patch'
MISSING_HOOKS=[
 {'site':'DescWriter::reset / build_descriptors return','required':'generated reset/build epoch, exact Program/stage identity, recording slot and recording-part association'},
 {'site':'draw_begin_recording / recording_wait / submit completion','required':'new epoch only after actual slot completion; retain bundle/physical pins until its real submission completes'},
 {'site':'stream_insert / uniform_range actual upload / retained streams at begin / stream destruction','required':'register every old/current mapping and actual covering upload generation; cache reuse must name an upload in this recording; revoke stale/recycled mappings'},
 {'site':'oo_vma_memory_created/freed / oo_register_buffer / destroy_buffer','required':'always-covered VkDeviceMemory lifecycle and VMA suballocation offset/extent plus physical retain provider'},
 {'site':'ImportAttempt commit / imported and sparse buffer binds / pool retirement','required':'real host backing lease validation, exact bind ranges, completed sparse edges, physical imported-buffer retention or rejection'},
 {'site':'host upload / transfer / storage / physical possible writes','required':'complete serialized owner+range write journal, including cross-recording aliases; unknown access invalidates leases'},
 {'site':'actual dispatch and indirect grid copy','required':'seal after final build, then extract before matching dispatch; indirect requires actual12-byte C0-prefix copy event and retained arguments owner'},
 {'site':'candidate record generation/binding','required':'separate reviewed live mode and writer admission; frozen core still rejects LiveAsSubmitted'},
]


def sha(path):return hashlib.sha256(Path(path).read_bytes()).hexdigest()

def body(source, marker):
    start=source.index(marker);at=source.index('{',start)+1;depth=1
    while depth:
        depth+=(source[at]=='{')-(source[at]=='}');at+=1
    return source[start:at]


def load_harness():
    spec=importlib.util.spec_from_file_location('authored_selector_harness',ROOT/'tests/test_compute_selector_extract.py')
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module);return module


def chain_check(checkout):
    spec=importlib.util.spec_from_file_location('native96_chain',ROOT/'patches/hookshot/check_mesh_warp32_profile96.py')
    c=importlib.util.module_from_spec(spec);spec.loader.exec_module(c)
    if subprocess.check_output(['git','rev-parse','HEAD'],cwd=checkout,text=True).strip()!=PIN:
        raise ValueError('Wrong private source pin')
    with tempfile.TemporaryDirectory(prefix='oo-selector-index-') as tmp:
        env=dict(os.environ,GIT_INDEX_FILE=str(Path(tmp)/'index'))
        subprocess.run(['git','read-tree',PIN],cwd=checkout,env=env,check=True)
        hashes=[]
        for name in (*c.BASE,'mesh-warp32-profile96.patch'):
            patch=ROOT/'patches/hookshot'/name;hashes.append({'patch':name,'sha256':sha(patch)})
            subprocess.run(['git','apply','--cached',str(patch)],cwd=checkout,env=env,check=True)
        before=subprocess.check_output(['git','write-tree'],cwd=checkout,env=env,text=True).strip()
        subprocess.run(['git','apply','--cached','--check',str(PATCH)],cwd=checkout,env=env,check=True)
        subprocess.run(['git','apply','--cached',str(PATCH)],cwd=checkout,env=env,check=True)
        for name,source in [('compute_interval_records.h',ROOT/'src/probes/compute_interval_records.h'),
                            ('compute_selector_extract.h',ROOT/'src/probes/compute_selector_extract.h')]:
            actual=subprocess.check_output(['git','show',':gpu/vk/'+name],cwd=checkout,env=env)
            if actual!=source.read_bytes():raise ValueError('Prototype patch payload differs from authored source')
        return {'base_twelve_tree':before,'base_patches':hashes,'additive_prototype_patch_applies':True}


def run(checkout, output, compiler=None):
    checkout=Path(checkout).resolve();output=private_output(Path(output).resolve())
    if output.exists():raise ValueError('Choose a new private output directory')
    if sha(ROOT/'src/probes/compute_interval_records.h')!=CORE_SHA:raise ValueError('Frozen record core changed')
    for name,expected in SOURCE_SHA.items():
        if not (checkout/name).is_file() or sha(checkout/name)!=expected:raise ValueError('Unreviewed renderer source: '+name)
    compiler=compiler or shutil.which('g++') or shutil.which('clang++')
    if not compiler:raise ValueError('C++17 compiler required')
    text=(checkout/'gpu/vk/vk_draw.cpp').read_text()
    actual_types=body(text,'struct DescWriter {')+';\n'+body(text,'struct RecordingRes {')+';\n'
    host=(checkout/'host/capture_backing.h').read_text()
    actual_types+=body(host,'typedef struct HosCaptureBackingInfoV1 {')+' HosCaptureBackingInfoV1;\n'
    actual_types+='static_assert(sizeof(HosCaptureBackingInfoV1)==88, \"host ABI framing changed\");\n'
    reset=body(actual_types,'void reset()')
    if 'generation' in reset or 'selector_extract' in text:raise ValueError('Expected unhooked frozen12 source')
    if 'std::vector<Stream> streams' not in actual_types:raise ValueError('Retained stream shape changed')
    dispatch=body(text,'void dispatch(VkCommandBuffer cmd, NvnState& s, uint32_t x, uint32_t y, uint32_t z, uint64_t indirect)')
    if not ('build_descriptors(s, p);' in dispatch and 'const VkBufferCopy gc{args.offset, grid_patch_at, 12}' in dispatch):
        raise ValueError('Actual final-writer/grid-copy boundary changed')
    if 'pointer_table' in (ROOT/'src/probes/compute_selector_extract.h').read_text():raise ValueError('Extractor reconstructs guest selectors')
    output.mkdir(parents=True);h=load_harness()
    executable=h.compile_harness(output,actual_types,compiler)
    results=[]
    for case in h.CASES:
        r=subprocess.run([str(executable),case],capture_output=True,text=True,timeout=10)
        (output/(case+'.txt')).write_text(r.stdout+r.stderr)
        if r.returncode:raise ValueError('Source-backed authored case failed: '+case+' '+r.stdout+r.stderr)
        results.append(case)
    internal=(checkout/'gpu/vk/vk_internal.h').read_text();owner=body(internal,'struct OoBufferOwner {')
    if any(name in owner for name in ('memory_offset','generation','backing')):raise ValueError('Legacy owner interface changed; review bridge again')
    report={'schema':'open-ocarina.selector-extract-source-probe/v1','cpu_only':True,
            'source_sha256':SOURCE_SHA,'extractor_sha256':sha(ROOT/'src/probes/compute_selector_extract.h'),
            'record_core_sha256':CORE_SHA,'tests_sha256':sha(ROOT/'tests/test_compute_selector_extract.py'),
            'checker_sha256':sha(Path(__file__)),'prototype_patch_sha256':sha(PATCH),
            'compiler':subprocess.run([compiler,'--version'],check=True,capture_output=True,text=True).stdout.splitlines()[0],
            'source_backed_struct_shapes':['DescWriter','RecordingRes','RecordingRes::Stream','HosCaptureBackingInfoV1 (88-byte ABI)'],
            'case_count':len(results),'cases':results,'harness_sha256':sha(output/'test.cpp'),'binary_sha256':sha(executable),
            'retention_provider':'Authored CPU mock; actual memory_allocation_retain_buffer and host import functions were not called',
            'actual_source_lifecycle_hooks_implemented':False,'live_certificate_produced':False,
            'live_record_core_acceptance':False,'shared_source_mutated':False,'missing_production_hooks':MISSING_HOOKS,
            **chain_check(checkout)}
    (output/'report.json').write_text(json.dumps(report,indent=2)+'\n');return report


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--checkout',required=True,type=Path)
    p.add_argument('--output',required=True,type=Path);p.add_argument('--compiler');a=p.parse_args()
    try:print(json.dumps(run(a.checkout,a.output,a.compiler),indent=2))
    except (ValueError,OSError,subprocess.SubprocessError)as exc:p.exit(1,str(exc)+'\n')


if __name__=='__main__':main()
