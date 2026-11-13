# SPDX-License-Identifier: GPL-3.0-or-later
import hashlib
import importlib.util
import json
from pathlib import Path
import shutil
import struct
import tempfile
import unittest

ROOT=Path(__file__).resolve().parents[1]
SPEC=importlib.util.spec_from_file_location("mesh_input_checker",ROOT/"patches/hookshot/check_mesh_input_profile96.py")
CHECK=importlib.util.module_from_spec(SPEC);SPEC.loader.exec_module(CHECK)


class MeshInputProfile96Tests(unittest.TestCase):
    def setUp(self):
        temporary=tempfile.TemporaryDirectory();self.addCleanup(temporary.cleanup);self.root=Path(temporary.name)
        self.input=bytes(i*7%256 for i in range(256));self.indirect=bytearray(64);struct.pack_into("<3I",self.indirect,12,10,2,3)
        self.event={"program":CHECK.PROGRAM,"variant":CHECK.VARIANT,"spirv_sha256":CHECK.DIGEST,"stage_index":0,"nvn_stage":7,"vk_stage":128,
                    "recording_part":7,"logical_value":8,"recording_sequence":15,"completed_recording_sequence":15,"selected_occurrence":64,"actual_site_confirmed":True}
        self.rows=[{"id":i,"buffer":f"{handle:016x}","size":size,"before":None} for i,handle,size in ((0,2,256),(1,3,64))]
        self.descriptors=[];self.slots=[]
        for b in range(9):
            for e in range(3 if b==8 else 1):
                offset=b*4+e;name=f"mesh6864-binding-{b}-{e}.bin";data=self.input[offset:offset+32]
                (self.root/name).write_bytes(data)
                self.descriptors.append({"binding":b,"element":e,"type":6 if b<4 else 7,"buffer":"0000000000000002","offset":offset,"range":32})
                self.slots.append({"set":0,"binding":b,"element":e,"type":6 if b<4 else 7,"range_bytes":32,"descriptor_range":32,
                                   "null_descriptor":False,"original_buffer_offset":offset,"buffer_handle":"0000000000000002","allocation_row":0,"buffer_lifetime":100,"backing_generation":None,
                                   "snapshot":{"path":name,"size":32,"sha256":hashlib.sha256(data).hexdigest()}})
        (self.root/"mesh6864-indirect.bin").write_bytes(self.indirect[12:24])
        self.document={"schema":"open-ocarina.mesh-input-profile96/v1","profile":"6864-position10-96/v1","runtime_execution_authorized":False,"equivalence_proven":False,"replay_complete":False,"mesh_stage_inputs_present":True,"event":self.event,
                       "launch":{"command":"vkCmdDrawMeshTasksIndirectEXT","draw_count":1,"stride":12,"converted":True,"original_buffer":"0000000000000003",
                                 "original_buffer_offset":12,"snapshot":{"path":"mesh6864-indirect.bin","size":12,"sha256":hashlib.sha256(self.indirect[12:24]).hexdigest()},"group_count":[10,2,3],"zero_grid_no_execution":False},
                       "descriptor_layout":[{"set":0,"binding":b,"type":6 if b<4 else 7,"count":3 if b==8 else 1,"stage_flags":128} for b in range(9)],
                       "bindings":self.slots,"features":{"nullDescriptor":False},"push_constants_not_used":True,"failures":[],
                       "coverage":{"cpu_writers_quiesced":False,"import_aliases_proven":False,"fragment_resources":False,"draw_equivalent_inputs_proven":False,"whole_rendering_equivalence":False}}
        identity={k:self.event[k] for k in ("program","variant","spirv_sha256","recording_part","recording_sequence","completed_recording_sequence","selected_occurrence")}
        self.allocations={"schema":"open-ocarina.gpu-allocations/v2",**identity,"null_descriptor_enabled":False,"after_recorded":False,"buffers":self.rows,"descriptors":self.descriptors}
        self.provenance={"schema":"open-ocarina.binding-provenance/v1",**identity,"objects":[{"catalog_id":i,"kind":"buffer","handle":f"{i+2:016x}","tracked":True,"lifetime_id":100+i} for i in range(2)]}
        self.table={"pipeline":{k:self.event[k] for k in ("program","variant","spirv_sha256")},"submission":{k:self.event[k] for k in ("recording_part","logical_value","recording_sequence","completed_recording_sequence")}}
        self.save()

    def save(self):
        for i,data in enumerate((self.input,self.indirect)):
            name=f"buffer-{i}-before.bin";(self.root/name).write_bytes(data)
            self.rows[i]["before"]={"artifact":name,"sha256":hashlib.sha256(data).hexdigest()}
        associations=[]
        for name,data in (("manifest.json",self.table),("allocations.json",self.allocations),("binding-provenance.json",self.provenance)):
            raw=json.dumps(data).encode();(self.root/name).write_bytes(raw);associations.append({"path":name,"sha256":hashlib.sha256(raw).hexdigest()})
        self.document["associations"]=associations;(self.root/"mesh-inputs-6864.json").write_text(json.dumps(self.document))

    def validate(self):return CHECK.validate_capture(self.root)

    def test_exact_inputs_still_leave_explicit_replay_gates(self):
        report=self.validate();self.assertTrue(report["mesh_stage_inputs_present"]);self.assertFalse(report["replay_complete"])
        self.assertEqual(report["slots"],11);self.assertIn("cpu_writers_quiesced",report["unresolved"])

    def test_rehashed_wrong_slice_cannot_replace_gpu_before_copy(self):
        slot=self.slots[0];data=b"x"*32;(self.root/slot["snapshot"]["path"]).write_bytes(data);slot["snapshot"]["sha256"]=hashlib.sha256(data).hexdigest();self.save()
        with self.assertRaisesRegex(ValueError,"authoritative GPU BEFORE"):self.validate()

    def test_cpu_grid_is_not_gpu_launch(self):
        self.document["launch"]["group_count"]=[100,1,1];self.save()
        with self.assertRaisesRegex(ValueError,"GPU launch words"):self.validate()

    def test_true_launch_owner_copy_is_checked(self):
        self.indirect[12]^=1;self.save()
        with self.assertRaisesRegex(ValueError,"Launch bytes differ"):self.validate()

    def test_later_compute_after_must_not_be_claimed_as_mesh_after(self):
        self.allocations["after_recorded"]=True;self.save()
        with self.assertRaisesRegex(ValueError,"later compute AFTER"):self.validate()

    def test_descriptor_array_requires_all_three_elements(self):
        self.slots.pop();self.save()
        with self.assertRaisesRegex(ValueError,"eleven exact"):self.validate()

    def test_exact_range_preserves_array_length(self):
        self.descriptors[0]["range"]=64;self.slots[0]["descriptor_range"]=64;self.save()
        with self.assertRaisesRegex(ValueError,"ArrayLength"):self.validate()

    def test_binding_lifetime_must_be_observed(self):
        self.slots[0]["buffer_lifetime"]=999;self.save()
        with self.assertRaisesRegex(ValueError,"lifetime"):self.validate()

    def test_count_and_multidraw_are_unsupported(self):
        self.document["launch"]["draw_count"]=2;self.save()
        with self.assertRaisesRegex(ValueError,"one converted"):self.validate()

    def test_failed_actual_site_confirmation_is_incomplete(self):
        self.event["actual_site_confirmed"]=False;self.save()
        with self.assertRaisesRegex(ValueError,"command identity"):self.validate()

    def test_mixed_recording_or_occurrence_is_refused(self):
        self.provenance["selected_occurrence"]=2;self.save()
        with self.assertRaisesRegex(ValueError,"occurrence"):self.validate()

    def test_zero_native_grid_is_captured_but_forbids_execution(self):
        struct.pack_into("<I",self.indirect,12,0);blob=self.indirect[12:24];(self.root/"mesh6864-indirect.bin").write_bytes(blob)
        self.document["launch"]["snapshot"]["sha256"]=hashlib.sha256(blob).hexdigest();self.document["launch"]["group_count"]=[0,2,3]
        self.document["launch"]["zero_grid_no_execution"]=True;self.save()
        self.assertFalse(self.validate()["nonzero_grid_guard_passed"])

    def test_private_artifact_symlinks_are_refused(self):
        path=self.root/"mesh6864-binding-0-0.bin";data=path.read_bytes();path.unlink();(self.root/"other.bin").write_bytes(data);path.symlink_to("other.bin")
        with self.assertRaisesRegex(ValueError,"nonregular"):self.validate()

    def add_defined_nulls(self):
        self.document["features"]["nullDescriptor"]=True;self.allocations["null_descriptor_enabled"]=True
        for index in (9,10):
            slot=self.slots[index];(self.root/slot["snapshot"]["path"]).unlink()
            slot.update(null_descriptor=True,range_bytes=None,snapshot=None,allocation_row=None,buffer_lifetime=None,backing_generation=None,
                        buffer_handle="0000000000000000",original_buffer_offset=0,descriptor_range=(1<<64)-1)
            self.descriptors[index].update(buffer="0000000000000000",offset=0,range=(1<<64)-1)
        self.save()

    def test_defined_nulls_preserve_all_eleven_positions_without_bytes(self):
        self.add_defined_nulls();report=self.validate()
        self.assertEqual(report["slots"],11);self.assertEqual(report["snapshot_slots"],9);self.assertEqual(report["null_slots"],2)
        self.assertFalse((self.root/"mesh6864-binding-8-1.bin").exists())

    def test_null_descriptor_requires_actual_enabled_feature(self):
        self.add_defined_nulls();self.document["features"]["nullDescriptor"]=False;self.save()
        with self.assertRaisesRegex(ValueError,"feature-disabled"):self.validate()

    def test_reported_null_feature_must_match_authoritative_device_state(self):
        self.add_defined_nulls();self.allocations["null_descriptor_enabled"]=False;self.save()
        with self.assertRaisesRegex(ValueError,"authoritative captured device"):self.validate()

    def test_null_framing_and_authoritative_descriptor_are_both_checked(self):
        self.add_defined_nulls();self.slots[9]["original_buffer_offset"]=4;self.save()
        with self.assertRaisesRegex(ValueError,"framing"):self.validate()
        self.slots[9]["original_buffer_offset"]=0;self.descriptors[9]["range"]=32;self.save()
        with self.assertRaisesRegex(ValueError,"authoritative submitted write"):self.validate()

    def test_null_slot_cannot_fake_snapshot_or_allocation(self):
        self.add_defined_nulls();self.slots[9]["range_bytes"]=0;self.save()
        with self.assertRaisesRegex(ValueError,"fabricated payload"):self.validate()

    def test_null_slot_has_no_provenance_owner(self):
        self.add_defined_nulls();self.provenance["objects"].append({"kind":"buffer","handle":"0000000000000000","tracked":True,"lifetime_id":999});self.save()
        with self.assertRaisesRegex(ValueError,"fabricated an allocation or provenance"):self.validate()

    def test_null_slot_never_accepts_zero_filled_substitute(self):
        self.add_defined_nulls();(self.root/"mesh6864-binding-8-1.bin").write_bytes(b"\0"*32)
        with self.assertRaisesRegex(ValueError,"must not have a payload"):self.validate()

    def test_null_fields_must_be_explicit(self):
        self.add_defined_nulls();self.slots[9].pop("snapshot");self.save()
        with self.assertRaisesRegex(ValueError,"fabricated payload or ownership"):self.validate()

    def test_fake_nonnull_bytes_cannot_replace_null_descriptor(self):
        self.add_defined_nulls();self.slots[9]["null_descriptor"]=False;self.save()
        with self.assertRaisesRegex(ValueError,"Nonnull descriptor has null"):self.validate()

    def test_old_d8_schema_is_not_admitted(self):
        self.document["schema"]="open-ocarina.mesh-logical-input/v1";self.save()
        with self.assertRaisesRegex(ValueError,"Unsupported"):self.validate()

    def test_profile_must_be_explicit_and_exact(self):
        self.document.pop("profile");self.save()
        with self.assertRaisesRegex(ValueError,"Explicit profile"):self.validate()

    def test_legacy_unchecked_execution_and_equivalence_are_false(self):
        for field in ("runtime_execution_authorized","equivalence_proven"):
            self.document[field]=True;self.save()
            with self.assertRaisesRegex(ValueError,"unchecked legacy"):self.validate()
            self.document[field]=False

    def test_d8_uniform_storage_split_is_refused(self):
        self.document["descriptor_layout"][4]["type"]=6;self.save()
        with self.assertRaisesRegex(ValueError,"descriptor layout"):self.validate()

    def test_original_d8_checker_rejects_new_profile(self):
        spec=importlib.util.spec_from_file_location("frozen_d8_input_checker",ROOT/"patches/hookshot/check_mesh_input_capture.py")
        original=importlib.util.module_from_spec(spec);spec.loader.exec_module(original)
        (self.root/"mesh-inputs.json").write_text(json.dumps(self.document))
        with self.assertRaisesRegex(ValueError,"Unsupported"):original.validate_capture(self.root)

    @unittest.skipUnless(shutil.which("clang++") or shutil.which("g++"),"C++20 compiler unavailable")
    def test_optimized_header_capture_boundaries(self):
        report=CHECK.check_helper();self.assertTrue(report["eleven_exact_descriptor_slices"]);self.assertTrue(report["same_recording_completion_checked"]);self.assertTrue(report["defined_null_positions_preserved_without_bytes"])


if __name__=="__main__":unittest.main()
