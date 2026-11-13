# SPDX-License-Identifier: GPL-3.0-or-later
import hashlib
import json
from pathlib import Path
import random
import shutil
import struct
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from tools import matrix_warp_bridge as bridge


class BridgeLayoutTests(unittest.TestCase):
    def test_one_warp_has_disjoint_complete_shared_reconstruction_and_tile_ownership(self):
        proof=bridge.layout_proof()
        self.assertEqual(proof["logical_warps"],1)
        self.assertEqual(proof["required_physical_subgroup_size"],32)
        self.assertEqual(proof["disjoint_shared_elements"],{"A":256,"B":128,"C":128})
        owners={j:lane for lane in range(32) for j in range(lane,128,32)}
        self.assertEqual(len(owners),128)
        self.assertTrue(all(j%32==lane for j,lane in owners.items()))
        self.assertEqual(proof["matrix_calls_per_dispatch"],4)

    def test_shader_reconstruction_formulas_match_independent_original_packing(self):
        rng=random.Random(98765)
        a=[[rng.randrange(65536) for _ in range(16)] for _ in range(16)]
        b=[[rng.randrange(65536) for _ in range(8)] for _ in range(16)]
        c=[[rng.randrange(65536) for _ in range(8)] for _ in range(16)]
        packed=bridge.oracle.pack_fragments(a,b,c)
        # Simulate the authored shader's address calculations without any
        # floating conversion/native matrix, comparing every shared cell.
        decoded_a,decoded_b,decoded_c=[None]*256,[None]*128,[None]*128
        def store(target,index,value):
            self.assertIsNone(target[index],"Overlapping shared lane writes")
            target[index]=value
        for lane in range(32):
            g,i=lane>>2,lane&3
            for word in range(4):
                row=g+(word&1)*8
                k=i*2+(word>>1)*8
                value=packed["A"][lane][word]
                store(decoded_a,row*16+k,value&65535)
                store(decoded_a,row*16+k+1,value>>16)
            for word in range(2):
                k=i*2+word*8
                value=packed["B"][lane][word]
                store(decoded_b,k*8+g,value&65535)
                store(decoded_b,(k+1)*8+g,value>>16)
                row=g+word*8
                value=packed["C"][lane][word]
                store(decoded_c,row*8+i*2,value&65535)
                store(decoded_c,row*8+i*2+1,value>>16)
        self.assertEqual(decoded_a,[v for row in a for v in row])
        self.assertEqual(decoded_b,[v for row in b for v in row])
        self.assertEqual(decoded_c,[v for row in c for v in row])

    def test_padded_shared_tile_addresses_are_always_in_bounds_and_select_correct_chunks(self):
        a=list(range(256))
        b=list(range(128))
        for m in range(2):
            for chunk in range(2):
                for j in range(128):
                    ar,ak=j//16,j%16
                    bk,bc=j//8,j%8
                    ai=(m*8+ar)*16+chunk*8+(ak&7)
                    bi=(chunk*8+(bk&7))*8+bc
                    self.assertIn(ai,range(256))
                    self.assertIn(bi,range(128))
                    if ak<8:
                        self.assertEqual(a[ai],(m*8+ar)*16+chunk*8+ak)
                    if bk<8:
                        self.assertEqual(b[bi],(chunk*8+bk)*8+bc)

    def test_identity_fixture_has_independent_original_fragment_corner_values(self):
        data=bridge.fixture("identity")
        self.assertEqual(struct.unpack_from("<8s4I",data),(b"OOMATW32",32,1,256,64))
        inputs=struct.unpack_from("<256I",data,24)
        expected=struct.unpack_from("<64I",data,1048)
        pair=lambda x,y:bridge.oracle.half(x)|(bridge.oracle.half(y)<<16)
        self.assertEqual(inputs[:8],(0x3c00,0,0,0x3c00,pair(1,9),pair(65,73),pair(0,1),pair(32,33)))
        self.assertEqual(inputs[31*8:31*8+4],(0x3c000000,0,0,0x3c000000))
        self.assertEqual(expected[:2],(pair(1,3),pair(97,99)))
        self.assertEqual(expected[-2:],(pair(97,99),pair(193,195)))

    def test_all_safe_packed_outputs_equal_full_scalar_checkpoint_oracle(self):
        for name in bridge.CASES:
            data=bridge.fixture(name)
            self.assertEqual(len(data),1304)
            a,b,c=bridge.matrices(name)
            expected=bridge.oracle.logical_hmma(a,b,c)
            output_words=struct.unpack_from("<64I",data,1048)
            for lane in range(32):
                g,i=divmod(lane,4)
                for word in range(2):
                    row=g+word*8
                    self.assertEqual(output_words[lane*2+word],expected[row][2*i]|(expected[row][2*i+1]<<16))

    def test_public_software_shuffle_addressing_agrees_with_logical_matrix_reference(self):
        o=bridge.oracle
        for name in bridge.CASES:
            a,b,c=bridge.matrices(name)
            packed=o.pack_fragments(a,b,c)
            actual=[]
            for lane in range(32):
                accumulators=[o.half_to_float32((word>>shift)&65535) for word in packed["C"][lane] for shift in (0,16)]
                for pair_index in range(8):
                    component,upper=pair_index%4,pair_index>=4
                    a_lane=(lane&28)+component
                    b_lane=((lane&3)<<3)+component
                    for row in range(2):
                        aw=packed["A"][a_lane][row+(2 if upper else 0)]
                        for col in range(2):
                            bw=packed["B"][b_lane+col*4][int(upper)]
                            index=row*2+col
                            for shift in (0,16):
                                accumulators[index]=o.fma32(o.half_to_float32((aw>>shift)&65535),o.half_to_float32((bw>>shift)&65535),accumulators[index])
                    if component==3:
                        accumulators=[o.half_to_float32(o.float32_to_half(word)) for word in accumulators]
                halves=[o.float32_to_half(word) for word in accumulators]
                actual.extend((halves[0]|(halves[1]<<16),halves[2]|(halves[3]<<16)))
            self.assertEqual(tuple(actual),struct.unpack("<64I",bridge.fixture(name)[-256:]))

    def test_adversarial_classification_separates_sign_zero_from_value_changes(self):
        reference=bridge.fixture("rounding")[-256:]
        result=bridge.classify(reference,reference)
        self.assertEqual(result["software_reference_half_classes"],{"positive_zero":32,"negative_zero":32,"subnormal":32,"normal":32,"infinity":0,"nan":0})
        halves=list(struct.unpack("<128H",reference))
        actual=struct.pack("<128H",*(0 if word==0x8000 else word for word in halves))
        result=bridge.classify(actual,reference)
        self.assertEqual(result["mismatched_packed_words"],16)
        self.assertEqual(result["mismatched_half_elements"],32)
        self.assertEqual(result["zero_sign_only_half_mismatches"],32)
        self.assertFalse(result["nan_payload_or_signaling_equivalence_claim"])

    def test_nonfinite_classification_and_malformed_extents_are_explicit(self):
        blob=struct.pack("<128H",0x7c00,0xfc00,0x7e01,0x8001,*([0]*124))
        result=bridge.classify(blob,blob)
        self.assertEqual(result["actual_half_classes"]["infinity"],2)
        self.assertEqual(result["actual_half_classes"]["nan"],1)
        self.assertEqual(result["actual_half_classes"]["subnormal"],1)
        with self.assertRaises(ValueError):bridge.classify(blob[:-4],blob)
        with self.assertRaises(ValueError):bridge.fixture("game")

    def test_shader_collective_path_is_fixed_singlewarp_with_explicit_shared_barriers(self):
        source=bridge.shader_source()
        for text in ("local_size_x=32", "subgroupMemoryBarrierShared();", "subgroupBarrier();",
                     "m < 2u", "chunk < 2u", "j += 32u", "half_checkpoint(mc[element])",
                     "destination.words[lane * 2u + word]", "(ak & 7u)", "(bk & 7u)"):
            self.assertIn(text,source)
        self.assertNotIn("gl_SubgroupID",source)
        self.assertNotIn("gl_NumSubgroups",source)
        self.assertEqual(hashlib.sha256((bridge.ROOT/"tools/matrix_restore_probe.py").read_bytes()).hexdigest(),bridge.ORACLE_SHA256)


class BridgeNativeCPUTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not shutil.which("cc"):
            raise unittest.SkipTest("Optional CPU helper validation requires a C compiler/Vulkan headers")
        check=subprocess.run(["cc","-x","c","-E","-"],input="#include <vulkan/vulkan.h>\n",capture_output=True,text=True,timeout=10)
        if check.returncode:raise unittest.SkipTest("Optional CPU helper validation requires Vulkan headers")
        cls.temporary=tempfile.TemporaryDirectory()
        cls.directory=Path(cls.temporary.name)
        trap=cls.directory/"trap.c"
        trap.write_text("#include <vulkan/vulkan.h>\n#include <stdlib.h>\nVkResult __wrap_vkCreateInstance(const VkInstanceCreateInfo *i,const VkAllocationCallbacks *a,VkInstance *o){(void)i;(void)a;(void)o;_Exit(91);}\n")
        cls.helper=cls.directory/"helper"
        subprocess.run(["cc","-std=c11","-O2","-DNDEBUG","-Wall","-Wextra","-Werror",str(bridge.ROOT/"src/probes/matrix_warp_bridge.c"),str(trap),"-Wl,--wrap=vkCreateInstance","-o",str(cls.helper),"-lvulkan"],check=True,capture_output=True,timeout=30)

    @classmethod
    def tearDownClass(cls):cls.temporary.cleanup()

    def cpu(self,data):
        path=self.directory/"fixture"
        path.write_bytes(data)
        return subprocess.run([str(self.helper),"cpu",str(path)],capture_output=True,text=True,timeout=5)

    def test_all_three_cpu_paths_return_before_actual_vulkan_api(self):
        for name in bridge.CASES:
            result=self.cpu(bridge.fixture(name))
            self.assertEqual(result.returncode,0,result.stderr)
            self.assertEqual(json.loads(result.stdout),{"profile":bridge.PROFILE,"cpu_validated":True,"vulkan_instance_created":False})

    def test_interposed_runtime_entry_traps_before_any_driver_can_load(self):
        path=self.directory/"fixture"
        path.write_bytes(bridge.fixture("identity"))
        module=self.directory/"dummy.spv"
        module.write_bytes(struct.pack("<5I",0x07230203,0x00010600,0,1,0))
        result=subprocess.run([str(self.helper),"properties",str(path),str(module),str(self.directory/"unused")],capture_output=True,timeout=5)
        self.assertEqual(result.returncode,91)

    def test_old_profile_narrowing_bad_counts_and_corrupt_packed_words_fail_before_vulkan(self):
        original=bridge.fixture("layout")
        self.assertNotEqual(self.cpu(b"OOMAT001"+original[8:]).returncode,0)
        for offset in (8,12,16,20,24,1044,1048,1303):
            altered=bytearray(original)
            altered[offset]^=1
            self.assertNotEqual(self.cpu(altered).returncode,0)
        for width in (8,16,64,128):
            altered=bytearray(original)
            struct.pack_into("<I",altered,8,width)
            self.assertNotEqual(self.cpu(altered).returncode,0)
        self.assertNotEqual(self.cpu(original[:-4]).returncode,0)


class BridgeReviewTests(unittest.TestCase):
    def test_wrong_plan_hash_and_unreviewed_case_cannot_reach_native_process(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)
            (path/"plan.json").write_text("{}")
            with self.assertRaisesRegex(ValueError,"plan hash"):bridge.reviewed(path,"0"*64)
        with patch.object(bridge,"reviewed",return_value=(Path("unused"),{"cases":[]})),patch.object(bridge,"run_process") as process:
            with self.assertRaisesRegex(ValueError,"reviewed synthetic case"):
                bridge.execute(Path("unused"),"0"*64,Path("unused"),"game","dispatch")
            process.assert_not_called()

    def test_unapproved_driver_does_not_start_a_native_process(self):
        case={"name":"identity","bundle":"identity.fixture"}
        plan={"cases":[case],"artifacts":{"identity.fixture":"x"}}
        with patch.object(bridge,"reviewed",return_value=(Path("unused"),plan)), \
                patch.object(bridge,"driver_identity",return_value={"library_sha256":"0"*64,"icd_sha256":"0"*64}), \
                patch.object(bridge,"run_process") as process:
            with self.assertRaisesRegex(ValueError,"exact E5"):
                bridge.execute(Path("unused"),"0"*64,Path("unused"),"identity","compile")
            process.assert_not_called()

    def test_missing_frozen_artifact_inventory_is_refused(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)
            plan={"schema":bridge.SCHEMA,"profile":bridge.PROFILE,"driver_profile":bridge.E5,
                  "oracle_sha256":bridge.ORACLE_SHA256,"dependencies":dict.fromkeys(bridge.DEPENDENCIES,"0"*64),
                  "required_width":32,"workgroup":[32,1,1],"artifacts":{}}
            encoded=json.dumps(plan).encode()
            (path/"plan.json").write_bytes(encoded)
            with self.assertRaisesRegex(ValueError,"artifact inventory"):
                bridge.reviewed(path,hashlib.sha256(encoded).hexdigest())

    def test_cpu_shader_compilation_validation_and_subgroup_barrier_scopes(self):
        if any(not shutil.which(name) for name in ("glslangValidator","spirv-val","spirv-dis")):
            self.skipTest("Optional CPU shader validation needs glslang/SPIRV-Tools")
        with tempfile.TemporaryDirectory() as directory:
            source=Path(directory)/"bridge.comp"
            module=Path(directory)/"bridge.spv"
            source.write_text(bridge.shader_source())
            subprocess.run(["glslangValidator","-V","--target-env","vulkan1.3","-Os","-o",str(module),str(source)],check=True,capture_output=True,timeout=30)
            subprocess.run(["spirv-val","--target-env","vulkan1.3",str(module)],check=True,capture_output=True,timeout=15)
            words=struct.unpack(f"<{module.stat().st_size//4}I",module.read_bytes())
            constants,barriers={},[]
            offset=5
            while offset<len(words):
                length,op=words[offset]>>16,words[offset]&65535
                args=words[offset+1:offset+length]
                if op==43 and len(args)==3:constants[args[1]]=args[2]
                if op==224:barriers.append(args)
                offset+=length
            self.assertTrue(barriers)
            for execution,memory,semantics in barriers:
                self.assertEqual(constants[execution],3)
                self.assertEqual(constants[memory],3)
                self.assertTrue(constants[semantics]&256)
                self.assertTrue(constants[semantics]&8)


if __name__=="__main__":unittest.main()
