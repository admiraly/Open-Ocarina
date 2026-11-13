# SPDX-License-Identifier: GPL-3.0-or-later
"""Authored event-registry and pending-DW adapter cases; never a live producer."""
from pathlib import Path
import os
import shutil
import subprocess
import tempfile
import unittest

ROOT=Path(__file__).resolve().parents[1]
COMPILER=os.environ.get('SELECTOR_EXTRACT_CXX') or shutil.which('g++') or shutil.which('clang++')
TYPES=r'''
struct DescWriter {
 std::vector<VkWriteDescriptorSet> writes;std::vector<VkDescriptorBufferInfo> bufs;
 std::vector<VkDescriptorImageInfo> imgs;std::vector<VkBufferView> views;const Program* prog=nullptr;
 void reset(){writes.clear();bufs.clear();imgs.clear();views.clear();}
};
struct RecordingRes {
 struct Stream{VkBuffer buffer;VmaAllocation alloc;uint8_t* mapped;uint64_t size;};
 std::vector<Stream> streams;VkBuffer stream=nullptr;VmaAllocation stream_alloc=nullptr;
 uint8_t* mapped=nullptr;uint64_t size=0,used=0;
};
'''
HARNESS=r'''
#include "compute_selector_extract.h"
#include <iostream>
#include <functional>
#include <unordered_map>
using namespace open_ocarina::selector_extract;
using namespace open_ocarina;
using VkBuffer=void*;using VmaAllocation=void*;using VkDescriptorPool=void*;using VkBufferView=void*;
inline constexpr std::nullptr_t VK_NULL_HANDLE=nullptr;
struct VkDescriptorImageInfo{};struct Program{};
struct VkDescriptorBufferInfo{VkBuffer buffer=nullptr;uint64_t offset=0,range=0;};
struct VkWriteDescriptorSet{uint32_t descriptorType=0,descriptorCount=0,dstBinding=0,dstArrayElement=0;const VkDescriptorBufferInfo* pBufferInfo=nullptr;};
@TYPES@
void check(bool x){if(!x)throw std::runtime_error("authored assertion");}
void blocked(std::function<void()> operation){try{operation();throw std::runtime_error("lease conflict accepted");}catch(const std::invalid_argument&){}}
void* h(U n){return reinterpret_cast<void*>(uintptr_t(n));}
struct FakeVmaInfo{void* deviceMemory;uint64_t offset,size;void* pMappedData;};
struct FakePin:Retention{PinInfo value;PinInfo current()const override{return value;}};
struct Pins:RetainSource{
 std::map<U,std::shared_ptr<FakePin>> pins;
 std::shared_ptr<Retention> acquire(U b)override{auto p=pins.find(b);return p==pins.end()?nullptr:p->second;}
};
struct FakeImport:ImportLease{ImportInfo info;ImportInfo acquired()const override{return info;}ImportInfo current()const override{auto x=info;if(invalidated)x.invalid_reason=1;return x;}bool invalidated=false;};
struct Fixture {
 Registry registry;Record recording;DescWriter dw;RecordingRes actual;Program program;
 Pins retain;std::vector<uint8_t> old_stream,new_stream;Writer writer;
 Identity identity{0xcc62764061c44f6aULL,0x095931037888515aULL,5,32,1,compute_intervals::kModuleSha256};
 Fixture():old_stream(4096),new_stream(4096,0xaa){
  recording=registry.begin(1,41);registry.memory_vma(1,32768);
  owner(101,201,1,512,4096,old_stream.data());owner(102,202,1,8192,4096,new_stream.data());
  registry.retained_stream(recording,101,201,old_stream.data(),old_stream.size());
  registry.retained_stream(recording,102,202,new_stream.data(),new_stream.size());
  actual.streams.push_back({h(101),h(201),old_stream.data(),old_stream.size()});
  actual.streams.push_back({h(102),h(202),new_stream.data(),new_stream.size()});
  actual.stream=h(102);actual.stream_alloc=h(202);actual.mapped=new_stream.data();actual.size=new_stream.size();
  dw.prog=&program;dw.bufs.reserve(10);dw.writes.reserve(8);
  dw.bufs.push_back({h(101),64,2224});dw.bufs.push_back({h(101),0,16});
  for(unsigned n=0;n<8;++n){registry.memory_vma(20+n,4096);owner(301+n,401+n,20+n,0,4096);dw.bufs.push_back({h(301+n),16,64});}
  for(unsigned binding=0,at=0;binding<8;++binding){auto count=binding==7?3u:1u;dw.writes.push_back({binding<2?6u:7u,count,binding,0,&dw.bufs[at]});at+=count;}
  for(unsigned n=0;n<8;++n){auto at=64+compute_intervals::detail::kSelectors[n];auto address=U(0x1000+n*0x1000);
   for(unsigned b=0;b<8;++b)old_stream[at+b]=uint8_t(address>>(b*8));
   old_stream[at+8]=64;old_stream[at+15]=uint8_t(n+1);}
  registry.mapped_upload_completed(recording,101,64,2224);
 }
 void owner(U b,U a,U m,U offset,U size,uint8_t* mapped=nullptr){
  if(a)registry.vma_buffer_created(b,a,size,FakeVmaInfo{h(m),offset,size,mapped});else registry.imported_buffer_created(b,m,offset,size);auto p=std::make_shared<FakePin>();p->value={b,a,m,offset,size};retain.pins[b]=p;}
 Writer seal(){return writer=registry.after_build(7,recording,dw,identity);}
 Bundle take(Dispatch dispatch={}){return registry.extract(writer,dw,actual,dispatch,retain);}
};
int main(int argc,char**argv){
 if(argc!=2)return 2;
 std::string mode=argv[1];bool expected_failure=mode.rfind("reject_",0)==0;
 try {
  Fixture f;
  if(mode=="reject_vma_unknown_block")f.registry.vma_buffer_created(991,992,16,FakeVmaInfo{h(99999),0,16,nullptr});
  if(mode=="reject_vma_short_allocation")f.registry.vma_buffer_created(991,992,16,FakeVmaInfo{h(1),0,8,nullptr});
  if(mode=="reject_vma_backing_extent")f.registry.vma_buffer_created(991,992,16,FakeVmaInfo{h(1),32767,16,nullptr});
  if(mode=="reject_import_without_lease")f.registry.imported_buffer_created(991,1,0,16);
  if(mode=="reject_unuploaded_capacity")f.dw.bufs[0].offset=128;
  if(mode=="reject_layout")f.dw.writes.back().dstBinding=8;
  if(mode=="reject_duplicate_slot")f.dw.writes.back().dstBinding=6;
  VkDescriptorBufferInfo unrelated{h(101),64,2224};
  if(mode=="reject_foreign_info_pointer")f.dw.writes[0].pBufferInfo=&unrelated;
  if(mode=="reject_stage")f.identity.vk_stage=128;
  if(mode=="reject_module")f.identity.module="unreviewed";
  if(mode=="reject_unknown_owner")f.dw.bufs[2].buffer=h(9999);
  if(mode=="reject_view_overflow")f.dw.bufs[2].offset=UINT64_MAX;
  if(mode=="reject_null_framing")f.dw.bufs[9]={nullptr,1,UINT64_MAX};
  if(mode=="nulls")for(unsigned n=8;n<10;++n)f.dw.bufs[n]={nullptr,0,UINT64_MAX};
  if(mode=="zero_fallback")f.dw.bufs[2].range=16;
  if(mode=="reject_binding6_alias"){f.owner(901,902,1,512+64+192,64);f.dw.bufs[6]={h(901),0,64};}
  std::shared_ptr<FakeImport> imported;
  if(mode=="imports"||mode=="reject_import_invalid"||mode=="lease_import_changed"||mode=="lease_import_disjoint"||mode=="reject_import_status"||mode=="reject_import_abi"){
   imported=std::make_shared<FakeImport>();imported->info={11,777,4,1024,8192,55,1,0,1,88,1,12,4,0};
   if(mode=="reject_import_status")imported->info.result=0;
   if(mode=="reject_import_abi")imported->info.abi=0;
   for(U n=0;n<2;++n){f.registry.memory_import(80+n,4096,imported);f.owner(801+n,0,80+n,0,4096);f.dw.bufs[2+n]={h(801+n),16,64};}
  }
  if(mode=="host_preseal")f.registry.host_write(f.recording,101,64+192,16);
  if(mode=="reject_gpu_preseal")f.registry.transfer_write(f.recording,101,64+192,16);
  if(mode=="reject_cross_record_writer"){auto other=f.registry.begin(2,52);f.registry.host_write(other,101,64+192,16);}
  f.seal();
  if(mode=="reject_upload_superseded")f.registry.mapped_upload_completed(f.recording,101,64,2224);
  if(mode=="reject_writer_reset")f.registry.writer_reset(7);
  if(mode=="reject_writer_generation")++f.writer.generation;
  if(mode=="reject_recording_part")++f.writer.part;
  if(mode=="reject_descriptor_mutation")++f.dw.bufs[2].range;
  if(mode=="reject_actual_reset")f.dw.reset();
  if(mode=="reject_stream_mapping")f.actual.streams[0].mapped=f.new_stream.data();
  if(mode=="reject_stream_missing")f.actual.streams.erase(f.actual.streams.begin());
  if(mode=="reject_pin_missing")f.retain.pins.erase(101);
  if(mode=="reject_pin_offset")++f.retain.pins[101]->value.memory_offset;
  if(mode=="reject_recycled_handle"){f.registry.buffer_destroy(301);f.owner(301,401,20,0,4096);}
  if(mode=="reject_unknown_writer")f.registry.unknown_writer();
  if(mode=="reject_selector_host_write")f.registry.host_write(f.recording,101,64+192+15,1);
  if(mode=="reject_alias_transfer") {f.owner(951,952,1,512+64+208,16);f.registry.transfer_write(f.recording,951,0,16);}
  if(mode=="reject_import_invalid")imported->invalidated=true;
  Dispatch d;
  if(mode=="indirect"||mode=="reject_indirect_missing"||mode=="reject_indirect_extent"||mode=="reject_direct_copy"||mode=="reject_argument_recycled") {
   f.registry.memory_vma(99,4096);f.owner(991,992,99,0,4096);d.kind=Dispatch::Kind::Indirect;d.arguments=991;d.arguments_offset=48;
   if(mode!="reject_indirect_missing")d.grid_copy_event=f.registry.indirect_grid_copy(f.recording,f.writer,991,48,101,64,mode=="reject_indirect_extent"?16:12);
   if(mode=="reject_direct_copy")d.kind=Dispatch::Kind::Direct;
   if(mode=="reject_argument_recycled"){f.registry.buffer_destroy(991);f.owner(991,992,99,0,4096);}
  }
  if(mode=="reject_zero_grid")d.direct_grid[0]=0;
  auto b=f.take(d);check(f.registry.valid(b)&&f.registry.matches_pending(b,f.dw));check(b.input().c0_bytes[192+15]==1);check(b.input().c0_bytes[208+15]==2);
  check(b.input().c0_bytes[192]!=0xaa&&b.input().mode==compute_intervals::SourceMode::LiveAsSubmitted);
  try{(void)compute_intervals::build(b.input());throw std::runtime_error("live build unexpectedly accepted");}catch(const std::invalid_argument&){}
  if(mode=="nulls")check(!b.input().roots[6].view.allocation.id&&!b.input().roots[7].view.allocation.id);
  if(mode=="zero_fallback")check(b.input().roots[0].view.allocation.id&&b.input().roots[0].view.range==16);
  if(mode=="imports"){auto a=compute_intervals::detail::owner(b.input(),b.input().roots[0].view.allocation);auto c=compute_intervals::detail::owner(b.input(),b.input().roots[1].view.allocation);check(a.backing==c.backing&&a.backing_offset==1024);}
  if(mode=="indirect")check(b.input().additional_writes.size()==1&&b.input().additional_writes[0].size==12);
  if(mode=="lease_late_descriptor_mutation"){++f.dw.bufs[2].range;check(!f.registry.matches_pending(b,f.dw));}
  if(mode=="lease_late_host"){blocked([&]{f.registry.host_write(f.recording,101,64+2176+15,1);});check(!f.registry.valid(b));}
  if(mode=="lease_late_alias"){f.owner(971,972,1,512+64+272,16);blocked([&]{f.registry.physical_write(f.recording,971,0,16);});check(!f.registry.valid(b));}
  if(mode=="lease_memory_recycled"){f.registry.memory_free(1);f.registry.memory_vma(1,32768);check(!f.registry.valid(b));}
  if(mode=="lease_import_disjoint"){++imported->info.current_generation;check(f.registry.valid(b));}
  if(mode=="lease_import_changed"){++imported->info.generation;check(!f.registry.valid(b));}
  if(mode=="lease_pin_changed"){++f.retain.pins[101]->value.memory_offset;check(!f.registry.valid(b));}
  if(mode=="lease_new_writer"){auto newer=f.seal();check(newer.generation!=b.writer().generation&&!f.registry.valid(b));}
  if(mode=="lease_reuse"){f.registry.submit(f.recording,100);try{f.registry.begin(1,42);throw std::runtime_error("reuse accepted early");}catch(const std::invalid_argument&){}f.registry.completed(100);auto next=f.registry.begin(1,42);check(next.id!=f.recording.id&&next.reuse_after==100&&!f.registry.valid(b));}
  if(mode=="lease_late_cross_record"){auto other=f.registry.begin(2,52);blocked([&]{f.registry.host_write(other,101,64+192,16);});check(!f.registry.valid(b));}
  if(mode=="lease_unknown_owner_write"){blocked([&]{f.registry.storage_write(f.recording,99999,0,4);});check(!f.registry.valid(b));}
  if(mode=="lease_unknown_writer"){f.registry.unknown_writer();check(!f.registry.valid(b));}
  if(expected_failure){std::cerr<<"rejection missing";return 1;}std::cout<<"PASS\n";return 0;
 }catch(const std::invalid_argument& e){if(expected_failure){std::cout<<e.what()<<"\n";return 0;}std::cerr<<e.what();return 1;}
 catch(const std::exception&e){std::cerr<<e.what();return 1;}
}
'''
CASES=('host_preseal','reject_gpu_preseal','reject_cross_record_writer','lease_late_cross_record','old_retained_stream','nulls','zero_fallback','imports','indirect','lease_late_descriptor_mutation','lease_late_host','lease_late_alias',
       'lease_pin_changed','lease_import_disjoint','reject_import_status','reject_import_abi','lease_import_changed','lease_memory_recycled','lease_new_writer','lease_reuse','lease_unknown_owner_write','lease_unknown_writer',
       'reject_vma_unknown_block','reject_vma_short_allocation','reject_vma_backing_extent','reject_import_without_lease','reject_unuploaded_capacity','reject_upload_superseded','reject_layout','reject_duplicate_slot','reject_foreign_info_pointer','reject_stage','reject_module',
       'reject_unknown_owner','reject_view_overflow','reject_null_framing','reject_binding6_alias',
       'reject_writer_reset','reject_writer_generation','reject_recording_part','reject_descriptor_mutation',
       'reject_actual_reset','reject_stream_mapping','reject_stream_missing','reject_pin_missing','reject_pin_offset',
       'reject_recycled_handle','reject_unknown_writer','reject_selector_host_write','reject_alias_transfer',
       'reject_import_invalid','reject_indirect_missing','reject_indirect_extent','reject_direct_copy',
       'reject_argument_recycled','reject_zero_grid')


def compile_harness(directory,types=TYPES,compiler=COMPILER):
    directory=Path(directory);source=directory/'test.cpp';source.write_text(HARNESS.replace('@TYPES@',types))
    executable=directory/'test'
    flags=['-std=c++17','-O2','-Wall','-Wextra','-Werror']
    if os.environ.get('SELECTOR_EXTRACT_UBSAN')=='1':flags+=['-fsanitize=undefined','-fno-sanitize-recover=all']
    subprocess.run([compiler,*flags,'-I'+str(ROOT/'src/probes'),str(source),'-o',str(executable)],check=True,capture_output=True,text=True,timeout=60)
    return executable


@unittest.skipUnless(COMPILER,'C++17 compiler required')
class ComputeSelectorExtractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp=tempfile.TemporaryDirectory(prefix='oo-selector-extract-')
        try:cls.executable=compile_harness(cls.tmp.name)
        except subprocess.CalledProcessError as exc:
            raise RuntimeError(exc.stderr)from exc
    @classmethod
    def tearDownClass(cls):cls.tmp.cleanup()


def test_case(mode):
    def run(self):
        result=subprocess.run([str(self.executable),mode],capture_output=True,text=True,timeout=10)
        self.assertEqual(result.returncode,0,result.stdout+result.stderr)
    return run

for case in CASES:setattr(ComputeSelectorExtractTests,'test_'+case,test_case(case))

if __name__=='__main__':unittest.main()
