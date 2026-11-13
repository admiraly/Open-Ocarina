# SPDX-License-Identifier: GPL-3.0-or-later
"""CPU-only same-dispatch metadata arithmetic and producer-denial regressions."""
import bisect
from pathlib import Path
import random
import shutil
import struct
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
PATCH = ROOT / "patches/hookshot/compute-interval-same-dispatch.patch"
SELECTORS = (192, 208, 256, 272, 352, 2176, 2192, 2208)
U64 = (1 << 64) - 1


def source():
    lines = []
    for line in PATCH.read_text().splitlines(keepends=True):
        if line.startswith("+") and not line.startswith("+++"):
            lines.append(line[1:])
    return "".join(lines)


# Every lifecycle value below is an AUTHORED fixture. The math transport also
# supports missing producer facts; it never fabricates a historical lease.
HARNESS = r'''
#include "gpu/vk/compute_interval_same_dispatch.h"
#include <iostream>
using namespace open_ocarina::same_dispatch_intervals;
static_assert(!Plan::live_certificate && !Prepared::live_certificate,
              "metadata values are not live certificates");
static_assert(!Plan::replacement_admitted && !Prepared::replacement_admitted,
              "metadata values do not admit shader replacement");
static U read(unsigned n){U x=0;for(unsigned i=0;i<n;++i){int c=std::cin.get();if(c<0)throw std::runtime_error("fixture truncated");x|=U(uint8_t(c))<<(8*i);}return x;}
static Owner owner(U buffer,U memory) {
 Owner o;o.creation=Creation::VmaAllocationInfo2;o.journal=55303;o.buffer=buffer;o.allocation=buffer+9000;o.memory=memory;
 o.resource_generation=871;o.memory_generation=41;o.resource_size=o.allocation_size=o.memory_size=16384;o.mapping=buffer+19000;
 o.usage=0x33;o.dedicated_memory=1;o.current_memory=memory;o.current_memory_generation=41;o.memory_property_flags=6;return o;
}
static Stream stream(const Owner&o,const Cut& c,U used) {
 Stream s;s.policy=StreamPolicy::AppendOnlyUntilRecordingSlotCompletion;s.flush=FlushPolicy::RegisteredRecordingSubmitFlush;
 s.journal=c.journal;s.recording_epoch=c.recording_epoch;s.recording_part=c.recording_part;s.command_buffer=c.command_buffer;s.slot=c.slot;
 s.generation=114;s.buffer=o.buffer;s.allocation=o.allocation;s.owner_generation=o.resource_generation;s.mapping=o.mapping;s.size=o.resource_size;s.used=used;return s;
}
static Upload upload(const Owner&o,const Stream&s,U offset,U bytes,U event){return{o.journal,event,s.recording_epoch,s.generation,o.resource_generation,o.buffer,offset,bytes};}
static Copy copy(const Owner&o,U offset,U bytes,U event){return{CopyPolicy::MappedCopyAndStampSerialized,o.journal,event,o.resource_generation,o.mapping,o.buffer,offset,bytes};}
static void facts(MetadataSameDispatch&m) {
 Cut c;c.journal=55303;c.serial=200;c.command_buffer=0xabc;c.recording_epoch=981;c.recording_part=7498;c.slot=3;c.dispatch_instance=150;
 c.writer=0x3555;c.built_generation=c.current_generation=140;c.program=0x4444;c.current_infos=m.infos;m.cut=c;
 SelectedC0 f;f.owner=owner(m.infos[0].buffer,0xb12);f.stream=stream(f.owner,c,m.infos[0].offset+2224);
 f.uploads.push_back(upload(f.owner,f.stream,m.infos[0].offset,2224,120));f.copy=copy(f.owner,m.infos[0].offset,2224,200);m.selected=f;
 Writes w;w.journal=c.journal;w.cut_event=c.serial;w.state=JournalState::Recording;m.writes=w;
}
static void write(MetadataSameDispatch&m,WriteKind k,U buffer,U offset,U bytes,U event=110,U seq=0) {
 m.writes->events.push_back({k,m.cut->journal,event,m.cut->recording_epoch,buffer,offset,bytes,seq});m.writes->observed_count=m.writes->events.size();
}
int main(int argc,char**argv) {
 try {
  const std::string test=argc>1?argv[1]:"math";MetadataSameDispatch m;
  m.identity={0xcc62764061c44f6aULL,0x095931037888515aULL,5,32,1,kModule};
  for(unsigned i=0;i<10;++i)m.infos.push_back({uint32_t(read(4)),uint32_t(read(4)),uint32_t(read(4)),read(8),read(8),read(8)});
  C0Bytes b;for(auto&v:b)v=uint8_t(read(1));m.c0=b;const U sizes=read(4);if(sizes>32)throw std::runtime_error("size fixture cap");
  for(U i=0;i<sizes;++i){const U handle=read(8),size=read(8);m.buffer_sizes.emplace(handle,size);}
  if(std::cin.get()!=std::char_traits<char>::eof())throw std::runtime_error("trailing fixture");
  if(test!="math")facts(m);
  if(test=="bad-identity")m.identity.module="unknown";
  if(test=="bad-layout")m.infos[3].binding=7;
  if(test=="no-owned-C0")m.c0.reset();
  if(test=="no-cut")m.cut.reset();
  if(test=="no-selected")m.selected.reset();
  if(test=="no-journal")m.writes.reset();
  if(test=="wrong-writer-generation")m.cut->current_generation++;
  if(test=="stale-current-infos")m.cut->current_infos[2].range++;
  if(test=="stale-memory")m.selected->owner.current_memory_generation++;
  if(test=="shared-C0")m.selected->owner.dedicated_memory=0;
  if(test=="BDA-C0")m.selected->owner.usage|=kBdaUsage;
  if(test=="no-C0-policy")m.selected->stream.policy=StreamPolicy::Unknown;
  if(test=="stale-stream")m.selected->stream.owner_generation++;
  if(test=="missing-upload")m.selected->uploads.clear();
  if(test=="partial-upload")m.selected->uploads[0].bytes--;
  if(test=="superseded-upload"){auto u=m.selected->uploads[0];u.event=160;m.selected->uploads.push_back(u);}
  if(test=="disjoint-new-upload")m.selected->uploads.push_back(upload(m.selected->owner,m.selected->stream,0,16,160));
  if(test=="unserialized-copy")m.selected->copy.policy=CopyPolicy::Unknown;
  const U at=m.infos[0].offset+192;
  if(test=="pending-GPU-before-copy")write(m,WriteKind::GpuBuffer,m.infos[0].buffer,at,16);
  if(test=="completed-GPU-before-copy"){write(m,WriteKind::GpuBuffer,m.infos[0].buffer,at,16,110,7);m.writes->completed_sequence=7;}
  if(test=="host-before-upload")write(m,WriteKind::HostMapped,m.infos[0].buffer,at,16,110);
  if(test=="host-after-upload")write(m,WriteKind::HostMapped,m.infos[0].buffer,at,16,160);
  if(test=="indirect-grid-copy")write(m,WriteKind::GpuBuffer,m.infos[0].buffer,m.infos[0].offset,12,180);
  if(test=="physical-class")write(m,WriteKind::PhysicalAddress,0,0,U(-1));
  if(test=="image-class")write(m,WriteKind::ImageOnly,0,0,U(-1));
  if(test=="unknown-class")write(m,WriteKind::Unknown,0,0,0);
  if(test=="unknown-buffer")write(m,WriteKind::GpuBuffer,0,0,0);
  if(test=="other-buffer")write(m,WriteKind::GpuBuffer,0xdead,0,U(-1));
  if(test=="journal-count")m.writes->observed_count=17;
  if(test=="journal-stopped")m.writes->state=JournalState::CapacityStopped;
  if(test=="callback-failure")m.writes->callback_failures=1;
  if(test=="write-domain"){write(m,WriteKind::GpuBuffer,0xdead,0,16);m.writes->events[0].journal++;}
  if(test=="binding6-alias"||test=="binding6-disjoint") {
   m.infos[6].buffer=m.infos[0].buffer;m.infos[6].offset=test=="binding6-alias"?at:0;m.infos[6].range=16;m.cut->current_infos=m.infos;
  }
  if(test=="record-covering-upload-tail") {
   m.selected->uploads[0].bytes+=1024;m.selected->stream.used+=1024;
  }
  if(test=="record-prior-unbound-allocation") {
   const U old=m.selected->stream.used;m.selected->stream.used+=1024;
   m.selected->uploads.push_back(upload(m.selected->owner,m.selected->stream,old,1024,100));
  }
  const auto calculation=calculate(m);
  for(const auto&d:calculation.denials)std::cerr<<"calculate:"<<d.code<<'\n';
  std::cerr<<"live_certificate:"<<(Plan::live_certificate||Prepared::live_certificate)
           <<"\nreplacement_admitted:"<<(Plan::replacement_admitted||Prepared::replacement_admitted)<<'\n';
  if(!calculation.plan)return 2;
  const auto& plan=*calculation.plan;
  if(test!="math") {
   MetadataSameDispatch current=m;
   if(current.cut){current.cut->serial=230;if(current.writes)current.writes->cut_event=230;if(current.selected)current.selected->copy.event=230;}
   RecordUpload r;r.info={12,0,7,0xa55,0,U(plan.bytes().size())};r.owner=owner(r.info.buffer,0xb55);
   if(current.cut)r.stream=stream(r.owner,*current.cut,r.info.range);
   r.upload=upload(r.owner,r.stream,0,r.info.range,220);r.copy=copy(r.owner,0,r.info.range,220);r.copied_bytes=plan.bytes();
   r.reservation=RecordUpload::Reservation{r.owner.journal,210,r.owner.buffer,r.owner.resource_generation,r.stream.generation,0};
   if(test=="missing-record"){}
   if(test=="record-bytes")r.copied_bytes[0]^=1;
   if(test=="record-copy-stamp")r.copy.event++;
   if(test=="record-owner")r.owner.resource_generation++;
   if(test=="record-slot")r.stream.slot++;
   if(test=="record-no-retention")r.stream.policy=StreamPolicy::Unknown;
   if(test=="record-no-registration")r.stream.flush=FlushPolicy::Unknown;
   if(test=="record-no-visibility")r.owner.memory_property_flags.reset();
   if(test=="record-noncoherent"||test=="record-flushed"||test=="record-flush-before-upload"||test=="record-flush-failed"||test=="record-flush-short") {
    r.owner.memory_property_flags=2;
    if(test!="record-noncoherent")r.flush=RecordUpload::Flush{r.owner.journal,225,r.info.buffer,r.owner.resource_generation,0,r.info.range,0};
    if(test=="record-flush-before-upload")r.flush->event=219;
    if(test=="record-flush-failed")r.flush->result=-4;
    if(test=="record-flush-short")r.flush->bytes--;
   }
   if(test=="record-conflicting-memory")r.owner.memory=r.owner.current_memory=m.selected->owner.memory;
   if(test=="record-original-overlap") {
    r.owner=current.selected->owner;r.stream=current.selected->stream;r.info.buffer=r.owner.buffer;r.info.offset=m.infos[0].offset;
    r.upload=upload(r.owner,r.stream,r.info.offset,r.info.range,220);r.copy=copy(r.owner,r.info.offset,r.info.range,220);
   }
   if(test=="record-same-stream-append"||test=="record-aligned-append"||test=="record-forged-previous-used"||test=="record-prefix-hole"||test=="record-covering-upload-tail"||test=="record-prior-unbound-allocation") {
    r.owner=current.selected->owner;r.stream=current.selected->stream;r.info.buffer=r.owner.buffer;
    const U previous=r.stream.used;r.info.offset=previous;
    if(test=="record-aligned-append")r.info.offset=(previous+255)&~U(255);
    if(test=="record-prefix-hole")r.info.offset=0;
    if(test=="record-covering-upload-tail")r.info.offset=m.infos[0].offset+2224;
    if(test=="record-prior-unbound-allocation")r.info.offset=m.infos[0].offset+2224+16;
    r.stream.used=std::max(previous,r.info.offset+r.info.range);
    r.reservation=RecordUpload::Reservation{r.owner.journal,210,r.owner.buffer,r.owner.resource_generation,r.stream.generation,previous};
    if(test=="record-forged-previous-used")r.reservation->previous_used=0;
    current.selected->stream.used=r.stream.used;r.upload=upload(r.owner,r.stream,r.info.offset,r.info.range,220);r.copy=copy(r.owner,r.info.offset,r.info.range,220);
    current.selected->uploads.push_back(r.upload);write(current,WriteKind::HostMapped,r.info.buffer,r.info.offset,r.info.range,210);
   }
   if(test=="record-no-reservation")r.reservation.reset();
   if(test=="record-reservation-before-plan")r.reservation->event=199;
   if(test=="record-wrong-reservation-stream")r.reservation->stream_generation++;
   if(test=="record-GPU-write")write(current,WriteKind::GpuBuffer,r.info.buffer,0,r.info.range,210);
   if(test=="changed-original-root"){current.infos[2].range++;current.cut->current_infos=current.infos;}
   if(test=="changed-C0")(*current.c0)[192]^=1;
   if(test=="changed-dispatch")current.cut->command_buffer++;
   if(test=="reused-writer-next-dispatch")current.cut->dispatch_instance++;
   const auto prepared=prepare(plan,current,test=="missing-record"?std::optional<RecordUpload>{}:std::optional<RecordUpload>{r});
   for(const auto&d:prepared.denials())std::cerr<<"prepare:"<<d.code<<'\n';
   std::cerr<<"metadata_consistent:"<<(prepared.metadata_consistent()?1:0)<<'\n';
   if(prepared.metadata_consistent()&&prepared.original_infos()!=m.infos)throw std::runtime_error("original roots changed");
  }
  std::cout.write(reinterpret_cast<const char*>(plan.bytes().data()),plan.bytes().size());return 0;
 }catch(const std::exception&e){std::cerr<<e.what()<<'\n';return 3;}
}
'''


def fixture(specs):
    infos = [(0, 0, 6, 101, 64, 2224), (1, 0, 6, 102, 0, 416)]
    c0 = bytearray(2224)
    for i, (guest, size, bound) in enumerate(specs):
        struct.pack_into("<QI", c0, SELECTORS[i], guest, size)
        infos.append((i + 2 if i < 5 else 7, 0 if i < 5 else i - 5, 7, 200 + i if bound else 0, 0, bound))
    return infos, c0, {}


def encode(data):
    infos, c0, sizes = data
    return b"".join(struct.pack("<IIIQQQ", *v) for v in infos) + c0 + struct.pack("<I", len(sizes)) + b"".join(struct.pack("<QQ", h, size) for h, size in sizes.items())


def certify(specs, blob):
    n4, n16, start4, start16 = struct.unpack_from("<4I", blob)
    assert (start4, start16) == (0, n4) and len(blob) == 16 + 48 * (n4 + n16)
    at = 16
    for width, count in ((4, n4), (16, n16)):
        claims = []
        for i, (guest, size, bound) in enumerate(specs):
            base = guest - guest % 16
            typed = (base + (((bound // width) % (1 << 32)) * width) % (1 << 32)) % (1 << 64)
            native = (guest + size) % (1 << 64)
            claims.append((base, typed if i >= 5 and size == 0 else min(typed, native), i + 2 if i < 5 else 7, 0 if i < 5 else i - 5))
        ends = {0, 1 << 64}
        for base, limit, _, _ in claims:
            ends.update((base, limit))
        rows = []
        for _ in range(count):
            low, high, base, binding, element, w, route, z0, z1 = struct.unpack_from("<QQQ6I", blob, at); at += 48
            assert w == width and route == (binding - 2 if binding < 7 else element + 5) and z0 == z1 == 0
            assert base <= low < high and (not rows or rows[-1][1] <= low)
            rows.append((low, high, base, binding, element)); ends.update((low, high))
        starts = [r[0] for r in rows]
        ends = sorted(ends)
        for low, high in zip(ends, ends[1:]):
            for address in (low, high - 1):
                old = next(((binding, element, base) for base, end, binding, element in claims if base <= address < end), None)
                i = bisect.bisect_right(starts, address) - 1
                new = (rows[i][3], rows[i][4], rows[i][2]) if i >= 0 and address < rows[i][1] else None
                assert old == new, (width, address, old, new)


@unittest.skipUnless(shutil.which("c++"), "Optional C++17 CPU metadata check")
class SameDispatchIntervalTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory(); cls.directory = Path(cls.temp.name)
        (cls.directory / "gpu/vk").mkdir(parents=True)
        (cls.directory / "gpu/vk/compute_interval_same_dispatch.h").write_text(source())
        path = cls.directory / "harness.cpp"; path.write_text(HARNESS); cls.exe = cls.directory / "fixture"
        result = subprocess.run(["c++", "-std=c++17", "-O2", "-Wall", "-Wextra", "-Werror", "-pedantic", str(path), "-o", str(cls.exe)], capture_output=True, text=True, timeout=45)
        if result.returncode:
            cls.temp.cleanup(); raise AssertionError(result.stderr)

    @classmethod
    def tearDownClass(cls):
        cls.temp.cleanup()

    def call(self, data=None, case="ready", denial=None, fatal=False):
        if data is None:
            data = fixture([(0x1000 + i * 0x1000, 64, 64) for i in range(8)])
        result = subprocess.run([str(self.exe), case], input=encode(data), capture_output=True, timeout=5)
        self.assertEqual(result.returncode, 2 if fatal else 0, result.stderr.decode())
        self.assertIn("live_certificate:0", result.stderr.decode())
        self.assertIn("replacement_admitted:0", result.stderr.decode())
        if denial:
            self.assertIn(denial, result.stderr.decode())
            self.assertNotIn("metadata_consistent:1", result.stderr.decode())
        elif case != "math":
            self.assertIn("metadata_consistent:1", result.stderr.decode())
        return result.stdout, result.stderr.decode()

    def test_metadata_scope_calculates_without_fabricated_live_or_owned_lease(self):
        specs = [(0x1000 + i * 0x1000, 64, 64) for i in range(8)]
        blob, status = self.call(fixture(specs), "math")
        certify(specs, blob)
        for reason in ("actual_current_pending_dispatch_cut_missing", "selected_C0_owner_upload_stream_facts_missing", "same_dispatch_write_journal_cut_missing"):
            self.assertIn(reason, status)

    def test_record_requires_new_suffix_after_the_committed_stream_prefix(self):
        data = fixture([(0x1000 + i * 0x1000, 64, 64) for i in range(8)])
        # The first hole is disjoint from every current descriptor but may hold
        # a previous draw's allocation. A covering upload's tail is occupied too.
        first = data[0][0]
        data[0][0] = (*first[:4], 2048, first[5])
        self.call(data, "record-prefix-hole",
                  denial="record_post_plan_append_reservation_missing_or_mismatched")
        for case in ("record-covering-upload-tail", "record-prior-unbound-allocation",
                     "record-forged-previous-used"):
            with self.subTest(case=case):
                self.call(case=case,
                          denial="record_reservation_overwrites_committed_stream_prefix")
        self.call(case="record-aligned-append")

    def test_record_reservation_is_observed_after_plan_on_the_same_stream(self):
        for case in ("record-no-reservation", "record-reservation-before-plan",
                     "record-wrong-reservation-stream"):
            with self.subTest(case=case):
                self.call(case=case,
                          denial="record_post_plan_append_reservation_missing_or_mismatched")

    def test_original_roots_need_no_new_import_payload_or_physical_retains(self):
        self.call()
        text = source()
        self.assertNotIn("OwnedFrozenGraph", text.replace("// This scope never constructs an OwnedFrozenGraph or a live certificate.", ""))
        for forbidden in ('#include "compute_interval_records.h"', "guest_pages", "ImportLease", "RetainSource"):
            self.assertNotIn(forbidden, text)

    def test_complete_domain_partitions_match_independent_source_walk(self):
        rng = random.Random(902733)
        for n in range(120):
            specs = [(rng.choice((rng.randrange(96), 0x1000 + rng.randrange(48), U64 - rng.randrange(48))), rng.choice((0, 1, 3, 64, 0xffffffff)), rng.choice((0, 4, 10, 16, 64, (1 << 32) + 16, 1 << 34))) for _ in range(8)]
            with self.subTest(case=n):
                blob, _ = self.call(fixture(specs), "ready"); certify(specs, blob)

    def test_zero_unaligned_overlap_typed_floor_and_wrap_edges(self):
        cases = [
            [(0x1003, 0, 64)] + [(0, 0, 0)] * 4 + [(0x1000, 0, 64), (0, 0, 0), (0, 0, 0)],
            [(0x1000, 64, 10), (0x1000, 64, 64)] + [(0, 0, 0)] * 6,
            [(0, 0, 0)] * 5 + [(0x1000, 0, (1 << 32) + 16), (0x1000, 0, 1 << 34), (0, 0, 0)],
            [(U64 - 15, 32, 64)] + [(0, 0, 0)] * 7,
            [(0, 0, 0)] * 8,
        ]
        for specs in cases:
            with self.subTest(specs=specs):
                blob, _ = self.call(fixture(specs)); certify(specs, blob)

    def test_null_and_whole_size_resolution_preserve_actual_typed_extent(self):
        specs = [(0, 0, 0)] * 5 + [(0x1000, 64, 16), (0x1000, 64, 0), (0x2000, 0, 64)]
        data = fixture(specs); data[0][7] = (*data[0][7][:-1], U64); data[2][205] = 16
        data[0][8] = (*data[0][8][:-1], U64)
        blob, _ = self.call(data); certify(specs, blob)
        data[2].clear()
        self.call(data, denial="whole_size_resource_extent_missing", fatal=True)

    def test_pending_cut_and_selector_owner_upload_unknowns_are_specific_denials(self):
        for case, reason in (
            ("no-cut", "actual_current_pending_dispatch_cut_missing"), ("no-selected", "selected_C0_owner_upload_stream_facts_missing"),
            ("no-journal", "same_dispatch_write_journal_cut_missing"), ("wrong-writer-generation", "pending_writer_generation_or_same_dispatch_mismatch"),
            ("stale-current-infos", "pending_writer_generation_or_same_dispatch_mismatch"), ("stale-memory", "C0_dedicated_nonBDA_current_VMA_owner_unproved"),
            ("shared-C0", "C0_dedicated_nonBDA_current_VMA_owner_unproved"), ("BDA-C0", "C0_dedicated_nonBDA_current_VMA_owner_unproved"),
            ("no-C0-policy", "selected_C0_retained_recording_stream_unproved"), ("stale-stream", "selected_C0_retained_recording_stream_unproved"),
            ("missing-upload", "selected_C0_fresh_upload_missing"), ("partial-upload", "selected_C0_fresh_upload_missing"),
            ("superseded-upload", "C0_upload_superseded_pending_writer"), ("unserialized-copy", "actual_serialized_C0_copy_missing"),
            ("journal-count", "write_journal_cut_or_count_mismatch"), ("write-domain", "write_event_domain_or_cut_mismatch"),
            ("journal-stopped", "producer_journal_stopped_failed_or_callbacks_missing"), ("callback-failure", "producer_journal_stopped_failed_or_callbacks_missing")):
            with self.subTest(case=case): self.call(case=case, denial=reason)

    def test_pending_gpu_write_not_discharged_by_cpu_record_order(self):
        self.call(case="pending-GPU-before-copy", denial="pending_writer_may_change_selectors")
        self.call(case="completed-GPU-before-copy")
        self.call(case="host-before-upload")
        self.call(case="host-after-upload", denial="pending_writer_may_change_selectors")

    def test_dedicated_nonBDA_excludes_other_buffer_physical_and_image_only_classes(self):
        for case in ("other-buffer", "physical-class", "image-class", "indirect-grid-copy", "disjoint-new-upload", "binding6-disjoint"):
            with self.subTest(case=case): self.call(case=case)
        self.call(case="unknown-class", denial="unclassified_possible_writer")
        self.call(case="unknown-buffer", denial="possible_buffer_writer_destination_unknown")
        self.call(case="binding6-alias", denial="known_binding6_may_write_selectors")

    def test_new_record_equal_bytes_do_not_establish_owner_visibility_or_lifetime(self):
        for case, reason in (("missing-record", "new_record_owned_upload_and_retained_stream_missing"),
                             ("record-bytes", "exact_record_upload_bytes_or_completion_stamp_missing"),
                             ("record-copy-stamp", "exact_record_upload_bytes_or_completion_stamp_missing"),
                             ("record-owner", "record_retained_recording_stream_or_submit_flush_unproved"),
                             ("record-slot", "record_retained_recording_stream_or_submit_flush_unproved"),
                             ("record-no-retention", "record_retained_recording_stream_or_submit_flush_unproved"),
                             ("record-no-registration", "record_retained_recording_stream_or_submit_flush_unproved"),
                             ("record-no-visibility", "record_memory_coherency_or_successful_flush_missing"),
                             ("record-noncoherent", "record_memory_coherency_or_successful_flush_missing"),
                             ("record-GPU-write", "pending_writer_may_change_record"),
                             ("record-conflicting-memory", "dedicated_memory_claim_conflicts_between_buffers"),
                             ("record-original-overlap", "record_upload_aliases_original_descriptor_range")):
            with self.subTest(case=case): self.call(case=case, denial=reason)

    def test_checked_record_flush_must_cover_range_and_follow_actual_upload(self):
        self.call(case="record-flushed")
        for case in ("record-flush-before-upload", "record-flush-failed", "record-flush-short"):
            with self.subTest(case=case): self.call(case=case, denial="record_checked_flush_facts_mismatched")

    def test_record_may_append_to_same_retained_stream_when_all_original_ranges_disjoint(self):
        self.call(case="record-same-stream-append")

    def test_current_cut_must_preserve_same_dispatch_roots_and_C0(self):
        for case in ("changed-original-root", "changed-C0", "changed-dispatch", "reused-writer-next-dispatch"):
            with self.subTest(case=case): self.call(case=case, denial="same_dispatch_plan_stale_or_original_roots_changed")

    def test_identity_and_layout_fail_closed_before_record_calculation(self):
        self.call(case="bad-identity", denial="original_stage_identity_missing_or_mismatched", fatal=True)
        self.call(case="bad-layout", denial="exact_pending_buffer_layout_missing", fatal=True)
        self.call(case="no-owned-C0", denial="owned_descriptor_visible_2224_C0_missing", fatal=True)
