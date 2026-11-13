#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Decode a bounded pinned Hookshot record subset into CPU observer inputs.

No native producer is bundled. Input bytes/events remain externally supplied
observations: immutable ownership is not historical atomicity or trustworthy GPU
provenance. Uniforms are candidates only; no scene/frame/FPS certificate is made.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import IntEnum
import hashlib
from pathlib import Path
import struct
import subprocess
import sys
import tempfile

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tools.benchmark_observer import (
    Coverage, Field, FieldKind, FrameReceipt, FrameToken, ObservationError, Observer,
    ReadResult, Record, RecordKind, ResourceUse, Source, Stamp, State, SubmissionKind,
    SubmissionToken, extent, integer, opaque,
)

PIN = "4cf785a0b143bb34026506c9a3fe4f4bb93c797d"
HEADER_SHA256 = "8a693687bc3318a999b4c4b78c838056b9bf202a40ab35f74c9ac6517c6121a8"
OBSERVER_SHA256 = "314d491f60559ee5ec829e873938a7dba52f6337c39967f5ed2737be0a59d33e"
PUBLIC_SOURCE_SHA256 = {
    "gpu/render/rx_nvn.h": HEADER_SHA256,
    "gpu/render/nvn_api.h": "f40e44b4b63aa20892399547167d367a652c1ed0793ca4a0f9299a8988289b82",
    "host/nvn_native.c": "209f21d3bc20c38dae34bbb1c71ed3520bf56930423678739b344ed64f729be0",
    "gpu/vk/vk_replay.cpp": "a971dbb387cf67bb3c809da5505d014339f051a412d367f5a757330b602b5163",
    "gpu/vk/vk_temporal.cpp": "e33ba36890098d9a8465a5147a37a7fa7ae76b3ac12b506e6f5ac530cf4fef13",
    "gpu/vk/vk_draw.cpp": "e0f809aaaa88f1ff945de1c5a082a3c417f7ae62357caa652716f9bf32473199",
}
MAX_STREAM = 1024 * 1024
MAX_COMMANDS = 4096
MAX_RANGES = 128
MAX_OWNED_BYTES = 2 * 1024 * 1024
CANDIDATE_SIZE = 0x230


class Op(IntEnum):
    END = 0
    BEGIN = 2
    UPDATE_UB = 7
    VIEWPORTS = 11
    SCISSOR = 12
    DEPTH_RANGE = 13
    BIND_UBO = 19
    DRAW = 25
    DISPATCH = 27


@dataclass(frozen=True)
class Command:
    ordinal: int
    offset: int
    op: Op
    payload: bytes


# Payload struct sizes from rx_nvn.h at PIN, x86-64 C ABI. Record padding is
# consumed but never treated as semantic payload. UPDATE_UB is variable length.
_PAYLOAD = {Op.BEGIN: 0, Op.END: 0, Op.VIEWPORTS: 264, Op.SCISSOR: 16,
            Op.DEPTH_RANGE: 8, Op.BIND_UBO: 24, Op.DRAW: 20, Op.DISPATCH: 16}


def decode_stream(data: bytes) -> tuple[Command, ...]:
    if type(data) is not bytes or not 16 <= len(data) <= MAX_STREAM:
        raise ObservationError(State.INVALID, "bounded immutable stream required")
    result, at = [], 0
    while at < len(data):
        if len(result) >= MAX_COMMANDS or len(data) - at < 8:
            raise ObservationError(State.INVALID, "command limit or truncated header")
        raw_op, size = struct.unpack_from("<II", data, at)
        if size < 8 or size % 8 or size > len(data) - at:
            raise ObservationError(State.INVALID, "invalid aligned record extent")
        try:
            op = Op(raw_op)
        except ValueError as exc:
            raise ObservationError(State.UNKNOWN, f"unsupported native opcode {raw_op}") from exc
        payload = data[at + 8:at + size]
        if op is Op.UPDATE_UB:
            if len(payload) < 16:
                raise ObservationError(State.INVALID, "truncated inline-update header")
            _, length = struct.unpack_from("<QQ", payload)
            if length > 0x10000 or size != ((24 + length + 7) & ~7):
                raise ObservationError(State.INVALID, "invalid native inline-update extent")
            payload = payload[:16 + length]
        else:
            needed = _PAYLOAD[op]
            if size != ((8 + needed + 7) & ~7):
                raise ObservationError(State.INVALID, "payload size differs from pinned native ABI")
            payload = payload[:needed]
        if (not result and op is not Op.BEGIN) or (result and op is Op.BEGIN):
            raise ObservationError(State.INVALID, "one leading native BEGIN required")
        result.append(Command(len(result), at, op, payload))
        at += size
        if op is Op.END:
            if at != len(data):
                raise ObservationError(State.INVALID, "bytes after native END")
            return tuple(result)
    raise ObservationError(State.INVALID, "missing native END")


@dataclass(frozen=True)
class Origin:
    session: bytes        # Fresh process/capture nonce, never a comparison field.
    ingress_serial: int    # Proposed global ingress event serial; not q->submits or a slot.
    generation: int      # Owned snapshot epoch, not proof of source atomicity.

    def __post_init__(self):
        if type(self.session) is not bytes or len(self.session) != 16 or not any(self.session):
            raise ValueError("nonzero 16-byte session required")
        for value in (self.ingress_serial, self.generation):
            integer(value, 64)
            if not value:
                raise ValueError("positive origin serial/generation required")


@dataclass(frozen=True)
class OwnedRange:
    origin: Origin
    address: int
    backing: int | str
    backing_offset: int
    data: bytes
    source: Source


@dataclass(frozen=True)
class SubmitCopy:
    origin: Origin
    native_queue: int | str  # Private original queue identity, never serialized.
    copied_at_ns: int
    stream: bytes
    ranges: tuple[OwnedRange, ...]


@dataclass(frozen=True)
class RecordingSignal:
    """Required source event AFTER successful non-batched vkQueueSubmit2.

    This is proposed producer data, not an existing Hookshot capture format.
    Exactly one owned stream (or one native present) per recording is supported.
    Native recording part and rec_seq are different from its ring slot/r.value.
    """
    origin: Origin
    stream_sha256: bytes  # Module-computed stream digest; empty only for present.
    timeline_lifetime: int | str  # Original rec_timeline semaphore lifetime.
    part: int
    rec_seq: int
    submitted_at_ns: int
    vk_result: int


@dataclass(frozen=True)
class PresentCopy:
    origin: Origin
    window_lifetime: int | str
    native_present_serial: int  # nvn_win::presents + 1 at native present ingress.
    texture_index: int
    crop: tuple[int, int, int, int]
    queued_at_ns: int


@dataclass(frozen=True)
class TimelinePoll:
    session: bytes
    timeline_lifetime: int | str
    serial: int
    counter: int
    observed_at_ns: int
    vk_result: int


@dataclass(frozen=True)
class UniformCandidate:
    submission: SubmissionToken
    command_ordinal: int
    stream_offset: int
    read: ReadResult
    view_projection_words: tuple[int, ...]
    projection_words: tuple[int, ...]
    size_words: tuple[int, int]
    jitter_words: tuple[int, int]
    layout_discrepancies: tuple[str, ...]
    # No primary camera/scene/present association is inferred from these offsets.


@dataclass(frozen=True)
class AttachedStream:
    token: SubmissionToken
    origin: Origin
    stream_sha256: bytes
    candidates: tuple[UniformCandidate, ...]


@dataclass(frozen=True)
class FrameObservation:
    bookkeeping: FrameReceipt
    native_present: PresentCopy
    candidates: tuple[UniformCandidate, ...]
    unresolved: tuple[str, ...]


@dataclass
class _Signal:
    frame: FrameToken
    token: SubmissionToken
    event: RecordingSignal
    completion_ns: int | None = None


class ReplayAdapter:
    """Ordered consumer for authored/proposed events, never a trusted native producer.

    Unknown/unsupported input latches adapter failure. Core observer is unchanged.
    Registry-independent copied bytes cannot establish historical ownership.
    """
    def __init__(self, observer: Observer, session: bytes):
        Origin(session, 1, 1)
        self.observer, self.session = observer, session
        self._signals: dict[SubmissionToken, _Signal] = {}
        self._origins: set[int] = set()
        self._parts: set[int] = set()
        self._candidates: dict[FrameToken, list[UniformCandidate]] = {}
        self._polls: dict[int | str, TimelinePoll] = {}
        self._presents: dict[FrameToken, PresentCopy] = {}
        self._windows: dict[int | str, int] = {}
        self._failure: str | None = None

    def _check(self):
        if self._failure is not None:
            raise ObservationError(State.UNKNOWN, self._failure)

    def _fail(self, frame, reason):
        self._failure = self._failure or reason
        self.observer.unknown(frame, self._failure)

    def _signal(self, frame, event, stream_digest):
        self._check()
        if type(event) is not RecordingSignal or type(event.origin) is not Origin:
            self._fail(frame, "owned recording signal event required")
        for value in (event.part, event.rec_seq):
            integer(value, 64)
            if value == 0:
                self._fail(frame, "positive native recording part/rec_seq required")
        integer(event.submitted_at_ns, 64)
        integer(event.vk_result, 32, signed=True)
        opaque(event.timeline_lifetime)
        if (event.origin.session != self.session or event.origin.ingress_serial in self._origins or
            event.part in self._parts or type(event.stream_sha256) is not bytes or
            event.stream_sha256 != stream_digest):
            self._fail(frame, "stale/reused origin, recording part or stream identity")
        if event.vk_result != 0:
            self._fail(frame, "native vkQueueSubmit2 did not succeed")
        previous = [s.event for s in self._signals.values() if s.event.timeline_lifetime == event.timeline_lifetime]
        if previous and (event.rec_seq <= previous[-1].rec_seq or event.submitted_at_ns < previous[-1].submitted_at_ns):
            self._fail(frame, "nonmonotonic native rec_timeline submission")
        last_poll = self._polls.get(event.timeline_lifetime)
        if last_poll and event.submitted_at_ns <= last_poll.observed_at_ns:
            self._fail(frame, "recording arrived after an already consumed completion observation")
        self._origins.add(event.origin.ingress_serial)
        self._parts.add(event.part)

    def attach(self, frame: FrameToken, copied: SubmitCopy, signal: RecordingSignal,
               dependencies: tuple[SubmissionToken, ...] = ()) -> AttachedStream:
        self._check()
        try:
            if type(copied) is not SubmitCopy or type(copied.origin) is not Origin:
                self._fail(frame, "owned submit copy required")
            integer(copied.copied_at_ns, 64)
            opaque(copied.native_queue)
            if copied.origin != signal.origin or copied.copied_at_ns > signal.submitted_at_ns:
                self._fail(frame, "copy/recording origin or timing mismatch")
            commands = decode_stream(copied.stream)
            digest = hashlib.sha256(copied.stream).digest()
            self._signal(frame, signal, digest)
            if type(copied.ranges) is not tuple or len(copied.ranges) > MAX_RANGES:
                self._fail(frame, "bounded owned range tuple required")
            if sum(len(r.data) for r in copied.ranges) > MAX_OWNED_BYTES:
                self._fail(frame, "owned byte capacity exceeded")
            token = self.observer.reserve(frame)
            stamp = Stamp(token.value, copied.origin.generation)
            coverage = Coverage(stamp, max_bytes=MAX_OWNED_BYTES, max_read_bytes=0x10000)
            for owned in copied.ranges:
                if (type(owned) is not OwnedRange or owned.origin != copied.origin or
                    type(owned.data) is not bytes or type(owned.source) is not Source):
                    self._fail(frame, "range generation/origin or immutable ownership mismatch")
                coverage.mapping(owned.address, len(owned.data), owned.backing, owned.backing_offset)
            # Deduplicate exact backing aliases. Conflicting copied values are unknown,
            # never independent allocations or an arbitrary first/last observation.
            prior = []
            for owned in copied.ranges:
                extent(owned.backing_offset, len(owned.data))
                left, end = owned.backing_offset, owned.backing_offset + len(owned.data)
                if owned.source is not Source.SNAPSHOT:
                    self._fail(frame, "range is not an owned snapshot: " + owned.source.value)
                peers = [p for p in prior if p.backing == owned.backing]
                cuts = sorted({left, end} | {max(left, p.backing_offset) for p in peers if p.backing_offset < end and left < p.backing_offset + len(p.data)} |
                              {min(end, p.backing_offset + len(p.data)) for p in peers if p.backing_offset < end and left < p.backing_offset + len(p.data)})
                for a, b in zip(cuts, cuts[1:]):
                    existing = next((p for p in peers if p.backing_offset <= a and b <= p.backing_offset + len(p.data)), None)
                    data = owned.data[a - left:b - left]
                    if existing:
                        if data != existing.data[a - existing.backing_offset:b - existing.backing_offset]:
                            self._fail(frame, "conflicting owned alias bytes")
                    else:
                        coverage.capture(owned.address + a - left, data, stamp, Source.SNAPSHOT)
                prior.append(owned)
            for command in commands:
                if command.op is Op.UPDATE_UB:
                    address, length = struct.unpack_from("<QQ", command.payload)
                    if length:
                        coverage.overlay(command.ordinal, address, command.payload[16:], stamp)
            coverage.seal()
            records, candidates = self._records(commands, coverage, stamp, token)
            self.observer.enqueue(token, signal.timeline_lifetime, signal.rec_seq,
                                  SubmissionKind.WORK, len(records), dependencies)
            for ordinal, record in enumerate(records):
                self.observer.observe(token, ordinal, record)
            self._signals[token] = _Signal(frame, token, signal)
            self._candidates.setdefault(frame, []).extend(candidates)
            return AttachedStream(token, copied.origin, digest, tuple(candidates))
        except ObservationError as exc:
            if self._failure is None:
                self._fail(frame, exc.reason)
            raise
        except (ValueError, TypeError, AttributeError, struct.error) as exc:
            self._fail(frame, "invalid native stream/event: " + str(exc))

    @staticmethod
    def _records(commands, coverage, stamp, token):
        binding = None
        viewport = scissor = depth = None
        records, candidates = [], []
        for command in commands:
            op, p, ordinal = command.op, command.payload, command.ordinal
            fields = [Field(0, FieldKind.U32, op.value)]
            resources = []
            kind = RecordKind.EVENT
            if op is Op.BIND_UBO:
                stage, index, address, size = struct.unpack("<iiQQ", p)
                if not 0 <= stage < 8 or not 0 <= index < 16:
                    raise ObservationError(State.UNKNOWN, "unsupported UBO stage/index")
                fields.extend((Field(1, FieldKind.I32, stage), Field(2, FieldKind.I32, index), Field(3, FieldKind.U64, size)))
                if stage == index == 0:
                    binding = (address, size)
            elif op is Op.UPDATE_UB:
                address, length = struct.unpack_from("<QQ", p)
                kind = RecordKind.INLINE_UPDATE
                fields.append(Field(1, FieldKind.HASHED_BYTES, p[16:]))
                if length:
                    resources.append(ResourceUse(0, coverage.read(address, length, stamp, ordinal)))
            elif op is Op.VIEWPORTS:
                first, count = struct.unpack_from("<ii", p)
                if first < 0 or count < 0 or first + count > 16:
                    raise ObservationError(State.INVALID, "invalid native viewport interval")
                words = struct.unpack_from("<" + "I" * (4 * count), p, 8)
                fields.extend((Field(1, FieldKind.I32, first), Field(2, FieldKind.I32, count)))
                fields.extend(Field(10 + i, FieldKind.F32_BITS, w) for i, w in enumerate(words))
                if first == 0 and count:
                    viewport = words[:4]
            elif op is Op.SCISSOR:
                scissor = struct.unpack("<4i", p)
                fields.extend(Field(1 + i, FieldKind.I32, v) for i, v in enumerate(scissor))
            elif op is Op.DEPTH_RANGE:
                depth = struct.unpack("<2I", p)
                fields.extend(Field(1 + i, FieldKind.F32_BITS, w) for i, w in enumerate(depth))
            elif op is Op.DRAW:
                if binding is None or binding[1] < CANDIDATE_SIZE or any(x is None for x in (viewport, scissor, depth)):
                    raise ObservationError(State.UNKNOWN, "draw lacks explicit candidate UBO/viewport/scissor/depth state")
                read = coverage.read(binding[0], CANDIDATE_SIZE, stamp, ordinal)
                data = read.require().data
                vp = struct.unpack_from("<16I", data, 0x30)
                projection = struct.unpack_from("<16I", data, 0x70)
                size_words = struct.unpack_from("<2I", data, 0x120)
                jitter = (projection[2] ^ 0x80000000, projection[6] ^ 0x80000000)
                row = struct.unpack_from("<4f", data, 0xa0)
                discrepancies = () if row == (0.0, 0.0, -1.0, 0.0) else ("source perspective-row pattern absent",)
                candidates.append(UniformCandidate(token, ordinal, command.offset, read, vp, projection, size_words, jitter, discrepancies))
                kind = RecordKind.DRAW
                fields.extend(Field(1 + i, FieldKind.I32, v) for i, v in enumerate(struct.unpack("<5i", p)))
                fields.extend(Field(100 + i, FieldKind.F32_BITS, w) for i, w in enumerate(vp + projection + size_words + jitter + viewport + depth))
                fields.extend(Field(200 + i, FieldKind.I32, v) for i, v in enumerate(scissor))
                # Only source-defined candidate matrix/size spans enter this record.
                # Other UBO bytes may be padding/pointers/other state and are unclassified.
                resources.extend((ResourceUse(0, coverage.read(binding[0] + 0x30, 0x80, stamp, ordinal)),
                                  ResourceUse(1, coverage.read(binding[0] + 0x120, 8, stamp, ordinal))))
            elif op is Op.DISPATCH:
                x, y, z, _padding = struct.unpack("<4i", p)
                if min(x, y, z) < 0:
                    raise ObservationError(State.UNKNOWN, "negative dispatch dimensions unsupported")
                kind = RecordKind.DISPATCH
                fields.extend(Field(1 + i, FieldKind.U32, v) for i, v in enumerate((x, y, z)))
            records.append(Record(kind, tuple(fields), tuple(resources)))
        return records, candidates

    def present(self, frame: FrameToken, copied: PresentCopy, signal: RecordingSignal,
                dependencies: tuple[SubmissionToken, ...]) -> SubmissionToken:
        self._check()
        try:
            if type(copied) is not PresentCopy or copied.origin != signal.origin:
                self._fail(frame, "owned native-present origin required")
            opaque(copied.window_lifetime)
            integer(copied.native_present_serial, 64)
            integer(copied.texture_index, 32)
            integer(copied.queued_at_ns, 64)
            if type(copied.crop) is not tuple or len(copied.crop) != 4:
                self._fail(frame, "complete native-present crop required")
            for value in copied.crop:
                integer(value, 32, signed=True)
            if copied.queued_at_ns > signal.submitted_at_ns:
                self._fail(frame, "native present ingress follows its GPU submit")
            previous = self._windows.get(copied.window_lifetime, 0)
            if copied.native_present_serial <= previous:
                self._fail(frame, "stale/reused native window-present serial")
            self._signal(frame, signal, b"")
            token = self.observer.reserve(frame)
            self.observer.enqueue(token, signal.timeline_lifetime, signal.rec_seq,
                                  SubmissionKind.PRESENT, 1, dependencies)
            # Producer event tag, not a native NVN opcode. Raw window handles,
            # global counters and timestamps are excluded from comparison bytes.
            fields = (Field(0, FieldKind.U32, 0x10000), Field(1, FieldKind.U32, copied.texture_index))
            fields += tuple(Field(2 + i, FieldKind.I32, value) for i, value in enumerate(copied.crop))
            self.observer.observe(token, 0, Record(RecordKind.EVENT, fields))
            self.observer.seal_frame(frame, token)
            self._signals[token] = _Signal(frame, token, signal)
            self._presents[frame] = copied
            self._windows[copied.window_lifetime] = copied.native_present_serial
            return token
        except ObservationError as exc:
            if self._failure is None:
                self._fail(frame, exc.reason)
            raise
        except (ValueError, TypeError, AttributeError) as exc:
            self._fail(frame, "invalid native present event: " + str(exc))

    def poll(self, event: TimelinePoll):
        self._check()
        if not self._signals:
            self._failure = "no owned recording to associate with completion"
            raise ObservationError(State.UNKNOWN, self._failure)
        frame = next(reversed(self._signals.values())).frame
        try:
            if type(event) is not TimelinePoll or event.session != self.session:
                self._fail(frame, "completion session mismatch")
            opaque(event.timeline_lifetime)
            for value in (event.serial, event.counter, event.observed_at_ns):
                integer(value, 64)
            integer(event.vk_result, 32, signed=True)
            if event.serial == 0 or event.vk_result != 0:
                self._fail(frame, "failed or unidentified native timeline query")
            previous = self._polls.get(event.timeline_lifetime)
            if previous and (event.serial <= previous.serial or event.counter < previous.counter or
                             event.observed_at_ns < previous.observed_at_ns):
                self._fail(frame, "stale/regressing completion poll")
            signals = [s for s in self._signals.values() if s.event.timeline_lifetime == event.timeline_lifetime]
            if not signals:
                self._fail(frame, "completion semaphore lifetime has no owned recording")
            for signal in signals:
                if signal.completion_ns is None and signal.event.rec_seq <= event.counter:
                    if event.observed_at_ns < signal.event.submitted_at_ns:
                        self._fail(signal.frame, "completion predates actual native submit")
                    self.observer.completed(signal.token, event.counter, event.observed_at_ns)
                    signal.completion_ns = event.observed_at_ns
            self._polls[event.timeline_lifetime] = event
        except ObservationError as exc:
            self._failure = self._failure or exc.reason
            raise
        except (ValueError, TypeError, AttributeError) as exc:
            self._fail(frame, "invalid native timeline event: " + str(exc))

    def frame_observation(self, frame: FrameToken) -> FrameObservation:
        self._check()
        receipt = self.observer.finalize_frame(frame)
        if frame not in self._presents:
            raise ObservationError(State.UNKNOWN, "no source native-present association")
        return FrameObservation(receipt, self._presents[frame], tuple(self._candidates.get(frame, ())), (
            "candidate uniform shader role and primary scene/present association unproved",
            "inherited targets/programs/other resource state unobserved",
            "native producer and historical snapshot/alias visibility unvalidated",
            "complete scene/input/workload/rendering correctness unobserved",
        ))


def audit_pinned_abi(checkout: Path, compiler="cc") -> dict:
    """Compile actual public pinned headers in a disposable directory, never game code."""
    headers = {}
    for name, expected in PUBLIC_SOURCE_SHA256.items():
        data = subprocess.check_output(["git", "-C", str(checkout), "show", PIN + ":" + name])
        if hashlib.sha256(data).hexdigest() != expected:
            raise ValueError("pinned public source identity mismatch: " + name)
        if name.startswith("gpu/render/"):
            headers[Path(name).name] = data
    observer_digest = hashlib.sha256(Path(__file__).with_name("benchmark_observer.py").read_bytes()).hexdigest()
    if observer_digest != OBSERVER_SHA256:
        raise ValueError("frozen observer identity mismatch")
    checks = ["sizeof(void*)==8", "sizeof(nvn_op_hdr)==8", "sizeof(nvn_p_bind_buf)==24",
              "offsetof(nvn_p_bind_buf,addr)==8", "offsetof(nvn_p_bind_buf,size)==16",
              "sizeof(nvn_p_update_ub)==16", "sizeof(nvn_p_viewports)==264",
              "offsetof(nvn_p_viewports,v)==8", "sizeof(nvn_p_scissor)==16",
              "sizeof(nvn_p_depth_range)==8", "sizeof(nvn_p_draw)==20", "sizeof(nvn_p_dispatch)==16"]
    checks += [f"NOP_{op.name}=={op.value}" for op in Op]
    source = '#include "rx_nvn.h"\n' + "\n".join(f'_Static_assert({c}, "pinned ABI {i}");' for i, c in enumerate(checks)) + '\nint main(void){return 0;}\n'
    with tempfile.TemporaryDirectory(prefix="oo-replay-abi-") as temporary:
        root = Path(temporary)
        for name, data in headers.items():
            (root / name).write_bytes(data)
        (root / "probe.c").write_text(source)
        command = [compiler, "-std=c11", "-O2", "-DNDEBUG", "-Werror", "-I" + str(root), "-c", str(root / "probe.c"), "-o", str(root / "probe.o")]
        subprocess.run(command, check=True, capture_output=True, text=True)
    return {"source_pin": PIN, "record_header_sha256": HEADER_SHA256,
            "frozen_observer_sha256": observer_digest, "public_source_sha256": PUBLIC_SOURCE_SHA256,
            "compiled_public_abi_assertions": len(checks), "compiler": compiler,
            "flags": ["-std=c11", "-O2", "-DNDEBUG", "-Werror"],
            "scope": "pinned public C record ABI only; no game/GPU execution or native producer validation"}


if __name__ == "__main__":
    import argparse
    import json
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--audit-checkout", type=Path, required=True)
    parser.add_argument("--compiler", default="cc")
    arguments = parser.parse_args()
    print(json.dumps(audit_pinned_abi(arguments.audit_checkout, arguments.compiler), indent=2))
