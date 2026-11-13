# SPDX-License-Identifier: GPL-3.0-or-later
import json
from pathlib import Path
import struct
import tempfile
import unittest

from tools.descriptor_array_runtime_fixture import (
    DATA_BYTES, F, GUARD, LANES, QUERIES, ROWS, S, T, U, canonical, fixture_source,
    frozen_generator, guarded, memory_contract, oracle, ownership_proof, query_rows,
    read_words, sha, values, verify_readback,
)


class TypedRuntimeFixtureTests(unittest.TestCase):
    def test_unique_changes_survive_reciprocal_alias_and_physical_policy(self):
        for follow in (False,True):
            initial,expected,output=oracle(follow)
            self.assertNotEqual(initial['shared'],expected['shared'])
            self.assertEqual(initial['physical']==expected['physical'],not follow)
            for name in ('short','zero_prefix','mapped_elsewhere','ordinary_miss'):
                self.assertEqual(initial[name],expected[name])
            self.assertEqual(len(output),LANES*ROWS*16)
            for lane in (0,1,31,32,63):
                rows=[struct.unpack('<4I',output[(lane*ROWS+index)*16:(lane*ROWS+index+1)*16]) for index in range(ROWS)]
                for group in (7,10,13):
                    for index,width in enumerate((4,8,16)):
                        words=values('storage',width,lane,follow)
                        self.assertEqual(rows[group+index],tuple(words+[0]*(4-len(words))))
                self.assertEqual(rows[0],(0xDA010000|lane,lane,int(follow),ROWS))

    def test_explicit_lengths_short_later_match_zero_prefix_and_page_edges(self):
        initial,_,output=oracle(False)
        row=lambda index:struct.unpack('<4I',output[index*16:(index+1)*16])
        self.assertEqual(row(1),(2,1,0,DATA_BYTES//4))
        self.assertEqual(row(2),(DATA_BYTES//8,DATA_BYTES//16,8,4))
        self.assertEqual(row(22)[0],read_words(initial['short'],4,4)[0])
        self.assertEqual(row(23)[:2],tuple(read_words(initial['short'],0,8)))
        self.assertEqual(row(24),tuple(read_words(initial['shared'],0,16)))
        self.assertEqual(row(25)[:2],(read_words(initial['zero_prefix'],4,4)[0],read_words(initial['ordinary_miss'],8,4)[0]))
        self.assertEqual(row(26),tuple(read_words(initial['zero_prefix'],0,16)))
        self.assertEqual(row(32),tuple(read_words(initial['shared'],4080,16)))
        self.assertEqual(row(34),(0,0,0,0))
        self.assertEqual(row(35),(0,0,0,0))
        self.assertEqual(row(36),(0,0,0,0))

    def test_literal_store_values_are_not_identical_writebacks(self):
        before,after,_=oracle(False)
        self.assertEqual(read_words(after['shared'],256,4),[0xA1000000])
        self.assertEqual(read_words(after['shared'],264,8),[0xA2000000,0xA2100000])
        self.assertEqual(read_words(after['shared'],272,16),[0xA4000000,0xA4100000,0xA4200000,0xA4300000])
        self.assertNotEqual(after['shared'][256:260],before['shared'][256:260])
        _,followed,_=oracle(True)
        self.assertEqual(read_words(followed['physical'],256+63*64,4),[0xB101003F])
        self.assertEqual(read_words(followed['shared'],256+63*64+16,16),[0xA401003F,0xA411003F,0xA421003F,0xA431003F])

    def test_actual_per_lane_descriptor_choice_is_nonuniform_and_bounded(self):
        dependency=frozen_generator()
        views=[dependency.Descriptor(**{key:view[key] for key in ('guest','size_word','bound_bytes','binding','element','kind')}) for view in memory_contract()]
        partitions=dependency.build_partitions(views)
        queries=query_rows()
        self.assertEqual(len(queries),LANES)
        for lane,row in enumerate(queries):
            self.assertEqual(len(row),QUERIES)
            for index,width in enumerate((4,8,16)):
                selected=dependency.interval_lookup(partitions,row[index],width)
                self.assertEqual(selected[:2],(1,0) if lane%2==0 else (0,1))
                self.assertEqual(selected[2],256+lane*64+(0,8,16)[index])
            self.assertTrue(all(address%4==0 for address in row))
            self.assertEqual(row[7],S+4092)
            self.assertEqual(row[8],F+4092)

    def test_ownership_proof_covers_all_disjoint_writes_and_protected_reads(self):
        proof=ownership_proof()
        self.assertEqual(proof['disjoint_written_bytes_per_allocation'],LANES*(4+8+16))
        self.assertTrue(proof['immutable_shared_reads_disjoint_from_all_writes'])
        self.assertTrue(proof['mutable_reads_use_own_lane_spans'])
        self.assertFalse(proof['cross_invocation_barrier_required'])

    def test_shader_emits_every_output_field_and_changed_stores(self):
        source=fixture_source()
        self.assertEqual(source.count('emit_result(lane,'),ROWS)
        self.assertIn('memoryBarrierBuffer()',source)
        self.assertIn('a16[1].values',source)
        self.assertIn('b16[0].values',source)
        self.assertIn('0xa4000000u',source)
        self.assertIn('0xb4000000u',source)
        self.assertNotIn('value^parameters.salt',source)
        self.assertNotIn('restrict',source)

    def test_full_readback_detects_one_byte_output_guard_root_or_table_errors(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            initial,expected,output=oracle(True)
            expected['results']=output+bytes([0xE5])*64
            expected['page_table']=bytes(66*8)
            expected['records']=bytes(32)
            expected['queries']=bytes(88)
            allocations=[]
            actual={}
            addresses={}
            for index,(name,data) in enumerate(expected.items()):
                reference=guarded(data);filename=name+'.bin';(root/filename).write_bytes(reference)
                allocations.append({'name':name,'expected_file':filename,'expected_sha256':sha(reference),'bytes':len(reference)})
                actual[name]=reference;addresses[name]=0x100000+index*0x10000
            relocations=[{'allocation':'page_table','write_offset':GUARD+64*8,'target_allocation':'physical','target_offset':GUARD}]
            plan={'allocations':allocations,'page_table_relocations':relocations}
            (root/'plan.json').write_bytes(canonical(plan))
            table=bytearray(actual['page_table']);struct.pack_into('<Q',table,GUARD+64*8,addresses['physical']+GUARD);actual['page_table']=bytes(table)
            self.assertTrue(verify_readback(root,actual,addresses)['match'])
            for name,offset in (('results',GUARD),('results',0),('shared',GUARD+256),('mapped_elsewhere',GUARD),('page_table',GUARD+64*8),('records',GUARD),('queries',GUARD)):
                altered=dict(actual);data=bytearray(altered[name]);data[offset]^=1;altered[name]=bytes(data)
                self.assertFalse(verify_readback(root,altered,addresses)['match'],(name,offset))
            with self.assertRaises(ValueError):verify_readback(root,{name:data for name,data in actual.items() if name!='short'},addresses)

    def test_equal_payload_copies_are_not_the_alias_resource_contract(self):
        roots=memory_contract()
        b0=next(view for view in roots if (view['binding'],view['element'])==(1,0))
        a1=next(view for view in roots if (view['binding'],view['element'])==(0,1))
        self.assertEqual(b0['allocation'],a1['allocation'])
        self.assertNotEqual(b0['guest'],a1['guest'])
        self.assertEqual(b0['bound_bytes'],a1['bound_bytes'])


if __name__=='__main__':unittest.main()
