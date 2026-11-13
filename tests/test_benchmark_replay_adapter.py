# SPDX-License-Identifier: GPL-3.0-or-later
"""Authored native-format fixtures; no game, GPU, installed source or captures."""
from dataclasses import replace
import hashlib
import struct
import unittest

from tools.benchmark_observer import FrameKind, ObservationError, Observer, Source, State
from tools.benchmark_replay_adapter import (
    CANDIDATE_SIZE, MAX_COMMANDS, MAX_STREAM, Op, Origin, OwnedRange, PresentCopy, RecordingSignal,
    ReplayAdapter, SubmitCopy, TimelinePoll, decode_stream,
)

SESSION = bytes.fromhex("0102030405060708090a0b0c0d0e0f10")


def record(op, payload=b"", padding=b"\xa5"):
    size = (8 + len(payload) + 7) & ~7
    return struct.pack("<II", int(op), size) + payload + padding * (size - 8 - len(payload))


def uniform():
    b = bytearray(CANDIDATE_SIZE)
    struct.pack_into("<16f", b, 0x30, *range(1, 17))
    struct.pack_into("<16f", b, 0x70, 1, 0, .125, 0, 0, 1, -.25, 0, 0, 0, -1, -2, 0, 0, -1, 0)
    struct.pack_into("<2f", b, 0x120, 640, 360)
    return bytes(b)


def stream(address=0x1000, update_address=None, later=9.0):
    if update_address is None:
        update_address = address
    viewport = struct.pack("<ii4f", 0, 1, 0, 0, 640, 360) + bytes(15 * 16)
    return b"".join((record(2), record(19, struct.pack("<iiQQ", 0, 0, address, CANDIDATE_SIZE)),
        record(11, viewport), record(12, struct.pack("<4i", 0, 0, 640, 360)),
        record(13, struct.pack("<2f", 0, 1)),
        record(7, struct.pack("<QQf", update_address + 0x30, 4, 6.0)),
        record(25, struct.pack("<5i", 3, 0, 12, 0, 1)),
        record(7, struct.pack("<QQf", update_address + 0x30, 4, later)),
        record(25, struct.pack("<5i", 3, 0, 12, 0, 1)), record(0)))


def copied(address=0x1000, *, alias=False, offset=0, backing="original-allocation", generation=1):
    origin = Origin(SESSION, 1, generation)
    ranges = (OwnedRange(origin, address, backing, offset, uniform(), Source.SNAPSHOT),)
    if alias:
        ranges += (OwnedRange(origin, address + 0x1000, backing, offset, uniform(), Source.SNAPSHOT),)
    data = stream(address, address + 0x1000 if alias else address)
    return SubmitCopy(origin, "original-nvn-queue", 5, data, ranges)


def signal(copy, *, part=1, sequence=1, at=10, timeline="rec-timeline-1"):
    return RecordingSignal(copy.origin, hashlib.sha256(copy.stream).digest(), timeline, part, sequence, at, 0)


def setup(copy=None):
    observer = Observer()
    frame = observer.begin_frame(FrameKind.NATIVE)
    adapter = ReplayAdapter(observer, SESSION)
    copy = copied() if copy is None else copy
    attached = adapter.attach(frame, copy, signal(copy))
    return observer, frame, adapter, attached


def present(adapter, frame, token, *, timeline="rec-timeline-1"):
    event = RecordingSignal(Origin(SESSION, 2, 1), b"", timeline, 2, 2, 20, 0)
    copied = PresentCopy(event.origin, "window-1", 1, 0, (0, 0, 640, 360), 16)
    return adapter.present(frame, copied, event, (token,))


class NativeDecoderTests(unittest.TestCase):
    def test_golden_bind_record_matches_pinned_u32_header_and_i32_i32_u64_u64_abi(self):
        bind = bytes.fromhex("1300000020000000000000000000000000100000000000003002000000000000")
        decoded = decode_stream(bytes.fromhex("0200000008000000") + bind + bytes.fromhex("0000000008000000"))
        self.assertEqual(tuple(c.op for c in decoded), (Op.BEGIN, Op.BIND_UBO, Op.END))
        self.assertEqual(tuple(c.offset for c in decoded), (0, 8, 40))
        self.assertEqual(struct.unpack("<iiQQ", decoded[1].payload), (0, 0, 0x1000, 0x230))

    def test_variable_update_owns_exact_payload_not_uninitialized_alignment_bytes(self):
        data = record(2) + record(7, struct.pack("<QQ", 0x1000, 3) + b"abc", padding=b"\xff") + record(0)
        decoded = decode_stream(data)
        self.assertEqual(decoded[1].payload, struct.pack("<QQ", 0x1000, 3) + b"abc")
        self.assertEqual(decoded[1].ordinal, 1)

    def test_unknown_pointer_state_and_indirect_opcodes_refuse_instead_of_skipping(self):
        for op in (8, 14, 44, 45, 46, 47, 48, 999):
            with self.subTest(op=op), self.assertRaisesRegex(ObservationError, "unsupported native opcode"):
                decode_stream(record(2) + record(op) + record(0))

    def test_truncation_size_wrap_missing_end_extra_bytes_and_nested_begin_fail(self):
        cases = (record(2) + struct.pack("<II", 25, 0), record(2) + struct.pack("<II", 25, 0xfffffff8),
                 record(2) + record(25, b""), record(2) + record(2) + record(0),
                 record(2) + record(0) + b"x", record(2) + record(27, bytes(16)),
                 record(0) + record(0), record(2) + struct.pack("<II", 7, 24) + struct.pack("<QQ", 0x1000, 65537))
        for data in cases:
            with self.subTest(data=data[:32]), self.assertRaises(ObservationError):
                decode_stream(data)

    def test_immutable_stream_and_record_limits_are_enforced(self):
        for data in (bytearray(record(2) + record(0)), b"x" * (MAX_STREAM + 1),
                     record(2) + record(27, bytes(16)) * MAX_COMMANDS + record(0)):
            with self.assertRaises(ObservationError):
                decode_stream(data)


class UniformAssociationTests(unittest.TestCase):
    def test_exact_inline_ordinal_yields_two_different_candidate_matrices_not_default_camera(self):
        observer, frame, adapter, attached = setup()
        a, b = attached.candidates
        self.assertEqual((a.command_ordinal, b.command_ordinal), (6, 8))
        self.assertEqual(struct.unpack("<f", struct.pack("<I", a.view_projection_words[0]))[0], 6.0)
        self.assertEqual(struct.unpack("<f", struct.pack("<I", b.view_projection_words[0]))[0], 9.0)
        self.assertEqual(a.read.require().overlay_commands, (5,))
        self.assertEqual(b.read.require().overlay_commands, (7,))
        self.assertEqual(tuple(struct.unpack("<f", struct.pack("<I", word))[0] for word in a.jitter_words), (-.125, .25))
        self.assertFalse(hasattr(a, "camera_valid"))
        self.assertFalse(hasattr(a, "primary_camera"))
        self.assertIs(observer.state(frame), State.PENDING)
        with self.assertRaisesRegex(ObservationError, "sealed present"):
            adapter.frame_observation(frame)

    def test_virtual_alias_update_uses_one_original_backing_and_conflicts_fail(self):
        _, _, _, attached = setup(copied(alias=True))
        self.assertEqual(struct.unpack("<f", struct.pack("<I", attached.candidates[0].view_projection_words[0]))[0], 6.0)
        copy = copied(alias=True)
        broken = replace(copy.ranges[1], data=b"!" + copy.ranges[1].data[1:])
        with self.assertRaisesRegex(ObservationError, "conflicting owned alias"):
            setup(replace(copy, ranges=(copy.ranges[0], broken)))

    def test_relocation_changes_stream_identity_but_not_normalized_local_record_identity(self):
        def run(copy):
            o, f, a, attached = setup(copy)
            present(a, f, attached.token)
            a.poll(TimelinePoll(SESSION, "rec-timeline-1", 1, 2, 30, 0))
            return attached, a.frame_observation(f)
        a, ar = run(copied())
        b, br = run(copied(0x9000, backing=777))
        self.assertNotEqual(a.stream_sha256, b.stream_sha256)
        self.assertEqual(ar.bookkeeping.canonical_frame_sha256, br.bookkeeping.canonical_frame_sha256)
        self.assertTrue(ar.unresolved)  # Matching candidate bytes do not certify a scene.

    def test_generation_origin_live_fallback_holes_and_mutable_ranges_refuse(self):
        copy = copied()
        owned = copy.ranges[0]
        cases = (replace(owned, origin=Origin(SESSION, 1, 2)),
                 replace(owned, origin=Origin(SESSION, 9, 1)),
                 replace(owned, source=Source.LIVE), replace(owned, source=Source.GPU_UNRESOLVED),
                 replace(owned, data=bytearray(owned.data)))
        for bad in cases:
            with self.subTest(bad=bad.origin), self.assertRaises(ObservationError):
                setup(replace(copy, ranges=(bad,)))
        # Keep mappings/captures on each side of a gap in a required projection span.
        ranges = (replace(owned, data=owned.data[:0x80]),
                  replace(owned, address=owned.address + 0x90, backing_offset=0x90, data=owned.data[0x90:]))
        with self.assertRaisesRegex(ObservationError, "unmapped"):
            setup(replace(copy, ranges=ranges))

    def test_missing_inherited_state_and_nonfinite_candidate_are_unknown(self):
        copy = copied()
        # BEGIN does not reset renderer state; the adapter cannot invent it.
        incomplete = record(2) + record(19, struct.pack("<iiQQ", 0, 0, 0x1000, 0x230)) + record(25, struct.pack("<5i", 3, 0, 3, 0, 1)) + record(0)
        with self.assertRaisesRegex(ObservationError, "explicit candidate"):
            setup(replace(copy, stream=incomplete))
        raw = bytearray(copy.ranges[0].data)
        struct.pack_into("<I", raw, 0x70, 0x7fc00000)
        with self.assertRaisesRegex(ObservationError, "nonfinite"):
            setup(replace(copy, ranges=(replace(copy.ranges[0], data=bytes(raw)),)))

    def test_layout_pattern_failure_remains_a_uniform_candidate_not_a_camera_choice(self):
        copy = copied()
        data = bytearray(copy.ranges[0].data)
        struct.pack_into("<f", data, 0xa8, 0)
        _, _, _, attached = setup(replace(copy, ranges=(replace(copy.ranges[0], data=bytes(data)),)))
        self.assertEqual(attached.candidates[0].layout_discrepancies, ("source perspective-row pattern absent",))

    def test_unclassified_unused_ubo_bytes_do_not_become_canonical_camera_fields(self):
        def digest(copy):
            _, f, a, attached = setup(copy)
            present(a, f, attached.token)
            a.poll(TimelinePoll(SESSION, "rec-timeline-1", 1, 2, 30, 0))
            return a.frame_observation(f).bookkeeping.logical_records_sha256
        copy = copied()
        changed = replace(copy.ranges[0], data=b"x" * 32 + copy.ranges[0].data[32:])
        self.assertEqual(len(changed.data), CANDIDATE_SIZE)
        self.assertEqual(digest(copy), digest(replace(copy, ranges=(changed,))))


class NativeEventTests(unittest.TestCase):
    def test_present_and_observed_rec_timeline_completion_are_both_required(self):
        o, f, a, attached = setup()
        a.poll(TimelinePoll(SESSION, "rec-timeline-1", 1, 1, 15, 0))
        self.assertIs(o.state(f), State.PENDING)
        present(a, f, attached.token)
        with self.assertRaisesRegex(ObservationError, "missing completion"):
            a.frame_observation(f)
        a.poll(TimelinePoll(SESSION, "rec-timeline-1", 2, 2, 30, 0))
        result = a.frame_observation(f)
        self.assertEqual(result.bookkeeping.completion_ns, 30)
        self.assertEqual(len(result.candidates), 2)
        self.assertTrue(result.unresolved)
        self.assertFalse(hasattr(result, "correctness_checked"))

    def test_wrong_stream_hash_session_reused_part_and_failed_submit_refuse(self):
        copy = copied()
        base = signal(copy)
        for bad in (replace(base, stream_sha256=b"x" * 32), replace(base, vk_result=-4),
                    replace(base, origin=Origin(bytes.fromhex("11" * 16), 1, 1)),
                    replace(base, submitted_at_ns=4), replace(base, vk_result=False)):
            o = Observer();f = o.begin_frame(FrameKind.NATIVE);a = ReplayAdapter(o, SESSION)
            with self.subTest(bad=bad), self.assertRaises(ObservationError):
                a.attach(f, copy, bad)
        _, f, a, attached = setup()
        with self.assertRaisesRegex(ObservationError, "reused"):
            changed = replace(base, stream_sha256=b"")
            a.present(f, PresentCopy(changed.origin, "window-1", 1, 0, (0, 0, 640, 360), 5), changed, (attached.token,))

    def test_new_generation_does_not_make_reused_native_ingress_identity_fresh(self):
        _, f, a, attached = setup()
        with self.assertRaisesRegex(ObservationError, "reused"):
            changed = RecordingSignal(Origin(SESSION, 1, 2), b"", "rec-timeline-1", 2, 2, 20, 0)
            a.present(f, PresentCopy(changed.origin, "window-1", 1, 0, (0, 0, 640, 360), 16), changed, (attached.token,))

    def test_wrong_semaphore_old_session_failure_boolean_and_predating_poll_refuse(self):
        for changes in ({"timeline_lifetime": "unowned"}, {"session": b"x" * 16},
                        {"vk_result": -4}, {"vk_result": False}, {"observed_at_ns": 9}):
            o, f, a, attached = setup()
            with self.subTest(changes=changes), self.assertRaises(ObservationError):
                a.poll(replace(TimelinePoll(SESSION, "rec-timeline-1", 1, 1, 30, 0), **changes))
            with self.assertRaises(ObservationError):
                a.frame_observation(f)

    def test_regressing_and_reused_poll_serials_cannot_complete_later_records(self):
        for serial, counter in ((1, 2), (2, 0)):
            _, f, a, attached = setup()
            a.poll(TimelinePoll(SESSION, "rec-timeline-1", 1, 1, 15, 0))
            present(a, f, attached.token)
            with self.assertRaisesRegex(ObservationError, "stale/regressing"):
                a.poll(TimelinePoll(SESSION, "rec-timeline-1", serial, counter, 30, 0))

    def test_present_metadata_requires_matching_origin_crop_and_native_event_order(self):
        for changes in ({"origin": Origin(SESSION, 9, 1)}, {"native_present_serial": 0},
                        {"crop": (0, 0, 640)}, {"queued_at_ns": 21}, {"texture_index": True}):
            _, f, a, attached = setup()
            event = RecordingSignal(Origin(SESSION, 2, 1), b"", "rec-timeline-1", 2, 2, 20, 0)
            copy = PresentCopy(event.origin, "window-1", 1, 0, (0, 0, 640, 360), 16)
            with self.subTest(changes=changes), self.assertRaises(ObservationError):
                a.present(f, replace(copy, **changes), event, (attached.token,))

    def test_present_on_other_timeline_needs_explicit_dependency(self):
        o, f, a, _ = setup()
        event = RecordingSignal(Origin(SESSION, 2, 1), b"", "other-timeline", 2, 1, 20, 0)
        with self.assertRaisesRegex(ObservationError, "missing from present dependency"):
            a.present(f, PresentCopy(event.origin, "window-1", 1, 0, (0, 0, 640, 360), 16), event, ())
        self.assertIs(o.state(f), State.UNKNOWN)


if __name__ == "__main__":
    unittest.main()
