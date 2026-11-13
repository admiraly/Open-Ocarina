# SPDX-License-Identifier: GPL-3.0-or-later
"""Authored CPU events only: no game, graphics runtime, driver or private inputs."""
import unittest

from tools.benchmark_observer import (
    Coverage, Field, FieldKind, FrameKind, FrameToken, MAGIC, ObservationError,
    Observer, Record, RecordKind, ResourceUse, Source, Stamp, State,
    SubmissionKind, SubmissionToken, canonical_records,
)


DRAW = Record(RecordKind.DRAW, (Field(1, FieldKind.U32, 3),))
DISPATCH = Record(RecordKind.DISPATCH, (Field(1, FieldKind.U32, 4),))


def covered(data=b"abcdefgh", *, stamp=Stamp(1, 1), source=Source.SNAPSHOT):
    c = Coverage(stamp)
    c.mapping(100, len(data), "allocation")
    c.capture(100, data, stamp, source)
    return c


def frame(observer, at_ns, *, queue="render", kind=FrameKind.NATIVE):
    token = observer.begin_frame(kind)
    work, present = observer.reserve(token), observer.reserve(token)
    observer.enqueue(work, queue, token.value * 2 - 1, SubmissionKind.WORK, 1)
    observer.enqueue(present, queue, token.value * 2, SubmissionKind.PRESENT, 0)
    observer.observe(work, 0, DRAW)
    observer.seal_frame(token, present)
    observer.completed(work, token.value * 2 - 1, at_ns - 1)
    observer.completed(present, token.value * 2, at_ns)
    return token


class CoverageTests(unittest.TestCase):
    def assert_unknown(self, result, reason):
        self.assertIs(result.state, State.UNKNOWN)
        self.assertIsNone(result.covered)
        with self.assertRaisesRegex(ObservationError, reason):
            result.require()

    def test_overlay_applies_only_before_consuming_ordinal_including_worker_cut(self):
        c = covered()
        c.overlay(2, 102, b"XY", Stamp(1, 1))
        c.overlay(5, 103, b"Z", Stamp(1, 1))
        c.seal()
        self.assertEqual(c.read(100, 8, Stamp(1, 1), 2).require().data, b"abcdefgh")
        before_later = c.read(100, 8, Stamp(1, 1), 3).require()
        self.assertEqual(before_later.data, b"abXYefgh")
        self.assertEqual(before_later.overlay_commands, (2,))
        self.assertEqual(c.read(100, 8, Stamp(1, 1), 6).require().data, b"abXZefgh")
        self.assertEqual(before_later.data, b"abXYefgh")  # Immutable prior observation.

    def test_generation_is_checked_for_each_range_not_just_first_page(self):
        c = Coverage(Stamp(1, 7))
        c.mapping(100, 8, "a")
        c.capture(100, b"abcd", Stamp(1, 7), Source.SNAPSHOT)
        c.capture(104, b"efgh", Stamp(1, 6), Source.SNAPSHOT)
        c.seal()
        self.assertEqual(c.read(100, 4, Stamp(1, 7), 0).require().data, b"abcd")
        self.assert_unknown(c.read(100, 8, Stamp(1, 7), 0), "generation mismatch")
        self.assert_unknown(c.read(100, 4, Stamp(2, 7), 0), "submission/generation mismatch")

    def test_live_fallback_unresolved_gpu_and_holes_never_return_partial_data(self):
        for source in (Source.LIVE, Source.GPU_UNRESOLVED, Source.UNKNOWN):
            with self.subTest(source=source):
                c = covered(source=source)
                c.seal()
                self.assert_unknown(c.read(100, 8, Stamp(1, 1), 0), "non-snapshot")
        c = Coverage(Stamp(1, 1))
        c.mapping(100, 8, "a")
        c.capture(100, b"abcd", Stamp(1, 1), Source.SNAPSHOT)
        c.seal()
        self.assert_unknown(c.read(100, 8, Stamp(1, 1), 0), "coverage hole")
        self.assert_unknown(c.read(99, 2, Stamp(1, 1), 0), "unmapped")

    def test_unresolved_gpu_observation_cannot_be_hidden_by_older_snapshot(self):
        c = covered()
        c.capture(102, b"??", Stamp(1, 1), Source.GPU_UNRESOLVED)
        c.seal()
        self.assert_unknown(c.read(100, 8, Stamp(1, 1), 0), "gpu_unresolved")

    def test_full_inline_write_can_cover_missing_base_but_partial_write_cannot(self):
        c = Coverage(Stamp(1, 1))
        c.mapping(100, 8, "a")
        c.overlay(1, 100, b"abcdefgh", Stamp(1, 1))
        c.seal()
        self.assert_unknown(c.read(100, 8, Stamp(1, 1), 1), "hole")
        self.assertEqual(c.read(100, 8, Stamp(1, 1), 2).require().data, b"abcdefgh")
        c = Coverage(Stamp(1, 1))
        c.mapping(100, 8, "a")
        c.overlay(1, 100, b"abcd", Stamp(1, 1))
        c.seal()
        self.assert_unknown(c.read(100, 8, Stamp(1, 1), 2), "hole")

    def test_newer_stale_overlay_cannot_reveal_older_valid_bytes(self):
        c = covered()
        c.overlay(1, 100, b"ABCDEFGH", Stamp(1, 1))
        c.overlay(2, 100, b"????????", Stamp(1, 2))
        c.seal()
        self.assert_unknown(c.read(100, 8, Stamp(1, 1), 3), "generation mismatch")

    def test_inline_write_is_visible_through_an_alias(self):
        c = covered()
        c.mapping(200, 8, "allocation")
        c.overlay(1, 202, b"XY", Stamp(1, 1))
        c.seal()
        self.assertEqual(c.read(100, 8, Stamp(1, 1), 2).require().data, b"abXYefgh")

    def test_reads_pending_until_seal_and_late_mutation_invalidates_ledger(self):
        c = covered()
        self.assertIs(c.read(100, 8, Stamp(1, 1), 0).state, State.PENDING)
        c.seal()
        with self.assertRaisesRegex(ObservationError, "mutated after seal"):
            c.overlay(1, 100, b"x", Stamp(1, 1))
        self.assertIs(c.read(100, 8, Stamp(1, 1), 0).state, State.INVALID)

    def test_late_ledger_failure_revokes_previously_known_range(self):
        c = covered()
        c.seal()
        result = c.read(100, 8, Stamp(1, 1), 0)
        self.assertIs(result.state, State.KNOWN)
        with self.assertRaises(ObservationError):
            c.mapping(200, 8, "b")
        self.assertIs(result.state, State.INVALID)
        with self.assertRaisesRegex(ObservationError, "mutated after seal"):
            result.require()

    def test_duplicate_write_ordinal_and_overlapping_mapping_fail_closed(self):
        c = covered()
        c.overlay(1, 100, b"x", Stamp(1, 1))
        with self.assertRaisesRegex(ObservationError, "duplicate inline"):
            c.overlay(1, 101, b"y", Stamp(1, 1))
        c = covered()
        with self.assertRaisesRegex(ObservationError, "overlapping virtual"):
            c.mapping(104, 8, "other")

    def test_coverage_storage_and_read_overflow_are_terminal(self):
        c = Coverage(Stamp(1, 1), max_bytes=4)
        c.mapping(100, 8, "a")
        with self.assertRaisesRegex(ObservationError, "capacity"):
            c.capture(100, b"abcdefgh", Stamp(1, 1), Source.SNAPSHOT)
        self.assertIs(c.read(100, 1, Stamp(1, 1), 0).state, State.INVALID)
        c = Coverage(Stamp(1, 1), max_read_bytes=4)
        c.mapping(100, 8, "a")
        c.capture(100, b"abcdefgh", Stamp(1, 1), Source.SNAPSHOT)
        c.seal()
        self.assertIs(c.read(100, 8, Stamp(1, 1), 0).state, State.INVALID)
        self.assertIs(c.read(100, 1, Stamp(1, 1), 0).state, State.INVALID)

    def test_u64_wrap_and_mutable_or_boolean_inputs_are_refused(self):
        for address, size in ((2**64 - 1, 2), (0, 0), (True, 4), (-1, 4)):
            with self.subTest(address=address, size=size), self.assertRaises(ValueError):
                Coverage(Stamp(1, 1)).mapping(address, size, "a")
        c = covered()
        with self.assertRaises(ValueError):
            c.capture(100, bytearray(b"x"), Stamp(1, 1), Source.SNAPSHOT)


class CanonicalTests(unittest.TestCase):
    def uses(self, *, relocation=0, aliased=True, offset=0, partition=False):
        c = Coverage(Stamp(1, 1))
        c.mapping(100 + relocation, 8, f"first-{relocation}")
        c.mapping(200 + relocation, 4, f"first-{relocation}" if aliased else f"second-{relocation}", offset)
        if partition:
            c.capture(100 + relocation, b"AAAA", Stamp(1, 1), Source.SNAPSHOT)
            c.capture(104 + relocation, b"AAAA", Stamp(1, 1), Source.SNAPSHOT)
        else:
            c.capture(100 + relocation, b"AAAAAAAA", Stamp(1, 1), Source.SNAPSHOT)
        if not aliased:
            c.capture(200 + relocation, b"AAAA", Stamp(1, 1), Source.SNAPSHOT)
        c.seal()
        return Record(RecordKind.DRAW, resources=(
            ResourceUse(0, c.read(100 + relocation, 4, Stamp(1, 1), 0)),
            ResourceUse(1, c.read(200 + relocation, 4, Stamp(1, 1), 0))))

    def test_fixed_width_little_endian_golden_and_float_bits(self):
        self.assertEqual(Field(7, FieldKind.U32, 0x12345678).encode().hex(), "07000178563412")
        self.assertEqual(Field(1, FieldKind.I32, -2).encode().hex(), "010002feffffff")
        self.assertEqual(Field(1, FieldKind.F32_BITS, 0x80000000).encode().hex(), "01000400000080")
        self.assertEqual(canonical_records(()), b"OOBENCHOBS\0\x01\0\0\0\0\0")
        self.assertNotEqual(Field(1, FieldKind.F32_BITS, 0).encode(),
                            Field(1, FieldKind.F32_BITS, 0x80000000).encode())

    def test_pointer_relocation_preserves_identity_alias_and_offset_changes_do_not(self):
        base = canonical_records((self.uses(),))
        self.assertEqual(base, canonical_records((self.uses(relocation=8192),)))
        self.assertNotEqual(base, canonical_records((self.uses(aliased=False),)))
        self.assertNotEqual(base, canonical_records((self.uses(offset=1),)))

    def test_snapshot_partition_does_not_change_resource_encoding(self):
        self.assertEqual(canonical_records((self.uses(),)), canonical_records((self.uses(partition=True),)))

    def test_field_order_is_canonical_but_record_order_and_payload_are_significant(self):
        fields = (Field(2, FieldKind.U64, 4), Field(1, FieldKind.U32, 3))
        self.assertEqual(canonical_records((Record(RecordKind.DRAW, fields),)),
                         canonical_records((Record(RecordKind.DRAW, tuple(reversed(fields))),)))
        self.assertNotEqual(canonical_records((DRAW, DISPATCH)), canonical_records((DISPATCH, DRAW)))
        self.assertNotEqual(canonical_records((DRAW,)), canonical_records((Record(RecordKind.DRAW,
                            (Field(1, FieldKind.U32, 4),)),)))

    def test_nonfinite_overflow_duplicate_tags_and_unknown_resource_are_rejected(self):
        for kind, value in ((FieldKind.U32, True), (FieldKind.U32, 2**32), (FieldKind.I32, -2**31 - 1),
                            (FieldKind.F32_BITS, 0x7f800000), (FieldKind.F64_BITS, 0x7ff8000000000000)):
            with self.subTest(kind=kind, value=value), self.assertRaises(ValueError):
                Field(0, kind, value).encode()
        with self.assertRaisesRegex(ValueError, "duplicate"):
            canonical_records((Record(RecordKind.DRAW, (Field(1, FieldKind.U32, 1), Field(1, FieldKind.U32, 2))),))
        c = covered(source=Source.LIVE)
        c.seal()
        with self.assertRaisesRegex(ObservationError, "non-snapshot"):
            canonical_records((Record(RecordKind.DRAW, resources=(ResourceUse(0, c.read(100, 8, Stamp(1, 1), 0)),)),))

    def test_canonical_limits_include_raw_inputs_not_only_their_small_hashes(self):
        with self.assertRaisesRegex(ObservationError, "input capacity"):
            canonical_records((Record(RecordKind.STATE_CANDIDATE,
                               (Field(1, FieldKind.HASHED_BYTES, b"x" * 129),)),), max_bytes=128)
        with self.assertRaisesRegex(ObservationError, "capacity"):
            canonical_records((), max_bytes=len(MAGIC))


class FrameTests(unittest.TestCase):
    def test_enqueue_is_pending_until_completions_and_all_declared_operations_arrive(self):
        o = Observer()
        f = o.begin_frame(FrameKind.NATIVE)
        work, present = o.reserve(f), o.reserve(f)
        o.enqueue(work, "q", 1, SubmissionKind.WORK, 1)
        o.enqueue(present, "q", 2, SubmissionKind.PRESENT, 0)
        o.seal_frame(f, present)
        with self.assertRaises(ObservationError) as raised:
            o.finalize_frame(f)
        self.assertIs(raised.exception.state, State.PENDING)
        o.completed(present, 2, 100)  # Collector delivery order need not equal dependency order.
        with self.assertRaisesRegex(ObservationError, "missing completion"):
            o.finalize_frame(f)
        o.completed(work, 1, 90)
        with self.assertRaisesRegex(ObservationError, "logical operations"):
            o.finalize_frame(f)
        o.observe(work, 0, DRAW)
        receipt = o.finalize_frame(f)
        self.assertEqual((receipt.completion_ns, receipt.operation_count), (100, 1))
        self.assertEqual(o.finalize_frame(f), receipt)

    def test_late_coverage_failure_invalidates_cached_frame_receipt(self):
        o = Observer()
        f = o.begin_frame(FrameKind.NATIVE)
        a = o.reserve(f)
        o.enqueue(a, "q", 1, SubmissionKind.PRESENT, 1)
        c = covered(stamp=Stamp(a.value, 1))
        c.seal()
        read = c.read(100, 8, Stamp(a.value, 1), 0)
        o.observe(a, 0, Record(RecordKind.DRAW, resources=(ResourceUse(0, read),)))
        o.seal_frame(f, a)
        self.assertIs(o.state(f), State.PENDING)
        o.completed(a, 1, 10)
        self.assertIs(o.state(f), State.COMPLETE)
        with self.assertRaises(ObservationError):
            c.overlay(1, 100, b"x", Stamp(a.value, 1))
        self.assertIs(o.state(f), State.INVALID)
        with self.assertRaisesRegex(ObservationError, "mutated after seal"):
            o.finalize_frame(f)

    def test_prior_frame_coverage_failure_blocks_a_cached_dependent_frame(self):
        o = Observer()
        first = o.begin_frame(FrameKind.NATIVE)
        a = o.reserve(first)
        o.enqueue(a, "q", 1, SubmissionKind.PRESENT, 1)
        c = covered(stamp=Stamp(a.value, 1))
        c.seal()
        o.observe(a, 0, Record(RecordKind.DRAW, resources=(ResourceUse(0, c.read(100, 8, Stamp(a.value, 1), 0)),)))
        o.seal_frame(first, a)
        o.completed(a, 1, 10)
        second = frame(o, 20, queue="q")
        self.assertIs(o.state(second), State.COMPLETE)
        with self.assertRaises(ObservationError):
            c.mapping(200, 4, "late")
        self.assertIs(o.state(second), State.INVALID)

    def test_dependency_edges_have_a_total_budget_before_graph_serialization(self):
        o = Observer(max_canonical_bytes=50)
        f = o.begin_frame(FrameKind.NATIVE)
        a, b, c, d = (o.reserve(f) for _ in range(4))
        o.enqueue(a, "a", 1, SubmissionKind.WORK, 0)
        o.enqueue(b, "b", 1, SubmissionKind.WORK, 0, (a,))
        o.enqueue(c, "c", 1, SubmissionKind.WORK, 0, (a, b))
        with self.assertRaisesRegex(ObservationError, "dependency capacity"):
            o.enqueue(d, "d", 1, SubmissionKind.PRESENT, 0, (a, b, c))
        self.assertIs(o.state(f), State.INVALID)

    def test_complete_frame_graph_must_fit_canonical_capacity(self):
        o = Observer(max_canonical_bytes=50)
        f = frame(o, 100)
        with self.assertRaisesRegex(ObservationError, "canonical frame capacity"):
            o.finalize_frame(f)
        self.assertIs(o.state(f), State.INVALID)

    def test_cross_queue_dependency_and_resume_preserve_single_logical_order(self):
        def run(delivery, queue_names):
            o = Observer()
            f = o.begin_frame(FrameKind.NATIVE)
            a, b, p = o.reserve(f), o.reserve(f), o.reserve(f)
            o.enqueue(a, queue_names[0], 17, SubmissionKind.WORK, 2)
            o.enqueue(b, queue_names[1], 88, SubmissionKind.WORK, 1, (a,))
            o.enqueue(p, queue_names[1], 89, SubmissionKind.PRESENT, 0)
            o.seal_frame(f, p)
            for index in delivery:  # An interrupted/resumed stream keeps its token and ordinals.
                o.observe(a, index, (DRAW, DISPATCH)[index])
            o.observe(b, 0, DRAW)
            o.completed(a, 17, 10)
            o.completed(b, 88, 20)
            o.completed(p, 89, 30)
            return o.finalize_frame(f)
        first = run((0, 1), ("graphics", "compute"))
        resumed = run((1, 0), (100000, 200000))
        self.assertEqual(first.logical_records_sha256, resumed.logical_records_sha256)
        self.assertEqual(first.canonical_frame_sha256, resumed.canonical_frame_sha256)
        self.assertEqual(first.operation_count, 3)

    def test_missing_cross_queue_dependency_and_reserved_unenqueued_work_fail_closed(self):
        for enqueue_work in (False, True):
            with self.subTest(enqueue_work=enqueue_work):
                o = Observer()
                f = o.begin_frame(FrameKind.NATIVE)
                a, p = o.reserve(f), o.reserve(f)
                if enqueue_work:
                    o.enqueue(a, "q1", 1, SubmissionKind.WORK, 0)
                o.enqueue(p, "q2", 1, SubmissionKind.PRESENT, 0, () if enqueue_work else (a,))
                with self.assertRaises(ObservationError) as raised:
                    o.seal_frame(f, p)
                self.assertIs(raised.exception.state, State.UNKNOWN)
                with self.assertRaises(ObservationError):
                    o.finalize_frame(f)

    def test_missing_external_dependency_and_cross_observer_tokens_are_refused(self):
        o = Observer()
        f = o.begin_frame(FrameKind.NATIVE)
        a = o.reserve(f)
        with self.assertRaisesRegex(ObservationError, "unknown submission"):
            o.enqueue(a, "q", 1, SubmissionKind.WORK, 0, (SubmissionToken(99),))
        other = Observer()
        foreign = other.begin_frame(FrameKind.NATIVE)
        o = Observer()
        o.begin_frame(FrameKind.NATIVE)
        with self.assertRaisesRegex(ObservationError, "unknown frame"):
            o.reserve(foreign)
        self.assertNotEqual(foreign, FrameToken(foreign.value))

    def test_cycle_is_invalid_even_with_all_tokens_reserved(self):
        o = Observer()
        f = o.begin_frame(FrameKind.NATIVE)
        a, b, p = o.reserve(f), o.reserve(f), o.reserve(f)
        o.enqueue(a, "q1", 1, SubmissionKind.WORK, 0, (b,))
        o.enqueue(b, "q2", 1, SubmissionKind.WORK, 0, (a,))
        o.enqueue(p, "q3", 1, SubmissionKind.PRESENT, 0, (a, b))
        with self.assertRaisesRegex(ObservationError, "cycle"):
            o.seal_frame(f, p)

    def test_duplicate_ordinal_from_dry_and_worker_walks_invalidates_frame(self):
        o = Observer()
        f = o.begin_frame(FrameKind.NATIVE)
        a = o.reserve(f)
        o.enqueue(a, "q", 1, SubmissionKind.WORK, 1)
        o.observe(a, 0, DRAW)
        with self.assertRaisesRegex(ObservationError, "duplicate"):
            o.observe(a, 0, DRAW)
        with self.assertRaisesRegex(ObservationError, "duplicate"):
            o.finalize_frame(f)

    def test_insufficient_timeline_and_impossible_completion_times_are_rejected(self):
        o = Observer()
        f = o.begin_frame(FrameKind.NATIVE)
        a = o.reserve(f)
        o.enqueue(a, "q", 10, SubmissionKind.PRESENT, 0)
        o.seal_frame(f, a)
        with self.assertRaises(ObservationError) as raised:
            o.completed(a, 9, 100)
        self.assertIs(raised.exception.state, State.UNKNOWN)
        o = Observer()
        f = o.begin_frame(FrameKind.NATIVE)
        a, p = o.reserve(f), o.reserve(f)
        o.enqueue(a, "q", 1, SubmissionKind.WORK, 0)
        o.enqueue(p, "q", 2, SubmissionKind.PRESENT, 0)
        o.seal_frame(f, p)
        o.completed(a, 1, 200)
        o.completed(p, 2, 100)
        with self.assertRaisesRegex(ObservationError, "precedes dependency"):
            o.finalize_frame(f)

    def test_resource_range_must_belong_to_the_same_submission_and_command(self):
        for stamp, command in ((Stamp(99, 1), 0), (Stamp(1, 1), 1)):
            with self.subTest(stamp=stamp, command=command):
                o = Observer()
                f = o.begin_frame(FrameKind.NATIVE)
                a = o.reserve(f)
                o.enqueue(a, "q", 1, SubmissionKind.WORK, 1)
                c = covered(stamp=stamp)
                c.seal()
                record = Record(RecordKind.DRAW, resources=(ResourceUse(0, c.read(100, 8, stamp, command)),))
                with self.assertRaisesRegex(ObservationError, "another submission/command"):
                    o.observe(a, 0, record)

    def test_unknown_resource_taints_frame_without_exporting_a_digest(self):
        o = Observer()
        f = o.begin_frame(FrameKind.NATIVE)
        a = o.reserve(f)
        o.enqueue(a, "q", 1, SubmissionKind.WORK, 1)
        c = covered(source=Source.GPU_UNRESOLVED)
        c.seal()
        with self.assertRaises(ObservationError) as raised:
            o.observe(a, 0, Record(RecordKind.DRAW, resources=(ResourceUse(0, c.read(100, 8, Stamp(a.value, 1), 0)),)))
        self.assertIs(raised.exception.state, State.UNKNOWN)
        with self.assertRaisesRegex(ObservationError, "gpu_unresolved"):
            o.finalize_frame(f)

    def test_n_intervals_require_n_plus_one_completed_native_boundaries(self):
        o = Observer()
        frames = tuple(frame(o, t) for t in (100, 120, 150, 190))
        result = o.window(frames)
        self.assertEqual((result.native_intervals, result.start_ns, result.end_ns, result.elapsed_ns), (3, 100, 190, 90))
        self.assertEqual(tuple(r.token for r in result.frames), frames[1:])
        with self.assertRaisesRegex(ValueError, "N\\+1"):
            o.window((frames[0],))
        with self.assertRaisesRegex(ValueError, "consecutive"):
            o.window((frames[0], frames[2]))

    def test_generated_frames_and_nonincreasing_boundary_times_cannot_enter_metric(self):
        o = Observer()
        a = frame(o, 100)
        b = frame(o, 200, kind=FrameKind.GENERATED)
        with self.assertRaisesRegex(ObservationError, "generated frame"):
            o.window((a, b))
        o = Observer()
        a = frame(o, 100, queue="q1")
        b = frame(o, 100, queue="q2")
        with self.assertRaisesRegex(ObservationError, "nonincreasing"):
            o.window((a, b))

    def test_all_storage_bounds_poison_observer_instead_of_dropping_records(self):
        o = Observer(max_frames=1)
        o.begin_frame(FrameKind.NATIVE)
        with self.assertRaisesRegex(ObservationError, "frame capacity"):
            o.begin_frame(FrameKind.NATIVE)
        o = Observer(max_submissions=1)
        f = o.begin_frame(FrameKind.NATIVE)
        o.reserve(f)
        with self.assertRaisesRegex(ObservationError, "submission capacity"):
            o.reserve(f)
        for options in ({"max_records": 1}, {"max_observed_bytes": 7}):
            with self.subTest(options=options):
                o = Observer(**options)
                f = o.begin_frame(FrameKind.NATIVE)
                a = o.reserve(f)
                o.enqueue(a, "q", 1, SubmissionKind.WORK, 2)
                record = Record(RecordKind.STATE_CANDIDATE, (Field(1, FieldKind.HASHED_BYTES, b"abcd"),))
                o.observe(a, 0, record)
                with self.assertRaisesRegex(ObservationError, "capacity"):
                    o.observe(a, 1, record)
                with self.assertRaisesRegex(ObservationError, "capacity"):
                    o.begin_frame(FrameKind.NATIVE)

    def test_dependency_structure_changes_frame_identity_without_changing_record_sequence(self):
        def run(dependent):
            o = Observer()
            f = o.begin_frame(FrameKind.NATIVE)
            a, b, p = o.reserve(f), o.reserve(f), o.reserve(f)
            o.enqueue(a, "q1", 1, SubmissionKind.WORK, 1)
            o.enqueue(b, "q2", 1, SubmissionKind.WORK, 1, (a,) if dependent else ())
            o.enqueue(p, "q3", 1, SubmissionKind.PRESENT, 0, (a, b))
            o.observe(a, 0, DRAW)
            o.observe(b, 0, DISPATCH)
            o.seal_frame(f, p)
            for token, time in ((a, 1), (b, 2), (p, 3)):
                o.completed(token, 1, time)
            return o.finalize_frame(f)
        a, b = run(False), run(True)
        self.assertEqual(a.logical_records_sha256, b.logical_records_sha256)
        self.assertNotEqual(a.canonical_frame_sha256, b.canonical_frame_sha256)


if __name__ == "__main__":
    unittest.main()
