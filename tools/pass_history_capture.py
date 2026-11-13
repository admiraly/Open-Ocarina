#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Prepare one private, bounded, metadata-first baseline pass-history diagnostic.

Preparation and validation are CPU only. `run` requires a separately granted
lease and exact frozen plan hash. It never signals unrelated processes. Native
caps stop diagnostic capture while game rendering continues.
"""
import argparse
import hashlib
import importlib.util
import json
import math
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import threading
import time

ROOT=Path(__file__).resolve().parents[1]
HOST='4eb32b56a91869b5d7ac1673f0ca1e3c0841e893a8a34f728ec85344a312cb30'
ORIGINAL='629addc24fe06d5170ebd0d6892cd0722dc69e7171ee951abc4188f17d26405d'
DRIVER='e5a1305ea3b423a18fccbb945aaedd32f8add303926fcc39276ee01032cd456f'
PILOT='8442ec2c79f54d7549f138e26dbfc4dcf00580272fb53daa462cea9d4c4fb4ee'
PILOT_PLAN='2ade0cd1bd126eecd7ab8c93aa045131a8915914419497bf705c402dbecdb355'
REQUIRED={'HS_SPILL_BATCH':'1','HS_PUSH_SHADOW':'1','shader_spilling_rate':'1'}
FORBIDDEN=('HS_CS32_MAX_INSTS','HS_FORCE_SIMD8','HS_SUBGROUP8','HS_SPILL_PCT','HS_LARGE_GRF','HS_LARGE_GRF_BIT','HS_RA_DEBUG','INTEL_DEBUG','RX_VK_NOOPT','RX_VK_WARP64')
QUEUE=re.compile(r'vk: pass-history CPU queues: epoch (\d+) normal (\d+) serial-total (\d+) serial-active (\d+) translated (\d+) failed (\d+) pipelines (\d+) pending-skips (\d+) left-workers (\d+)')
LOG_CAP=16<<20
BYTE_CAP=384<<20
JOURNAL_CAP=256<<10


def require(condition,message):
    if not condition:raise ValueError(message)


def sha(path):
    with Path(path).open('rb') as stream:return hashlib.file_digest(stream,'sha256').hexdigest()


def write(path,value):
    Path(path).write_text(json.dumps(value,indent=2,sort_keys=True)+'\n')


def bounded_read(path,cap):
    with Path(path).open('rb') as stream:data=stream.read(cap+1)
    require(len(data)<=cap,'Observed file exceeds cap')
    return data


def pilot(path):
    require(sha(path)==PILOT,'Reviewed pilot helper differs')
    spec=importlib.util.spec_from_file_location('pass_history_pilot',path);module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    module.ROOT=ROOT
    return module


def queues_ready(text):
    if any('pass-history CPU queues:' in line and not QUEUE.search(line) for line in text.splitlines()):return False
    rows=[tuple(map(int,m.groups())) for m in QUEUE.finditer(text)]
    # Three periodic native observations, not silence or guessed state=play.
    if len(rows)<3:return False
    selected=rows[-3:]
    return selected[0]==selected[1]==selected[2] and selected[0][1:4]==(0,0,0) and selected[0][-1]==0 and selected[0][0]<(1<<64)-1


def validate_journal(data):
    require(len(data)<=JOURNAL_CAP,'Journal byte cap exceeded')
    rows=[json.loads(line) for line in data.splitlines()]
    require(rows and rows[0].get('event')=='begin' and rows[-1].get('event')=='end','Incomplete journal framing')
    require(len(rows)<=513,'Journal row cap exceeded')
    require(sum(row.get('event')=='begin' for row in rows)==1,'Multiple frames in one journal')
    require(rows[0].get('pixels') is False,'This reviewed phase is metadata only')
    end=rows[-1]
    for name in ('buffer_reserved','image_reserved','copy_reserved','buffer_actual','image_actual','copy_actual','admitted','attempts'):
        require(type(end.get(name)) is int and end[name]==0,'Metadata-only phase recorded pixel work')
    ordering=[row['order'] for row in rows if row.get('event') in ('pass','attachment')]
    require(ordering and all(a<b for a,b in zip(ordering,ordering[1:])),'Missing or unordered pass history')
    require(all(row.get('width') in (1536,2048) for row in rows if 'width' in row),'Unexpected selected width')
    ready=[row for row in rows if row.get('event')=='readiness_end']
    require(len(ready)==1,'Missing end readiness counters')
    fields=('epoch','normal','serial','active','translated','failed','pipelines','skips','draw_skips','translation_ms','left_workers')
    require(all(key in rows[0] and key in ready[0] for key in fields),'Incomplete readiness fields')
    for snapshot in (rows[0],ready[0]):
        require(all(type(snapshot[key]) is int and snapshot[key]>=0 for key in fields if key not in ('translation_ms','left_workers')),'Invalid readiness counter type/value')
        require(type(snapshot['left_workers']) is bool and type(snapshot['translation_ms']) in (int,float) and math.isfinite(snapshot['translation_ms']) and snapshot['translation_ms']>=0,'Invalid readiness state/time')
    same=all(rows[0][key]==ready[0][key] for key in fields) and rows[0]['normal']==rows[0]['serial']==rows[0]['active']==0 and rows[0]['left_workers'] is False and rows[0]['epoch']<(1<<64)-1
    require(ready[0].get('unchanged_work') is same,'Native/Python readiness classification differs')
    require(any(row.get('event')=='pass' for row in rows),'No selected pass rows')
    if end.get('incomplete') is False:require(end.get('reason')==0 and end.get('rows')==len(rows)-1,'Inconsistent complete journal counters')
    complete=end.get('incomplete') is False and same
    return {'complete_for_selected_width_metadata':complete,'rows':len(rows),'passes':sum(row.get('event')=='pass' for row in rows),
            'reason':end.get('reason'),'pixel_capture':False,'card_producer_identified':False}


def validate_approval(value,shot_sha):
    require(value=={'screenshot_sha256':shot_sha,'visible_card_reviewed':True},'Exact screenshot/card review certificate required')


def selected_arc(text):
    return 'vk: Intel(R) Arc(tm) A770 Graphics (DG2), Vulkan 1.4, driver 0x6802004;' in text


def validate_png(path):
    data=bounded_read(path,8<<20)
    require(24<=len(data)<=8<<20 and data[:8]==b'\x89PNG\r\n\x1a\n' and
            (int.from_bytes(data[16:20],'big'),int.from_bytes(data[20:24],'big'))==(2048,1152),'Control PNG dimensions/size differ')
    return hashlib.sha256(data).hexdigest()


def proc_start(pid):
    value=Path(f'/proc/{pid}/stat').read_text();return int(value[value.rfind(')')+2:].split()[19])


def stop_owned(proc,started):
    if proc.poll() is not None:return False
    require(proc_start(proc.pid)==started,'Owned process identity changed; refusing signal')
    proc.terminate()
    try:proc.wait(timeout=8);return False
    except subprocess.TimeoutExpired:
        require(proc_start(proc.pid)==started,'Owned process identity changed before kill')
        proc.kill();proc.wait(timeout=5);return True


def prepare(args):
    out=args.output.resolve();require(out.is_relative_to(ROOT/'local') and not out.exists(),'New private output required')
    require(sha(args.pilot_plan)==PILOT_PLAN,'Exact prior seed/save plan required')
    old=json.loads(args.pilot_plan.read_text());helper=pilot(args.pilot_launcher)
    require(old['schema']=='private.native-v3-e5-pilot/1','Wrong seed-plan family')
    base=args.pilot_plan.parent;donor=base/'baseline0/install';source=args.overlay_source.resolve();renderer=args.renderer.resolve()
    require(sha(donor/'bin/hookshot')==HOST,'Host differs')
    require(sha(donor/'bin/hookshot_vk.so')==ORIGINAL,'Original baseline renderer differs')
    require(sha(args.driver)==DRIVER,'E5 driver differs')
    overlay=json.loads(args.overlay_preparation.read_text())
    for name,digest in overlay['candidate_sha256'].items():require(sha(source/name)==digest,'Overlay source differs')
    expected=old['seeds'];out.mkdir();case=out/'case';install=case/'install';(install/'bin').mkdir(parents=True)
    for directory in ('observer','home','config','xdg-cache'):(case/directory).mkdir()
    for name in ('userdata','mesa-cache'):
        require(helper.manifest(base/'seed'/name)==expected[name],'Immutable seed changed')
        shutil.copytree(base/'seed'/name,case/name)
        for path in (case/name).rglob('*'):path.chmod(0o755 if path.is_dir() else 0o644)
        require(helper.manifest(case/name)==expected[name],'Seed copy differs')
    for name in ('image.bin','hookshot_shaders.bin','romfs','prompts'):(install/name).symlink_to((donor/name).resolve(strict=True))
    for name in ('hookshot','libSDL2-2.0.so.0'):shutil.copy2(donor/'bin'/name,install/'bin'/name)
    shutil.copy2(renderer,install/'bin/hookshot_vk.so')
    for name in ('hookshot_options.ini','language.txt'):shutil.copy2(donor/name,install/name)
    options=dict(line.split('=',1) for line in (install/'hookshot_options.ini').read_text().splitlines() if '=' in line)
    require(all(options.get(k)==v for k,v in {'game_speed':'0','frame_rate_cap':'0','frame_generation':'0','output_res':'2048x1152'}.items()),'Baseline settings differ')
    env_bytes=b'RX_VK_PRECOMPILE=0\n'
    for path in (case/'environment-pin.txt',install/'hookshot_env.txt'):path.write_bytes(env_bytes);path.chmod(0o444)
    env=dict(old['cases']['baseline0']['environment'])
    for key in list(env):
        if key in FORBIDDEN or key.startswith('RX_VK_DUMP') or key.startswith('RX_VK_PASS_TRACE') or key.startswith('RX_VK_FLASH'):env.pop(key,None)
    for key,relative in {'HOME':'home','XDG_CONFIG_HOME':'config','XDG_CACHE_HOME':'xdg-cache','XDG_DATA_HOME':'userdata','MESA_SHADER_CACHE_DIR':'mesa-cache'}.items():env[key]=str(case/relative)
    env.update(REQUIRED);env.update({'PATH':'/usr/local/bin:/usr/bin:/bin','HOS_ENV_FILE':str(case/'environment-pin.txt'),'HOS_CONTROL':str(case/'control.sock'),'VK_DRIVER_FILES':str(args.icd.resolve()),
        'RX_VK_OO_MESH_WARP32':'0','RX_VK_OO_MESH_WARP32_PROFILE96':'1','RX_VK_OO_GRAPHICS_IDENTITY_FILE':str(case/'observer/graphics-identity.jsonl'),
        'RX_VK_OO_PASS_HISTORY':'1','RX_VK_OO_PASS_PIXELS':'0','RX_VK_OO_PASS_HISTORY_FILE':str(case/'observer/history.jsonl'),
        'RX_VK_DUMP_FILE':str(case/'observer/trigger'),'RX_VK_DUMP_DIR':str(case/'observer/images'),'RX_VK_DUMP_W':'320','RX_VK_DUMP_ONLY_W':'1536,2048'})
    require(len(env['HOS_CONTROL'].encode())<104,'Control socket path too long')
    require(Path(json.loads(args.icd.read_text())['ICD']['library_path']).resolve()==args.driver.resolve(),'ICD target differs')
    (case/'observer/images').mkdir()
    pins={str(p.relative_to(case)):helper.identity(p) for p in [*list((install/'bin').iterdir()),install/'hookshot_options.ini',install/'language.txt',case/'environment-pin.txt',install/'hookshot_env.txt']}
    dependencies={'driver':helper.identity(args.driver),'icd':helper.identity(args.icd),'pilot_launcher':helper.identity(args.pilot_launcher),'pilot_plan':helper.identity(args.pilot_plan),'overlay_preparation':helper.identity(args.overlay_preparation)}
    assets={name:helper.identity(install/name,content=name in ('image.bin','hookshot_shaders.bin')) for name in ('image.bin','hookshot_shaders.bin','romfs','prompts')}
    plan={'schema':'open-ocarina.private-pass-history-plan/v1','phase':'metadata-only','launcher_sha256':sha(__file__),'renderer_base_sha256':ORIGINAL,'renderer_observer_sha256':sha(renderer),'host_sha256':HOST,
        'overlay':overlay,'dependencies':dependencies,'case_file_pins':pins,'static_inputs':assets,'seed_manifests':expected,'environment':env,'settings':options,
        'bounds':{'process_seconds':240,'native_frames_to_settle':120,'native_readback_resource_copy_payload_cap':BYTE_CAP,'native_image_attempt_cap':128,'native_metadata_rows':512,'native_metadata_file_bytes':JOURNAL_CAP,'observer_log_bytes':LOG_CAP,'control_screenshots':2,'diagnostic_disk_budget':512<<20},
        'readiness':'Three identical explicit normal/serial/active native observations and120 sampled nonloading gameplay frames; actual frame begin/end counters must also remain unchanged. This covers instrumented source queues/counters only.',
        'original_binary_limitations':['jobs_pending omits serial jobs','DUMP_PROGS overrides step filter','depth ignores dump width','no live aggregate byte cap or disarm API'],
        'initial_reference':{'PNG_sha256':'de591bcb28de7f4c91ae5bda6d82b2a1946d664e1e6bfdbf9c423e47065cedbb','size':[2048,1152],'manual_card_ROI':[128,286,307,347]},
        'reference_programs':old['targets'],'program_identity_scope':'Expected original modules from earlier baseline; capped startup identities do not prove module identity for every captured pass or identify the card shader.',
        'nondeterminism':['Camera transform is not observed/pinned by this host.','The same save and seven-A script do not fix particle RNG, temporal history or simulation timing.','A new screenshot/card review is required; ROI coordinates are not assumed stable.'],
        'runtime_authorized':False,'GPU_executed_during_preparation':False,'capture_goal':'Ordered scene/output metadata candidates first; this phase cannot locate the first pixel producer of the card.'}
    write(out/'plan.json',plan);(out/'plan.json').chmod(0o444)
    return {'plan_sha256':sha(out/'plan.json'),'renderer_sha256':sha(renderer),'runtime_authorized':False}


def run(args):
    require(args.lease and args.plan_sha256,'Separate exact parent runtime lease required')
    # The child changes cwd to install; derive executable and cwd from one
    # absolute plan path so a relative CLI argument cannot change resolution.
    args.plan=Path(args.plan).resolve()
    require(sha(args.plan)==args.plan_sha256,'Frozen plan hash differs')
    plan=json.loads(args.plan.read_text());require(plan['launcher_sha256']==sha(__file__),'Launcher changed')
    require(plan['phase']=='metadata-only' and plan['environment']['RX_VK_OO_PASS_PIXELS']=='0','Unreviewed pixel phase')
    helper=pilot(Path(plan['dependencies']['pilot_launcher']['path']));case=args.plan.parent/'case';observer=case/'observer';env=plan['environment']
    helper.no_games();require(not (observer/'launch.json').exists(),'One launch only; no retry')
    for pin in plan['dependencies'].values():require(helper.identity(pin['path'])==pin,'Dependency changed')
    for name,pin in plan['case_file_pins'].items():require(helper.identity(case/name)==pin,'Case changed')
    for name,rows in plan['seed_manifests'].items():require(helper.manifest(case/name)==rows,'Save/cache seed reused')
    for name,pin in plan['static_inputs'].items():require(helper.identity(case/'install'/name,content='sha256' in pin)==pin,'Static input changed')
    require(all(k not in env for k in FORBIDDEN) and all(env.get(k)==v for k,v in REQUIRED.items()),'Compiler controls differ')
    require({k for k in env if k.startswith('RX_VK_DUMP')}=={'RX_VK_DUMP_FILE','RX_VK_DUMP_DIR','RX_VK_DUMP_W','RX_VK_DUMP_ONLY_W'},'Unreviewed dump controls')
    started=time.monotonic();deadline=started+240;record={'plan_sha256':args.plan_sha256,'lease':args.lease,'pid':None,'diagnostic_complete':False};truncated=[False]
    proc=subprocess.Popen([str(case/'install/bin/hookshot')],cwd=case/'install',env=env,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,stdin=subprocess.DEVNULL,start_new_session=True)
    birth=proc_start(proc.pid);record.update(pid=proc.pid,proc_start=birth);write(observer/'launch.json',record)
    done=threading.Event();watchdog_result=[]
    def watchdog():
        if not done.wait(max(0,deadline-15-time.monotonic())):
            try:watchdog_result.append({'forced':stop_owned(proc,birth)})
            except Exception as exc:watchdog_result.append({'cleanup_failure':str(exc)})
    watcher=threading.Thread(target=watchdog,daemon=True);watcher.start()
    def drain():
        left=LOG_CAP
        with (observer/'process.log').open('xb') as output:
            while True:
                data=proc.stdout.read1(65536)
                if not data:break
                kept=min(left,len(data));output.write(data[:kept]);left-=kept;output.flush()
                if kept<len(data):truncated[0]=True
    reader=threading.Thread(target=drain,daemon=True);reader.start();first=None;triggered=False
    try:
        while time.monotonic()<deadline-20:
            require(proc.poll() is None,'Owned game exited')
            text=(observer/'process.log').read_text(errors='replace') if (observer/'process.log').exists() else ''
            require(not truncated[0],'Log cap reached; refusing to arm diagnostics')
            journal=observer/'history.jsonl'
            if triggered and journal.exists():
                data=bounded_read(journal,JOURNAL_CAP)
                if data.endswith(b'\n') and data.splitlines() and json.loads(data.splitlines()[-1]).get('event')=='end':
                    record['history']=validate_journal(data)
                    helper.request(case/'control.sock',{'cmd':'shot','path':str(observer/'after.png'),'timeout':10},12)
                    record['diagnostic_complete']=record['history']['complete_for_selected_width_metadata'];break
            if triggered:time.sleep(0.25);continue
            try:status=helper.request(case/'control.sock',{'cmd':'status'},1)
            except (OSError,RuntimeError):time.sleep(0.25);continue
            eligible=status.get('state')=='play' and not status.get('stalled',True) and not (status.get('game') or {}).get('loading',True) and status.get('map')=='KokiriForest_Child' and status.get('frame',0)>=1092
            if not eligible:first=None;time.sleep(0.25);continue
            if first is None:first=status['frame']
            if status['frame']-first<120 or not queues_ready(text) or not selected_arc(text):time.sleep(0.25);continue
            if not (observer/'before.png').exists():
                mapping,raw=helper.mapped_file_rows(proc.pid,{'renderer':plan['case_file_pins']['install/bin/hookshot_vk.so'],'driver':plan['dependencies']['driver']})
                require(all(mapping.values()),'Loaded renderer/driver identity differs');require(len(raw.encode())<=4<<20,'Maps observation exceeds bound');(observer/'maps.txt').write_text(raw)
                helper.check_process_environment(proc.pid,env)
                options=helper.request(case/'control.sock',{'cmd':'option_get'},10);helper.runtime_settings(options);write(observer/'options.json',options)
                before=helper.request(case/'control.sock',{'cmd':'shot','path':str(observer/'before.png'),'timeout':10},12);write(observer/'before-reply.json',before)
                write(observer/'awaiting-card-review.json',{'screenshot_sha256':validate_png(observer/'before.png'),'required_certificate':'card-review.json'})
            certificate=observer/'card-review.json'
            if not certificate.exists():time.sleep(0.25);continue
            validate_approval(json.loads(certificate.read_text()),sha(observer/'before.png'))
            if not triggered:
                (observer/'trigger').touch(exist_ok=False);triggered=True;record['trigger_status']=status
            time.sleep(0.25)
    except Exception as exc:record['failure']=str(exc)
    finally:
        # Removing the trigger prevents future arms; native caps handle an armed frame.
        (observer/'trigger').unlink(missing_ok=True)
        try:record['forced_owned_stop']=stop_owned(proc,birth)
        except Exception as exc:record['cleanup_failure']=str(exc)
        done.set();watcher.join(timeout=1)
        reader.join(timeout=3)
        record.update(exit_code=proc.returncode,owned_process_terminal=proc.poll() is not None,log_reader_settled=not reader.is_alive(),watchdog_settled=not watcher.is_alive(),watchdog=watchdog_result,log_truncated=truncated[0],elapsed_seconds=time.monotonic()-started)
        try:
            record['post_dependency_pins_match']=all(helper.identity(pin['path'])==pin for pin in plan['dependencies'].values())
            actual=dict(line.split('=',1) for line in (case/'install/hookshot_options.ini').read_text().splitlines() if '=' in line)
            record['all_settings_preserved']=actual==plan['settings']
            record['control_images']={name:validate_png(observer/name) for name in ('before.png','after.png') if (observer/name).exists()}
        except Exception as exc:record['postcheck_failure']=str(exc);record['post_dependency_pins_match']=False
        if not record.get('post_dependency_pins_match') or not record.get('all_settings_preserved') or not record['owned_process_terminal'] or not record['watchdog_settled'] or not record['log_reader_settled'] or truncated[0]:record['diagnostic_complete']=False
        if not record['diagnostic_complete']:record['incomplete_reason']=record.get('failure','No complete settled, reviewed, bounded metadata frame was admitted before the deadline')
        write(observer/'result.json',record)
    return record


def main():
    p=argparse.ArgumentParser(description=__doc__);sub=p.add_subparsers(dest='command',required=True)
    q=sub.add_parser('prepare')
    for name in ('pilot-plan','pilot-launcher','overlay-source','overlay-preparation','renderer','driver','icd','output'):q.add_argument('--'+name,type=Path,required=True)
    q=sub.add_parser('run');q.add_argument('--plan',type=Path,required=True);q.add_argument('--plan-sha256',required=True);q.add_argument('--lease',required=True)
    a=p.parse_args()
    try:print(json.dumps(prepare(a) if a.command=='prepare' else run(a),indent=2))
    except (OSError,ValueError) as e:print(str(e),file=sys.stderr);return 2
    return 0


if __name__=='__main__':raise SystemExit(main())
