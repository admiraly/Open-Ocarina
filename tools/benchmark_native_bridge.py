#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Read bounded native bridge v1 diagnostic events; never certify a benchmark."""
from __future__ import annotations

import argparse
from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
import struct

MAGIC = b"OOBENCHNATIVE1\0\0"
HEADER = struct.Struct("<16sIIQQ16sII")
EVENT = struct.Struct("<Ii10QII")
SCOPE_UNKNOWN = 15 << 48
MAX_EVENTS, MAX_BYTES = 4096, 8 * 1024 * 1024
KINDS = {1: "lifetime", 2: "retired", 3: "submit_ingress", 4: "present_ingress", 5: "owned_stream",
         6: "value_binding", 7: "part_begin", 8: "part_plan", 9: "held_batch", 10: "submit_result",
         11: "batch_result", 12: "timeline_poll", 13: "part_complete", 14: "unknown", 15: "run_stage", 16: "present_recording_closed"}


@dataclass(frozen=True)
class NativeEvent:
    kind: int
    result: int
    serial: int
    at_ns: int
    values: tuple[int, ...]
    payload: bytes


@dataclass(frozen=True)
class NativeCapture:
    flags: int
    lost: int
    session: bytes
    events: tuple[NativeEvent, ...]
    sha256: str


def decode(data: bytes) -> NativeCapture:
    if type(data) is not bytes or not HEADER.size <= len(data) <= HEADER.size + MAX_EVENTS * EVENT.size + MAX_BYTES:
        raise ValueError("bounded immutable native capture required")
    magic, version, record_size, flags, lost, session, count, size = HEADER.unpack_from(data)
    if magic != MAGIC or version != 1 or record_size != 96 or not any(session):
        raise ValueError("unsupported native bridge header/session")
    if count > MAX_EVENTS or size > MAX_BYTES or len(data) != HEADER.size + count * EVENT.size + size:
        raise ValueError("truncated/oversized native capture")
    if flags & SCOPE_UNKNOWN != SCOPE_UNKNOWN:
        raise ValueError("native bridge cannot clear unimplemented coverage scope")
    payload_start, used, previous, previous_time = HEADER.size + count * EVENT.size, 0, 0, 0
    events = []
    for index in range(count):
        row = EVENT.unpack_from(data, HEADER.size + index * EVENT.size)
        kind, result, serial, at_ns, *rest = row
        values, offset, length = tuple(rest[:8]), rest[8], rest[9]
        if kind not in KINDS or serial <= previous or at_ns < previous_time:
            raise ValueError("invalid native event kind/order/clock")
        if offset > size or length > size - offset:
            raise ValueError("native payload range escapes owned bytes")
        if kind == 5:
            if length < 16 or length > MAX_BYTES or offset != used or values[2] != length or values[3] != values[0]:
                raise ValueError("owned recording payload/generation mismatch")
            used += length
        elif offset or length:
            raise ValueError("unexpected payload on native metadata event")
        events.append(NativeEvent(kind, result, serial, at_ns, values, data[payload_start + offset:payload_start + offset + length]))
        previous, previous_time = serial, at_ns
    if used != size:
        raise ValueError("unreferenced or overlapping owned payload")
    return NativeCapture(flags, lost, session, tuple(events), hashlib.sha256(data).hexdigest())


def read(path: Path) -> NativeCapture:
    if path.is_symlink() or not path.is_file() or path.stat().st_size > HEADER.size + MAX_EVENTS * EVENT.size + MAX_BYTES:
        raise ValueError("native capture must be a bounded regular file")
    return decode(path.read_bytes())


def _signed32(value):
    return value - (1 << 32) if value & (1 << 31) else value


def summarize(capture: NativeCapture) -> dict:
    """Replay observed state transitions only; missing links remain unknown.

    A file digest/session checks framing/identity, not origin or producer honesty.
    CPU poll timestamps are upper bounds, never exact GPU execution times.
    """
    lives, origins, values, parts, polls, slots = {}, {}, {}, {}, {}, {}
    streams, presents, results, gaps = [], [], [], []
    closed, generated = {}, {}
    if capture.lost:
        gaps.append("bounded producer lost events/bytes")
    if capture.flags & ~SCOPE_UNKNOWN:
        gaps.append("producer fault flags are set")
    for event in capture.events:
        v, kind = event.values, event.kind
        if kind == 1:
            if not v[0] or v[0] in lives or v[1] not in (1, 2, 3) or not v[2]:
                gaps.append("ambiguous object lifetime")
            lives[v[0]] = {"kind": v[1], "generation": v[2], "active": True}
        elif kind == 2:
            if v[0] not in lives:
                gaps.append("retirement has no creation")
            else:
                lives[v[0]]["active"] = False
        elif kind in (3, 4):
            obj = lives.get(v[1])
            if v[0] != event.serial or v[0] in origins or not obj or obj["kind"] != (1 if kind == 3 else 2) or not obj["active"]:
                gaps.append("ingress lifetime/identity unavailable")
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
        elif kind == 10:
            p = parts.get(v[0])
            results.append({"part": v[0], "rec_seq": v[1], "timeline_lifetime": v[2], "origin": v[3],
                            "batch": v[4], "vk_result": event.result, "observed_submit_result_ns": event.at_ns})
            if not p or p["state"] not in ("planned", "held") or (p["rec_seq"], p["timeline_lifetime"], p["origin"], p["batch"]) != (v[1], v[2], v[3], v[4]):
                gaps.append("submit result lacks exact part/sequence ancestry")
            else:
                p["state"] = "submitted" if event.result == 0 else "failed"
        elif kind == 12:
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
            gaps.append("producer reports unknown reason " + str(event.result))
        elif kind == 15 and v[2] in (5, 6):
            generated[v[0]] = "observed_active" if v[2] == 6 else "observed_off"
        elif kind == 16:
            if v[0] in closed or origins.get(v[0], {}).get("kind") != 4 or values.get(v[1]) != v[0]:
                gaps.append("present recording closure has missing/duplicate ancestry")
            closed[v[0]] = {"value": v[1], "last_part": v[2], "count": v[3], "closed_at_ns": event.at_ns}
    for origin in origins.values():
        if origin["kind"] == 3 and len(origin["seen"]) != origin["handles"]:
            gaps.append("post-resolve owned handle coverage incomplete")
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
    return {"protocol": "open-ocarina-native-bridge-v1", "capture_sha256": capture.sha256,
            "session": capture.session.hex(), "events": len(capture.events), "flags": capture.flags, "lost": capture.lost,
            "owned_streams": streams, "native_presents": presents, "parts": list(parts.values()), "submit_results": results,
            "unresolved": sorted(set(gaps + ["primary camera/scene association unimplemented", "effective memory/input/dependency coverage incomplete",
                                             "source atomicity and externally audited producer identity unestablished", "no rendering or FPS certification"])),
            "timing_scope": "observed CPU event/poll timestamps only; no exact GPU execution time or native FPS"}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("capture", type=Path)
    args = parser.parse_args()
    try:
        print(json.dumps(summarize(read(args.capture)), indent=2))
    except (OSError, ValueError) as exc:
        parser.exit(1, str(exc) + "\n")


if __name__ == "__main__":
    main()
