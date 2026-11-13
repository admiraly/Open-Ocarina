# CPU benchmark observation primitives

Status: `tools/benchmark_observer.py` implements bounded, independently authored
CPU primitives with synthetic tests. It is not an integrated runtime producer,
a scene-coverage certificate, a rendering check or a benchmark manifest exporter.
It does not change [the current benchmark validator](benchmark.md) or implement
[the proposed verification-cohort contract](../research/benchmark-producer-plan.md).
No GPU, game, private inputs or external Python dependencies are needed.

The module provides three pieces:

- `Coverage`: requested-range snapshot/generation checks with command-position
  inline writes and virtual aliases.
- `canonical_records`: typed, fixed-width serialization that omits local addresses
  and normalizes backing references while retaining alias relationships.
- `Observer`: declared submission dependencies, logical records, native present
  roots and completion-boundary bookkeeping.

These pieces check consistency of observations supplied by an adapter. They
cannot prove that bytes came from a real snapshot, that the adapter reported all
work, or that a reported GPU completion happened. A runtime bridge and its source
coverage still need independent review. There are no manual correctness,
isolation, camera-validity or benchmark-acceptance switches.

## Strict range reads

Create `Stamp(submission, generation)` with positive u64 values. `submission`
corresponds to an observer submission token's `.value`; `generation` identifies
its immutable snapshot epoch. Every stored range retains its own stamp and
source. This epoch is not a substitute for auditing per-page allocation versions,
backing lifetime or GPU visibility in the adapter.

Register nonoverlapping virtual mappings using
`mapping(address, size, backing, offset=0)`. Different virtual ranges may alias
the same opaque backing token and overlapping backing offsets. Address and
backing tokens remain local metadata; neither appears in canonical bytes.
Backing offsets must describe actual alias coordinates, rather than a label
chosen to make runs match. Virtual overlapping mappings are refused.

Supply immutable `bytes` with
`capture(address, data, stamp, source)`. Sources are `SNAPSHOT`, `LIVE`,
`GPU_UNRESOLVED` or `UNKNOWN`; only snapshot bytes can supply a known read.
Overlapping snapshot observations at the same stamp/source are refused rather
than choosing one arbitrarily. An unresolved GPU/unknown observation can block
an otherwise present base snapshot. This is not an API for pretending to know
a GPU-written value: actual unresolved writes and their ordering remain the
adapter's responsibility.

Register inline updates with `overlay(command, address, data, stamp)`, then
`seal()` the ledger. Writes are resolved through backing coordinates, so a write
through one virtual alias affects reads through another. An update ordinal is
unique within the submission. Unknown mapping coverage, ambiguous observations,
late mutation and capacity overflow invalidate the ledger.

`read(address, size, stamp, command)` returns `ReadResult`. The read observes
bytes **before** the requested logical command: a write at command 2 affects
command 3, but not command 2. Registering a later update in advance does not make
it visible to an earlier worker segment. The latest preceding write wins for
each covered range. A stale newer write cannot reveal an older valid value.
A complete inline overwrite can supply bytes absent from the base snapshot;
a partial overwrite cannot conceal the remaining hole.

States are explicit:

| State | Meaning |
| --- | --- |
| `PENDING` | Ledger has not been sealed; no usable bytes returned. |
| `KNOWN` | Every requested byte has the required stamp and snapshot/preceding-update source. This says nothing about unrequested state. |
| `UNKNOWN` | Mapping/capture hole, stale stamp, live fallback or unresolved source; no partial byte result returned. |
| `INVALID` | Protocol/capacity failure; subsequent smaller requests do not recover the ledger. |

Call `result.require()` to obtain immutable `CoveredBytes`. It contains canonical
backing spans, observed command/stamp and the applied overlay ordinals. A late
ledger protocol failure revokes earlier results through shared health state;
`require()` checks this state again. Previously returned raw bytes cannot be
revoked, so a consumer must retain/check the `ReadResult`, rather than turning
it into an unconditional pass flag.

```python
from tools.benchmark_observer import Coverage, Source, Stamp

stamp = Stamp(1, 7)
coverage = Coverage(stamp)
coverage.mapping(0x1000, 8, "local-backing")
coverage.capture(0x1000, b"abcdefgh", stamp, Source.SNAPSHOT)
coverage.overlay(2, 0x1002, b"XY", stamp)
coverage.seal()
assert coverage.read(0x1000, 8, stamp, 2).require().data == b"abcdefgh"
assert coverage.read(0x1000, 8, stamp, 3).require().data == b"abXYefgh"
```

This is an authored example, not a game camera observation.

## Canonical wire format, revision 1

This revision number describes the internal binary format only. It does not
refer to benchmark manifest schema 1 or authorize any validator acceptance.
The format uses no JSON/repr, native struct layout, padding or automatic pointer
serialization. All integers below are little-endian, with fixed declared widths.

- Stream: bytes `OOBENCHOBS` followed by NUL, u16 revision `1`, u32 record count.
- Record: u16 `RecordKind`, u32 body byte length, then the body.
- Body: u16 field count, u16 resource count, sorted fields, sorted resource uses.
- Field: u16 tag, u8 `FieldKind`, then its typed payload.
- Resource use: u16 binding, u32 span count, then each span's u32 first-use backing
  ID, u64 backing offset, u64 byte length and 32-byte SHA-256 of its observed bytes.

Field payloads are u32, i32, u64, IEEE f32 bits encoded as u32, IEEE f64 bits
encoded as u64, or `HASHED_BYTES` encoded as u32 original length plus a 32-byte
SHA-256 computed from supplied bytes. Nonfinite floating-point values, booleans
masquerading as integers, out-of-range values, duplicate field tags and duplicate
resource bindings are refused. Signed zero is retained bit-for-bit. Field order
is sorted by tag; record order remains significant.

`RecordKind` currently distinguishes camera/state **candidates**, draw, dispatch,
clear, copy, inline update and event. These names do not establish complete scene
coverage. A future reviewed adapter must define each numeric field tag, unit,
record meaning and binding. Integer fields are for semantic quantities, never
raw pointers or handles. Resource addresses belong in the mapping/read API.
Guest shader bytes can be represented by an explicitly defined hashed-byte field;
caller-supplied strings claiming a camera/workload hash are not an observation.

`ResourceUse(binding, read_result)` requires a known range. Backings receive IDs
in first-use order across the ordered record sequence. Renaming opaque tokens or
relocating virtual mappings preserves the encoding. Changing shared versus
separate backing, overlapping offsets, bytes or semantic fields changes it.
Adjacent backing-contiguous snapshot fragments are coalesced, so merely splitting
an otherwise identical capture into pages does not change identity.

This normalizes observed alias coordinates; it does not derive real backing
identity from arbitrary tokens. A bridge that assigns unrelated resources the
same token can lie to the normalizer. Backing provenance must remain audited.

## Frame and completion bookkeeping

Use one ordered consumer; the classes do not synchronize concurrent callbacks.
A future native collector must order/buffer its events before calling this API.
Tokens are scoped to one `Observer`; tokens from another instance are refused.

1. `begin_frame(FrameKind.NATIVE)` creates a frame token. Generated frames have an
   explicit different kind and cannot enter a native timing window.
2. `reserve(frame)` assigns submission tokens, including tokens needed for later
   dependency resolution. Reserve all frame submissions before sealing it.
3. `enqueue(token, queue, value, kind, operations, dependencies=())` records a
   positive per-queue timeline value, `WORK`/`PRESENT` kind, declared logical
   record count and dependency tokens. Queue values must increase. The preceding
   submission on the same queue becomes an implicit dependency. Cross-queue
   dependencies must be supplied explicitly; missing, cyclic or unenqueued
   dependencies cannot be treated as completed work.
4. `observe(token, ordinal, record)` records each submission-local ordinal exactly
   once. Worker delivery can be out of ordinal order. An interrupted/resumed
   stream keeps the same token and continues the original ordinals. Dry and
   worker walks must not both emit a record for the same operation. Resource
   reads must carry this submission's stamp and the same consuming ordinal.
5. `seal_frame(frame, present)` requires exactly one present root and verifies
   that its dependency closure includes every declared frame submission. Sealing
   does not imply completion. Record/completion events may still arrive.
6. `completed(token, observed_value, at_ns)` records an actual observed timeline
   value and monotonic-clock timestamp supplied by the collector. A value below
   the submitted value, duplicate completion or a completion before enqueue
   fails. Collector delivery may be out of order across queues; all dependencies
   still need observations, and their timestamps cannot exceed the dependent's.
7. `finalize_frame(frame)` returns `FrameReceipt` only after all declared records
   and dependency completions exist. `state(frame)` reports `PENDING`, `COMPLETE`,
   `UNKNOWN` or `INVALID`; **COMPLETE means local bookkeeping only**. Unavailable
   coverage can be recorded with `unknown(frame, reason)` and cannot be reset.

A frame receipt contains token, completion time, record/submission counts and two
local digests. `logical_records_sha256` hashes the canonical record stream.
`canonical_frame_sha256` additionally includes a versioned dependency graph:
NUL-terminated `OOBENCHGRAPH`, u16 revision 1 and u32 submission count; each
submission has a u32 normalized queue ID, u8 kind (work 1, present 2), u32 declared
operation count, u32 dependency count, and sorted dependency pairs of i64 relative
frame offset and u32 index within that frame. Absolute tokens, timeline values,
opaque queue keys and timestamps are omitted. Both graphs and records are
bounded. Neither digest is a complete camera, scene or workload certificate.

`window((boundary, frame1, ..., frameN))` requires N+1 consecutive completed
native frames with strictly increasing completion times. It returns N intervals,
`end_ns - start_ns`, and the N measured frame receipts excluding the initial
boundary. It computes no FPS or speedup. It cannot establish display scanout,
fixed simulation dt, a completed warmup, cache state or unseen GPU dependencies.

Missing events remain pending; malformed protocol and storage overflow are
terminal. Uncertain coverage remains unknown. Cached receipts are rechecked
against dependency/source health when requested again. A caller must discard
all affected evidence after late failure, rather than keeping an older digest.

## Bounds, validation and remaining integration

Default limits are 4,096 frames, 16,384 submissions, 65,536 logical records and
8 MiB each for canonical bytes and retained observation payload. Coverage has
4,096 total mapping/byte entries, 8 MiB stored bytes and 1 MiB per read. Limits
are configurable positive fixed-width integers. Raw byte payloads count toward
limits even when their canonical representation is only a small hash. Total
retained dependency references are additionally capped by the canonical-byte
budget divided by their 12-byte wire width, before graph serialization. Overflow
fails; no eviction, implicit growth or dropped-event success path exists.

```sh
python3 -m unittest discover -s tests -p test_benchmark_observer.py -v
```

Synthetic tests cover ordinal/worker-overlay boundaries, per-range generations,
aliases, holes, live/GPU-unknown sources, late invalidation, protocol/storage
limits, relocation versus alias changes, multi-queue completion, resumes,
duplicate ordinals, missing dependencies, cycles, and N-interval boundaries.

Still missing: native source hooks; authenticated capture/visibility provenance;
actual primary-camera association; complete simulation/scene/input/resource
coverage; indirect GPU argument resolution; omission/compile/competitor
observations; real GPU completion collection; rendering validation; and reviewed
producer-overhead measurement. This Python core is not a measured fast native
collector. The proposed verification-cohort revision remains unimplemented.
