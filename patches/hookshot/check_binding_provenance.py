#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Verify the real import/bind observer with optimized, independent synthetic inputs."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile

HERE = Path(__file__).resolve().parent
PATCH = HERE / 'binding-provenance.patch'
PIN = '4cf785a0b143bb34026506c9a3fe4f4bb93c797d'
BASE = ('compute-identity', 'page-table-snapshot', 'allocation-capture', 'occurrence-capture',
        'graphics-identity', 'sampled-image-capture', 'dispatch-parameters', 'backing-lease')
CASES = ('ordinary', 'reuse', 'sparse', 'rollback', 'retention', 'lease-invalid', 'disjoint-generation',
         'unknown', 'overlap', 'overflow', 'mirror', 'complete', 'stale-export', 'premature', 'disabled',
         'no-overwrite', 'short-write', 'attempt-rollback', 'prepublication-free', 'missing-api', 'late-invalidation')


def require(ok, message):
    if not ok:
        raise ValueError(message)


def header_from_patch(patch=PATCH, name='gpu/vk/binding_provenance.h'):
    lines = Path(patch).read_text().splitlines(keepends=True)
    try:
        start = lines.index('+++ b/' + name + '\n') + 1
    except ValueError as exc:
        raise ValueError('New standalone helper missing') from exc
    match = re.fullmatch(r'@@ -0,0 \+1,(\d+) @@\n', lines[start])
    require(match is not None, 'Malformed helper patch framing')
    payload = lines[start + 1:start + 1 + int(match[1])]
    require(len(payload) == int(match[1]) and all(line.startswith('+') for line in payload), 'Truncated standalone helper')
    return ''.join(line[1:] for line in payload)


def check_source(checkout):
    checkout = Path(checkout).resolve()
    require(subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=checkout, text=True).strip() == PIN, 'Pinned HEAD required')
    with tempfile.TemporaryDirectory(prefix='oo-binding-index-') as td:
        env = dict(os.environ, GIT_INDEX_FILE=str(Path(td) / 'index'))
        subprocess.run(['git', 'read-tree', PIN], cwd=checkout, env=env, check=True)
        hashes = {}
        for name in BASE:
            patch = HERE / (name + '.patch')
            hashes[name] = hashlib.sha256(patch.read_bytes()).hexdigest()
            subprocess.run(['git', 'apply', '--cached', str(patch)], cwd=checkout, env=env, check=True)
        subprocess.run(['git', 'apply', '--cached', '--check', str(PATCH)], cwd=checkout, env=env, check=True)
        subprocess.run(['git', 'apply', '--cached', str(PATCH)], cwd=checkout, env=env, check=True)
        files = {name: subprocess.check_output(['git', 'show', ':gpu/vk/' + name], cwd=checkout, env=env, text=True)
                 for name in ('vk_memory.cpp', 'vk_vma.cpp', 'binding_provenance.h')}
    memory = files['vk_memory.cpp'];vma = files['vk_vma.cpp']
    require(files['binding_provenance.h'] == header_from_patch(), 'Applied and tested helpers differ')
    require(memory.count('vkFreeMemory(') == 1 and 'vkFreeMemory(dev.device,memory,nullptr);' in memory,
            'Raw import frees bypass the provenance lease wrapper')
    require(memory.index('ImportAttempt provenance(lo,size') < memory.index('const int fd = ioctl('), 'DMA-BUF lease acquired after export')
    host = memory[memory.index('bool import_pool('):]
    require(host.index('ImportAttempt provenance(lo,bytes') < host.index('vkAllocateMemory('), 'Host-pointer lease acquired after import')
    require('bind.resourceOffset,bind.size,bind.memoryOffset,bind.flags,true,provenance_completion' in memory,
            'Sparse edges lose original offsets/flags or fence completion')
    require('ledger.sparse_submitted' in memory and 'ledger.sparse_completed' in memory, 'Sparse successful ordering missing')
    require('VmaDeviceMemoryCallbacks callbacks' in vma and 'ci.pDeviceMemoryCallbacks=&callbacks' in vma,
            'VMA physical memory lifetimes are inferred from reused handles')
    require('size?size:info.size,info.offset' in vma, 'VMA suballocation offset missing')
    require('oo_binding_pending=ledger.snapshot(wanted)' in memory and 'oo_binding_pending={}' in memory,
            'Selected provenance snapshot retention or cancellation release missing')
    require('oo_bind::write_completed(oo_binding_pending,request,completed' in memory, 'Exact recording completion export missing')
    return {'pinned_chain_apply_check': True, 'base_patches': hashes, 'raw_free_paths_centralized': True,
            'lease_before_raw_import': True, 'sparse_exact_edges': True, 'vma_memory_callbacks': True}


HARNESS = r'''
#include "gpu/vk/binding_provenance.h"
#include <sys/resource.h>
#include <csignal>
using namespace mkwvk;
#define CHECK(x) do{if(!(x)){std::fprintf(stderr,"line %d: %s\n",__LINE__,#x);return 3;}}while(0)
static int releases=0;static bool stale=false,late_invalidation=false;static uint64_t generation=1;static uint64_t serial=5;
static std::map<uint64_t,HosCaptureBackingInfoV1> leases;
extern "C" int hos_capture_backing_acquire(uint32_t abi,uint64_t guest,uint64_t size,HosCaptureBackingInfoV1* out,uint32_t n) {
 if(abi!=1||n!=sizeof(*out))return -1;
 *out={1,sizeof(*out),HOS_BACKING_FILE,0,++serial,1,2,guest,size,1,generation,3,0};leases[out->lease_id]=*out;return 1;
}
extern "C" int hos_capture_backing_validate(uint32_t,uint64_t id,HosCaptureBackingInfoV1* out,uint32_t) {
 if(!leases.count(id))return -2;
 *out=leases.at(id);out->current_generation=generation;
 if(stale){out->invalid_reason=HOS_BACKING_DISCARDED;out->invalidated_event=12;return -4;}return 1;
}
extern "C" int hos_capture_backing_release(uint32_t,uint64_t id){++releases;return leases.erase(id)?1:-2;}
extern "C" int hos_capture_backing_events(uint32_t,uint64_t after,HosCaptureBackingEventV1* out,uint32_t,uint32_t,uint64_t* next) {
 if(late_invalidation&&after==3){*out={1,sizeof(*out),HOS_BACKING_DISCARD_COMMIT,0,12,1,2,1024,4096,2};*next=12;return 1;}
 if(after>=3){*next=after;return 0;}*out={1,sizeof(*out),HOS_BACKING_MAPPING_COMMIT,0,3,1,2,0,65536,1};*next=3;return 1;
}
static oo_bind::Host api{hos_capture_backing_acquire,hos_capture_backing_validate,hos_capture_backing_release,hos_capture_backing_events};
int main(int argc,char** argv) {
 CHECK(argc==2);const std::string mode=argv[1];oo_bind::Ledger l;
 if(mode=="disabled"){ {oo_bind::ImportAttempt attempt(0,4096,"disabled_attempt");}CHECK(leases.empty());return 0;}
 if(mode=="attempt-rollback") {
  {oo_bind::ImportAttempt attempt(0,4096,"failed_attempt");attempt.result=-7;CHECK(leases.size()==1);}
  CHECK(leases.empty()&&releases==1);auto t=oo_bind::ledger().snapshot({});
  CHECK(std::any_of(t.events.begin(),t.events.end(),[](const auto& e){return e.kind=="import_rollback"&&e.result==-7;}));return 0;
 }
 if(mode=="prepublication-free") {
  auto pending=oo_bind::acquire(api,0,4096);l.memory(500,4096,"host-pointer",pending);pending.reset();
  auto held=l.free_memory(500);CHECK(releases==0);held.reset();CHECK(releases==1);return 0;
 }
 auto lease=oo_bind::acquire(mode=="missing-api"?oo_bind::Host{}:api,1024,4096);l.memory(100,4096,"dma-buf",lease);l.object(200,1024);l.bind(200,100,0,1024,128);
 auto s=l.snapshot({{200,0,"buffer"}});CHECK(s.objects.size()==1&&s.objects[0].known&&s.memories.size()==1);
 const auto old=s.objects[0].object.id;
 if(mode=="ordinary"){CHECK(oo_bind::covered(s.objects[0].object,s.memories));CHECK(s.objects[0].object.edges[0].memory_offset==128);return 0;}
 if(mode=="reuse"){l.destroy(200);l.free_memory(100);l.memory(100,4096,"vma");l.object(200,1024);l.bind(200,100,0,1024,0);auto t=l.snapshot({{200,0,"buffer"}});CHECK(t.objects[0].object.id>old&&t.memories.begin()->first!=s.memories.begin()->first);return 0;}
 if(mode=="sparse"||mode=="rollback"||mode=="overlap") {
  l.object(201,6000);l.memory(101,8192,"vma");auto submitted=l.sparse_submitted(201,0);auto done=l.sparse_completed(submitted,mode=="rollback"?-1:0);
  if(mode!="rollback"){l.bind(201,100,0,4096,0,7,true,done);l.bind(201,101,mode=="overlap"?2048:4096,4096,1024,3,true,done);}
  auto t=l.snapshot({{201,0,"buffer"}});CHECK(oo_bind::covered(t.objects[0].object,t.memories)==(mode=="sparse"));
  if(mode=="sparse"){const auto& e=t.objects[0].object.edges[1];CHECK(e.resource_offset==4096&&e.size==4096&&e.memory_offset==1024&&e.flags==3&&e.completion_sequence==done);}return 0;
 }
 if(mode=="retention"){lease.reset();l.destroy(200);auto keep=l.free_memory(100);CHECK(releases==0);keep.reset();CHECK(releases==0);s={};CHECK(releases==1);return 0;}
 if(mode=="lease-invalid"||mode=="disjoint-generation") {stale=mode=="lease-invalid";generation=2;HosCaptureBackingInfoV1 current{};auto status=api.validate(1,lease->acquired.lease_id,&current,sizeof current);CHECK(oo_bind::valid_info(lease->acquired,current,status)==!stale);return 0;}
 if(mode=="unknown"){CHECK(!l.snapshot({{999,0,"buffer"}}).objects[0].known);return 0;}
 if(mode=="overflow"){l.bind(200,100,UINT64_MAX,8,0);CHECK(!l.healthy());return 0;}
 if(mode=="mirror"){l.memory(101,8192,"vma");l.object(201,1024,"buffer",true);l.object(202,1024,"buffer",true);l.bind(201,101,0,1024,0);l.bind(202,101,0,1024,2048);auto t=l.snapshot({{200,0,"buffer"},{201,1,"buffer"},{202,2,"buffer"}});CHECK(t.memories.size()==2&&t.objects[1].object.allocation_id!=t.objects[2].object.allocation_id&&t.objects[1].object.edges[0].memory_offset!=t.objects[2].object.edges[0].memory_offset);return 0;}
 oo_pt::Request req;req.recording_part=2;req.logical_value=3;req.submitted_sequence=4;req.key=1;req.variant=2;req.shader_digest=std::string(64,'b');
 if(mode=="short-write"){std::signal(SIGXFSZ,SIG_IGN);rlimit limit{64,64};CHECK(setrlimit(RLIMIT_FSIZE,&limit)==0);}
 if(mode=="stale-export")stale=true;
 if(mode=="late-invalidation")late_invalidation=true;
 const auto before=mode=="premature"?3:4;
 const bool expected=mode!="premature"&&mode!="disabled"&&mode!="short-write";
 CHECK(oo_bind::write_completed(s,req,before,64,l.healthy())==expected);
 if(mode=="no-overwrite")CHECK(!oo_bind::write_completed(s,req,4,64));
 return 0;
}
'''


def check_helper(compiler=None):
    compiler = compiler or shutil.which('clang++') or shutil.which('g++')
    require(bool(compiler), 'C++20 compiler required')
    with tempfile.TemporaryDirectory(prefix='oo-binding-helper-') as temporary:
        root = Path(temporary)
        for patch, name in (('compute-identity', 'compute_identity.h'), ('page-table-snapshot', 'page_table_capture.h'),
                            ('allocation-capture', 'allocation_capture.h'), ('binding-provenance', 'binding_provenance.h')):
            path = root / 'gpu/vk' / name;path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(header_from_patch(HERE / (patch + '.patch'), 'gpu/vk/' + name))
        host = root / 'host/capture_backing.h';host.parent.mkdir()
        host.write_text(header_from_patch(HERE / 'backing-lease.patch', 'host/capture_backing.h'))
        (root / 'test.cpp').write_text(HARNESS)
        subprocess.run([compiler, '-std=c++20', '-O2', '-DNDEBUG', '-Wall', '-Wextra', '-Werror', '-rdynamic',
                        str(root / 'test.cpp'), '-ldl', '-pthread', '-o', str(root / 'helper')], check=True, capture_output=True, text=True)
        base = {k: v for k, v in os.environ.items() if not k.startswith('RX_VK_OO_')}
        for mode in CASES:
            output = root / mode;output.mkdir()
            env = dict(base, RX_VK_OO_IDENTITY_FILE=str(root / 'unused.jsonl'), RX_VK_OO_PT_SNAPSHOT_DIR=str(output),
                       RX_VK_OO_ALLOC_CAPTURE='1', RX_VK_OO_BINDING_PROVENANCE='0' if mode == 'disabled' else '1',
                       RX_VK_OO_PT_SNAPSHOT_SHA256='b' * 64)
            subprocess.run([str(root / 'helper'), mode], env=env, check=True, capture_output=True, text=True)
            artifact = output / 'binding-provenance.json'
            if mode in ('complete', 'no-overwrite', 'stale-export', 'missing-api', 'late-invalidation'):
                document = json.loads(artifact.read_text())
                require(document['backing_provenance_complete'] == (mode not in ('stale-export', 'missing-api', 'late-invalidation')), 'Lease validation completeness mismatch')
                require(document['cpu_writers_quiesced'] is False and document['replay_complete'] is False, 'Overstated replay evidence')
                validate_document(document)
            elif mode in ('premature', 'disabled', 'short-write'):
                require(not artifact.exists() and not artifact.with_suffix('.json.part').exists(), 'Incomplete/disabled export committed an artifact')
    return {'optimized_helper': True, 'cases': list(CASES), 'compiler': Path(compiler).name, 'gpu_execution': False}


def integer(value, label, low=0):
    require(type(value) is int and low <= value < 1 << 64, 'Invalid ' + label)
    return value


def validate_document(document):
    require(document.get('schema') == 'open-ocarina.binding-provenance/v1', 'Unknown provenance schema')
    require(document.get('replay_complete') is False and document.get('cpu_writers_quiesced') is False, 'Overstated replay/writer evidence')
    sequence = integer(document.get('recording_sequence'), 'recording sequence', 1)
    integer(document.get('completed_recording_sequence'), 'completion', sequence)
    for field in ('recording_part', 'logical_value', 'selected_occurrence', 'selected_ledger_sequence'):
        integer(document.get(field), field, 1)
    require(document.get('registry_start_sequence') == 0 and type(document['registry_start_sequence']) is int, 'Untracked registry start')
    require(type(document.get('backing_provenance_complete')) is bool, 'Missing provenance result')
    for field, width in (('program', 16), ('variant', 16), ('spirv_sha256', 64)):
        require(isinstance(document.get(field), str) and re.fullmatch('[0-9a-f]{%d}' % width, document[field]), 'Malformed pipeline identity')
    require(isinstance(document.get('gaps'), list) and all(type(g) is str for g in document['gaps']), 'Missing explicit gaps')
    require(not document['backing_provenance_complete'] or not document['gaps'], 'Complete provenance conceals gaps')
    events = {};previous = 0
    for event in document.get('events', []):
        current = integer(event.get('sequence'), 'event sequence', previous + 1)
        require(current <= document['selected_ledger_sequence'], 'Event follows selected snapshot')
        previous = current;events[current] = event
        require(isinstance(event.get('kind'), str), 'Malformed event kind')
        for field in ('object', 'related', 'offset', 'size', 'memory_offset', 'flags'):
            integer(event.get(field), 'event ' + field)
        require(type(event.get('result')) is int and -(1 << 63) <= event['result'] < 1 << 63, 'Invalid Vulkan result')
    require(events.get(document['selected_ledger_sequence'], {}).get('kind') == 'capture_selected', 'Selected ledger event missing')

    def event_matches(sequence, kind, **fields):
        event = events.get(sequence, {})
        require(event.get('kind') == kind and all(type(event.get(k)) is type(v) and event[k] == v for k, v in fields.items()),
                'Binding/lifetime event evidence differs')
        return event

    retired = {kind: {event['object'] for event in events.values() if event['kind'] == kind}
               for kind in ('memory_free', 'resource_destroy')}

    def selected_lifetime(created, lifetime, retired_kind):
        created = integer(created, 'lifetime creation sequence', 1)
        require(created < document['selected_ledger_sequence'], 'Lifetime created after selection')
        require(lifetime not in retired[retired_kind], 'Selected lifetime was destroyed or freed before selection')
        return created

    memories = {}
    for memory in document.get('memories', []):
        mid = integer(memory.get('lifetime_id'), 'memory lifetime', 1)
        require(isinstance(memory.get('handle'), str) and re.fullmatch('[0-9a-f]{16}', memory['handle']) and int(memory['handle'], 16), 'Invalid memory handle identity')
        require(mid not in memories, 'Duplicate memory lifetime')
        integer(memory.get('size'), 'memory size', 1);memories[mid] = memory
        selected_lifetime(memory.get('created_sequence'), mid, 'memory_free')
        event_matches(memory.get('created_sequence'), 'memory_create', object=mid, size=memory['size'])
        require(memory.get('kind') in ('vma', 'dma-buf', 'host-pointer'), 'Unknown memory import kind')
        if memory['kind'] != 'vma' and document['backing_provenance_complete']:
            a = memory['acquired'];b = memory['validated']
            require(a.get('abi_version') == 1 and b.get('abi_version') == 1 and a.get('struct_size') == 88 and b.get('struct_size') == 88 and a.get('kind') in (1, 2) and a['kind'] == b.get('kind'), 'Host lease ABI/kind differs')
            require(a.get('size') == memory['size'] and a.get('invalid_reason') == 0 and a.get('invalidated_event') == 0, 'Imported extent or acquired status differs')
            require(memory.get('acquire_result') == 1 and memory.get('validation_result') == 1 and not b.get('invalid_reason') and not b.get('invalidated_event'), 'Invalid host lease')
            for field in ('lease_id', 'backing_id', 'mapping_id', 'backing_offset', 'size', 'acquired_generation', 'acquired_event'):
                integer(a.get(field), 'lease ' + field, 0 if field == 'backing_offset' else 1)
                require(type(b.get(field)) is int and a[field] == b[field], 'Changed host lease origin')
            integer(b.get('current_generation'), 'current generation', a['current_generation'])
    ids = set()
    for obj in document.get('objects', []):
        require(type(obj.get('tracked')) is bool and obj.get('kind') in ('buffer', 'image'), 'Malformed object')
        integer(obj.get('catalog_id'), 'catalog index')
        require(isinstance(obj.get('handle'), str) and re.fullmatch('[0-9a-f]{16}', obj['handle']), 'Invalid resource handle identity')
        if not obj['tracked']:
            require(not document['backing_provenance_complete'], 'Untracked owner declared complete');continue
        require(int(obj['handle'], 16) != 0, 'Tracked null resource')
        oid = integer(obj.get('lifetime_id'), 'resource lifetime', 1)
        require(oid not in ids, 'Duplicate resource lifetime');ids.add(oid)
        extent = integer(obj.get('size'), 'resource size', 1);intervals = []
        allocation_id = integer(obj.get('allocation_lifetime_id'), 'allocation lifetime')
        created = selected_lifetime(obj.get('created_sequence'), oid, 'resource_destroy')
        event_matches(obj.get('created_sequence'), 'resource_create', object=oid, related=allocation_id, size=extent)
        for edge in obj.get('edges', []):
            mid = integer(edge.get('memory_lifetime_id'), 'edge memory', 1)
            require(mid in memories, 'Edge references unknown memory')
            ro = integer(edge.get('resource_offset'), 'resource offset');size = integer(edge.get('size'), 'bind size', 1)
            mo = integer(edge.get('memory_offset'), 'memory offset');integer(edge.get('flags'), 'sparse flags')
            require(ro + size < 1 << 64 and mo + size <= memories[mid]['size'], 'Binding interval outside memory')
            sequence = integer(edge.get('bind_sequence'), 'bind sequence', 1)
            require(max(created, memories[mid]['created_sequence']) < sequence < document['selected_ledger_sequence'],
                    'Binding must follow resource and memory creation and precede selection')
            require(type(edge.get('sparse')) is bool, 'Sparse bind policy missing')
            event_matches(sequence, 'sparse_bind_commit' if edge['sparse'] else 'ordinary_bind_commit',
                          object=oid, related=mid, offset=ro, size=size, memory_offset=mo, flags=edge['flags'])
            if edge['sparse']:
                completion = integer(edge.get('fence_completion_sequence'), 'sparse completion', 1)
                require(completion < sequence, 'Sparse bind precedes fence completion')
                fence = event_matches(completion, 'sparse_fence_result', result=0)
                require(fence['related'] < completion, 'Sparse fence precedes queue submission')
                require(max(created, memories[mid]['created_sequence']) < fence['related'],
                        'Sparse submission precedes resource or memory creation')
                event_matches(fence['related'], 'sparse_queue_result', object=oid, result=0)
            else:
                require(edge.get('fence_completion_sequence') == 0 and edge['flags'] == 0, 'Ordinary bind carries unsupported sparse flags')
            if ro < extent:intervals.append((ro, min(extent, ro + size)))
        if document['backing_provenance_complete']:
            end = 0
            for start, finish in sorted(intervals):
                require(start == end, 'Binding gap/overlap');end = finish
            require(end == extent, 'Incomplete binding interval')
    require(ids or not document['backing_provenance_complete'], 'Empty complete catalog')
    host_previous = 0
    require(type(document.get('host_event_history_complete')) is bool, 'Host event coverage missing')
    for event in document.get('host_events', []):
        host_previous = integer(event.get('event_id'), 'host event sequence', host_previous + 1)
        for field in ('kind', 'result_errno', 'backing_id', 'mapping_id', 'backing_offset', 'size', 'generation'):
            integer(event.get(field), 'host event ' + field)
        require(event['backing_offset'] + event['size'] < 1 << 64, 'Host event interval overflows')
    require(document.get('host_event_endpoint') == host_previous, 'Host event endpoint differs')
    if document['backing_provenance_complete']:
        for memory in memories.values():
            if memory['kind'] == 'vma':
                continue
            a = memory['acquired']
            require(document['host_event_history_complete'], 'Incomplete host event history')
            require(a['backing_offset'] + a['size'] < 1 << 64, 'Host lease interval overflows')
            require(any(event['kind'] == 1 and event['backing_id'] == a['backing_id'] and event['mapping_id'] == a['mapping_id']
                        and event['event_id'] <= a['acquired_event']
                        and event['backing_offset'] <= a['backing_offset']
                        and a['backing_offset'] + a['size'] <= event['backing_offset'] + event['size']
                        for event in document['host_events']), 'Original host mapping does not cover retained import')
            for event in document['host_events']:
                if event['event_id'] <= a['acquired_event'] or event['backing_id'] != a['backing_id'] or event['kind'] == 1:
                    continue
                require(not (a['backing_offset'] < event['backing_offset'] + event['size'] and
                             event['backing_offset'] < a['backing_offset'] + a['size']), 'Host invalidation overlaps retained import')
    require(document.get('remaining_gates') == ['cpu_writer_epoch', 'temporal_state', 'canonical_alias_payload_agreement', 'independent_output_comparison'], 'Unresolved replay gates concealed')
    return {'artifact_integrity_verified': True, 'backing_provenance_complete': document['backing_provenance_complete'], 'replay_complete': False}


def validate_capture(directory):
    directory = Path(directory)
    require(directory.is_dir() and not directory.is_symlink(), 'Regular capture directory required')
    path = directory / 'binding-provenance.json'
    require(path.is_file() and not path.is_symlink() and path.stat().st_size <= 64 << 20, 'Missing/nonregular/oversized ledger')
    document = json.loads(path.read_text())
    result = validate_document(document)
    associated = {}
    names = ['manifest.json', 'allocations.json']
    if any(obj['kind'] == 'image' for obj in document['objects']):
        names.append('images.json')
    for name in names:
        artifact = directory / name
        require(artifact.is_file() and not artifact.is_symlink() and artifact.stat().st_size <= 16 << 20,
                'Missing/nonregular associated capture manifest: ' + name)
        associated[name] = json.loads(artifact.read_text())
    table = associated['manifest.json']
    require(table.get('schema') == 'open-ocarina.gpu-page-table/v1', 'Associated page-table schema differs')
    require(table.get('pipeline') == {name: document[name] for name in ('program', 'variant', 'spirv_sha256')},
            'Associated table pipeline differs')
    require(table.get('submission') == {name: document[name] for name in
            ('recording_part', 'logical_value', 'recording_sequence', 'completed_recording_sequence')},
            'Associated exact recording differs')
    for name in names[1:]:
        other = associated[name]
        schema = 'open-ocarina.gpu-allocations/v2' if name == 'allocations.json' else 'open-ocarina.sampled-images/v1'
        require(other.get('schema') == schema, 'Associated resource schema differs')
        for field in ('program', 'variant', 'spirv_sha256', 'recording_part', 'recording_sequence',
                      'completed_recording_sequence', 'selected_occurrence'):
            require(type(other.get(field)) is type(document[field]) and other[field] == document[field],
                    'Associated resource identity/recording differs')
    buffers = associated['allocations.json'].get('buffers')
    require(isinstance(buffers, list), 'Associated buffer catalog missing')
    sources = [obj for obj in document['objects'] if obj['kind'] == 'buffer']
    require(len(sources) == len(buffers), 'Selected buffer catalog coverage differs')
    require(sorted(obj.get('catalog_id') for obj in sources) == list(range(len(buffers))), 'Catalog buffer indices differ')
    for obj in sources:
        row = buffers[obj['catalog_id']]
        require(row.get('buffer') == obj.get('handle') and (not obj['tracked'] or row.get('size') == obj.get('size')),
                'Ledger object differs from selected buffer catalog')
    if 'images.json' in associated:
        rows = associated['images.json'].get('images')
        require(isinstance(rows, list), 'Associated sampled-image catalog missing')
        sources = [obj for obj in document['objects'] if obj['kind'] == 'image']
        require({obj['handle'] for obj in sources} == {row.get('image', {}).get('handle') for row in rows},
                'Selected image catalog coverage differs')
        for obj in sources:
            index = integer(obj.get('catalog_id'), 'image catalog index')
            require(index < len(rows) and rows[index].get('image', {}).get('handle') == obj.get('handle'),
                    'Ledger object differs from selected image catalog')
    result['same_capture_catalog_checked'] = True
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--checkout', type=Path);parser.add_argument('--compiler');parser.add_argument('--validate-snapshot', type=Path)
    args = parser.parse_args()
    result = validate_capture(args.validate_snapshot) if args.validate_snapshot else check_helper(args.compiler)
    if args.checkout:result['source'] = check_source(args.checkout)
    print(json.dumps(result, indent=2))


if __name__ == '__main__':
    main()
