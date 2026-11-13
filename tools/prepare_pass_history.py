#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Prepare a default-off bounded diagnostic overlay in a new private source copy.

No renderer is loaded or executed. Rendering work is never suppressed by this
overlay; only its diagnostic readbacks can be denied. The first planned mode is
metadata only. Pixel capture needs separate review/authorization.
"""
import argparse
import difflib
import hashlib
import json
from pathlib import Path
import shutil
import sys

ROOT=Path(__file__).resolve().parents[1]
FILES=('gpu/vk/vk_dump.cpp','gpu/vk/vk_dump.h','gpu/vk/vk_draw.cpp',
       'gpu/vk/vk_replay.cpp','gpu/vk/vk_jobs.cpp','gpu/vk/vk_jobs.h')
BASE_SHA256=dict(zip(FILES,(
 '3a27c59c48b0f67484979a7d4192f883126f38f4ab65477169408f5993ba61e0',
 '71b0bcb8a7751b1a6a8c428929129f67a845b1e5b02782ca2bce9e35edad5ce7',
 '1f0243d368973d6969cb04c766fb1fa713b578cc4932c0af14f7310f48475b49',
 '32455c2f47ae68cb5335065f3d4ea42badb6ea3d12920fdf2a5de61b11d62492',
 '98fd29c5eee68a9558bb1bccf58b8d9b64e9cbe546c8aae4e0d4bb4c34906046',
 '55c43b5168f2466ee041f0bbc6c82b67bcaa7c96b01bf71252fd65a226dcde9f')))
POLICY=r'''// SPDX-License-Identifier: GPL-3.0-or-later
// Authored CPU policy; budget counts logical diagnostic resource/copy bytes.
#pragma once
#include <cstdint>
#include <limits>
namespace mkwvk::pass_history {
constexpr uint64_t kByteCap=384ull<<20;
constexpr uint64_t kFileCap=256ull<<10;
constexpr uint32_t kStepCap=128,kRowCap=512;
enum class Reason : uint32_t { None,Bytes,Steps,Rows,IO,Repeated,Readiness,Unsupported,Accounting };
struct Snapshot {
 uint64_t epoch=0; int normal=0,serial=0,active=0;
 int64_t translated=0,failed=0,pipelines=0,skips=0,draw_skips=0;
 double translation_ms=0; bool left_workers=false;
 bool idle() const { return epoch!=std::numeric_limits<uint64_t>::max() && normal==0 && serial==0 && active==0 && !left_workers; }
 bool same_work(const Snapshot& b) const {
  return idle()&&b.idle()&&epoch==b.epoch&&translated==b.translated&&failed==b.failed&&pipelines==b.pipelines&&skips==b.skips&&draw_skips==b.draw_skips&&translation_ms==b.translation_ms;
 }
};
struct Budget {
 bool used=false,pixels=false,incomplete=false,stopped=false; Reason reason=Reason::None;
 uint32_t rows=0,attempts=0,admitted=0;
 uint64_t reserved_buffer=0,reserved_image=0,reserved_copy=0;
 uint64_t actual_buffer=0,actual_image=0,actual_copy=0;
 void stop(Reason r) { incomplete=true;stopped=true;if(reason==Reason::None)reason=r; }
 bool begin(bool want_pixels,const Snapshot& s) {
  if(used){stop(Reason::Repeated);return false;}used=true;pixels=want_pixels;
  if(!s.idle())stop(Reason::Readiness);
  return true;
 }
 bool row() { if(rows>=kRowCap){stop(Reason::Rows);return false;}++rows;return true; }
 bool reserve(uint64_t buffer,uint64_t image,uint64_t copy) {
  if(!pixels||stopped)return false;
  if(attempts>=kStepCap){stop(Reason::Steps);return false;}++attempts;
  if(!buffer||copy!=buffer||image>buffer){stop(Reason::Accounting);return false;}
  const uint64_t used=reserved_buffer+reserved_image+reserved_copy;
  if(buffer>kByteCap-used || image>kByteCap-used-buffer || copy>kByteCap-used-buffer-image){stop(Reason::Bytes);return false;}
  reserved_buffer+=buffer;reserved_image+=image;reserved_copy+=copy;++admitted;return true;
 }
 bool commit(uint64_t& actual,uint64_t reserved,uint64_t n) {
  if(n>reserved || actual>reserved-n){stop(Reason::Accounting);return false;}actual+=n;return true;
 }
 bool finish(const Snapshot& begin,const Snapshot& end) {
  if(actual_buffer!=reserved_buffer||actual_image!=reserved_image||actual_copy!=reserved_copy)stop(Reason::Accounting);
  if(!begin.same_work(end))stop(Reason::Readiness);
  return !incomplete;
 }
};
} // namespace mkwvk::pass_history
'''
NATIVE=r'''
// Default-off diagnostic observer. File/row/step/byte bounds are independent of
// game rendering; an exhausted diagnostic never returns from a draw/dispatch.
bool history_on() { static const bool on=env_on("RX_VK_OO_PASS_HISTORY");return on; }
pass_history::Snapshot history_snapshot() {
 pass_history::Snapshot s;
 jobs_history_snapshot(&s.epoch,&s.normal,&s.serial,&s.active);
 shader_stats(&s.translated,&s.failed);s.translation_ms=shader_translate_ms();
 int64_t draws=0,dispatches=0;draw_stats(&draws,&s.draw_skips,&dispatches,&s.pipelines);
 int64_t waits=0;double ms=0;draw_stats3(&s.skips,&waits,&ms);
 s.left_workers=prepare_left_workers();return s;
}
struct History {
 pass_history::Budget budget;pass_history::Snapshot begin;
 FILE* file=nullptr;uint64_t written=0,order=0;bool ended=false;
 int64_t present=0;
} history;
void history_row(const char* fmt,...) {
 if(!history.file || !history.budget.row())return;
 char line[768];va_list ap;va_start(ap,fmt);const int n=vsnprintf(line,sizeof line,fmt,ap);va_end(ap);
 if(n<0 || n>=(int)sizeof line || history.written+(uint64_t)n+4096>pass_history::kFileCap){history.budget.stop(pass_history::Reason::Rows);return;}
 const size_t put=fwrite(line,1,(size_t)n,history.file);history.written+=put;
 if(put!=(size_t)n || fflush(history.file)){history.budget.stop(pass_history::Reason::IO);return;}
}
void history_begin(int64_t present) {
 if(!history_on())return;
 const auto s=history_snapshot();
 if(!history.budget.begin(env_on("RX_VK_OO_PASS_PIXELS"),s)){logf("vk: pass-history repeated frame refused");return;}
 history.begin=s;history.present=present;
 const char* path=getenv("RX_VK_OO_PASS_HISTORY_FILE");
 if(!path||!*path || !(history.file=fopen(path,"wbx"))){history.budget.stop(pass_history::Reason::IO);return;}
 history_row("{\"event\":\"begin\",\"present\":%lld,\"epoch\":%llu,\"normal\":%d,\"serial\":%d,\"active\":%d,\"translated\":%lld,\"failed\":%lld,\"pipelines\":%lld,\"skips\":%lld,\"draw_skips\":%lld,\"translation_ms\":%.17g,\"left_workers\":%s,\"pixels\":%s}\n",(long long)present,(unsigned long long)s.epoch,s.normal,s.serial,s.active,(long long)s.translated,(long long)s.failed,(long long)s.pipelines,(long long)s.skips,(long long)s.draw_skips,s.translation_ms,s.left_workers?"true":"false",history.budget.pixels?"true":"false");
}
void history_close_if_done() {
 if(!history_on()||!history.file||!history.ended||dump_pending())return;
 const auto& b=history.budget;char line[2048];
 const int n=snprintf(line,sizeof line,"{\"event\":\"end\",\"present\":%lld,\"incomplete\":%s,\"reason\":%u,\"rows\":%u,\"attempts\":%u,\"admitted\":%u,\"buffer_reserved\":%llu,\"image_reserved\":%llu,\"copy_reserved\":%llu,\"buffer_actual\":%llu,\"image_actual\":%llu,\"copy_actual\":%llu}\n",(long long)history.present,b.incomplete?"true":"false",(unsigned)b.reason,b.rows,b.attempts,b.admitted,(unsigned long long)b.reserved_buffer,(unsigned long long)b.reserved_image,(unsigned long long)b.reserved_copy,(unsigned long long)b.actual_buffer,(unsigned long long)b.actual_image,(unsigned long long)b.actual_copy);
 bool ok=n>0&&n<(int)sizeof line&&history.written+(uint64_t)n<=pass_history::kFileCap&&fwrite(line,1,(size_t)n,history.file)==(size_t)n;
 if(fclose(history.file))ok=false;history.file=nullptr;
 if(!ok)history.budget.stop(pass_history::Reason::IO);
 logf("vk: pass-history terminal: incomplete %d, reason %u, rows %u, images %u, copied %llu bytes",(int)history.budget.incomplete,(unsigned)history.budget.reason,b.rows,b.admitted,(unsigned long long)b.actual_copy);
}
void history_end() {
 if(!history_on()||!history.budget.used||history.ended)return;
 const auto end=history_snapshot();history.budget.finish(history.begin,end);history.ended=true;
 history_row("{\"event\":\"readiness_end\",\"epoch\":%llu,\"normal\":%d,\"serial\":%d,\"active\":%d,\"translated\":%lld,\"failed\":%lld,\"pipelines\":%lld,\"skips\":%lld,\"draw_skips\":%lld,\"translation_ms\":%.17g,\"left_workers\":%s,\"unchanged_work\":%s}\n",(unsigned long long)end.epoch,end.normal,end.serial,end.active,(long long)end.translated,(long long)end.failed,(long long)end.pipelines,(long long)end.skips,(long long)end.draw_skips,end.translation_ms,end.left_workers?"true":"false",history.begin.same_work(end)?"true":"false");
 history_close_if_done();
}
bool history_attachment(Texture* tex,uint32_t level,const char* what) {
 if(!history_on())return true;
 ++history.order;if(!tex->levels){history.budget.stop(pass_history::Reason::Unsupported);return false;}level=std::min(level,tex->levels-1);
 const auto w=std::max(1u,tex->width>>level),h=std::max(1u,tex->height>>level);
 const bool selected=(w==1536||w==2048);
 if(!selected)return false;
 // Labels here are source-generated pass labels containing only fixed words and hex IDs.
 history_row("{\"event\":\"attachment\",\"order\":%llu,\"step\":%d,\"width\":%u,\"height\":%u,\"format\":%d,\"texture\":%u,\"gpu\":%llu,\"label\":\"%.96s\",\"pixels_requested\":%s,\"diagnostic_stopped\":%s}\n",(unsigned long long)history.order,seq,w,h,(int)tex->format,tex->id,(unsigned long long)tex->gpu,what,history.budget.pixels?"true":"false",history.budget.stopped?"true":"false");
 return history.budget.pixels&&!history.budget.stopped;
}
bool history_reserve(uint64_t buffer,uint64_t image) {
 if(!history_on())return true;
 const bool allowed=history.budget.reserve(buffer,image,buffer);
 history_row("{\"event\":\"reservation\",\"buffer\":%llu,\"image\":%llu,\"copy\":%llu,\"allowed\":%s,\"reason\":%u}\n",(unsigned long long)buffer,(unsigned long long)image,(unsigned long long)buffer,allowed?"true":"false",(unsigned)history.budget.reason);
 return allowed&&!history.budget.stopped;
}
void history_alloc(bool image,uint64_t n) {
 if(!history_on())return;
 if(image)history.budget.commit(history.budget.actual_image,history.budget.reserved_image,n);
 else history.budget.commit(history.budget.actual_buffer,history.budget.reserved_buffer,n);
}
void history_copy(uint64_t n) {
 if(history_on())history.budget.commit(history.budget.actual_copy,history.budget.reserved_copy,n);
}
'''
SNAPSHOT=r'''void jobs_history_snapshot(uint64_t* epoch,int* normal,int* serial,int* active) {
 for(unsigned tries=0;tries<8;++tries){const auto a=history_epoch.load();*normal=pending.load();*serial=history_serial.load();*active=history_active.load();const auto b=history_epoch.load();if(a==b){*epoch=b;return;}}
 *epoch=UINT64_MAX; // unstable observation is explicitly not readiness
}
'''


def change(text,old,new,count=1):
    if text.count(old)!=count:raise ValueError('Source anchor changed: '+old[:90])
    return text.replace(old,new)


def transform(original):
    out=dict(original)
    key='gpu/vk/vk_jobs.h'
    out[key]+='\n// Default-off pass-history diagnostic accounting; no queue behavior changes.\nnamespace mkwvk { void jobs_history_snapshot(uint64_t*,int*,int*,int*); }\n'
    out[key]=change(out[key],'#pragma once','#pragma once\n#include <cstdint>')
    key='gpu/vk/vk_jobs.cpp';text=out[key]
    text=change(text,'#include "vk_jobs.h"','#include "vk_jobs.h"\n#include "hookshot_env.h"\n#include <cstdint>\n#include <limits>')
    text=change(text,'std::atomic<int> pending{0};','''std::atomic<int> pending{0};
std::atomic<int> history_serial{0},history_active{0};
std::atomic<uint64_t> history_epoch{0};
bool history_jobs_on(){static const bool v=env_on("RX_VK_OO_PASS_HISTORY");return v;}
void history_change(){if(!history_jobs_on())return;auto old=history_epoch.load();for(unsigned tries=0;tries<32&&old!=UINT64_MAX;++tries){if(history_epoch.compare_exchange_weak(old,old+1))return;}if(old!=UINT64_MAX)history_epoch.store(UINT64_MAX);}
''')
    text=change(text,'\n        pending--;','\n        pending--;\n        history_change();',1)
    text=change(text,'    pending++;','    pending++;\n    history_change();')
    text=change(text,'                    sq.pop_front();','                    sq.pop_front();\n                    if(history_jobs_on())history_active++;')
    text=change(text,'                run_guarded(fn);\n                jobs_notify();','                run_guarded(fn);\n                if(history_jobs_on()){history_active--;history_serial--;history_change();}\n                jobs_notify();')
    text=change(text,'    sq.push_back(std::move(fn));','    if(history_jobs_on()){history_serial++;history_change();}\n    sq.push_back(std::move(fn));')
    text=change(text,'\n                pending--;','\n                pending--;\n                history_change();')
    text=change(text,'int jobs_pending() {',SNAPSHOT+'int jobs_pending() {')
    out[key]=text
    key='gpu/vk/vk_dump.h';out[key]+='\nnamespace mkwvk { bool dump_history_active(); void dump_history_status(); void dump_history_pass(uint32_t,uint32_t,uint64_t,uint64_t,int,int); }\n'
    key='gpu/vk/vk_dump.cpp';text=out[key]
    text=change(text,'#include "vk_work.h"','#include "pass_history_policy.h"\n#include "vk_jobs.h"\n#include "vk_draw.h"\n#include "vk_shader.h"\n#include <cstdarg>\n#include "vk_work.h"')
    text=change(text,'std::string dir;','std::string dir;\n'+NATIVE)
    text=change(text,'    write_png(path, rgba.data(), it.w, it.h);','    if(!write_png(path, rgba.data(), it.w, it.h) && history_on())history.budget.stop(pass_history::Reason::IO);')
    text=change(text,'    armed = std::find(targets.begin(), targets.end(), presents) != targets.end();','    if(armed)history_end();\n    armed = std::find(targets.begin(), targets.end(), presents) != targets.end();')
    text=change(text,'    seq = 0;\n    const char* base', '    seq = 0;\n    history_begin(presents);\n    const char* base')
    text=change(text,'    if (!armed || !tex || !tex->image || tex->samples != VK_SAMPLE_COUNT_1_BIT) return;',
        '    if (!armed || !tex || !tex->image) return;\n    if(!history_attachment(tex,level,what))return void(seq++);\n    if(tex->samples != VK_SAMPLE_COUNT_1_BIT){if(history_on())history.budget.stop(pass_history::Reason::Unsupported);return;}')
    anchor='        if (!create_buffer((uint64_t)it.w * it.h * it.depth_bytes,'
    text=change(text,anchor,'        const uint64_t history_bytes=(uint64_t)it.w*it.h*it.depth_bytes;\n        if(!history_reserve(history_bytes,0))return;\n'+anchor)
    text=change(text,'                           it.buffer_alloc, &it.map))\n            return;','                           it.buffer_alloc, &it.map)){if(history_on())history.budget.stop(pass_history::Reason::IO);return;}')
    text=change(text,'        full_barrier(cmd);\n        const VkBufferImageCopy c{','        history_alloc(false,history_bytes);\n        full_barrier(cmd);\n        const VkBufferImageCopy c{')
    text=change(text,'        vkCmdCopyImageToBuffer(cmd, tex->image, VK_IMAGE_LAYOUT_GENERAL, it.buffer, 1, &c);','        vkCmdCopyImageToBuffer(cmd, tex->image, VK_IMAGE_LAYOUT_GENERAL, it.buffer, 1, &c);\n        history_copy(history_bytes);')
    text=change(text,'    if (!create_image(ci, it.image, it.image_alloc)) return;','    const uint64_t history_bytes=(uint64_t)it.w*it.h*16;\n    if(!history_reserve(history_bytes,history_bytes))return;\n    if (!create_image(ci, it.image, it.image_alloc)){if(history_on())history.budget.stop(pass_history::Reason::IO);return;}\n    history_alloc(true,history_bytes);')
    text=change(text,'    full_barrier(cmd);\n    barrier(cmd, it.image, VK_IMAGE_LAYOUT_UNDEFINED,','    history_alloc(false,history_bytes);\n    full_barrier(cmd);\n    barrier(cmd, it.image, VK_IMAGE_LAYOUT_UNDEFINED,')
    text=change(text,'        destroy_image(it.image, it.image_alloc);\n        return;','        if(history_on())history.budget.stop(pass_history::Reason::IO);\n        destroy_image(it.image, it.image_alloc);\n        return;')
    text=change(text,'    if (!(fp.optimalTilingFeatures & VK_FORMAT_FEATURE_BLIT_SRC_BIT)) {','    if (!(fp.optimalTilingFeatures & VK_FORMAT_FEATURE_BLIT_SRC_BIT)) {\n        if(history_on())history.budget.stop(pass_history::Reason::Unsupported);')
    text=change(text,'    vkCmdCopyImageToBuffer(cmd, it.image, VK_IMAGE_LAYOUT_TRANSFER_SRC_OPTIMAL, it.buffer, 1, &c);','    vkCmdCopyImageToBuffer(cmd, it.image, VK_IMAGE_LAYOUT_TRANSFER_SRC_OPTIMAL, it.buffer, 1, &c);\n    history_copy(history_bytes);')
    text=change(text,'    if (!armed || !src || !n) return;','    if (!armed || !src || !n) return;\n    if(history_on()){history.budget.stop(pass_history::Reason::Unsupported);return;}')
    text=change(text,'    buf_items.clear();','    buf_items.clear();\n    history_close_if_done();')
    marker='bool dump_on() {'
    text=change(text,marker,'''bool dump_history_active(){return history_on();}
void dump_history_status(){
 if(!history_on())return;const auto s=history_snapshot();
 logf("vk: pass-history CPU queues: epoch %llu normal %d serial-total %d serial-active %d translated %lld failed %lld pipelines %lld pending-skips %lld left-workers %d",(unsigned long long)s.epoch,s.normal,s.serial,s.active,(long long)s.translated,(long long)s.failed,(long long)s.pipelines,(long long)s.skips,(int)s.left_workers);
}
void dump_history_pass(uint32_t w,uint32_t h,uint64_t first,uint64_t last,int draws,int clears){
 if(!history_on()||!armed)return;++history.order;
 if(w!=1536&&w!=2048)return;
 history_row("{\\"event\\":\\"pass\\",\\"order\\":%llu,\\"width\\":%u,\\"height\\":%u,\\"first_program\\":%llu,\\"last_program\\":%llu,\\"draws\\":%d,\\"clears\\":%d}\\n",(unsigned long long)history.order,w,h,(unsigned long long)first,(unsigned long long)last,draws,clears);
}
'''+marker)
    out[key]=text
    key='gpu/vk/vk_draw.cpp';text=out[key]
    text=change(text,'    if (dump_on() && !dump_mesh_indirect().empty()) {','    if (dump_on() && !dump_history_active() && !dump_mesh_indirect().empty()) {')
    text=change(text,'            if (dump_on() && d.indirect && dump_mesh_indirect().size() < 256)','            if (dump_on() && !dump_history_active() && d.indirect && dump_mesh_indirect().size() < 256)')
    text=change(text,'    if (dump_on())\n        for (int i = 0; i < pass().n; i++)','    if(dump_on())dump_history_pass(pass().width,pass().height,pass().first_prog,pass().prog,pass().draws,pass().clears);\n    if (dump_on())\n        for (int i = 0; i < pass().n; i++)')
    out[key]=text
    key='gpu/vk/vk_replay.cpp';out[key]=change(out[key],'             shader_translate_ms(), jobs_pending(), libs);','             shader_translate_ms(), jobs_pending(), libs);\n        dump_history_status();')
    out['gpu/vk/pass_history_policy.h']=POLICY
    return out


def prepare(source_root,output):
    source_root,output=Path(source_root).resolve(),Path(output).resolve()
    if output.exists() or output.is_relative_to(source_root) or not output.is_relative_to(ROOT/'local'):
        raise ValueError('New private output outside source required')
    original={name:(source_root/name).read_text() for name in FILES}
    for name,text in original.items():
        if hashlib.sha256(text.encode()).hexdigest()!=BASE_SHA256[name]:raise ValueError('Original frozen12 source hash differs: '+name)
    candidate=transform(original)
    output.mkdir(parents=True);chunks=[]
    for name,text in candidate.items():
        for side,content in [('before',original.get(name)),('after',text)]:
            if content is not None:
                p=output/side/name;p.parent.mkdir(parents=True,exist_ok=True);p.write_text(content)
        if text!=original.get(name):chunks.extend(difflib.unified_diff(original.get(name,'').splitlines(True),text.splitlines(True),fromfile='a/'+name if name in original else '/dev/null',tofile='b/'+name))
    patch=output/'bounded-pass-history.patch';patch.write_text(''.join('\n' if line==' \n' else line for line in chunks));sha=lambda p:hashlib.sha256(Path(p).read_bytes()).hexdigest()
    report={'schema':'open-ocarina.bounded-pass-history-overlay/v1','patch_sha256':sha(patch),'original_sha256':{n:hashlib.sha256(t.encode()).hexdigest() for n,t in original.items()},'candidate_sha256':{n:hashlib.sha256(t.encode()).hexdigest() for n,t in candidate.items()},'default_off':True,'GPU_executed':False,'shared_source_modified':False}
    (output/'preparation.json').write_text(json.dumps(report,indent=2)+'\n');return report


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--source-root',type=Path,required=True);p.add_argument('--output',type=Path,required=True);a=p.parse_args()
    try:print(json.dumps(prepare(a.source_root,a.output),indent=2))
    except (OSError,ValueError) as e:print(str(e),file=sys.stderr);return 2
    return 0


if __name__=='__main__':raise SystemExit(main())
