import random
import struct
import unittest

from tools.interval_compile_experiment import Claim, flatten_intervals, interval_select, legacy_select, retain_helpers, shader_source, structure


class IntervalCompileTests(unittest.TestCase):
    def test_first_match_retains_original_base_after_overlap_split(self):
        claims=[Claim(10,20,1), Claim(0,30,2)]
        intervals=flatten_intervals(claims)
        self.assertEqual(intervals,[(0,10,2,0),(10,20,1,10),(20,30,2,0)])
        self.assertEqual(interval_select(claims,intervals,25),(2,25))
        self.assertEqual(interval_select(claims,intervals,15),(1,5))

    def test_nearest_candidate_and_tie_precede_single(self):
        claims=[Claim(0,100,8,True),Claim(20,60,9,True),Claim(20,70,10,True),Claim(0,100,1)]
        for address,expected in [(5,(8,5)),(25,(9,5)),(65,(10,45)),(90,(8,90))]:
            self.assertEqual(legacy_select(claims,address),expected)
            self.assertEqual(interval_select(claims,flatten_intervals(claims),address),expected)

    def test_holes_and_empty_ranges_are_not_claims(self):
        claims=[Claim(0,0,0),Claim(2,4,1),Claim(6,8,2)]
        self.assertIsNone(interval_select(claims,flatten_intervals(claims),5))
        self.assertIsNone(legacy_select(claims,8))

    def test_deterministic_random_grid_checks_identity_not_equal_zero_bytes(self):
        rng=random.Random(4721)
        for _ in range(100):
            claims=[]
            for identity in range(20):
                base=rng.randrange(0,128);end=base+rng.randrange(0,50)
                claims.append(Claim(base,end,identity,identity<2))
            intervals=flatten_intervals(claims)
            self.assertTrue(all(a[1]<=b[0] for a,b in zip(intervals,intervals[1:])))
            for address in range(192):
                self.assertEqual(interval_select(claims,intervals,address),legacy_select(claims,address))

    def test_source_shapes_share_interface_and_dynamic_queries(self):
        cascade=shader_source('cascade',32)
        interval=shader_source('interval',32)
        self.assertIn('claims[33]',cascade)
        self.assertIn('while(low<high)',interval)
        self.assertNotIn('claims[33]',interval)
        for text in (cascade,interval):
            self.assertIn('queries[',text)
            self.assertIn('results[invocation]',text)
            self.assertNotIn('nonuniform',text.lower())

    def test_small_target_shaped_legacy_table_keeps_its_existing_loop(self):
        cascade=shader_source('cascade',5,candidates=0,table_entries=3)
        interval=shader_source('interval',5,candidates=0,table_entries=3)
        self.assertIn('claims[4]',cascade)
        self.assertIn('i<3u',cascade)
        self.assertIn('claims[5u+i]',cascade)
        self.assertIn('parameters.interval_count,15u',interval)
        self.assertNotIn('claims[5u+i]',interval)

    def test_bounded_shapes_and_input_validation(self):
        for args in [('cascade',128),('unknown',16),('interval',16,17),('interval',16,2,5)]:
            with self.assertRaises(ValueError):shader_source(*args)
        with self.assertRaises(ValueError):flatten_intervals([Claim(30,20,0)])

    def test_dontinline_marks_only_nonentry_function(self):
        words=[0x07230203,0x00010300,0,20,0,
               (5<<16)|15,5,1,0x6e69616d,0,
               (5<<16)|54,2,1,0,3,(1<<16)|56,
               (5<<16)|54,2,4,1,3,(1<<16)|56]
        data,count=retain_helpers(struct.pack(f'<{len(words)}I',*words))
        result=struct.unpack(f'<{len(words)}I',data)
        self.assertEqual(count,1)
        self.assertEqual(result[13],0)
        self.assertEqual(result[19],2)

    def test_loop_hint_is_explicit_and_structure_is_metadata_only(self):
        words=[0x07230203,0x00010300,0,20,0,
               (5<<16)|15,5,1,0x6e69616d,0,
               (5<<16)|54,2,1,0,3,
               (4<<16)|246,8,9,0,(1<<16)|56,
               (5<<16)|54,2,4,1,3,(1<<16)|56]
        data,_=retain_helpers(struct.pack(f'<{len(words)}I',*words))
        self.assertEqual(structure(data),{'loop_merges':1,'selection_merges':0,
                                         'function_calls':0,'dontinline_helpers':1})
        with self.assertRaises(ValueError):structure(struct.pack(f'<{len(words)}I',*words))
        with self.assertRaises(ValueError):retain_helpers(b'bad')


if __name__=='__main__':
    unittest.main()
