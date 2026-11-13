from collections import Counter
import hashlib
from pathlib import Path
import shutil
import struct
import tempfile
import unittest
from unittest.mock import patch

from tools import mesh_output_profile96 as profile
from tools import mesh_output_instrument as frozen


def interpret_fixture(assembly, origin, groups):
    """Small scalar SPIR-V interpreter for authored fixtures only.

    Interpret emitted instructions, including shadow SSBO addresses and branches;
    do not use the oracle's field addressing or output formula.
    """
    items, defs, functions, _ = frozen.parse(assembly.replace("%oomcap_", "%authcap_"))
    entry = next(i['args'][1] for i in items if i['op'] == 'OpEntryPoint')
    body = functions[entry]
    labels = {i['result']: n for n, i in enumerate(body) if i['op'] == 'OpLabel'}
    decos = {}
    for i in items:
        if i['op'] == 'OpDecorate':
            decos.setdefault(i['args'][0], {})[i['args'][1]] = i['args'][2:]
    cap = next(k for k, v in decos.items() if v.get('DescriptorSet') == ['1'] and v.get('Binding') == ['0'])
    control = next(k for k, v in decos.items() if v.get('DescriptorSet') == ['1'] and v.get('Binding') == ['1'])
    wg = next(k for k,v in decos.items() if v.get('BuiltIn') == ['WorkgroupId'])
    lid = next(k for k,v in decos.items() if v.get('BuiltIn') == ['LocalInvocationId'])
    data = bytearray(profile.initial_capture(groups[0] * groups[1] * groups[2]))
    globals_ = {}
    for i in items:
        if i['op'] == 'OpConstant':
            globals_[i['result']] = int(i['args'][1], 0)
        elif i['op'] in {'OpConstantTrue', 'OpConstantFalse'}:
            globals_[i['result']] = int(i['op'] == 'OpConstantTrue')
        elif i['op'] == 'OpVariable':
            globals_[i['result']] = (i['result'], ())
    for z in range(groups[2]):
        for y in range(groups[1]):
            for x in range(groups[0]):
                for lane in range(32):
                    values, pc, steps = dict(globals_), 1, 0
                    while pc < len(body):
                        i = body[pc];op, args, result = i['op'], i['args'], i['result']
                        pc += 1; steps += 1
                        if steps > 20000:
                            raise AssertionError('Fixture interpreter instruction limit')
                        v = lambda k: values[k]
                        if op in {'OpLabel', 'OpSelectionMerge', 'OpControlBarrier', 'OpSetMeshOutputsEXT'}:
                            continue
                        if op == 'OpReturn':break
                        if op == 'OpLoad':
                            root, path = v(args[1])
                            if root == wg and path == ():value = (x, y, z)
                            elif root == lid and path == ():value = (lane, 0, 0)
                            elif root == control and path in {(0,), (1,)}:
                                value = tuple(origin if path == (0,) else groups) + (0,)
                            else:raise AssertionError('Fixture attempted unexpected load')
                        elif op == 'OpAccessChain':
                            root, path = v(args[1]);value = (root, path + tuple(v(k) for k in args[2:]))
                        elif op == 'OpStore':
                            root, path = v(args[0])
                            if root == cap:
                                if len(path) != 2 or path[0] != 0:raise AssertionError('Unexpected capture pointer')
                                struct.pack_into('<I', data, path[1] * 4, v(args[1]))
                            elif defs[root]['args'][1] != 'Output':
                                raise AssertionError('Fixture wrote unexpected external memory')
                            continue
                        elif op == 'OpCompositeExtract':value = v(args[1])[int(args[2])]
                        elif op == 'OpCompositeConstruct':value = tuple(v(k) for k in args[1:])
                        elif op == 'OpVectorShuffle':
                            vector = v(args[1]) + v(args[2]);value = tuple(vector[int(k)] for k in args[3:])
                        elif op in {'OpIAdd', 'OpIMul', 'OpUMod'}:
                            a,b = v(args[1]),v(args[2])
                            fn = {'OpIAdd':lambda a,b:a+b,'OpIMul':lambda a,b:a*b,'OpUMod':lambda a,b:a%b}[op]
                            value = tuple(fn(c,d) & 0xffffffff for c,d in zip(a,b)) if isinstance(a,tuple) else fn(a,b)&0xffffffff
                        elif op == 'OpConvertUToF':value = struct.unpack('<f',struct.pack('<f',float(v(args[1]))))[0]
                        elif op == 'OpBitcast':value = struct.unpack('<I',struct.pack('<f',v(args[1])))[0]
                        elif op == 'OpULessThan':value = int(v(args[1]) < v(args[2]))
                        elif op == 'OpBranchConditional':pc = labels[args[1] if v(args[0]) else args[2]];continue
                        elif op == 'OpBranch':pc = labels[args[0]];continue
                        else:raise AssertionError('Unsupported authored fixture operation: '+op)
                        values[result] = value
    return bytes(data)


def with_payload(source):
    lines = source.splitlines()
    entry = next(n for n,l in enumerate(lines) if l.startswith('OpEntryPoint'))
    lines[entry] += ' %payload'
    first = lines.index('%main = OpFunction %void None %fn')
    lines[first:first] = ['%pa = OpTypeArray %u32 %k32', '%pp = OpTypePointer TaskPayloadWorkgroupEXT %pa',
                         '%pw = OpTypePointer TaskPayloadWorkgroupEXT %u32', '%payload = OpVariable %pp TaskPayloadWorkgroupEXT']
    at = lines.index('%gx = OpCompositeExtract %u32 %group 0')
    lines[at:at+1] = ['%rawgx = OpCompositeExtract %u32 %group 0', '%access = OpAccessChain %pw %payload %k0',
                      '%pload = OpLoad %u32 %access', '%true = OpIEqual %bool %k0 %k0',
                      '%gx = OpSelect %u32 %true %rawgx %pload']
    return '\n'.join(lines)+'\n'


class Profile96Tests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.source = profile.synthetic_fixture_assembly()
        cls.instrumented, cls.report = profile.instrument_assembly(cls.source, 'synthetic-fixture')

    def test_frozen_d8_module_and_globals_are_unchanged(self):
        self.assertEqual(hashlib.sha256(Path(frozen.__file__).read_bytes()).hexdigest(),profile.FROZEN_SHA256)
        self.assertEqual(frozen.RECORD_BYTES,44928)
        self.assertEqual(len(frozen.LOCATIONS),9)
        self.assertEqual(frozen.instrument_assembly(frozen.synthetic_fixture_assembly(),'synthetic-fixture')[1]['original_output_store_sites'],168)

    def test_full_fixture_interpretation_matches_independent_oracle(self):
        for origin,groups in [([0,0,0],[1,1,1]),([95,0,0],[2,1,1]),([3,2,1],[2,2,2]),([0xffffffff,0,0],[1,1,1])]:
            with self.subTest(origin=origin,groups=groups):
                self.assertEqual(interpret_fixture(self.instrumented,origin,groups),profile.fixture_expected_capture(origin,groups))

    def test_complete_guard_padding_counts_markers_and_unemitted_values(self):
        for primitives in (0,1,95,96):
            words = struct.unpack('<9216I',profile.fixture_expected_capture([primitives,0,0],[1,1,1]))
            self.assertEqual(words[:16],(profile.INNER_GUARD,)*16)
            self.assertEqual(words[9200:],(profile.INNER_GUARD,)*16)
            self.assertEqual(words[16:80],(96,primitives)*32)
            self.assertEqual(words[4688:8912],(1,)*4224)
            self.assertEqual(words[8912:9200],(1,)*(3*primitives)+(0,)*(288-3*primitives))
            for i in range(96):
                self.assertEqual(words[4304+i*4+3],profile.VALUE_SENTINEL)
                if i>=primitives:self.assertEqual(words[4304+i*4:4304+i*4+3],(profile.VALUE_SENTINEL,)*3)

    def test_every_original_output_and_collective_is_retained(self):
        relevant=lambda l:l.startswith('OpStore ')or l.startswith('OpSetMeshOutputsEXT')or l.startswith('OpControlBarrier')
        before=Counter(l for l in self.source.splitlines() if relevant(l));after=Counter(self.instrumented.splitlines())
        self.assertTrue(all(after[l]==n for l,n in before.items()))
        self.assertIn('OpDecorate %position Invariant',self.instrumented)
        self.assertEqual(self.report['original_output_store_sites'],138)
        self.assertFalse(self.report['runtime_execution_authorized']);self.assertFalse(self.report['equivalence_proven'])

    def test_actual_layout_reflection_is_four_ubo_seven_ssbo_elements(self):
        rows=self.report['input_bindings']
        self.assertEqual([(r['binding'],r['descriptor_type'],r['descriptor_count'])for r in rows],
                         [(b,'uniform_buffer'if b<4 else 'storage_buffer',3 if b==8 else 1)for b in range(9)])
        self.assertEqual(profile.LAYOUT['stride_bytes'],36864)

    def test_profile_and_coverage_mismatches_fail(self):
        for old,new in [('OutputVertices 96','OutputVertices 128'),('Location 12','Location 13'),
                        ('%fptr %generic0 %vertex0 %k3','%fptr %generic0 %vertex0 %k2'),
                        ('%vertex0 = OpIAdd %u32 %x %k0','%vertex0 = OpIAdd %u32 %x %k1')]:
            with self.subTest(old=old),self.assertRaises(ValueError):
                profile.instrument_assembly(self.source.replace(old,new),'synthetic-fixture')
        with self.assertRaises(ValueError):profile.instrument_assembly(self.source,'made-up-kind')

    def test_output_loads_pointer_escapes_and_external_writes_fail(self):
        at='OpStore %p_f0_0_0 %f_f0_0_0'
        for extra in ['%bad = OpLoad %f32 %p_f0_0_0','%bad = OpCopyObject %fptr %p_f0_0_0',
                      '%badptr = OpAccessChain %ssbouint %ssbo0 %k0 %k0\nOpStore %badptr %k0']:
            source=self.source.replace('%main = OpFunction','%ssbouint = OpTypePointer StorageBuffer %u32\n%main = OpFunction')
            with self.subTest(extra=extra),self.assertRaises(ValueError):
                profile.instrument_assembly(source.replace(at,at+'\n'+extra),'synthetic-fixture')

    def test_exact_dead_payload_proof_and_live_payload_refusal(self):
        source=with_payload(self.source)
        out,report=profile.instrument_assembly(source,'synthetic-fixture')
        self.assertTrue(report['dead_payload_cleanup']['proved_dead'])
        self.assertIn('%gx = OpCopyObject %u32 %rawgx',out)
        self.assertNotIn('%payload = OpVariable',out)
        for old,new in [('%true = OpIEqual %bool %k0 %k0','%true = OpIEqual %bool %k0 %k1'),
                        ('%gx = OpSelect','%extra = OpIAdd %u32 %pload %k1\n%gx = OpSelect')]:
            with self.assertRaises(ValueError):profile.instrument_assembly(source.replace(old,new),'synthetic-fixture')

    def test_digest_pin_and_counted_specialization_fail_closed(self):
        with patch.object(profile,'FROZEN_SHA256','0'*64),self.assertRaisesRegex(ValueError,'digest'):
            profile.synthetic_fixture_assembly()
        with self.assertRaisesRegex(ValueError,'context'):
            profile._specialized(frozen.audit,[('no-such-source-context','x',1)],dict(vars(frozen)))
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'bad.spv';path.write_bytes(bytes.fromhex('03022307')+b'\0'*16)
            with self.assertRaisesRegex(ValueError,'digest'):profile.prepare(path,Path(tmp)/'out')

    def test_bounds(self):
        for count in (0,33,True,-1):
            with self.assertRaises(ValueError):profile.initial_capture(count)
        for origin,groups in [([0,0,0],[33,1,1]),([0xffffffff,0,0],[2,1,1]),([0,0,0],[0,1,1]),([False,0,0],[1,1,1])]:
            with self.assertRaises(ValueError):profile.fixture_expected_capture(origin,groups)

    @unittest.skipUnless(shutil.which('spirv-as')and shutil.which('spirv-val')and shutil.which('spirv-dis'),'SPIR-V Tools required')
    def test_authored_fixture_assembles_validates_and_has_explicit_profile(self):
        with tempfile.TemporaryDirectory(dir=profile.frozen.ROOT/'local') as tmp:
            report=profile.prepare_fixture(Path(tmp)/'out')
            self.assertTrue(report['structural_validation_passed'])
            self.assertEqual(report['output_profile'],profile.PROFILE)
            self.assertEqual(report['record_layout'],profile.LAYOUT)
            self.assertEqual(report['original_push_constant_bytes'],0)
            self.assertFalse(report['runtime_execution_authorized'])


if __name__=='__main__':unittest.main()
