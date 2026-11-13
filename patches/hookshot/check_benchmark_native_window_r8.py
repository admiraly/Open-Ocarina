#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Compile the r8 window header from its patch against CPU-only synthetic fixtures."""
from __future__ import annotations
import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
ROOT=Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:sys.path.insert(0,str(ROOT))
import re
from tools.benchmark_native_window_r8 import read, summarize
import tools.benchmark_native_window_r8 as reader
def function(source, signature):
    start=source.index(signature); opening=source.index('{', start); depth=0
    for end in range(opening, len(source)):
        if source[end]=='{': depth+=1
        elif source[end]=='}':
            depth-=1
            if not depth: return source[start:end+1]
    raise ValueError('unterminated production helper')
PATCH=ROOT/'patches/hookshot/benchmark-native-window-r8.patch'
HEADER_SHA='207893b73e8e884c46b0201e76cdfbc946563ae8e705ef52c458f25ad63066fc'
EXECUTED={'host/nvn_native.c':'cf22df90f2df652762a1fc697cb7d3e6f895c7b60980fb16078b61c6d798f6ab','host/control.c':'1439a963d035d8791aa3bcb4128b998ae352285dd351ba60dbbd4b25985a8550','gpu/vk/vk_replay.cpp':'bcc7cb05d2f24797044eea819ba391cf2f36f81cc900910b0b9a4a87142487b1','gpu/vk/oo_benchmark_renderer.h':'f46c0102c1f98b42f29164c991359070acded85025ad9182b57c5586ad52bad3','host/oo_benchmark_window.h':'207893b73e8e884c46b0201e76cdfbc946563ae8e705ef52c458f25ad63066fc'}
BASE_TREE='85562df89be995cba1985717f26aa5df8cd118f8'
def added_file(name, patch):
    lines=patch.read_text().splitlines(keepends=True)
    marker='+++ b/'+name+'\n'
    start=lines.index(marker)+1
    match=re.fullmatch(r'@@ -0,0 \+1,(\d+) @@\n', lines[start])
    if not match: raise ValueError('unsupported addition framing '+name)
    n=int(match.group(1)); payload=lines[start+1:start+1+n]
    if len(payload)!=n or any(not line.startswith('+') for line in payload): raise ValueError('truncated addition '+name)
    return ''.join(line[1:] for line in payload)
HEADER=added_file('host/oo_benchmark_window.h', PATCH)
CASES=('startup','startup_failure','boundary','mixed_batch','mixed_batch_earlier','startup_capacity','startup_reinit','held','partial','missing_closure','failed_batch','slot_reuse','lifetime_reuse','overflow','existing','symlink','sync_failure','close_failure','publish_failure','exit_without_close','disabled','bridge_only','large_owned','burst_cover','stuck_link','aggregate_refuse','resumed_cover','shared_cover','missing_first','foreign_part','cross_boundary','failed_link','held_link','partial_link','lifetime_link','link_capacity','changed_origin','midrun-gap','midrun-no-links','suspended-run','settled-missing-link')
DRIVER=r'''
#define _GNU_SOURCE
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>
static uint64_t ticks;
static uint64_t clock_tick(void){return ++ticks;}
static int fault;
static int sync_file(int fd){return fault==1?-1:fsync(fd);}
static int close_file(int fd){int result=close(fd);return fault==2?-1:result;}
static int publish_file(const char* from,const char* to);
#define OO_WINDOW_SYNC(fd) sync_file(fd)
#define OO_WINDOW_CLOSE(fd) close_file(fd)
#define OO_WINDOW_LINK(from,to) publish_file(from,to)
#define OO_BENCH_CLOCK_NS() clock_tick()
#define OO_BENCH_BRIDGE_IMPLEMENTATION
#include "oo_benchmark_window.h"
static int publish_file(const char* from,const char* to){return fault==3?-1:link(from,to);}
#define CHECK(x) do{if(!(x)){fprintf(stderr,"line %d: %s\n",__LINE__,#x);return 3;}}while(0)
static const OoBenchBridgeV1* b;
static uint64_t ingress(uint32_t kind,uint64_t key,uint64_t serial,uint64_t value,uint32_t slot,uint64_t generation,uint64_t part,uint64_t seq){
 int32_t crop[4]={-1,2,640,360};unsigned char payload[16]={1,2,3,4};uint64_t origin=b->ingress(kind,key,serial,0,crop,kind==OO_BENCH_SUBMIT?1:0);
 if(kind==OO_BENCH_SUBMIT)b->stream(origin,0,payload,sizeof payload);
 b->bind_value(value,origin,kind==OO_BENCH_SUBMIT?key:0,kind);b->part_begin(part,slot,generation,value);b->part_plan(part,seq,99);return origin;
}
int main(int argc,char** argv){CHECK(argc==2);b=hos_posix_oo_benchmark_window_v2();if(!strcmp(argv[1],"disabled")||!strcmp(argv[1],"bridge_only")){CHECK(!b);return 0;}CHECK(b);OoWindowStatus w;OoBenchStatusV1 s;
 CHECK(b->lifetime(OO_BENCH_QUEUE,42,OO_BENCH_CREATE));CHECK(b->lifetime(OO_BENCH_WINDOW,77,OO_BENCH_CREATE));CHECK(b->lifetime(OO_BENCH_TIMELINE,99,OO_BENCH_CREATE));
 for(uint64_t i=1;i<=12000;++i){CHECK(!b->ingress(OO_BENCH_SUBMIT,42,i,0,NULL,1));b->bind_value(i,0,42,OO_BENCH_SUBMIT);b->part_begin(i,0,i,0);b->part_plan(i,i,99);b->part_result(i,i,0);b->counter(99,0,i);b->note(OO_BENCH_SOURCE_ATOMICITY,0,0,0);}
 if(!strcmp(argv[1],"startup_capacity")){for(uint64_t i=1000;i<2030;++i)b->lifetime(OO_BENCH_QUEUE,i,OO_BENCH_CREATE);CHECK(b->window(1,&w)==-3);return 0;}
 if(!strcmp(argv[1],"startup_reinit")){CHECK(!b->lifetime(OO_BENCH_QUEUE,42,OO_BENCH_CREATE));CHECK(b->window(1,&w)==-3);return 0;}
 if(!strcmp(argv[1],"startup_failure"))b->part_result(12000,12000,-4);
 if(!strcmp(argv[1],"mixed_batch_earlier"))b->part_hold(11999,11); // unselected earlier member is not the batch maximum
 CHECK(b->status(&s)&&s.events==0&&s.bytes==0&&s.lost==0);CHECK(b->window(0,&w)==0&&w.state==OO_WINDOW_IDLE);CHECK(b->window(1,&w)==0&&w.state==OO_WINDOW_OPEN&&w.events==1&&w.bytes==0);CHECK(b->window(1,&w)==-2);
 if(!strcmp(argv[1],"stuck_link")){uint64_t origin=b->ingress(OO_BENCH_SUBMIT,42,12001,0,NULL,1);CHECK(origin);b->note(OO_BENCH_BAD_ORIGIN,origin,0,1048576+24);b->note(OO_BENCH_BURST,origin,0,0);b->note(OO_BENCH_EVENT_WAIT,origin,0,0);b->note(OO_BENCH_SOURCE_ATOMICITY,origin,0,0);CHECK(b->window(0,&w)==0&&w.events<64);int result=b->window(2,&w);uint32_t stopped=w.events;for(int i=0;i<5000;++i){b->counter(99,0,2);b->part_begin(30000u+(uint64_t)i,3,(uint64_t)i+1,0);b->note(OO_BENCH_BURST,origin,0,0);}CHECK(b->window(0,&w)==0&&w.events==stopped&&w.lost==0&&result==-3&&w.state==OO_WINDOW_ERROR);CHECK(w.denial_count&&w.denial_reason==OO_BENCH_BAD_ORIGIN&&w.denial_handle==0&&w.denial_prefix==1048576+24&&w.pending_streams);return 0;}
 if(!strcmp(argv[1],"aggregate_refuse")){uint64_t origin=b->ingress(OO_BENCH_SUBMIT,42,12001,0,NULL,1);unsigned char* big=malloc((size_t)OO_BENCH_MAX_BYTES+1);CHECK(big);memset(big,7,(size_t)OO_BENCH_MAX_BYTES+1);CHECK(b->stream(origin,0,big,(uint32_t)OO_BENCH_MAX_BYTES+1)==OO_BENCH_CAPACITY);b->note(OO_BENCH_CAPACITY,origin,0,(uint32_t)OO_BENCH_MAX_BYTES+1);CHECK(b->window(2,&w)==-3&&w.state==OO_WINDOW_ERROR&&w.bytes==0&&w.denial_reason==OO_BENCH_CAPACITY&&w.denial_prefix==(uint32_t)OO_BENCH_MAX_BYTES+1);free(big);return 0;}
 if(!strcmp(argv[1],"large_owned")||!strcmp(argv[1],"burst_cover")){unsigned char payload[16]={1,2,3,4};const uint32_t big_n=!strcmp(argv[1],"large_owned")?(1024u*1024u+64u):16u;unsigned char* big=malloc(big_n);CHECK(big);memset(big,0x5a,big_n);uint64_t o1=b->ingress(OO_BENCH_SUBMIT,42,12001,0,NULL,1);CHECK(b->stream(o1,0,big,big_n)==0);big[0]=0;unsigned char kept=0;CHECK(b->read_bytes(0,&kept,1)&&kept==0x5a);b->bind_value(11,o1,42,OO_BENCH_SUBMIT);b->part_begin(20001,0,12001,11);b->part_plan(20001,12001,99);b->part_result(20001,12001,0);uint64_t o2=b->ingress(OO_BENCH_SUBMIT,42,12002,0,NULL,1);CHECK(b->stream(o2,0,payload,sizeof payload)==0);b->bind_value(12,o2,42,OO_BENCH_SUBMIT);b->note(0,o2,20001,4);b->part_begin(8,0,12002,0);int32_t crop[4]={1,2,3,4};uint64_t present=b->ingress(OO_BENCH_PRESENT,77,1,0,crop,0);b->bind_value(13,present,0,OO_BENCH_PRESENT);b->part_begin(20002,1,1,13);b->part_plan(20002,12002,99);b->part_result(20002,12002,0);b->present_closed(13);CHECK(b->window(2,&w)==1&&w.state==OO_WINDOW_DRAINING);CHECK(b->window(0,&w)==0);uint32_t before=w.events;for(int i=0;i<4000;++i){b->counter(99,0,1);b->part_begin(40000u+(uint64_t)i,4,(uint64_t)i+1,0);b->note(OO_BENCH_SOURCE_ATOMICITY,o1,0,0);}CHECK(b->window(0,&w)==0&&w.events<before+32&&w.lost==0);b->counter(99,0,12002);CHECK(b->window(2,&w)==0&&w.state==OO_WINDOW_WRITTEN&&w.pending==0);free(big);_exit(0);}
 uint64_t o=ingress(OO_BENCH_SUBMIT,42,12001,1,0,12001,20001,12001);CHECK(o);uint64_t p=ingress(OO_BENCH_PRESENT,77,1,2,1,1,20002,12002);CHECK(p);
 if(!strcmp(argv[1],"midrun-gap")||!strcmp(argv[1],"midrun-no-links")||!strcmp(argv[1],"suspended-run")||!strcmp(argv[1],"settled-missing-link")){
  b->note(0,o,1,1);
  if(strcmp(argv[1],"midrun-no-links"))b->note(0,o,20001,4);
  b->part_begin(20003,2,1,1);b->part_plan(20003,12003,99);
  b->part_result(20001,12001,0);b->part_result(20002,12002,0);b->part_result(20003,12003,0);b->present_closed(2);b->counter(99,0,12003);
  if(!strcmp(argv[1],"settled-missing-link")){b->note(0,o,1,3);CHECK(b->window(2,&w)==-3&&w.state==OO_WINDOW_ERROR);return 0;}
  if(!strcmp(argv[1],"suspended-run"))b->note(0,o,1,2);
  CHECK(b->window(2,&w)==1&&w.state==OO_WINDOW_DRAINING&&w.pending&&!(w.flags&(UINT64_C(1)<<OO_BENCH_BAD_PART)));
  if(!strcmp(argv[1],"suspended-run"))b->note(0,o,1,1);
  b->note(0,o,20001,4);b->note(0,o,20003,4);b->note(0,o,1,3);
  CHECK(b->window(2,&w)==0&&w.state==OO_WINDOW_WRITTEN&&w.pending==0);return 0;
 }
 if(!strcmp(argv[1],"overflow")){for(int i=0;i<5000;++i)b->note(OO_BENCH_SOURCE_ATOMICITY,o,0,0);CHECK(b->window(2,&w)!=0&&w.lost);return 0;}
 if(!strcmp(argv[1],"exit_without_close"))return 0;
 if(!strcmp(argv[1],"held")||!strcmp(argv[1],"mixed_batch")||!strcmp(argv[1],"mixed_batch_earlier")||!strcmp(argv[1],"failed_batch")){b->part_hold(20001,11);b->part_hold(20002,11);b->counter(99,0,12002);CHECK(b->part_state(20001)==OO_BENCH_PENDING_BATCH);CHECK(b->window(2,&w)==1&&w.state==OO_WINDOW_DRAINING);b->batch_result(11,!strcmp(argv[1],"mixed_batch")?12003:12002,!strcmp(argv[1],"failed_batch")?-4:0);if(!strcmp(argv[1],"failed_batch")){CHECK(b->window(2,&w)==1&&w.pending);return 0;}}
 else{b->part_result(20001,12001,0);b->part_result(20002,12002,0);}
 if(!strcmp(argv[1],"missing_closure")){b->counter(99,0,12002);CHECK(b->window(2,&w)==1&&w.pending);return 0;}
 b->present_closed(2);
 if(!strcmp(argv[1],"partial")){b->counter(99,0,12001);CHECK(b->part_state(20001)==OO_BENCH_DONE&&b->part_state(20002)==OO_BENCH_SUBMITTED);CHECK(b->window(2,&w)==1&&w.pending);}
 if(!strcmp(argv[1],"lifetime_reuse")){CHECK(b->lifetime(OO_BENCH_TIMELINE,99,OO_BENCH_RETIRE));CHECK(b->lifetime(OO_BENCH_TIMELINE,99,OO_BENCH_CREATE));b->counter(99,0,12002);CHECK(b->part_state(20001)==OO_BENCH_SUBMITTED);CHECK(b->window(2,&w)==1);return 0;}
 b->counter(99,0,12002);
 if(!strcmp(argv[1],"resumed_cover")||!strcmp(argv[1],"shared_cover")||!strcmp(argv[1],"missing_first")||!strcmp(argv[1],"foreign_part")||!strcmp(argv[1],"cross_boundary")||!strcmp(argv[1],"failed_link")||!strcmp(argv[1],"held_link")||!strcmp(argv[1],"partial_link")||!strcmp(argv[1],"lifetime_link")||!strcmp(argv[1],"link_capacity")||!strcmp(argv[1],"changed_origin")){
  if(!strcmp(argv[1],"changed_origin")){uint64_t o2=b->ingress(OO_BENCH_SUBMIT,42,12004,0,NULL,1);unsigned char payload[16]={9,9,9,9,9,9,9,9,9,9,9,9,9,9,9,9};CHECK(b->stream(o2,0,payload,16)==0);b->bind_value(1,o2,42,OO_BENCH_SUBMIT);b->note(0,o2,1,3);CHECK(b->window(2,&w)==-3&&w.state==OO_WINDOW_ERROR&&(w.flags&(UINT64_C(1)<<OO_BENCH_BAD_ORIGIN))&&w.fault_count);return 0;}
  if(!strcmp(argv[1],"foreign_part")){b->note(0,o,999999,4);CHECK(b->window(2,&w)==-3&&w.state==OO_WINDOW_ERROR&&w.fault_count&&w.faults[0].reason==OO_BENCH_BAD_PART&&w.faults[0].part==999999);return 0;}
  if(!strcmp(argv[1],"cross_boundary")){b->part_begin(20004,3,1,0);b->note(0,o,1,4);b->note(0,o,20004,4);CHECK(b->window(2,&w)==-3&&w.state==OO_WINDOW_ERROR&&w.fault_count>=1&&w.faults[0].reason==OO_BENCH_BAD_PART);return 0;}
  if(!strcmp(argv[1],"failed_link")){b->part_begin(20003,2,1,1);b->part_plan(20003,12003,99);b->part_result(20003,12003,-4);b->note(0,o,20001,4);b->note(0,o,20003,4);CHECK(b->window(2,&w)==-3&&w.state==OO_WINDOW_ERROR&&(w.flags&(UINT64_C(1)<<OO_BENCH_BAD_PART)));return 0;}
  if(!strcmp(argv[1],"held_link")){b->part_begin(20003,2,1,1);b->part_plan(20003,12003,99);b->part_hold(20003,77);b->note(0,o,20001,4);b->note(0,o,20003,4);CHECK(b->window(2,&w)==1&&w.state==OO_WINDOW_DRAINING&&w.pending&&w.state!=OO_WINDOW_WRITTEN);return 0;}
  if(!strcmp(argv[1],"partial_link")){b->part_begin(20003,2,1,1);b->part_plan(20003,12003,99);b->part_result(20003,12003,0);b->note(0,o,20001,4);b->note(0,o,20003,4);CHECK(b->window(2,&w)==1&&w.state==OO_WINDOW_DRAINING&&w.pending_parts);return 0;}
  if(!strcmp(argv[1],"lifetime_link")){CHECK(b->lifetime(OO_BENCH_TIMELINE,99,OO_BENCH_RETIRE));CHECK(b->lifetime(OO_BENCH_TIMELINE,99,OO_BENCH_CREATE));b->note(0,o,20001,4);CHECK(b->window(2,&w)==-3&&w.state==OO_WINDOW_ERROR&&w.fault_count&&w.faults[0].reason==OO_BENCH_BAD_PART);return 0;}
  if(!strcmp(argv[1],"missing_first")){b->part_begin(20003,2,1,1);b->part_plan(20003,12003,99);b->part_result(20003,12003,0);b->counter(99,0,12003);b->note(0,o,20003,4);uint32_t events_before=0;OoBenchStatusV1 before;CHECK(b->status(&before));events_before=before.events;int rr=b->window(2,&w);uint32_t stopped=w.events;uint64_t lost=w.lost;for(int i=0;i<800;++i){b->note(OO_BENCH_BURST,o,0,0);b->note(OO_BENCH_SOURCE_ATOMICITY,o,0,0);b->part_begin(60000u+(uint64_t)i,9,(uint64_t)i+1,0);}CHECK(rr==-3&&w.state==OO_WINDOW_ERROR&&w.fault_count&&w.faults[0].reason==OO_BENCH_BAD_PART&&w.faults[0].part==20001);CHECK(b->window(0,&w)==0&&w.state==OO_WINDOW_ERROR&&w.events==stopped&&w.lost==lost&&w.fault_count);CHECK(b->window(2,&w)==-2&&w.fault_count&&w.pending_parts+w.pending_streams+w.pending_presents==w.pending);(void)events_before;return 0;}
  if(!strcmp(argv[1],"link_capacity")){b->note(0,o,20001,4);for(uint32_t i=1;i<=512;++i){uint64_t part=30000u+i;b->part_begin(part,7,i,1);b->part_plan(part,13000u+i,99);b->part_result(part,13000u+i,0);b->note(0,o,part,4);}b->counter(99,0,20000);CHECK(b->window(2,&w)==-3&&w.state==OO_WINDOW_ERROR&&(w.flags&(UINT64_C(1)<<OO_BENCH_CAPACITY))&&w.fault_count);return 0;}
  b->note(0,o,20001,4);if(!strcmp(argv[1],"shared_cover")){unsigned char payload[16]={9,9,9,9,9,9,9,9,9,9,9,9,9,9,9,9};uint64_t o2=b->ingress(OO_BENCH_SUBMIT,42,12004,0,NULL,1);CHECK(b->stream(o2,0,payload,16)==0);b->note(0,o2,20001,4);}else{b->part_begin(20003,2,1,1);b->part_plan(20003,12003,99);b->part_result(20003,12003,0);b->note(0,o,20003,4);b->counter(99,0,12003);}CHECK(b->window(2,&w)==0&&w.state==OO_WINDOW_WRITTEN&&w.pending==0&&!(w.flags&(UINT64_C(1)<<OO_BENCH_BAD_PART)));_exit(0);}
 if(!strcmp(argv[1],"slot_reuse")||!strcmp(argv[1],"boundary")){if(!strcmp(argv[1],"boundary"))b->part_begin(20004,0,12002,0);uint64_t p2=ingress(OO_BENCH_PRESENT,77,2,3,0,!strcmp(argv[1],"boundary")?12003:12002,20003,12003);CHECK(p2);b->part_result(20003,12003,0);b->present_closed(3);b->counter(99,0,12003);}
 if(!strcmp(argv[1],"sync_failure"))fault=1;if(!strcmp(argv[1],"close_failure"))fault=2;if(!strcmp(argv[1],"publish_failure"))fault=3;
 int result=b->window(2,&w);
 if(!strcmp(argv[1],"existing")||!strcmp(argv[1],"symlink")||fault){CHECK(result==-4&&w.state==OO_WINDOW_ERROR);return 0;}
 CHECK(result==0&&w.state==OO_WINDOW_WRITTEN&&w.pending==0);CHECK(b->window(2,&w)==0);CHECK(b->ingress(OO_BENCH_PRESENT,77,99,0,NULL,0)==0);
 FILE* output=fopen(getenv("HOS_OO_BENCH_OUTPUT"),"rb");CHECK(output);fseek(output,0,SEEK_END);CHECK(ftell(output)>256);fclose(output);CHECK((w.flags&OO_BENCH_SCOPE_UNKNOWN)==OO_BENCH_SCOPE_UNKNOWN);
 /* Force process exit without atexit handlers: close already serialized. */
 _exit(0);
}
'''

def compile_fixture(directory: Path,compiler='cc'):
    directory.mkdir(parents=True,exist_ok=True)
    (directory/'oo_benchmark_window.h').write_text(HEADER);(directory/'driver.c').write_text(DRIVER)
    command=[compiler,'-O2','-DNDEBUG','-march=x86-64-v3','-Wall','-Wextra','-Werror','-Wno-misleading-indentation','-pthread',str(directory/'driver.c'),'-o',str(directory/'driver')]
    if compiler in ('gcc','clang','cc'):command[1:1]=['-fsanitize=undefined','-fsanitize-trap=undefined']
    subprocess.run(command,check=True,capture_output=True,text=True,timeout=30)
    return directory/'driver',command

def run_case(driver: Path,case: str,directory: Path):
    output=directory/(case+'.bin');env={**os.environ,'HOS_OO_BENCH_BRIDGE':'1','HOS_OO_BENCH_WINDOW':'1','HOS_OO_BENCH_OUTPUT':str(output)}
    if case=='disabled':env['HOS_OO_BENCH_BRIDGE']='0'
    if case=='bridge_only':env.pop('HOS_OO_BENCH_WINDOW')
    if case=='existing':output.write_bytes(b'original')
    if case=='symlink':
        target=directory/'symlink-target';target.write_bytes(b'original');output.symlink_to(target)
    subprocess.run([str(driver),case],env=env,check=True,capture_output=True,text=True,timeout=15)
    complete=case in ('startup','startup_failure','boundary','mixed_batch','mixed_batch_earlier','held','partial','slot_reuse','large_owned','burst_cover','resumed_cover','shared_cover','midrun-gap','midrun-no-links','suspended-run')
    if complete:
        report=summarize(read(output))
        if case=='resumed_cover':
            window=read(output)
            links=[e for e in window.capture.events if e.kind==15 and e.values[2]==4]
            if len(links)<2:raise ValueError('resume did not keep both recording links')
            from dataclasses import replace
            dropped=tuple(e for e in window.capture.events if e is not links[0])
            mutated=reader._summarize(replace(window.capture,events=dropped),window.baseline)
            if not any('omits an owned recording part' in item or 'not fully observed complete' in item for item in mutated['structural_errors']):
                raise ValueError('reader accepted a link set that dropped the first part')
            foreign=replace(links[-1],values=(links[-1].values[0],999999)+links[-1].values[2:])
            foreign_events=tuple(foreign if e is links[-1] else e for e in window.capture.events)
            foreign_report=reader._summarize(replace(window.capture,events=foreign_events),window.baseline)
            if not any('missing recording part' in item for item in foreign_report['structural_errors']):
                raise ValueError('reader accepted an untracked part')
            changed=replace(links[-1],values=(links[-1].values[0],links[-1].values[1],4,links[-1].values[3],links[-1].values[4],links[-1].values[5]+1)+links[-1].values[6:])
            changed_events=tuple(changed if e is links[-1] else e for e in window.capture.events)
            changed_report=reader._summarize(replace(window.capture,events=changed_events),window.baseline)
            if not any('generation does not match' in item for item in changed_report['structural_errors']):
                raise ValueError('reader accepted a mismatched generation')
            report['reader_negatives']=['dropped_first','foreign_part','mismatched_generation']
        if case in ('midrun-gap','midrun-no-links','suspended-run'):
            if not report['all_native_runs_settled']: raise ValueError('settled run was not reported settled')
            window=read(output)
            from dataclasses import replace
            def dropped(code):
                events=tuple(e for e in window.capture.events if not(e.kind==15 and e.values[2]==code))
                return reader._summarize(replace(window.capture,events=events),window.baseline)
            end=dropped(3); start=dropped(1)
            if end['all_native_runs_settled'] or 'native replay has not settled its final recording links' not in end['structural_errors']:
                raise ValueError('reader accepted a capture that dropped the run end')
            if start['all_native_runs_settled'] or not start['structural_errors']:
                raise ValueError('reader accepted a capture that dropped the run start')
            report['reader_negatives']=['dropped_run_end','dropped_run_start']
        if case=='startup' and (report['all_native_runs_settled'] or 'native replay lifecycle unobserved for legacy fixture' not in report['unresolved']):
            raise ValueError('legacy fixture without a native run was treated as settled')
        return report
    if case in ('existing','symlink'):
        if output.read_bytes()!=b'original':raise ValueError('pre-existing output overwritten')
    elif output.exists():raise ValueError('incomplete/failed window published')
    return None

PRE_R8_COUNTS='static void oo_window_counts(uint32_t* parts_left,uint32_t* streams_left,uint32_t* presents_left,int* permanent){OoBenchState* s=oo_bench_s;*parts_left=*streams_left=*presents_left=0;*permanent=0;\n for(uint32_t i=0;i<s->parts_n;++i)if(s->parts[i].state!=OO_BENCH_DONE)++*parts_left;\n for(uint32_t i=0;i<s->status.events;++i){const OoBenchEventV1* e=&s->events[i];if(e->kind==OO_BENCH_SUBMIT){uint32_t streams=0,parts=0,links=0;for(uint32_t j=0;j<s->status.events;++j)streams+=s->events[j].kind==OO_BENCH_STREAM&&s->events[j].v[0]==e->serial;for(uint32_t j=0;j<s->parts_n;++j)parts+=s->parts[j].origin==e->serial&&s->parts[j].in_window;for(uint32_t j=0;j<s->links_n;++j)links+=s->links[j].origin==e->serial;if(streams!=e->v[4]){++*streams_left;if(oo_bench_denied(e->serial))*permanent=1;}else if(links){int ready=1;for(uint32_t j=0;j<s->links_n;++j)if(s->links[j].origin==e->serial){OoBenchPart* p=oo_bench_find_part(s->links[j].part);int changed=p&&oo_bench_value_locked(p->value)!=p->origin;int dead=0;if(p&&p->timeline){dead=1;for(uint32_t k=0;k<s->objects_n;++k)if(s->objects[k].id==p->timeline)dead=!s->objects[k].active;}if(!p||!p->in_window||changed||dead||p->state==OO_BENCH_FAILED){*permanent=1;ready=0;}else if(p->state!=OO_BENCH_DONE)ready=0;}for(uint32_t j=0;j<s->parts_n;++j)if(s->parts[j].origin==e->serial&&s->parts[j].in_window&&!oo_bench_linked(e->serial,s->parts[j].part)){oo_bench_fault_ctx=(OoBenchFaultV1){OO_BENCH_BAD_PART,0,e->serial,s->parts[j].part,s->parts[j].slot,s->parts[j].generation,s->parts[j].seq};oo_bench_fault(OO_BENCH_BAD_PART);*permanent=1;ready=0;}if(!ready&&!*permanent)++*streams_left;}else if(!parts){++*streams_left;if(oo_bench_settled(e->serial))*permanent=1;}}\n  if(e->kind==OO_BENCH_PRESENT){uint32_t closed=0,parts=0;uint64_t last=0;for(uint32_t j=0;j<s->parts_n;++j)if(s->parts[j].origin==e->serial){++parts;if(s->parts[j].part>last)last=s->parts[j].part;}for(uint32_t j=0;j<s->status.events;++j){const OoBenchEventV1* c=&s->events[j];if(c->kind==OO_BENCH_PRESENT_CLOSED&&c->v[0]==e->serial&&c->v[2]==last&&c->v[3]==parts)++closed;}if(closed!=1||!parts){++*presents_left;if(closed&&!parts)*permanent=1;}}}\n if(!*presents_left){int any=0;for(uint32_t i=0;i<s->status.events;++i)any+=s->events[i].kind==OO_BENCH_PRESENT;if(!any)++*presents_left;}}'
PRE_R8_HEADER_SHA='cba83179398b0c20b2efc72aef00683cb5e78737be445c4e5dc0fea3e295dfe5'

def prior_header_reproduces_pending_run_bug(compiler="gcc"):
    """Restore the exact open r7 counts helper and reproduce both runtime regressions."""
    current=function(HEADER,"static void oo_window_counts(")
    header=HEADER.replace(current,PRE_R8_COUNTS)
    seen=function(header,"static int oo_bench_run_seen(")
    header=header.replace(seen+"\n", "")
    if hashlib.sha256(header.encode()).hexdigest()!=PRE_R8_HEADER_SHA:
        raise ValueError("reconstructed prior header differs")
    with tempfile.TemporaryDirectory(prefix="oo-r7-negative-") as temporary:
        directory=Path(temporary)
        (directory/"oo_benchmark_window.h").write_text(header)
        (directory/"driver.c").write_text(DRIVER)
        command=[compiler,"-fsanitize=undefined","-fsanitize-trap=undefined","-O2","-DNDEBUG","-march=x86-64-v3","-Wall","-Wextra","-Werror","-Wno-misleading-indentation","-pthread",str(directory/"driver.c"),"-o",str(directory/"driver")]
        subprocess.run(command,check=True,capture_output=True,text=True,timeout=30)
        failed=[]
        for case in ("midrun-gap","midrun-no-links"):
            env={**os.environ,"HOS_OO_BENCH_BRIDGE":"1","HOS_OO_BENCH_WINDOW":"1","HOS_OO_BENCH_OUTPUT":str(directory/(case+".bin"))}
            ran=subprocess.run([str(directory/"driver"),case],env=env,capture_output=True,text=True,timeout=15)
            if ran.returncode!=3 or "OO_WINDOW_DRAINING" not in ran.stderr:
                raise ValueError("prior header did not reproduce pending-run failure: "+case)
            failed.append(case)
        return {"compiled":True,"failed_regressions":failed,"header_sha256":PRE_R8_HEADER_SHA}

def prove_reapplication():
    """Disposable index only. Requires the pinned private object alternates; does not touch the shared checkout."""
    alternates=[ROOT/p for p in ('local/benchmark-native-buildcheck-baseline-header/git-objects','local/benchmark-native-producer/git-objects','local/hookshot-source/.git/objects')]
    git_dir=ROOT/'local/hookshot-source/.git'
    if not git_dir.is_dir() or not all(p.is_dir() for p in alternates):
        return {'available':False}
    with tempfile.TemporaryDirectory(prefix='oo-r8-apply-') as temporary:
        temporary=Path(temporary); objects=temporary/'objects'; objects.mkdir(); index=temporary/'index'
        env={**os.environ,'GIT_INDEX_FILE':str(index),'GIT_OBJECT_DIRECTORY':str(objects),'GIT_ALTERNATE_OBJECT_DIRECTORIES':':'.join(str(p.resolve()) for p in alternates),'GIT_CONFIG_COUNT':'0'}
        gd=['git','--git-dir='+str(git_dir.resolve())]
        def git(*args):
            proc=subprocess.run(gd+list(args),env=env,capture_output=True,timeout=60)
            if proc.returncode: raise ValueError(proc.stderr.decode()[-500:])
            return proc.stdout
        git('read-tree',BASE_TREE)
        strict=subprocess.run(gd+['apply','--cached','--check','--whitespace=error',str(PATCH.resolve())],env=env,capture_output=True,text=True,timeout=60)
        git('apply','--cached','--check','--whitespace=nowarn',str(PATCH.resolve()))
        git('apply','--cached','--whitespace=nowarn',str(PATCH.resolve()))
        got={}
        for name,digest in EXECUTED.items():
            blob=git('show',':'+name)
            got[name]=hashlib.sha256(blob).hexdigest()
            if got[name]!=digest: raise ValueError('applied '+name+' is not the executed source')
        tree=git('write-tree').decode().strip()
        replay=git('show',':gpu/vk/vk_replay.cpp').decode()
    return {'available':True,'whitespace_error_check':strict.returncode!=0,'whitespace_nowarn_byte_identical':True,'applied_tree':tree,'files':got,'replay':replay}

def compile_watch(replay_text: str, compiler='g++'):
    start=replay_text.index('struct OoCoverWatch {'); end=replay_text.index('/// The next recording slot, begun,')
    chunk=replay_text[start:end]
    source=r'''
#include <cstdint>
#include <vector>
#include <array>
#include <cstdlib>
constexpr uint32_t OO_BENCH_CAPACITY=1;
struct Bridge {mutable std::vector<std::array<uint64_t,4>> calls;void note(uint32_t reason,uint64_t origin,uint64_t a,uint64_t b) const {calls.push_back({reason,origin,a,b});}} bridge;
bool enabled=true;
const Bridge* oo_bench_bridge(){return enabled?&bridge:nullptr;}
'''+chunk+r'''
int main(){
 if(oo_cover_observed_value(91,UINT64_MAX)!=91)return 1;
 oo_cover_watch.active=1;oo_cover_watch.origin=101;oo_cover_watch.observed_value=11;
 if(oo_cover_observed_value(91,UINT64_MAX)!=11)return 2;
 if(oo_cover_observed_value(91,12)!=12)return 3;
 uint64_t actual_scheduling_tag=91;
 if(oo_cover_observed_value(actual_scheduling_tag,UINT64_MAX)!=11||actual_scheduling_tag!=91)return 4;
 oo_cover_add(1001,1);oo_cover_add(1002,2);oo_cover_add(1001,5);oo_cover_flush();
 if(bridge.calls.size()!=2||bridge.calls[0][1]!=101||bridge.calls[0][2]!=1001||bridge.calls[1][2]!=1002)return 5;
 if(oo_cover_watch.active||oo_cover_observed_value(91,UINT64_MAX)!=91)return 6;
 oo_cover_watch.active=1;oo_cover_watch.origin=102;oo_cover_watch.observed_value=12;
 for(int i=0;i<33;++i)oo_cover_add(2000+i,2);
 if(bridge.calls.size()!=3||bridge.calls.back()[0]!=OO_BENCH_CAPACITY||oo_cover_watch.n!=32)return 7;
 oo_cover_flush();if(bridge.calls.size()!=35)return 8;
 return 0;}
'''
    with tempfile.TemporaryDirectory(prefix='oo-r8-watch-') as temporary:
        directory=Path(temporary); cpp=directory/'watch.cpp'; cpp.write_text(source)
        binary=directory/'watch'
        command=[compiler,'-std=c++20','-O2','-DNDEBUG','-Wall','-Wextra','-Werror','-fsanitize=undefined','-fsanitize-trap=undefined',str(cpp),'-o',str(binary)]
        subprocess.run(command,check=True,capture_output=True,text=True,timeout=30)
        subprocess.run([str(binary)],check=True,capture_output=True,text=True,timeout=10)
    return {'cases':8,'compiler':compiler}

def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--compiler',default='cc');parser.add_argument('--source',type=Path);parser.add_argument('--prove',action='store_true');args=parser.parse_args()
    if hashlib.sha256(HEADER.encode()).hexdigest()!=HEADER_SHA: raise SystemExit('r8 header in the patch is not the executed header')
    proof=prove_reapplication() if args.prove else None
    replay=proof.pop('replay',None) if proof else None
    watch=compile_watch(replay) if replay else None
    old=prior_header_reproduces_pending_run_bug('gcc' if args.compiler=='cc' else args.compiler)
    with tempfile.TemporaryDirectory(prefix='oo-native-window-') as temporary:
        directory=Path(temporary);driver,command=compile_fixture(directory,args.compiler)
        reports={case:run_case(driver,case,directory) for case in CASES}
        forwarding=compile_forwarding(directory,args.source,args.compiler) if args.source else None
    print(json.dumps({'r8_patch_sha256':hashlib.sha256(PATCH.read_bytes()).hexdigest(),'header_sha256':HEADER_SHA,'cases':list(reports),'prior_header_regressions':old,'watch':watch,'reapplication':proof,'actual_forwarding':bool(forwarding),'compile_command':command,'scope':'CPU-only r8 window; no game/GPU/FPS'},indent=2))

FORWARDING=r'''
#define _GNU_SOURCE
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <stdarg.h>
static uint64_t hos_now_ns(void){static uint64_t tick;return ++tick;}
#define OO_BENCH_BRIDGE_IMPLEMENTATION
#include "oo_benchmark_window.h"
#define ASSERT(x) do{if(!(x)){fprintf(stderr,"forward line %d: %s\n",__LINE__,#x);abort();}}while(0)
typedef struct {uint64_t x[31];} Ctx;
typedef struct {uint64_t submits;} nvn_queue;
typedef int32_t LONG;
static LONG InterlockedIncrement(volatile LONG* n){return ++*n;}
static void hos_log(const char* fmt,...){(void)fmt;}
static void die(const char* fmt,...){(void)fmt;abort();}
void hos_renderer_wait(void){}
static int hold_on,nheld,held_cs,copy_lock,forwarded,resolved,checked,away,back;
static struct {int n;uint64_t h[64],oo_origin;} held[2];
static void held_init(void){}
static void EnterCriticalSection(int* c){(void)c;}
static void LeaveCriticalSection(int* c){(void)c;}
static void AcquireSRWLockShared(int* c){(void)c;}
static void ReleaseSRWLockShared(int* c){(void)c;}
static int hos_vcore_away(void){++away;return 1;}
static void hos_vcore_back(void){++back;}
typedef struct {uint64_t magic,bytes,cls;} copy_prefix;
#define COPY_MAGIC UINT64_C(0x59504f43444d43)
static struct {copy_prefix prefix;unsigned char bytes[16];} recording;
static uint64_t handle,large_handle;
static int live_handle_has(uint64_t h){return h==handle||(large_handle&&h==large_handle);}
static void check_handle(uint64_t h){ASSERT(h==handle);++checked;}
static void resolve_deferred(uint8_t* p){ASSERT(p==recording.bytes);p[0]=2;++resolved;}
static uint64_t expected_queue,observed_origin;
static void rx_nvn_submit_q(const uint64_t* handles,int n,uint64_t queue){ASSERT(n==1&&handles[0]==handle&&queue==expected_queue);ASSERT(checked>0&&resolved>0);++forwarded;const OoBenchBridgeV1* b=hos_posix_oo_benchmark_window_v2();observed_origin=b?b->current_origin():0;recording.bytes[0]=3;}
static void rx_nvn_submit(const uint64_t* handles,int n){ASSERT(n==1&&handles[0]==handle);++forwarded;}
#define NV(name) static void n_##name(Ctx* c,void* unused)
#define P(i) ((void*)(uintptr_t)c->x[i])
#define I(i) ((int)c->x[i])
#define PN(i,n) ((void)(n),(void*)(uintptr_t)c->x[i])
/* COPIER */
/* SUBMIT */
int main(void){
 const OoBenchBridgeV1* b=hos_posix_oo_benchmark_window_v2();ASSERT(b);nvn_queue q={0};expected_queue=(uint64_t)(uintptr_t)&q;handle=(uint64_t)(uintptr_t)recording.bytes;recording.prefix=(copy_prefix){COPY_MAGIC,sizeof recording,0};Ctx c={0};c.x[0]=expected_queue;c.x[1]=1;c.x[2]=(uint64_t)(uintptr_t)&handle;
 ASSERT(b->lifetime(OO_BENCH_QUEUE,expected_queue,OO_BENCH_CREATE));
 n_nvnQueueSubmitCommands(&c,NULL);ASSERT(forwarded==1&&q.submits==1&&observed_origin==0&&away==back);OoBenchStatusV1 s;ASSERT(b->status(&s)&&s.events==0);
 OoWindowStatus window;ASSERT(b->window(1,&window)==0);n_nvnQueueSubmitCommands(&c,NULL);ASSERT(forwarded==2&&q.submits==2&&observed_origin&&recording.bytes[0]==3&&away==back);
 unsigned char copied[16]={0};ASSERT(b->read_bytes(0,copied,sizeof copied)&&copied[0]==2);ASSERT(b->current_origin()==0);
 const uint32_t big_n=1024u*1024u+64u;unsigned char* block=calloc(1,sizeof(copy_prefix)+big_n);ASSERT(block);copy_prefix* pre=(copy_prefix*)block;pre->magic=COPY_MAGIC;pre->bytes=sizeof(copy_prefix)+big_n;memset(block+sizeof(copy_prefix),0x11,big_n);large_handle=(uint64_t)(uintptr_t)(block+sizeof *pre);uint64_t one=large_handle;uint64_t copied_origin=oo_bench_copy_ingress(expected_queue,9,&one,1);ASSERT(copied_origin);block[sizeof(copy_prefix)]=0;unsigned char edge[2]={0,0};OoBenchStatusV1 sized;ASSERT(b->status(&sized)&&sized.bytes>=big_n);ASSERT(b->read_bytes(sized.bytes-big_n,edge,2)&&edge[0]==0x11&&edge[1]==0x11);
 copy_prefix bad={0,99,0};large_handle=(uint64_t)(uintptr_t)((unsigned char*)&bad+sizeof bad);uint64_t dead=large_handle;ASSERT(oo_bench_copy_ingress(expected_queue,10,&dead,1));OoWindowStatus denied;ASSERT(b->window(2,&denied)==-3&&denied.state==OO_WINDOW_ERROR&&denied.denial_reason==OO_BENCH_BAD_ORIGIN&&denied.denial_prefix==0&&denied.events<300&&denied.lost<8);
 hold_on=1;n_nvnQueueSubmitCommands(&c,NULL);n_nvnQueueSubmitCommands(&c,NULL);ASSERT(nheld==2&&forwarded==2);n_nvnQueueSubmitCommands(&c,NULL);ASSERT(nheld==1&&forwarded==4&&held[0].h[0]==handle&&q.submits==5);
 puts("actual native submit wrapper forwarded original handles/queue exactly; post-resolve owned copy survives source mutation; owned streams larger than 1 MiB stay exact; a bad prefix fails closed; original held queue-loss path unchanged");return 0;
}
'''

def compile_forwarding(directory: Path,source: Path,compiler='cc'):
    text=(source/'host/nvn_native.c').read_text()
    source_header=(source/'host/oo_benchmark_window.h').read_text()
    if source_header!=HEADER:raise ValueError('forwarding source header is not the r8 patch header')
    driver=FORWARDING.replace('/* COPIER */',function(text,'static uint64_t oo_bench_copy_ingress(')).replace('/* SUBMIT */',function(text,'NV(nvnQueueSubmitCommands)'))
    (directory/'oo_benchmark_window.h').write_text(source_header)
    (directory/'forwarding.c').write_text(driver)
    command=[compiler,'-O2','-DNDEBUG','-march=x86-64-v3','-Wall','-Wextra','-Werror','-Wno-misleading-indentation','-Wno-unused-parameter','-fsanitize=undefined','-fsanitize-trap=undefined','-pthread',str(directory/'forwarding.c'),'-o',str(directory/'forwarding')]
    subprocess.run(command,check=True,capture_output=True,text=True,timeout=30)
    env={**os.environ,'HOS_OO_BENCH_BRIDGE':'1','HOS_OO_BENCH_WINDOW':'1'};env.pop('HOS_OO_BENCH_OUTPUT',None)
    p=subprocess.run([str(directory/'forwarding')],env=env,check=True,capture_output=True,text=True,timeout=10)
    return {'command':command,'stdout':p.stdout,'source_sha256':hashlib.sha256(text.encode()).hexdigest()}

if __name__=='__main__':main()
