#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Read explicitly closed bounded native windows; never certify FPS or scene identity."""
from __future__ import annotations
import argparse
from dataclasses import dataclass, replace
import hashlib
import json
from pathlib import Path
import struct
import sys
ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:sys.path.insert(0,str(ROOT))
from tools.benchmark_native_bridge import EVENT, HEADER, MAGIC, MAX_BYTES, MAX_EVENTS, NativeCapture, NativeEvent, SCOPE_UNKNOWN, _signed32, decode as decode_v1
MAGIC_WINDOW=b"OOBENCHWINDOW2\0\0"
WINDOW_HEADER=256
MAX_BASELINE=1024+2048+1

@dataclass(frozen=True)
class NativeWindow:
    capture: NativeCapture
    baseline: tuple[NativeEvent,...]
    begin_ns: int
    close_ns: int
    startup_flags: int
    startup_lost: int
    startup_events: tuple[int,...]
    boundary_parts: int

def decode(data: bytes) -> NativeWindow:
    if type(data) is not bytes or not WINDOW_HEADER<=len(data)<=WINDOW_HEADER+(MAX_EVENTS+MAX_BASELINE)*EVENT.size+MAX_BYTES:
        raise ValueError("bounded immutable closed window required")
    magic,abi,width,flags,lost,session,count,size=HEADER.unpack_from(data)
    base_count,state=struct.unpack_from("<II",data,64)
    begin,close,startup_flags,startup_lost=struct.unpack_from("<4Q",data,72)
    startup=struct.unpack_from("<16Q",data,104)
    boundary=struct.unpack_from("<Q",data,232)[0]
    if magic!=MAGIC_WINDOW or abi!=2 or width!=96 or state!=3 or not begin or close<begin or count<2 or count>MAX_EVENTS or base_count>MAX_BASELINE or size>MAX_BYTES or any(data[240:256]):
        raise ValueError("unsupported/incomplete native window header")
    if flags&0x1c1e or flags&~(SCOPE_UNKNOWN|((1<<14)-2)) or startup_flags&~((1<<14)-2) or startup_lost or startup_flags&0x181e:raise ValueError("unsupported flags or incomplete startup baseline")
    if len(data)!=WINDOW_HEADER+(base_count+count)*EVENT.size+size:
        raise ValueError("truncated native window")
    baseline=[]; previous=0; lives=set(); slots=set()
    for i in range(base_count):
        kind,result,serial,at,*tail=EVENT.unpack_from(data,WINDOW_HEADER+i*EVENT.size)
        values,offset,length=tuple(tail[:8]),tail[8],tail[9]
        if kind not in (17,18) or result or serial<=previous or at!=begin or offset or length:
            raise ValueError("invalid explicit baseline framing")
        if kind==17:
            if not values[0] or values[0] in lives or values[1] not in (1,2,3) or not values[2] or values[3] not in (0,1) or values[5] not in (0,1) or any(values[6:]):
                raise ValueError("invalid lifetime baseline")
            lives.add(values[0])
        else:
            if values[0]>0xffffffff or values[0] in slots or not values[1] or any(values[2:]):raise ValueError("invalid slot-generation baseline")
            slots.add(values[0])
        baseline.append(NativeEvent(kind,result,serial,at,values,b""));previous=serial
    offset=WINDOW_HEADER+base_count*EVENT.size
    rows=[EVENT.unpack_from(data,offset+i*EVENT.size) for i in range(count)]
    if rows[0][0]!=19 or rows[-1][0]!=20 or rows[0][1] or rows[-1][1] or rows[0][4]!=base_count or rows[0][5]!=startup_flags or rows[-1][5]!=base_count:
        raise ValueError("missing or inconsistent window begin/close marker")
    if rows[0][2]<=previous or rows[0][3]<begin or rows[-1][3]!=close or any(r[0] in (17,18,19,20) for r in rows[1:-1]) or any(r[-2:]!=(0,0) for r in (rows[0],rows[-1])):
        raise ValueError("invalid window boundary sequence")
    if any(a[2]>=b[2] or a[3]>b[3] for a,b in zip(rows,rows[1:])):raise ValueError("window event order/clock")
    if any(r[0] not in (*range(1,17),21) or (r[0]==21 and (r[1] or r[4]>0xffffffff or any(r[-2:]) or not r[5] or not r[6] or any(r[7:12]))) for r in rows[1:-1]):raise ValueError("unsupported/malformed window event")
    core=b"".join(data[offset+i*EVENT.size:offset+(i+1)*EVENT.size] for i,r in enumerate(rows) if r[0] in range(1,17))
    payload=data[offset+count*EVENT.size:]
    capture=decode_v1(HEADER.pack(MAGIC,1,96,flags,lost,session,sum(r[0] in range(1,17) for r in rows),size)+core+payload)
    extras=[NativeEvent(r[0],r[1],r[2],r[3],tuple(r[4:12]),b"") for r in rows if r[0]==21]
    capture=replace(capture,events=tuple(sorted((*capture.events,*extras),key=lambda e:e.serial)))
    capture=replace(capture,sha256=hashlib.sha256(data).hexdigest())
    window=NativeWindow(capture,tuple(baseline),begin,close,startup_flags,startup_lost,tuple(startup),boundary)
    report=summarize(window)
    if report['structural_errors'] or not report['native_presents'] or not report['parts'] or any(p['state']!='observed_complete' for p in report['parts']) or any(p['recording_completion']!='observed_complete' for p in report['native_presents']) or rows[-1][4]!=len(report['parts']):
        raise ValueError("closed window lacks exact recording/submission/completion closure")
    return window

def read(path: Path) -> NativeWindow:
    if path.is_symlink() or not path.is_file() or path.stat().st_size>WINDOW_HEADER+(MAX_EVENTS+MAX_BASELINE)*EVENT.size+MAX_BYTES:raise ValueError("window must be a private regular file")
    return decode(path.read_bytes())
def _summarize(capture, baseline) -> dict:
    """Replay observed state transitions only; missing links remain unknown.

    A file digest/session checks framing/identity, not origin or producer honesty.
    CPU poll timestamps are upper bounds, never exact GPU execution times.
    """
    lives={e.values[0]: {"kind":e.values[1],"generation":e.values[2],"active":bool(e.values[3]),"counter":e.values[4] if e.values[5] else None} for e in baseline if e.kind==17}
    slots={e.values[0]:e.values[1] for e in baseline if e.kind==18}
    origins, values, parts, polls, batch_results = {}, {}, {}, {}, {}
    streams, presents, results, gaps, coverage = [], [], [], [], []
    closed, generated, covers, native_runs = {}, {}, {}, {}
    if capture.lost:
        gaps.append("bounded producer lost events/bytes")
    if capture.flags & ~SCOPE_UNKNOWN:
        coverage.append("producer fault flags are set")
    for event in capture.events:
        v, kind = event.values, event.kind
        if kind == 21:
            if slots.get(v[0],0)+1!=v[1]:gaps.append("unselected slot generation missing")
            slots[v[0]]=v[1]
        elif kind == 1:
            if not v[0] or v[0] in lives or v[1] not in (1, 2, 3) or not v[2]:
                gaps.append("ambiguous object lifetime")
            lives[v[0]] = {"kind": v[1], "generation": v[2], "active": True,"counter":None}
        elif kind == 2:
            if v[0] not in lives:
                gaps.append("retirement has no creation")
            else:
                lives[v[0]]["active"] = False
        elif kind in (3, 4):
            obj = lives.get(v[1])
            if v[0] != event.serial or v[0] in origins or not obj or obj["kind"] != (1 if kind == 3 else 2) or not obj["active"]:
                gaps.append("ingress lifetime/identity unavailable")
            if obj and obj.get("counter") is not None and v[2]<=obj["counter"]:gaps.append("native serial does not advance baseline")
            if obj:obj["counter"]=v[2]
            origins[v[0]] = {"kind": kind, "object": v[1], "native_serial": v[2], "handles": v[4], "seen": set()}
            if kind == 4:
                presents.append({"origin": v[0], "window_lifetime": v[1], "native_present_serial": v[2],
                                 "texture_index": v[3], "crop": [_signed32(v[5] & 0xffffffff), _signed32(v[5] >> 32),
                                                                  _signed32(v[6] & 0xffffffff), _signed32(v[6] >> 32)],
                                 "observed_ingress_ns": event.at_ns})
        elif kind == 5:
            origin = origins.get(v[0])
            if not origin or origin["kind"] != 3 or v[1] >= origin["handles"] or v[1] in origin["seen"]:
                gaps.append("owned stream has missing/duplicate ingress edge")
            else:
                origin["seen"].add(v[1])
            streams.append({"origin": v[0], "handle_index": v[1], "owned_generation": v[3],
                            "size": len(event.payload), "sha256": hashlib.sha256(event.payload).hexdigest()})
        elif kind == 6:
            if v[0] not in origins or v[1] in values:
                gaps.append("value has missing/duplicate origin")
            values[v[1]] = v[0]
        elif kind == 7:
            if not v[0] or v[0] in parts or slots.get(v[1], 0) + 1 != v[2] or values.get(v[3]) != v[4]:
                gaps.append("recording slot generation or value ancestry missing")
            slots[v[1]] = v[2]
            parts[v[0]] = {"part": v[0], "slot": v[1], "slot_generation": v[2], "value": v[3], "origin": v[4],
                           "state": "new", "rec_seq": 0, "timeline_lifetime": 0, "batch": 0}
        elif kind == 8:
            p, life = parts.get(v[0]), lives.get(v[2])
            collision = any(other is not p and other["rec_seq"] == v[1] and other["timeline_lifetime"] == v[2]
                            for other in parts.values())
            if not p or p["state"] != "new" or not v[1] or not life or life["kind"] != 3 or collision:
                gaps.append("recording plan mapping unavailable")
            else:
                p.update(state="planned", rec_seq=v[1], timeline_lifetime=v[2])
        elif kind == 9:
            p = parts.get(v[0])
            if not p or p["state"] != "planned" or not v[1]:
                gaps.append("held batch mapping unavailable")
            else:
                p.update(state="held", batch=v[1])
        elif kind == 11:
            members=[p for p in parts.values() if p['state']=='held' and p['batch']==v[0]]
            if not members or len(members)!=v[2] or max(p['rec_seq'] for p in members)>v[1]:gaps.append("actual batch result lacks selected held subset")
            for p in members:batch_results[p['part']]=(v[0],event.result)
        elif kind == 10:
            p = parts.get(v[0])
            results.append({"part": v[0], "rec_seq": v[1], "timeline_lifetime": v[2], "origin": v[3],
                            "batch": v[4], "vk_result": event.result, "observed_submit_result_ns": event.at_ns})
            if p and p["state"]=="held" and batch_results.get(p["part"])!=(p["batch"],event.result):gaps.append("held result lacks actual batch result")
            if not p or p["state"] not in ("planned", "held") or (p["rec_seq"], p["timeline_lifetime"], p["origin"], p["batch"]) != (v[1], v[2], v[3], v[4]):
                gaps.append("submit result lacks exact part/sequence ancestry")
            else:
                p["state"] = "submitted" if event.result == 0 else "failed"
        elif kind == 12:
            life=lives.get(v[0])
            if not life or life['kind']!=3 or not life['active']:gaps.append("poll has no active exact timeline lifetime")
            elif event.result==0:
                if life.get('counter') is not None and v[1]<life['counter']:gaps.append("timeline counter regressed from baseline")
                life['counter']=v[1]
            key = (v[0], v[2])
            if key in polls:
                gaps.append("duplicate native timeline query")
            polls[key] = (event.result, v[1], event.at_ns)
        elif kind == 13:
            p, poll = parts.get(v[0]), polls.get((v[2], v[3]))
            if not p or not poll or p["state"] != "submitted" or poll[0] != 0 or poll[1] < v[1] or (p["rec_seq"], p["timeline_lifetime"], p["origin"]) != (v[1], v[2], v[4]):
                gaps.append("completion lacks submitted exact-rec_timeline query")
            else:
                p.update(state="observed_complete", completion_poll=v[3], observed_complete_ns=event.at_ns)
        elif kind == 14:
            coverage.append("producer reports unknown reason " + str(event.result))
        elif kind == 15 and v[2] in (1, 2, 3):
            run=native_runs.setdefault(v[0], {"starts":0,"stops":0,"settled":False,"running":False,"value":v[1]})
            if origins.get(v[0],{}).get("kind")!=3 or run["value"]!=v[1]:gaps.append("native run value/origin mismatch")
            if v[2]==1:
                if run["running"] or run["settled"]:gaps.append("native run duplicate start after running/settled")
                run["starts"]+=1;run["running"]=True
            else:
                if not run["running"]:gaps.append("native run end lacks a running start")
                run["stops"]+=1;run["running"]=False
                if v[2]==3:run["settled"]=True
        elif kind == 15 and v[2] == 4:
            origin_id, part_id = v[0], v[1]
            if not origin_id or not part_id:
                gaps.append("coverage link is missing origin or part")
            else:
                rows = covers.setdefault(origin_id, [])
                if part_id not in rows:
                    rows.append(part_id)
                claimed = parts.get(part_id)
                if not claimed:
                    gaps.append("coverage link names a missing recording part")
                elif v[5] and claimed["slot_generation"] != v[5]:
                    gaps.append("coverage link generation does not match the recording part")
                elif v[4] and claimed["slot"] != v[4]:
                    gaps.append("coverage link slot does not match the recording part")
        elif kind == 15 and v[2] in (5, 6):
            generated[v[0]] = "observed_active" if v[2] == 6 else "observed_off"
        elif kind == 16:
            if v[0] in closed or origins.get(v[0], {}).get("kind") != 4 or values.get(v[1]) != v[0]:
                gaps.append("present recording closure has missing/duplicate ancestry")
            closed[v[0]] = {"value": v[1], "last_part": v[2], "count": v[3], "closed_at_ns": event.at_ns}
    for origin_id, origin in origins.items():
        if origin["kind"] == 3 and len(origin["seen"]) != origin["handles"]:
            gaps.append("post-resolve owned handle coverage incomplete")
        if origin["kind"] == 3:
            run=native_runs.get(origin_id)
            if run and (not run["settled"] or run["running"] or run["starts"]!=run["stops"]):gaps.append("native replay has not settled its final recording links")
            if not run:coverage.append("native replay lifecycle unobserved for legacy fixture")
            owned = [p for p in parts.values() if p["origin"] == origin_id]
            linked = covers.get(origin_id)
            if linked is None:
                if not owned:
                    gaps.append("submit lacks its own recording part or a completed shared-recording coverage link")
            else:
                owned_ids = {p["part"] for p in owned}
                if not set(linked) or not owned_ids.issubset(set(linked)):
                    gaps.append("coverage link set omits an owned recording part")
                for part_id in linked:
                    shared = parts.get(part_id)
                    if not shared or shared["state"] != "observed_complete":
                        gaps.append("coverage link set is not fully observed complete")
    for present in presents:
        origin = present["origin"]
        closure = closed.get(origin)
        linked = [p for p in parts.values() if p["origin"] == origin]
        present["recording_parts"] = [p["part"] for p in linked]
        present["frame_generation"] = generated.get(origin, "unobserved")
        present["recording_completion"] = "pending_or_unassociated"
        if closure:
            present["recording_closed_at_ns"] = closure["closed_at_ns"]
            if closure["count"] != len(linked) or not linked or closure["last_part"] != max(p["part"] for p in linked):
                gaps.append("present part set differs from closed native recording")
            elif all(p["state"] == "observed_complete" for p in linked):
                present["recording_completion"] = "observed_complete"
                present["observed_recording_complete_ns"] = max(p["observed_complete_ns"] for p in linked)
    return {"protocol": "open-ocarina-native-window-v2", "capture_sha256": capture.sha256,
            "session": capture.session.hex(), "events": len(capture.events), "flags": capture.flags, "lost": capture.lost,
            "owned_streams": streams, "native_presents": presents, "parts": list(parts.values()), "submit_results": results,
            "coverage_links": {str(origin): rows for origin, rows in covers.items()},
            "native_run_lifecycle": {str(origin): run for origin,run in native_runs.items()},
            "all_native_runs_settled": bool(native_runs) and all(origin in native_runs and native_runs[origin]["settled"] and not native_runs[origin]["running"] and native_runs[origin]["starts"]==native_runs[origin]["stops"] for origin,o in origins.items() if o["kind"]==3),
            "structural_errors": sorted(set(gaps)),
            "unresolved": sorted(set(gaps + coverage + ["primary camera/scene association unimplemented", "effective memory/input/dependency coverage incomplete",
                                             "source atomicity and externally audited producer identity unestablished", "no rendering or FPS certification"])),
            "timing_scope": "observed CPU event/poll timestamps only; no exact GPU execution time or native FPS"}


def summarize(window: NativeWindow) -> dict:
    report=_summarize(window.capture,window.baseline)
    report.update(begin_ns=window.begin_ns,close_ns=window.close_ns,
                  baseline_lifetimes=[{'lifetime':e.values[0],'kind':e.values[1],'generation':e.values[2],'active':bool(e.values[3]),'last_native_serial_or_counter':e.values[4],'counter_observed':bool(e.values[5])} for e in window.baseline if e.kind==17],
                  baseline_slots=[{'slot':e.values[0],'generation':e.values[1]} for e in window.baseline if e.kind==18],
                  startup={'flags':window.startup_flags,'lost':window.startup_lost,'observed_event_hook_counts':list(window.startup_events),'command_bytes_retained':False,'part_dependency_history_retained':False},
                  unselected_boundary_part_starts=window.boundary_parts)
    report['unresolved']=sorted(set(report['unresolved']+['startup command and pending dependency history not retained; baseline facts are not earlier event history','begin/close are controller CPU boundaries, not a whole-frame or scene isolation guarantee']))
    return report

def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('capture',type=Path);args=parser.parse_args()
    try:print(json.dumps(summarize(read(args.capture)),indent=2))
    except (OSError,ValueError) as exc:parser.exit(1,str(exc)+'\n')
if __name__=='__main__':main()
