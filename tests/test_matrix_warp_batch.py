# SPDX-License-Identifier: GPL-3.0-or-later
import hashlib
import json
from pathlib import Path
import shutil
import struct
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from tools import matrix_warp_batch as batch


class CorpusTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):cls.bundle,cls.cases=batch.corpus()

    def payload(self,group):
        _,_,reference,_=batch.unpack_bundle(self.bundle)
        start=group*batch.OUTPUT_STRIDE*4+64
        return reference[start:start+256]

    def logical_half(self,group,row,column):
        lane=(row%8)*4+column//2
        word=lane*2+row//8
        return (struct.unpack("<64I",self.payload(group))[word]>>(16*(column%2)))&65535

    def test_corpus_is_deterministic_bounded_and_all_inputs_are_finite(self):
        second,cases=batch.corpus()
        self.assertEqual(second,self.bundle)
        self.assertEqual(cases,self.cases)
        self.assertEqual(len(self.bundle),123440)
        self.assertEqual(len(self.cases),64)
        self.assertEqual(sum(case["safe_exact"] for case in self.cases),16)
        self.assertEqual(len({case["name"] for case in self.cases}),64)
        for group in range(64):
            _,_,a,b,c=batch.case(group)
            self.assertTrue(all(word&0x7c00!=0x7c00 for matrix in (a,b,c) for row in matrix for word in row))
        aggregate={key:sum(case["reference_half_classes"][key] for case in self.cases) for key in self.cases[0]["reference_half_classes"]}
        self.assertEqual(sum(aggregate.values()),8192)
        self.assertGreater(aggregate["subnormal"],0)
        self.assertGreater(aggregate["infinity"],0)
        self.assertEqual(aggregate["nan"],0) # observed for this fixed corpus only

    def test_safe_oracles_match_independent_integer_products(self):
        def integer(word):return int(struct.unpack("<e",struct.pack("<H",word))[0])
        for group in range(16):
            _,safe,a,b,c=batch.case(group)
            self.assertTrue(safe)
            for row in range(16):
                for column in range(8):
                    value=integer(c[row][column])+sum(integer(a[row][k])*integer(b[k][column]) for k in range(16))
                    self.assertLessEqual(value,2048)
                    expected=int.from_bytes(struct.pack("<e",value),"little")
                    self.assertEqual(self.logical_half(group,row,column),expected)

    def test_existing_three_counterexamples_are_named_and_retain_their_semantics(self):
        o=batch.o
        for group in (16,17,18):
            _,safe,a,b,c=batch.case(group)
            self.assertFalse(safe)
            av=a[0]
            bv=[b[k][0] for k in range(16)]
            if group==16:
                self.assertEqual(o.dot16(av,bv),0)
                self.assertEqual(o.dot16(av,bv,checkpoint=16),o.half(2**-11))
            elif group==17:
                self.assertEqual(o.dot16(av,bv,c[0][0]),0x8000)
                self.assertEqual(o.dot16(av,bv,c[0][0],zero_pad=True),0)
            else:
                self.assertEqual(o.dot16(av,bv),0)
                self.assertEqual(o.dot16(av,bv,order=[0,2,1]+list(range(3,16))),o.half(1))

    def test_targeted_half_boundaries_use_computed_oracle_classes_and_known_ties(self):
        self.assertEqual(self.cases[19]["reference_half_classes"]["subnormal"],128)
        self.assertEqual(self.logical_half(20,0,0),0)
        self.assertEqual(self.logical_half(20,2,0),1)
        self.assertEqual(self.logical_half(20,3,4),0x8001)
        self.assertEqual(self.logical_half(20,3,0),0) # K16 positive-zero terms change the K8 -0
        self.assertEqual([self.logical_half(22,row,0) for row in range(4)],[0x7bff,0x7c00,0x7c00,0xfc00])
        self.assertEqual([self.logical_half(23,row,0) for row in range(4)],[0x3c00,0x3c02,0xbc00,0xbc02])
        for group in list(range(40,48))+list(range(56,64)):
            self.assertEqual(self.cases[group]["reference_half_classes"]["infinity"],0)
            self.assertEqual(self.cases[group]["reference_half_classes"]["nan"],0)

    def test_group_relative_addresses_are_disjoint_complete_and_skip_inner_guards(self):
        ins={g*288+16+lane*8+word for g in range(64) for lane in range(32) for word in range(8)}
        outs={g*96+16+lane*2+word for g in range(64) for lane in range(32) for word in range(2)}
        self.assertEqual(len(ins),64*256)
        self.assertEqual(len(outs),64*64)
        self.assertTrue(all(16<=index%288<272 for index in ins))
        self.assertTrue(all(16<=index%96<80 for index in outs))
        self.assertLess(max(ins),64*288)
        self.assertLess(max(outs),64*96)
        proof=batch.layout_proof()
        self.assertEqual(proof["unique_input_payload_words"],16384)
        self.assertEqual(proof["unique_output_payload_words"],4096)
        self.assertEqual(proof["parent_single_warp"]["matrix_calls_per_dispatch"],4)
        source=batch.shader_source()
        reverted=source.replace("\n    uint group = gl_WorkGroupID.x;","")
        reverted=reverted.replace("source.words[group * 288u + 16u + lane * 8u +","source.words[lane * 8u +")
        reverted=reverted.replace("destination.words[group * 96u + 16u + lane * 2u + word]","destination.words[lane * 2u + word]")
        reverted=reverted.replace("layout(set=0,binding=2,std430) writeonly buffer Writtenness { uint words[]; } completion;\n","")
        reverted=reverted.replace("\n        subgroupMemoryBarrierBuffer();\n        completion.words[group * 96u + 16u + lane * 2u + word] = 0x57420000u | (group * 64u + lane * 2u + word + 1u);","")
        self.assertEqual(reverted,batch.bridge.shader_source())

    def test_complete_reference_comparison_and_safe_diagnostic_partition(self):
        _,_,reference,_=batch.unpack_bundle(self.bundle)
        markers=batch.unpack_bundle(self.bundle)[3]
        result=batch.classify_output(reference,markers,self.bundle,self.cases)
        self.assertEqual(result["complete_payload_packed_words"],4096)
        self.assertEqual(result["complete_payload_half_elements"],8192)
        self.assertEqual(result["safe_mismatched_packed_words"],0)
        self.assertEqual(result["diagnostic_mismatched_packed_words"],0)
        self.assertEqual(result["verified_writtenness_markers"],4096)
        self.assertTrue(result["all_payload_words_marked"])
        actual=bytearray(reference)
        actual[64]^=1
        actual[17*384+64]^=1
        result=batch.classify_output(actual,markers,self.bundle,self.cases)
        self.assertEqual(result["safe_mismatched_packed_words"],1)
        self.assertEqual(result["diagnostic_mismatched_packed_words"],1)
        actual[63]^=1
        with self.assertRaisesRegex(ValueError,"guard changed"):
            batch.classify_output(actual,markers,self.bundle,self.cases)

    def test_every_marker_has_unique_group_lane_word_ownership(self):
        values=[batch.marker_token(group,lane*2+word) for group in range(64) for lane in range(32) for word in range(2)]
        self.assertEqual(len(set(values)),4096)
        self.assertTrue(all(value not in (0,batch.INNER_GUARD,batch.SENTINEL) for value in values))
        source=batch.shader_source()
        self.assertIn('binding=2,std430) writeonly buffer Writtenness',source)
        self.assertLess(source.index('destination.words[group'),source.index('subgroupMemoryBarrierBuffer();'))
        self.assertLess(source.index('subgroupMemoryBarrierBuffer();'),source.index('completion.words[group'))

    def test_missing_or_partial_diagnostic_markers_rejected_before_any_classification(self):
        _,_,reference,reference_markers=batch.unpack_bundle(self.bundle)
        actual=bytearray(reference)
        markers=bytearray(reference_markers)
        for group in range(16,64):
            actual[group*384+64:group*384+320]=struct.pack('<64I',*([batch.SENTINEL]*64))
            markers[group*384+64:group*384+320]=bytes(256)
        with patch.object(batch.bridge,'classify') as classify:
            with self.assertRaisesRegex(ValueError,'writtenness marker'):
                batch.classify_output(actual,markers,self.bundle,self.cases)
            classify.assert_not_called()
        markers=bytearray(reference_markers)
        struct.pack_into('<I',markers,63*384+64+63*4,0)
        with patch.object(batch.bridge,'classify') as classify:
            with self.assertRaisesRegex(ValueError,'writtenness marker'):
                batch.classify_output(reference,markers,self.bundle,self.cases)
            classify.assert_not_called()

    def test_wrong_owner_marker_and_marker_guard_rejected_before_classification(self):
        _,_,reference,reference_markers=batch.unpack_bundle(self.bundle)
        for offset,value in ((64,batch.marker_token(1,0)),(0,0)):
            markers=bytearray(reference_markers)
            struct.pack_into('<I',markers,offset,value)
            with patch.object(batch.bridge,'classify') as classify:
                with self.assertRaises(ValueError):
                    batch.classify_output(reference,markers,self.bundle,self.cases)
                classify.assert_not_called()

    def test_independent_python_checks_all_outer_frames_and_input_before_classification(self):
        _,inputs,reference,reference_markers=batch.unpack_bundle(self.bundle)
        outer=bytes([0xa5])*256
        frames=[outer+inputs+outer,outer+reference+outer,outer+reference_markers+outer]
        result=batch.validate_readback(*frames,self.bundle,self.cases)
        self.assertTrue(result['all_three_allocation_outer_guards_unchanged'])
        self.assertTrue(result['all_input_bytes_unchanged'])
        for allocation in range(3):
            for offset in (0,len(frames[allocation])-1):
                changed=list(frames)
                changed[allocation]=bytearray(changed[allocation])
                changed[allocation][offset]^=1
                with patch.object(batch.bridge,'classify') as classify:
                    with self.assertRaisesRegex(ValueError,'outer guard'):
                        batch.validate_readback(*changed,self.bundle,self.cases)
                    classify.assert_not_called()
        changed=list(frames)
        changed[0]=bytearray(changed[0])
        changed[0][256+64]^=1
        with patch.object(batch.bridge,'classify') as classify:
            with self.assertRaisesRegex(ValueError,'Readonly input'):
                batch.validate_readback(*changed,self.bundle,self.cases)
            classify.assert_not_called()

    def test_malformed_abi_counts_widths_profiles_and_extents_are_refused(self):
        for offset,value in ((8,1),(12,16),(16,65),(20,256),(24,64),(28,64),(32,1),(36,1),(40,1),(44,63)):
            data=bytearray(self.bundle)
            struct.pack_into("<I",data,offset,value)
            with self.assertRaises(ValueError):batch.unpack_bundle(data)
        with self.assertRaises(ValueError):batch.unpack_bundle(b"OOMATW32"+self.bundle[8:])
        with self.assertRaises(ValueError):batch.unpack_bundle(self.bundle[:-4])
        with self.assertRaises(ValueError):batch.case(64)


class NativeBatchCPUTests(unittest.TestCase):
    @classmethod
    def build(cls,path,bundle):
        path.mkdir(parents=True,exist_ok=True)
        source=path/"helper.c"
        source.write_bytes((batch.ROOT/"src/probes/matrix_warp_batch.c").read_bytes())
        values=",".join(f"0x{v:02x}" for v in hashlib.sha256(bundle).digest())
        (path/"fixture_binding.h").write_text("#define APPROVED_GROUP_COUNT 64u\nstatic const uint8_t approved_bundle_sha256[32]={"+values+"};\n")
        trap=path/"trap.c"
        trap.write_text("#include <vulkan/vulkan.h>\n#include <stdlib.h>\nVkResult __wrap_vkCreateInstance(const VkInstanceCreateInfo *i,const VkAllocationCallbacks *a,VkInstance *o){(void)i;(void)a;(void)o;_Exit(91);}\n")
        helper=path/"helper"
        subprocess.run(["cc","-std=c11","-O2","-DNDEBUG","-Wall","-Wextra","-Werror",str(source),str(trap),"-Wl,--wrap=vkCreateInstance","-o",str(helper),"-lvulkan"],check=True,capture_output=True,timeout=30)
        return helper

    @classmethod
    def setUpClass(cls):
        if not shutil.which("cc"):raise unittest.SkipTest("CPU native validation needs C compiler/Vulkan headers")
        probe=subprocess.run(["cc","-x","c","-E","-"],input="#include <vulkan/vulkan.h>\n",capture_output=True,text=True,timeout=10)
        if probe.returncode:raise unittest.SkipTest("CPU native validation needs Vulkan headers")
        cls.temporary=tempfile.TemporaryDirectory()
        cls.path=Path(cls.temporary.name)
        cls.bundle,cls.cases=batch.corpus()
        cls.helper=cls.build(cls.path/"base",cls.bundle)
        harness=cls.path/"base"/"hash.c"
        harness.write_text('#define main batch_program_main\n#include "helper.c"\n#undef main\nint main(int argc,char **argv){if(argc!=2)return 2;FILE *f=fopen(argv[1],"rb");if(!f)return 2;uint8_t *p=malloc(131072);if(!p)return 2;size_t n=fread(p,1,131072,f);fclose(f);uint8_t hash[32];sha256(p,n,hash);for(unsigned i=0;i<32;++i)printf("%02x",hash[i]);putchar(10);free(p);return 0;}\n')
        cls.hash_helper=cls.path/"hash-helper"
        subprocess.run(["cc","-std=c11","-O2","-DNDEBUG","-Wall","-Wextra","-Werror",str(harness),str(cls.path/"base"/"trap.c"),"-Wl,--wrap=vkCreateInstance","-o",str(cls.hash_helper),"-lvulkan"],check=True,capture_output=True,timeout=30)

    @classmethod
    def tearDownClass(cls):cls.temporary.cleanup()

    def cpu(self,data,helper=None):
        path=self.path/"case.fixture"
        path.write_bytes(data)
        return subprocess.run([str(helper or self.helper),"cpu",str(path)],capture_output=True,text=True,timeout=5)

    def check(self,output,markers):
        fixture=self.path/'case.fixture'
        values=self.path/'check-output.bin'
        written=self.path/'check-markers.bin'
        fixture.write_bytes(self.bundle)
        values.write_bytes(output)
        written.write_bytes(markers)
        return subprocess.run([str(self.helper),'check',str(fixture),str(values),str(written)],capture_output=True,text=True,timeout=5)

    def test_sha256_selftest_and_full_corpus_cpu_validation_precede_instance_creation(self):
        result=subprocess.run([str(self.helper),"selftest"],capture_output=True,text=True,timeout=5)
        self.assertEqual(result.returncode,0,result.stderr)
        self.assertEqual(json.loads(result.stdout),{"sha256_selftest":True,"vulkan_instance_created":False})
        result=self.cpu(self.bundle)
        self.assertEqual(result.returncode,0,result.stderr)
        self.assertEqual(json.loads(result.stdout),{"profile":batch.PROFILE,"cpu_validated":True,"groups":64,"safe_groups_independently_integer_validated":16,"vulkan_instance_created":False})

    def test_c_sha256_matches_independent_hashlib_across_padding_boundaries(self):
        for length in (0,1,3,55,56,63,64,65,119,120,127,128,129,1000,98856,123440):
            data=bytes((i*37+13)&255 for i in range(length))
            path=self.path/"hash-input"
            path.write_bytes(data)
            result=subprocess.run([str(self.hash_helper),str(path)],capture_output=True,text=True,timeout=5)
            self.assertEqual(result.returncode,0,result.stderr)
            self.assertEqual(result.stdout.strip(),hashlib.sha256(data).hexdigest())

    def test_trap_intercepts_runtime_entry_before_any_real_vulkan_call(self):
        fixture=self.path/"case.fixture"
        fixture.write_bytes(self.bundle)
        module=self.path/"dummy.spv"
        module.write_bytes(struct.pack("<5I",0x07230203,0x00010600,0,1,0))
        result=subprocess.run([str(self.helper),"properties",str(fixture),str(module),str(self.path/"unused"),str(self.path/"unused-markers"),str(self.path/"unused-input")],capture_output=True,timeout=5)
        self.assertEqual(result.returncode,91)

    def test_all_sampled_bundle_mutations_are_rejected_by_compiled_digest(self):
        for offset in (0,8,12,16,39,40,551,552,1000,len(self.bundle)-1):
            data=bytearray(self.bundle)
            data[offset]^=1
            result=self.cpu(data)
            self.assertNotEqual(result.returncode,0)
            self.assertNotEqual(result.returncode,91)

    def test_independent_safe_integer_verifier_rejects_wrong_reference_even_with_rebound_hash(self):
        data=bytearray(self.bundle)
        start=48+64*8+64*288*4
        data[start+64]^=1
        helper=self.build(self.path/"rebound-safe",data)
        result=self.cpu(data,helper)
        self.assertNotEqual(result.returncode,0)
        self.assertIn("sum<=2048",result.stderr)

    def test_structural_width_gate_rejects_rebound_narrowed_bundle(self):
        data=bytearray(self.bundle)
        struct.pack_into("<I",data,12,16)
        helper=self.build(self.path/"rebound-width",data)
        result=self.cpu(data,helper)
        self.assertNotEqual(result.returncode,0)
        self.assertIn("f->width==32",result.stderr)

    def test_native_cpu_output_check_requires_every_marker_before_diagnostics(self):
        _,_,reference,reference_markers=batch.unpack_bundle(self.bundle)
        result=self.check(reference,reference_markers)
        self.assertEqual(result.returncode,0,result.stderr)
        self.assertTrue(json.loads(result.stdout)['all_payload_words_marked'])
        self.assertEqual(json.loads(result.stdout)['verified_writtenness_markers'],4096)
        actual=bytearray(reference)
        markers=bytearray(reference_markers)
        for group in range(16,64):
            actual[group*384+64:group*384+320]=struct.pack('<64I',*([batch.SENTINEL]*64))
            markers[group*384+64:group*384+320]=bytes(256)
        result=self.check(actual,markers)
        self.assertNotEqual(result.returncode,0)
        self.assertIn('no numerical classification',result.stderr)
        self.assertEqual(result.stdout,'')
        markers=bytearray(reference_markers)
        struct.pack_into('<I',markers,63*384+64+63*4,0)
        result=self.check(reference,markers)
        self.assertNotEqual(result.returncode,0)
        self.assertIn('no numerical classification',result.stderr)
        self.assertEqual(result.stdout,'')

    def test_native_cpu_wrong_owner_and_marker_guard_are_rejected(self):
        _,_,reference,reference_markers=batch.unpack_bundle(self.bundle)
        for offset,value in ((64,batch.marker_token(1,0)),(0,0)):
            markers=bytearray(reference_markers)
            struct.pack_into('<I',markers,offset,value)
            result=self.check(reference,markers)
            self.assertNotEqual(result.returncode,0)
            self.assertEqual(result.stdout,'')
        self.assertNotEqual(self.check(reference,reference_markers[:-4]).returncode,0)

    def test_rebound_expected_marker_corruption_is_rejected_before_instance(self):
        data=bytearray(self.bundle)
        marker_start=48+64*8+(64*288+64*96)*4
        struct.pack_into('<I',data,marker_start+64,batch.marker_token(1,0))
        helper=self.build(self.path/'rebound-marker',data)
        result=self.cpu(data,helper)
        self.assertNotEqual(result.returncode,0)
        self.assertIn('MARKER_BASE',result.stderr)

    def test_native_full_frame_check_rejects_outer_guard_or_input_changes_before_diagnostics(self):
        _,inputs,reference,reference_markers=batch.unpack_bundle(self.bundle)
        outer=bytes([0xa5])*256
        frames=[outer+inputs+outer,outer+reference+outer,outer+reference_markers+outer]
        fixture=self.path/'case.fixture'
        fixture.write_bytes(self.bundle)
        paths=[self.path/f'frame-{i}.bin' for i in range(3)]
        def check(values):
            for path,data in zip(paths,values):path.write_bytes(data)
            return subprocess.run([str(self.helper),'frames',str(fixture),*(str(path) for path in paths)],capture_output=True,text=True,timeout=5)
        result=check(frames)
        self.assertEqual(result.returncode,0,result.stderr)
        self.assertTrue(json.loads(result.stdout)['cpu_frame_validated'])
        for allocation in range(3):
            changed=list(frames)
            changed[allocation]=bytearray(changed[allocation])
            changed[allocation][-1]^=1
            result=check(changed)
            self.assertNotEqual(result.returncode,0)
            self.assertEqual(result.stdout,'')
        changed=list(frames)
        changed[0]=bytearray(changed[0])
        changed[0][256+64]^=1
        result=check(changed)
        self.assertNotEqual(result.returncode,0)
        self.assertEqual(result.stdout,'')


class ReviewTests(unittest.TestCase):
    def test_wrong_plan_hash_and_unapproved_driver_cannot_start_native_process(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)
            (path/"plan.json").write_text("{}")
            with self.assertRaisesRegex(ValueError,"plan hash"):batch.reviewed(path,"0"*64)
        with patch.object(batch,"reviewed",return_value=(Path("unused"),{})),patch.object(batch,"driver_identity",return_value={"library_sha256":"0"*64,"icd_sha256":"0"*64}),patch.object(batch,"run_process") as process:
            with self.assertRaisesRegex(ValueError,"exact E5"):
                batch.execute(Path("unused"),"0"*64,Path("unused"),"compile")
            process.assert_not_called()

    def test_cpu_compile_validate_and_inspect_group_relative_spirv(self):
        if any(not shutil.which(name) for name in ("glslangValidator","spirv-val","spirv-dis")):
            self.skipTest("CPU shader preparation needs glslang and SPIRV-Tools")
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)
            source=path/"batch.comp"
            module=path/"batch.spv"
            source.write_text(batch.shader_source())
            subprocess.run(["glslangValidator","-V","--target-env","vulkan1.3","-Os","-o",str(module),str(source)],check=True,capture_output=True,timeout=30)
            subprocess.run(["spirv-val","--target-env","vulkan1.3",str(module)],check=True,capture_output=True,timeout=15)
            result=subprocess.run(["spirv-dis",str(module)],check=True,capture_output=True,text=True,timeout=15).stdout
            self.assertIn("BuiltIn WorkgroupId",result)
            self.assertIn("LocalSize 32 1 1",result)
            self.assertIn("OpCooperativeMatrixMulAddKHR",result)
            self.assertIn("OpControlBarrier",result)
            self.assertIn('OpDecorate %completion Binding 2',result)
            self.assertIn('OpDecorate %completion NonReadable',result)
            self.assertNotIn('OpDecorate %completion NonWritable',result)


if __name__=="__main__":unittest.main()
