#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Bounded CPU observation primitives, not a benchmark evidence producer.

Adapters must supply audited mapping/capture/event observations. This module cannot
verify their origin, identify a scene, certify coverage beyond requested ranges,
verify pixels or produce manifests accepted by benchmark_contract.py.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum, IntEnum
import hashlib
import math
import struct


class State(Enum):
    PENDING = "pending"
    KNOWN = "known"       # Only a requested byte range, not a scene certificate.
    COMPLETE = "complete"  # Only local frame bookkeeping.
    UNKNOWN = "unknown"
    INVALID = "invalid"


class ObservationError(ValueError):
    def __init__(self, state: State, reason: str):
        self.state, self.reason = state, reason
        super().__init__(f"{state.value}: {reason}")


def integer(value: int, bits: int, *, signed: bool = False) -> int:
    low, high = (-(1 << (bits - 1)), 1 << (bits - 1)) if signed else (0, 1 << bits)
    if type(value) is not int or not low <= value < high:
        raise ValueError(f"expected {'i' if signed else 'u'}{bits}")
    return value


def extent(offset: int, size: int) -> None:
    integer(offset, 64)
    integer(size, 64)
    if size == 0 or offset + size > 1 << 64:
        raise ValueError("empty or overflowing extent")


def opaque(key: object) -> None:
    if type(key) not in (int, str) or key == "":
        raise ValueError("opaque key must be an integer or nonempty string")


@dataclass(frozen=True)
class Stamp:
    submission: int
    generation: int

    def __post_init__(self):
        integer(self.submission, 64)
        integer(self.generation, 64)
        if not self.submission or not self.generation:
            raise ValueError("stamp components must be positive")


class Source(Enum):
    SNAPSHOT = "snapshot"
    LIVE = "live"
    GPU_UNRESOLVED = "gpu_unresolved"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class Span:
    backing: int | str  # Opaque adapter token, never serialized.
    offset: int
    data: bytes


@dataclass(frozen=True)
class CoveredBytes:
    stamp: Stamp
    command: int
    spans: tuple[Span, ...]
    overlay_commands: tuple[int, ...]

    @property
    def data(self) -> bytes:
        return b"".join(span.data for span in self.spans)


@dataclass
class _CoverageHealth:
    failure: str | None = None


@dataclass(frozen=True)
class ReadResult:
    _state: State
    _reason: str
    covered: CoveredBytes | None = None
    _health: _CoverageHealth | None = field(default=None, repr=False, compare=False)

    @property
    def state(self) -> State:
        return State.INVALID if self._health is not None and self._health.failure is not None else self._state

    @property
    def reason(self) -> str:
        return self._health.failure if self._health is not None and self._health.failure is not None else self._reason

    def require(self) -> CoveredBytes:
        if self._health is not None and self._health.failure is not None:
            raise ObservationError(State.INVALID, self._health.failure)
        if self.state is not State.KNOWN or self.covered is None:
            raise ObservationError(self.state, self.reason)
        return self.covered


@dataclass(frozen=True)
class _Mapping:
    address: int
    size: int
    backing: int | str
    offset: int


@dataclass(frozen=True)
class _Bytes:
    backing: int | str
    offset: int
    data: bytes
    stamp: Stamp
    source: Source
    command: int | None = None


class Coverage:
    """One submission snapshot plus its inline writes, sealed before reads.

    read(..., command=N) observes bytes BEFORE operation N: an inline write at
    ordinal N is visible only to later ordinals. Backing coordinates make writes
    through one virtual alias visible through its other aliases. No live callback
    or zero-fill fallback exists. A KNOWN result covers only that requested range.
    """
    def __init__(self, stamp: Stamp, *, max_entries=4096, max_bytes=8 * 1024 * 1024,
                 max_read_bytes=1024 * 1024):
        if type(stamp) is not Stamp:
            raise ValueError("Stamp required")
        for value in (max_entries, max_bytes, max_read_bytes):
            integer(value, 32)
            if not value:
                raise ValueError("limits must be positive")
        self.stamp = stamp
        self.max_entries, self.max_bytes, self.max_read_bytes = max_entries, max_bytes, max_read_bytes
        self._maps: list[_Mapping] = []
        self._bytes: list[_Bytes] = []
        self._commands: set[int] = set()
        self._used = 0
        self._sealed = False
        self._health = _CoverageHealth()

    @property
    def _failure(self):
        return self._health.failure

    @_failure.setter
    def _failure(self, reason):
        self._health.failure = reason

    def _fail(self, reason):
        self._failure = self._failure or reason
        raise ObservationError(State.INVALID, self._failure)

    def _mutable(self):
        if self._failure:
            self._fail(self._failure)
        if self._sealed:
            self._fail("coverage mutated after seal")

    def _room(self, entries, size=0):
        if len(self._maps) + len(self._bytes) + entries > self.max_entries or self._used + size > self.max_bytes:
            self._fail("coverage capacity exceeded")

    def mapping(self, address: int, size: int, backing: int | str, offset: int = 0):
        self._mutable()
        extent(address, size)
        extent(offset, size)
        opaque(backing)
        if any(address < m.address + m.size and m.address < address + size for m in self._maps):
            self._fail("overlapping virtual mappings")
        self._room(1)
        self._maps.append(_Mapping(address, size, backing, offset))

    def _resolve(self, address, size):
        end, at, parts = address + size, address, []
        while at < end:
            mapping = next((m for m in self._maps if m.address <= at < m.address + m.size), None)
            if mapping is None:
                return None
            length = min(end, mapping.address + mapping.size) - at
            parts.append((mapping.backing, mapping.offset + at - mapping.address, length))
            at += length
        return parts

    def capture(self, address: int, data: bytes, stamp: Stamp, source: Source):
        self._store(address, data, stamp, source, None)

    def overlay(self, command: int, address: int, data: bytes, stamp: Stamp):
        integer(command, 64)
        self._mutable()
        if command in self._commands:
            self._fail("duplicate inline-write ordinal")
        self._store(address, data, stamp, Source.SNAPSHOT, command)
        self._commands.add(command)

    def _store(self, address, data, stamp, source, command):
        self._mutable()
        if type(data) is not bytes or type(stamp) is not Stamp or type(source) is not Source:
            raise ValueError("immutable bytes, Stamp and Source required")
        extent(address, len(data))
        parts = self._resolve(address, len(data))
        if parts is None:
            self._fail("capture has unmapped bytes")
        self._room(len(parts), len(data))
        added, at = [], 0
        for backing, offset, length in parts:
            item = _Bytes(backing, offset, data[at:at + length], stamp, source, command)
            if any(b.command == command and b.backing == backing and b.stamp == stamp
                and b.source is source and offset < b.offset + len(b.data) and b.offset < offset + length
                for b in self._bytes + added):
                self._fail("overlapping observations at one write position")
            added.append(item)
            at += length
        self._bytes.extend(added)
        self._used += len(data)

    def seal(self):
        self._mutable()
        self._sealed = True

    def read(self, address: int, size: int, stamp: Stamp, command: int) -> ReadResult:
        extent(address, size)
        integer(command, 64)
        if type(stamp) is not Stamp:
            raise ValueError("Stamp required")
        if self._failure:
            return ReadResult(State.INVALID, self._failure)
        if not self._sealed:
            return ReadResult(State.PENDING, "coverage not sealed")
        if size > self.max_read_bytes:
            self._failure = "read capacity exceeded"
            return ReadResult(State.INVALID, self._failure)
        if stamp != self.stamp:
            return ReadResult(State.UNKNOWN, "submission/generation mismatch")
        resolved = self._resolve(address, size)
        if resolved is None:
            return ReadResult(State.UNKNOWN, "unmapped range")
        spans: list[Span] = []
        applied: set[int] = set()
        for backing, offset, length in resolved:
            end = offset + length
            relevant = [b for b in self._bytes if b.backing == backing
                        and b.offset < end and offset < b.offset + len(b.data)
                        and (b.command is None or b.command < command)]
            cuts = sorted({offset, end} | {max(offset, b.offset) for b in relevant}
                          | {min(end, b.offset + len(b.data)) for b in relevant})
            for left, right in zip(cuts, cuts[1:]):
                covering = [b for b in relevant if b.offset <= left and right <= b.offset + len(b.data)]
                # A later write with uncertain generation cannot be hidden by an older valid write.
                writes = [b for b in covering if b.command is not None]
                uncertain = next((b for b in covering if b.stamp == stamp and
                                  b.source in (Source.GPU_UNRESOLVED, Source.UNKNOWN)), None)
                chosen = max(writes, key=lambda b: b.command) if writes else uncertain or next(
                    (b for b in covering if b.stamp == stamp and b.source is Source.SNAPSHOT),
                    covering[0] if covering else None)
                if chosen is None:
                    return ReadResult(State.UNKNOWN, "snapshot coverage hole")
                if chosen.stamp != stamp:
                    return ReadResult(State.UNKNOWN, "range submission/generation mismatch")
                if chosen.source is not Source.SNAPSHOT:
                    return ReadResult(State.UNKNOWN, "non-snapshot source: " + chosen.source.value)
                data = chosen.data[left - chosen.offset:right - chosen.offset]
                if chosen.command is not None:
                    applied.add(chosen.command)
                if spans and spans[-1].backing == backing and spans[-1].offset + len(spans[-1].data) == left:
                    previous = spans.pop()
                    spans.append(Span(backing, previous.offset, previous.data + data))
                else:
                    spans.append(Span(backing, left, data))
        return ReadResult(State.KNOWN, "requested range covered",
                          CoveredBytes(stamp, command, tuple(spans), tuple(sorted(applied))), self._health)


class FieldKind(IntEnum):
    U32 = 1
    I32 = 2
    U64 = 3
    F32_BITS = 4
    F64_BITS = 5
    HASHED_BYTES = 6


@dataclass(frozen=True)
class Field:
    tag: int
    kind: FieldKind
    value: int | bytes

    def encode(self) -> bytes:
        integer(self.tag, 16)
        if type(self.kind) is not FieldKind:
            raise ValueError("FieldKind required")
        if self.kind is FieldKind.HASHED_BYTES:
            if type(self.value) is not bytes or len(self.value) > 1024 * 1024:
                raise ValueError("bounded immutable byte field required")
            payload = struct.pack("<I", len(self.value)) + hashlib.sha256(self.value).digest()
        else:
            bits = 64 if self.kind in (FieldKind.U64, FieldKind.F64_BITS) else 32
            integer(self.value, bits, signed=self.kind is FieldKind.I32)
            fmt = "<i" if self.kind is FieldKind.I32 else "<Q" if bits == 64 else "<I"
            payload = struct.pack(fmt, self.value)
            if self.kind in (FieldKind.F32_BITS, FieldKind.F64_BITS):
                if not math.isfinite(struct.unpack("<f" if bits == 32 else "<d", payload)[0]):
                    raise ValueError("nonfinite floating-point field")
        return struct.pack("<HB", self.tag, self.kind.value) + payload


class RecordKind(IntEnum):
    CAMERA_CANDIDATE = 1
    STATE_CANDIDATE = 2
    DRAW = 3
    DISPATCH = 4
    CLEAR = 5
    COPY = 6
    INLINE_UPDATE = 7
    EVENT = 8


@dataclass(frozen=True)
class ResourceUse:
    binding: int
    read: ReadResult


@dataclass(frozen=True)
class Record:
    kind: RecordKind
    fields: tuple[Field, ...] = ()
    resources: tuple[ResourceUse, ...] = ()


MAGIC = b"OOBENCHOBS\x00" + struct.pack("<H", 1)


def canonical_records(records: tuple[Record, ...], *, max_bytes=8 * 1024 * 1024) -> bytes:
    """Encode semantic records only; no host addresses, stamps or opaque keys.

    Field tags/order and resource-binding meanings require a reviewed adapter
    schema. This format does not establish complete camera/scene/workload coverage.
    """
    if type(records) is not tuple or len(records) > 65536:
        raise ValueError("bounded tuple of records required")
    integer(max_bytes, 32)
    if max_bytes < len(MAGIC) + 4:
        raise ObservationError(State.INVALID, "canonical byte capacity exceeded")
    aliases: dict[int | str, int] = {}
    observed_bytes = 0
    output = bytearray(MAGIC + struct.pack("<I", len(records)))
    for record in records:
        if type(record) is not Record or type(record.kind) is not RecordKind:
            raise ValueError("typed Record required")
        if type(record.fields) is not tuple or type(record.resources) is not tuple:
            raise ValueError("immutable field/resource tuples required")
        if len(record.fields) > 256 or len(record.resources) > 64:
            raise ValueError("record capacity exceeded")
        fields = sorted(record.fields, key=lambda f: f.tag)
        resources = sorted(record.resources, key=lambda r: r.binding)
        if len({f.tag for f in fields}) != len(fields) or len({r.binding for r in resources}) != len(resources):
            raise ValueError("duplicate field tag or resource binding")
        body = bytearray(struct.pack("<HH", len(fields), len(resources)))
        for value in fields:
            observed_bytes += len(value.value) if type(value.value) is bytes else 0
            if observed_bytes > max_bytes:
                raise ObservationError(State.INVALID, "canonical input capacity exceeded")
            body.extend(value.encode())
        for resource in resources:
            integer(resource.binding, 16)
            covered = resource.read.require()
            body.extend(struct.pack("<HI", resource.binding, len(covered.spans)))
            for span in covered.spans:
                opaque(span.backing)
                if type(span.data) is not bytes:
                    raise ValueError("immutable resource bytes required")
                observed_bytes += len(span.data)
                if observed_bytes > max_bytes:
                    raise ObservationError(State.INVALID, "canonical input capacity exceeded")
                extent(span.offset, len(span.data))
                alias = aliases.setdefault(span.backing, len(aliases))
                body.extend(struct.pack("<IQQ", alias, span.offset, len(span.data)))
                body.extend(hashlib.sha256(span.data).digest())
        output.extend(struct.pack("<HI", record.kind.value, len(body)))
        output.extend(body)
        if len(output) > max_bytes:
            raise ObservationError(State.INVALID, "canonical byte capacity exceeded")
    return bytes(output)


@dataclass(frozen=True)
class FrameToken:
    value: int
    _owner: object = field(default=None, repr=False)

    def __post_init__(self):
        integer(self.value, 64)
        if not self.value:
            raise ValueError("positive frame token required")


@dataclass(frozen=True)
class SubmissionToken:
    value: int
    _owner: object = field(default=None, repr=False)

    def __post_init__(self):
        integer(self.value, 64)
        if not self.value:
            raise ValueError("positive submission token required")


class FrameKind(Enum):
    NATIVE = "native"
    GENERATED = "generated"


class SubmissionKind(Enum):
    WORK = "work"
    PRESENT = "present"


@dataclass
class _Submission:
    frame: FrameToken
    queue: int | str | None = None
    value: int = 0
    kind: SubmissionKind = SubmissionKind.WORK
    dependencies: tuple[SubmissionToken, ...] = ()
    operations: int = 0
    records: dict[int, Record] = field(default_factory=dict)
    completion_ns: int | None = None


@dataclass
class _Frame:
    kind: FrameKind
    submissions: list[SubmissionToken] = field(default_factory=list)
    present: SubmissionToken | None = None
    issue: tuple[State, str] | None = None
    receipt: FrameReceipt | None = None


@dataclass(frozen=True)
class FrameReceipt:
    token: FrameToken
    completion_ns: int
    logical_records_sha256: str
    canonical_frame_sha256: str
    operation_count: int
    submission_count: int


@dataclass(frozen=True)
class IntervalReceipt:
    native_intervals: int
    start_ns: int
    end_ns: int
    elapsed_ns: int
    frames: tuple[FrameReceipt, ...]  # Boundary frame excluded; N measured frames.


class Observer:
    """Single ordered event consumer. COMPLETE means bookkeeping, never benchmark eligibility.

    reserve/enqueue describe intended work; completed records actual timeline
    observations. Suspend/resume uses the same token and continuing local ordinal.
    No completion, correctness, camera-coverage or isolation pass switches exist.
    """
    def __init__(self, *, max_frames=4096, max_submissions=16384, max_records=65536,
                 max_canonical_bytes=8 * 1024 * 1024, max_observed_bytes=8 * 1024 * 1024):
        for n in (max_frames, max_submissions, max_records, max_canonical_bytes, max_observed_bytes):
            integer(n, 32)
            if not n:
                raise ValueError("limits must be positive")
        self.limits = (max_frames, max_submissions, max_records, max_canonical_bytes, max_observed_bytes)
        self._owner = object()
        self._observed_bytes = 0
        self._frames: dict[FrameToken, _Frame] = {}
        self._submissions: dict[SubmissionToken, _Submission] = {}
        self._queues: dict[int | str, SubmissionToken] = {}
        self._records = 0
        self._dependency_edges = 0
        self._failure: str | None = None

    def _fail(self, reason, frame=None, state=State.INVALID):
        if frame is None:
            self._failure = self._failure or reason
        else:
            self._frames[frame].issue = self._frames[frame].issue or (state, reason)
        raise ObservationError(state, reason)

    def _frame(self, token):
        if self._failure:
            raise ObservationError(State.INVALID, self._failure)
        if type(token) is not FrameToken or token not in self._frames:
            self._fail("unknown frame token")
        frame = self._frames[token]
        if frame.issue:
            raise ObservationError(*frame.issue)
        return frame

    def _submission(self, token):
        if type(token) is not SubmissionToken or token not in self._submissions:
            self._fail("unknown submission token")
        submission = self._submissions[token]
        self._frame(submission.frame)
        return submission

    def begin_frame(self, kind: FrameKind) -> FrameToken:
        if self._failure:
            raise ObservationError(State.INVALID, self._failure)
        if type(kind) is not FrameKind:
            raise ValueError("FrameKind required")
        if len(self._frames) >= self.limits[0]:
            self._fail("frame capacity exceeded")
        token = FrameToken(len(self._frames) + 1, self._owner)
        self._frames[token] = _Frame(kind)
        return token

    def reserve(self, frame: FrameToken) -> SubmissionToken:
        state = self._frame(frame)
        if state.present is not None:
            self._fail("submission reserved after frame seal", frame)
        if len(self._submissions) >= self.limits[1]:
            self._fail("submission capacity exceeded")
        token = SubmissionToken(len(self._submissions) + 1, self._owner)
        self._submissions[token] = _Submission(frame)
        state.submissions.append(token)
        return token

    def enqueue(self, token: SubmissionToken, queue: int | str, value: int,
                kind: SubmissionKind, operations: int, dependencies: tuple[SubmissionToken, ...] = ()):
        sub = self._submission(token)
        frame = self._frame(sub.frame)
        opaque(queue)
        integer(value, 64)
        integer(operations, 32)
        if type(kind) is not SubmissionKind or type(dependencies) is not tuple:
            raise ValueError("SubmissionKind and dependency tuple required")
        if sub.queue is not None or frame.present is not None:
            self._fail("duplicate or late enqueue", sub.frame)
        if len(dependencies) > self.limits[1]:
            self._fail("dependency capacity exceeded")
        deps = list(dependencies)
        previous = self._queues.get(queue)
        if value == 0 or (previous is not None and value <= self._submissions[previous].value):
            self._fail("nonmonotonic queue timeline", sub.frame)
        if previous is not None and previous not in deps:
            deps.append(previous)
        if len(set(deps)) != len(deps):
            self._fail("duplicate dependency", sub.frame)
        if self._dependency_edges + len(deps) > self.limits[3] // 12:
            self._fail("dependency capacity exceeded")
        for dependency in deps:
            other = self._submission(dependency)
            if dependency == token or other.frame.value > sub.frame.value:
                self._fail("self or future-frame dependency", sub.frame)
        sub.queue, sub.value, sub.kind = queue, value, kind
        sub.operations, sub.dependencies = operations, tuple(deps)
        self._queues[queue] = token
        self._dependency_edges += len(deps)

    def observe(self, token: SubmissionToken, ordinal: int, record: Record):
        sub = self._submission(token)
        integer(ordinal, 32)
        if sub.queue is None or self._frame(sub.frame).receipt is not None:
            self._fail("operation outside recording lifetime", sub.frame)
        if ordinal in sub.records or ordinal >= sub.operations:
            self._fail("duplicate or out-of-range operation ordinal", sub.frame)
        if self._records >= self.limits[2]:
            self._fail("record capacity exceeded")
        try:
            canonical_records((record,), max_bytes=self.limits[3])
            retained = sum(len(f.value) for f in record.fields if type(f.value) is bytes)
            for resource in record.resources:
                covered = resource.read.require()
                if covered.stamp.submission != token.value or covered.command != ordinal:
                    self._fail("resource read belongs to another submission/command", sub.frame, State.UNKNOWN)
                retained += sum(len(span.data) for span in covered.spans)
            if self._observed_bytes + retained > self.limits[4]:
                self._fail("retained observation capacity exceeded")
        except ObservationError as exc:
            self._fail(exc.reason, sub.frame, State.UNKNOWN if exc.state is State.PENDING else exc.state)
        except (ValueError, TypeError, AttributeError) as exc:
            self._fail("malformed canonical record: " + str(exc), sub.frame)
        sub.records[ordinal] = record
        self._records += 1
        self._observed_bytes += retained

    def _closure(self, token):
        visiting, done = set(), set()
        # Iterative traversal remains bounded by submission capacity, not Python recursion.
        stack = [(token, False)]
        while stack:
            current, exiting = stack.pop()
            sub = self._submission(current)
            if exiting:
                visiting.remove(current)
                done.add(current)
                continue
            if current in done:
                continue
            if current in visiting:
                self._fail("dependency cycle", sub.frame)
            if sub.queue is None:
                self._fail("dependency was not enqueued", sub.frame, State.UNKNOWN)
            visiting.add(current)
            stack.append((current, True))
            stack.extend((d, False) for d in reversed(sub.dependencies))
        return done

    def seal_frame(self, frame: FrameToken, present: SubmissionToken):
        state = self._frame(frame)
        sub = self._submission(present)
        if state.present is not None or sub.frame != frame or sub.kind is not SubmissionKind.PRESENT or sub.queue is None:
            self._fail("invalid frame present root", frame)
        if sum(self._submissions[t].kind is SubmissionKind.PRESENT for t in state.submissions) != 1:
            self._fail("multiple present roots", frame)
        closure = self._closure(present)
        if not set(state.submissions) <= closure:
            self._fail("frame work missing from present dependency closure", frame, State.UNKNOWN)
        state.present = present

    def completed(self, token: SubmissionToken, observed_value: int, at_ns: int):
        sub = self._submission(token)
        integer(observed_value, 64)
        integer(at_ns, 64)
        if sub.queue is None or sub.completion_ns is not None:
            self._fail("completion before enqueue or duplicate completion", sub.frame)
        if observed_value < sub.value:
            self._fail("observed timeline has not completed submission", sub.frame, State.UNKNOWN)
        sub.completion_ns = at_ns

    def unknown(self, frame: FrameToken, reason: str):
        self._frame(frame)
        if type(reason) is not str or not reason or len(reason) > 256:
            raise ValueError("bounded unknown-coverage reason required")
        self._fail(reason, frame, State.UNKNOWN)

    def finalize_frame(self, frame: FrameToken) -> FrameReceipt:
        state = self._frame(frame)
        if state.receipt is not None:
            # A late source-ledger protocol failure invalidates previously issued
            # local receipts too; do not reuse their digest without checking health.
            try:
                for token in self._closure(state.present):
                    for record in self._submissions[token].records.values():
                        for resource in record.resources:
                            resource.read.require()
            except ObservationError as exc:
                self._fail(exc.reason, frame, exc.state)
            return state.receipt
        if state.present is None:
            raise ObservationError(State.PENDING, "frame has no sealed present")
        closure = self._closure(state.present)
        if any(self._submissions[t].completion_ns is None or
               len(self._submissions[t].records) != self._submissions[t].operations for t in closure):
            raise ObservationError(State.PENDING, "missing completion or logical operations")
        for token in closure:
            sub = self._submission(token)
            try:
                for record in sub.records.values():
                    for resource in record.resources:
                        resource.read.require()
            except ObservationError as exc:
                self._fail(exc.reason, frame, exc.state)
            for dependency in sub.dependencies:
                if self._submissions[dependency].completion_ns > sub.completion_ns:
                    self._fail("completion timestamp precedes dependency", frame)
        records = tuple(record for token in state.submissions
                        for _, record in sorted(self._submissions[token].records.items()))
        try:
            encoded = canonical_records(records, max_bytes=self.limits[3])
        except ObservationError as exc:
            self._fail(exc.reason, frame, exc.state)
        # Canonical graph retains logical queue/dependency structure but drops local
        # handles, absolute timeline values, timestamps and global token numbers.
        graph = bytearray(b"OOBENCHGRAPH\x00" + struct.pack("<HI", 1, len(state.submissions)))
        queues = {}
        positions = {t: i for f in self._frames.values() for i, t in enumerate(f.submissions)}
        for token in state.submissions:
            sub = self._submissions[token]
            queue = queues.setdefault(sub.queue, len(queues))
            refs = sorted((self._submissions[d].frame.value - frame.value, positions[d])
                          for d in sub.dependencies)
            graph.extend(struct.pack("<IBII", queue, 1 if sub.kind is SubmissionKind.WORK else 2,
                                     sub.operations, len(refs)))
            for delta, index in refs:
                graph.extend(struct.pack("<qI", delta, index))
            if len(graph) + len(encoded) > self.limits[3]:
                self._fail("canonical frame capacity exceeded", frame)
        state.receipt = FrameReceipt(frame, self._submissions[state.present].completion_ns,
                                    hashlib.sha256(encoded).hexdigest(),
                                    hashlib.sha256(graph + encoded).hexdigest(), len(records), len(state.submissions))
        return state.receipt

    def state(self, frame: FrameToken) -> State:
        """Current bookkeeping state; never a scene or benchmark acceptance state."""
        try:
            self.finalize_frame(frame)
        except ObservationError as exc:
            return exc.state
        return State.COMPLETE

    def window(self, boundaries: tuple[FrameToken, ...]) -> IntervalReceipt:
        if type(boundaries) is not tuple or len(boundaries) < 2:
            raise ValueError("N intervals require N+1 frame boundaries")
        for token in boundaries:
            if self._frame(token).kind is not FrameKind.NATIVE:
                self._fail("generated frame in native window", token)
        if any(b.value != a.value + 1 for a, b in zip(boundaries, boundaries[1:])):
            raise ValueError("native frame boundaries must be consecutive")
        receipts = tuple(self.finalize_frame(t) for t in boundaries)
        if any(b.completion_ns <= a.completion_ns for a, b in zip(receipts, receipts[1:])):
            self._fail("nonincreasing native completion times")
        start, end = receipts[0].completion_ns, receipts[-1].completion_ns
        return IntervalReceipt(len(receipts) - 1, start, end, end - start, receipts[1:])
