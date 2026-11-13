# SPDX-License-Identifier: GPL-3.0-or-later
import importlib.util
import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from unittest.mock import Mock,patch

ROOT=Path(__file__).resolve().parents[1]


def load(name):
    spec=importlib.util.spec_from_file_location(name,ROOT/'tools'/f'{name}.py')
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module);return module


prepare=load('prepare_pass_history');capture=load('pass_history_capture')


def journal(**changes):
    counters=dict(epoch=7,normal=0,serial=0,active=0,translated=12,failed=10,pipelines=44,skips=5,draw_skips=6,translation_ms=1.5,left_workers=False)
    rows=[dict(event='begin',pixels=False,**counters),dict(event='pass',order=1,width=1536,height=864),
          dict(event='readiness_end',unchanged_work=True,**counters),dict(event='end',incomplete=False,reason=0,rows=3,attempts=0,admitted=0,
          buffer_reserved=0,image_reserved=0,copy_reserved=0,buffer_actual=0,image_actual=0,copy_actual=0)]
    for (row,key),value in changes.items():rows[row][key]=value
    return ('\n'.join(json.dumps(row) for row in rows)+'\n').encode()


class JournalTests(unittest.TestCase):
    def test_complete_metadata_only(self):
        result=capture.validate_journal(journal());self.assertTrue(result['complete_for_selected_width_metadata']);self.assertFalse(result['card_producer_identified'])

    def test_missing_end_rejected(self):
        with self.assertRaises(ValueError):capture.validate_journal(journal().rsplit(b'\n',2)[0])

    def test_oversized_file_rejected(self):
        with self.assertRaisesRegex(ValueError,'byte cap'):capture.validate_journal(b'x'*(capture.JOURNAL_CAP+1))

    def test_pixel_work_rejected(self):
        for key in ('buffer_actual','copy_actual','image_actual','admitted'):
            with self.subTest(key=key),self.assertRaises(ValueError):capture.validate_journal(journal(**{}).replace(f'"{key}": 0'.encode(),f'"{key}": 1'.encode()))

    def test_boolean_cannot_forge_numeric_zero(self):
        with self.assertRaises(ValueError):capture.validate_journal(journal().replace(b'"copy_actual": 0',b'"copy_actual": false'))

    def test_changed_epoch_rejects_false_native_readiness(self):
        with self.assertRaisesRegex(ValueError,'classification differs'):capture.validate_journal(journal(**{}).replace(b'"event": "readiness_end", "unchanged_work": true, "epoch": 7',b'"event": "readiness_end", "unchanged_work": true, "epoch": 8'))

    def test_incomplete_cap_retained_without_acceptance(self):
        data=journal().replace(b'"incomplete": false',b'"incomplete": true').replace(b'"reason": 0',b'"reason": 1')
        self.assertFalse(capture.validate_journal(data)['complete_for_selected_width_metadata'])

    def test_missing_readiness_fields_rejected(self):
        with self.assertRaisesRegex(ValueError,'fields'):capture.validate_journal(journal().replace(b'"serial": 0, ',b''))

    def test_inconsistent_complete_summary_rejected(self):
        with self.assertRaisesRegex(ValueError,'summary|counters'):capture.validate_journal(journal().replace(b'"rows": 3',b'"rows": 2'))

    def test_unordered_history_rejected(self):
        rows=journal().splitlines();rows.insert(2,b'{"event":"attachment","order":1,"width":1536}')
        with self.assertRaisesRegex(ValueError,'unordered'):capture.validate_journal(b'\n'.join(rows))


class ReadinessAndOwnershipTests(unittest.TestCase):
    def test_relative_plan_resolves_before_child_cwd_changes(self):
        with tempfile.TemporaryDirectory(dir=ROOT) as directory:
            plan=Path(directory)/'plan.json';plan.write_text('{}')
            args=argparse.Namespace(lease='root-test',plan_sha256='expected',plan=plan.relative_to(ROOT))
            with patch.object(capture,'sha',side_effect=ValueError('stop after path resolution')) as sha,patch.object(capture.subprocess,'Popen') as popen:
                with self.assertRaisesRegex(ValueError,'stop after path resolution'):capture.run(args)
            sha.assert_called_once_with(plan.resolve());popen.assert_not_called()

    def test_silence_is_not_ready(self):self.assertFalse(capture.queues_ready('state play, 0 jobs queued'))

    def test_enumerated_gpu_is_not_selected_device_proof(self):
        self.assertFalse(capture.selected_arc('vk: device 0: Intel(R) Arc(tm) A770 Graphics (DG2), Vulkan 1.4, driver 0x6802004, type 2'))
        self.assertTrue(capture.selected_arc('vk: Intel(R) Arc(tm) A770 Graphics (DG2), Vulkan 1.4, driver 0x6802004; mesh shaders yes'))

    def test_observed_file_read_is_bounded(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'large';path.write_bytes(b'x'*100)
            with self.assertRaisesRegex(ValueError,'exceeds cap'):capture.bounded_read(path,10)

    def test_three_explicit_stable_zero_queue_samples(self):
        line='vk: pass-history CPU queues: epoch 4 normal 0 serial-total 0 serial-active 0 translated 3 failed 10 pipelines 9 pending-skips 5 left-workers 0\n'
        self.assertTrue(capture.queues_ready(line*3));self.assertFalse(capture.queues_ready(line*2))
        self.assertFalse(capture.queues_ready((line*2)+line.replace('serial-total 0','serial-total 1')))
        self.assertFalse(capture.queues_ready((line*2)+line.replace('epoch 4','epoch 5')))
        self.assertFalse(capture.queues_ready((line*3)+line.replace('normal 0','normal -1')))

    def test_card_approval_binds_exact_new_screenshot(self):
        capture.validate_approval({'screenshot_sha256':'a'*64,'visible_card_reviewed':True},'a'*64)
        with self.assertRaises(ValueError):capture.validate_approval({'screenshot_sha256':'b'*64,'visible_card_reviewed':True},'a'*64)
        with self.assertRaises(ValueError):capture.validate_approval({'screenshot_sha256':'a'*64,'visible_card_reviewed':False},'a'*64)

    def test_unrelated_pid_is_not_signalled(self):
        proc=Mock();proc.poll.return_value=None;proc.pid=123
        with patch.object(capture,'proc_start',return_value=999),self.assertRaisesRegex(ValueError,'refusing signal'):capture.stop_owned(proc,111)
        proc.terminate.assert_not_called();proc.kill.assert_not_called()

    def test_terminal_owned_process_is_not_signalled(self):
        proc=Mock();proc.poll.return_value=0
        self.assertFalse(capture.stop_owned(proc,111));proc.terminate.assert_not_called();proc.kill.assert_not_called()

    def test_pid_identity_is_rechecked_before_escalation(self):
        proc=Mock();proc.pid=123;proc.poll.return_value=None;proc.wait.side_effect=subprocess.TimeoutExpired('owned',8)
        with patch.object(capture,'proc_start',side_effect=[111,999]),self.assertRaisesRegex(ValueError,'before kill'):capture.stop_owned(proc,111)
        proc.terminate.assert_called_once();proc.kill.assert_not_called()

    def test_runtime_requires_separate_lease_before_plan_or_process(self):
        args=Mock(lease='',plan_sha256='')
        with patch.object(capture.subprocess,'Popen') as popen,self.assertRaises(ValueError):capture.run(args)
        popen.assert_not_called()

    def test_exact_base_source_required(self):
        with tempfile.TemporaryDirectory() as directory:
            source=Path(directory)
            for name in prepare.FILES:
                path=source/name;path.parent.mkdir(parents=True,exist_ok=True);path.write_text('different source')
            with self.assertRaisesRegex(ValueError,'source hash differs'):
                prepare.prepare(source,ROOT/'local'/'never-created-pass-history-negative')


@unittest.skipUnless(shutil.which('c++'),'optional authored native CPU policy compiler')
class NativeBudgetTests(unittest.TestCase):
    def test_exact_caps_overflows_commits_and_frame_readiness(self):
        source=prepare.POLICY.replace('#pragma once','')+r'''
struct ScriptedEpoch {bool unstable=false;uint64_t calls=0;uint64_t load(){return unstable?calls++:17;}} history_epoch;
struct ScriptedCount {int value=0;int load(){return value;}} pending,history_serial,history_active;
'''+prepare.SNAPSHOT+r'''
using namespace mkwvk::pass_history;
int main(){
 Snapshot idle{},busy{};busy.serial=1;busy.active=1;
 Budget a;if(!a.begin(true,idle)||!a.reserve(kByteCap/2,0,kByteCap/2))return 1;
 if(!a.commit(a.actual_buffer,a.reserved_buffer,kByteCap/2)||!a.commit(a.actual_copy,a.reserved_copy,kByteCap/2))return 2;
 if(a.reserve(1,0,1)||a.reason!=Reason::Bytes||!a.incomplete)return 3;
 Budget b;b.begin(true,idle);if(b.reserve(UINT64_MAX,0,UINT64_MAX)||b.reason!=Reason::Bytes)return 4;
 Budget c;c.begin(true,idle);for(unsigned i=0;i<kStepCap;++i)if(!c.reserve(1,0,1))return 5;
 if(c.reserve(1,0,1)||c.reason!=Reason::Steps||c.admitted!=kStepCap)return 6;
 Budget d;d.begin(false,idle);if(d.reserve(1,0,1)||d.incomplete||d.actual_copy)return 7;
 for(unsigned i=0;i<kRowCap;++i)if(!d.row())return 8;
 if(d.row()||d.reason!=Reason::Rows||d.rows!=kRowCap)return 9;
 Budget e;e.begin(true,busy);if(!e.incomplete||e.reserve(1,0,1))return 10;
 Budget f;f.begin(true,idle);Snapshot changed=idle;changed.epoch=1;if(f.finish(idle,changed)||!f.incomplete)return 11;
 Budget g;g.begin(false,idle);if(!g.finish(idle,idle)||g.begin(false,idle)||g.reason!=Reason::Repeated)return 12;
 Budget h;h.begin(true,idle);h.reserve(1,0,1);if(h.commit(h.actual_copy,h.reserved_copy,2)||h.actual_copy||h.reason!=Reason::Accounting)return 13;
 for(unsigned field=0;field<5;++field){Snapshot s=idle;
  if(field==0)s.translated++;
  if(field==1)s.failed++;
  if(field==2)s.pipelines++;
  if(field==3)s.skips++;
  if(field==4)s.draw_skips++;
  if(idle.same_work(s))return 14;
 }
 Snapshot saturated=idle;saturated.epoch=UINT64_MAX;if(saturated.idle())return 15;
 uint64_t epoch=0;int normal=0,serial=0,active=0;jobs_history_snapshot(&epoch,&normal,&serial,&active);
 if(epoch!=17||normal||serial||active)return 16;
 history_epoch.unstable=true;jobs_history_snapshot(&epoch,&normal,&serial,&active);
 if(epoch!=UINT64_MAX||history_epoch.calls!=16)return 17;
 Budget missing;missing.begin(true,idle);missing.reserve(1,0,1);
 if(missing.finish(idle,idle)||missing.reason!=Reason::Accounting)return 18;
 Budget accounted;accounted.begin(true,idle);accounted.reserve(4,4,4);
 accounted.commit(accounted.actual_buffer,accounted.reserved_buffer,4);accounted.commit(accounted.actual_image,accounted.reserved_image,4);accounted.commit(accounted.actual_copy,accounted.reserved_copy,4);
 if(!accounted.finish(idle,idle))return 19;
 return 0;
}
'''
        with tempfile.TemporaryDirectory() as directory:
            cpp=Path(directory)/'policy.cpp';binary=cpp.with_suffix('');cpp.write_text(source)
            result=subprocess.run(['c++','-std=c++20','-O2','-DNDEBUG','-Wall','-Wextra','-Werror',str(cpp),'-o',str(binary)],capture_output=True,text=True,timeout=30)
            self.assertEqual(result.returncode,0,result.stderr);result=subprocess.run([str(binary)],capture_output=True,text=True,timeout=5)
            self.assertEqual(result.returncode,0,result.stdout+result.stderr)


@unittest.skipUnless(os.name=='posix' and shutil.which('c++'),'optional POSIX CPU observer helper compiler')
class NativeObserverHelpers(unittest.TestCase):
    def test_actual_added_helpers_defaultoff_metadata_and_caps(self):
        source=prepare.POLICY.replace('#pragma once','')+r'''
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <cstdarg>
#include <algorithm>
#include <filesystem>
#include <string>
bool partial_write=false;
size_t test_write(const void* p,size_t size,size_t count,FILE* f){return std::fwrite(p,size,partial_write&&count?count-1:count,f);}
#define fwrite test_write
namespace mkwvk {
bool enabled=true,want_pixels=false;int seq=0;
pass_history::Snapshot fixture_snapshot;
struct Texture {uint32_t levels=1,width=1536,height=864,id=1;int format=122;uint64_t gpu=123;};
bool env_on(const char* name){return std::strcmp(name,"RX_VK_OO_PASS_HISTORY")==0?enabled:want_pixels;}
void logf(const char*,...){}
bool dump_pending(){return false;}
void jobs_history_snapshot(uint64_t*e,int*n,int*s,int*a){*e=fixture_snapshot.epoch;*n=fixture_snapshot.normal;*s=fixture_snapshot.serial;*a=fixture_snapshot.active;}
void shader_stats(int64_t*t,int64_t*f){*t=fixture_snapshot.translated;*f=fixture_snapshot.failed;}
double shader_translate_ms(){return fixture_snapshot.translation_ms;}
void draw_stats(int64_t*d,int64_t*s,int64_t*x,int64_t*p){*d=0;*s=fixture_snapshot.draw_skips;*x=0;*p=fixture_snapshot.pipelines;}
void draw_stats3(int64_t*s,int64_t*w,double*m){*s=fixture_snapshot.skips;*w=0;*m=0;}
bool prepare_left_workers(){return fixture_snapshot.left_workers;}
'''+prepare.NATIVE+r'''
}
int main(int argc,char**argv){using namespace mkwvk;if(argc!=3)return 1;
 std::string name=argv[1];setenv("RX_VK_OO_PASS_HISTORY_FILE",argv[2],1);
 if(name=="off")enabled=false;
 if(name=="bytes")want_pixels=true;
 if(name=="partial")partial_write=true;
 if(name=="busy")fixture_snapshot.serial=1;
 history_begin(10);
 if(name=="off")return history.budget.used||std::filesystem::exists(argv[2])?2:0;
 history_row("{\"event\":\"pass\",\"order\":1,\"width\":1536,\"height\":864}\n");history.order=1;
 Texture texture;history_attachment(&texture,0,"pass c0 prog 1234567890abcdef");
 if(name=="bytes"){
  if(!history_reserve(pass_history::kByteCap/2,0))return 3;
  history_alloc(false,pass_history::kByteCap/2);history_copy(pass_history::kByteCap/2);
  if(history_reserve(1,0))return 4;
 }
 if(name=="rows"||name=="partial")for(unsigned i=0;i<1000;++i)history_row("{\"event\":\"fixture\",\"payload\":\"%0600d\"}\n",0);
 if(name=="changed")fixture_snapshot.epoch++;
 history_end();
 if(history.file || std::filesystem::file_size(argv[2])>pass_history::kFileCap)return 5;
 if(name=="meta")return history.budget.incomplete||history.budget.actual_buffer||history.budget.actual_copy?6:0;
 if(!history.budget.incomplete || !history.budget.stopped)return 7;
 // A diagnostic denial returns to its caller; the fixture's original-work marker still advances.
 unsigned original_work=0;if(!history_reserve(1,0))++original_work;return original_work==1?0:8;
}
'''
        with tempfile.TemporaryDirectory() as directory:
            folder=Path(directory);cpp=folder/'observer.cpp';binary=folder/'observer';cpp.write_text(source)
            result=subprocess.run(['c++','-std=c++20','-O2','-DNDEBUG','-Wall','-Wextra','-Werror','-Wno-misleading-indentation',str(cpp),'-o',str(binary)],capture_output=True,text=True,timeout=30)
            self.assertEqual(result.returncode,0,result.stderr)
            for name in ('off','meta','bytes','rows','partial','busy','changed'):
                path=folder/(name+'.jsonl')
                result=subprocess.run([str(binary),name,str(path)],capture_output=True,text=True,timeout=5)
                self.assertEqual(result.returncode,0,name+':'+result.stdout+result.stderr)
                if name=='meta':self.assertTrue(capture.validate_journal(path.read_bytes())['complete_for_selected_width_metadata'])


if __name__=='__main__':unittest.main()
