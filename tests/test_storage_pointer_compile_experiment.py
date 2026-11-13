import unittest
from pathlib import Path

from tools.storage_pointer_compile_experiment import CONTROLLED_DRIVER_OPTIONS, ROOT, View, adapted_helper, controlled_environment, model_access, pointer_transform, resolved_claims, select_view, source, typed_bound_bytes


class StoragePointerExperimentTests(unittest.TestCase):
    def test_private_helper_adds_only_required_variable_pointer_feature(self):
        original=(ROOT/'tools/vulkan_compile_probe.c').read_text()
        adapted=adapted_helper(original)
        self.assertIn('!supported11.variablePointersStorageBuffer',adapted)
        self.assertIn('.variablePointersStorageBuffer = VK_TRUE',adapted)
        self.assertNotIn('.variablePointers = VK_TRUE',adapted)
        self.assertNotIn('vkCmdDispatch(',adapted)
        self.assertNotIn('vkQueueSubmit(',adapted)
        self.assertEqual((ROOT/'tools/vulkan_compile_probe.c').read_text(),original)

    def test_adapter_fails_when_frozen_source_shape_changes(self):
        with self.assertRaises(ValueError):adapted_helper('unrelated source')

    def test_generated_source_has_distinct_storage_roots_and_physical_miss(self):
        text=source('interval')
        for expected in ('data_a.words','data_b.words','PageReference','ValueReference','parameters.follow_stores==0u','&~3u'):
            self.assertIn(expected,text)
        self.assertNotIn('nonuniform',text.lower())

    def test_pointer_transform_keeps_storage_hits_and_physical_fallback_separate(self):
        assembly='''OpCapability Shader
OpName %lookup "lookup_id(u1;"
OpName %load "global_load(u1;"
OpName %store "global_store(u1;u1;"
OpName %fallback_load "fallback_load(u1;"
OpName %fallback_store "fallback_store(u1;u1;"
OpName %a "data_a"
OpName %b "data_b"
OpName %old_local "removed local"
%u = OpTypeInt 32 0
%bool = OpTypeBool
%void = OpTypeVoid
%v2 = OpTypeVector %u 2
%sp = OpTypePointer StorageBuffer %u
%fp = OpTypePointer Function %u
%load = OpFunction %u None %ft
%arg = OpFunctionParameter %fp
%entry = OpLabel
%old_local = OpLoad %u %arg
OpReturnValue %old_local
OpFunctionEnd
%store = OpFunction %void None %fst
%sarg = OpFunctionParameter %fp
%value = OpFunctionParameter %fp
%sentry = OpLabel
OpReturn
OpFunctionEnd
'''
        transformed=pointer_transform(assembly)
        self.assertIn('OpCapability VariablePointersStorageBuffer',transformed)
        self.assertIn('OpConstantNull %sp',transformed)
        self.assertIn('%oo_pa = OpAccessChain %sp %a',transformed)
        self.assertIn('%oo_pb = OpAccessChain %sp %b',transformed)
        self.assertIn('OpFunctionCall %sp %oo_lookup %arg',transformed)
        self.assertIn('OpFunctionCall %u %fallback_load %arg',transformed)
        self.assertIn('OpStore %oo_store_ptr %oo_store_value',transformed)
        self.assertNotIn('OpName',transformed)
        self.assertNotIn('%old_local',transformed)
        with self.assertRaises(ValueError):pointer_transform('not synthetic assembly')

    def test_wide_bound_length_uses_typed_array_floor(self):
        data=bytearray(range(20))
        views=[View(32,100,memoryview(data))]
        self.assertEqual(resolved_claims(views,16),[(32,48,0,False)])
        # The native helper floors to a16-byte element, not an exact byte address.
        self.assertEqual(select_view(views,44,16),(0,0))
        self.assertEqual(model_access(views,{},44,16),bytes(range(16)))
        self.assertIsNone(select_view(views,48,16))
        self.assertEqual(model_access(views,{},48,16),bytes(16))

    def test_zero_size_is_special_only_for_table_entries(self):
        payload=memoryview(bytearray(range(32)))
        self.assertEqual(resolved_claims([View(64,0,payload,'single')],4),[])
        self.assertEqual(resolved_claims([View(64,0,payload,'table')],4),[(64,96,0,False)])

    def test_overlap_first_match_and_interval_return_same_owner(self):
        views=[View(96,32,memoryview(bytearray([1])*32)),View(112,32,memoryview(bytearray([2])*32))]
        for width in (4,8,16):
            for address in range(80,160):
                self.assertEqual(select_view(views,address,width),select_view(views,address,width,True))
                self.assertEqual(model_access(views,{},address,width),model_access(views,{},address,width,interval=True))
        self.assertEqual(select_view(views,116,4),(0,20))
        self.assertEqual(select_view(views,132,4),(1,20))

    def test_nearest_candidate_precedes_single_and_ties_keep_first(self):
        payload=memoryview(bytearray(64))
        views=[View(96,0,payload,'candidate'),View(112,0,payload,'candidate'),View(112,64,payload)]
        self.assertEqual(select_view(views,120,4),(1,8))
        views.insert(1,View(112,0,payload,'candidate'))
        self.assertEqual(select_view(views,120,4),(1,8))

    def test_storage_alias_survives_selected_pointer_store(self):
        for interval in (False,True):
            backing=bytearray(32)
            views=[View(96,16,memoryview(backing)[:16]),View(192,16,memoryview(backing)[8:24])]
            model_access(views,{},104,4,interval=interval,value=b'ABCD')
            self.assertEqual(model_access(views,{},192,4,interval=interval),b'ABCD')

    def test_wide_hit_floors_to_typed_element_and_miss_rejects_page_crossing(self):
        data=memoryview(bytearray([7])*8192)
        views=[View(0,8192,data)]
        self.assertEqual(select_view(views,4092,8),(0,4088))
        self.assertEqual(model_access(views,{},4092,8),bytes([7])*8)
        pages={0:memoryview(bytearray([9])*4096),1:memoryview(bytearray([8])*4096)}
        self.assertEqual(model_access([],pages,4092,8),bytes(8))

    def test_store_miss_obeys_follow_stores_gate(self):
        page=bytearray(4096);pages={0:memoryview(page)}
        model_access([],pages,64,4,value=b'ABCD')
        self.assertEqual(page[64:68],bytes(4))
        model_access([],pages,64,4,value=b'ABCD',follow_stores=True)
        self.assertEqual(page[64:68],b'ABCD')

    def test_invalid_or_overflowing_model_inputs_fail(self):
        view=View(0,16,memoryview(bytearray(16)))
        with self.assertRaises(ValueError):resolved_claims([view],3)
        with self.assertRaises(ValueError):resolved_claims([View((1<<64)-4,100,view.payload)],4)

    def test_u32_bound_shift_wrap_is_rejected_without_large_allocation(self):
        self.assertEqual(typed_bound_bytes(0xFFFFFFFF,16),0xFFFFFFF0)
        for width in (4,8,16):
            with self.assertRaises(ValueError):typed_bound_bytes(0x100000000,width)
        with self.assertRaises(ValueError):typed_bound_bytes(-1,4)

    def test_all_controlled_compiler_options_are_cleared_and_recorded(self):
        ambient={key:'unexpected' for key in CONTROLLED_DRIVER_OPTIONS}
        ambient['unrelated']='retained'
        environment,record=controlled_environment(ambient,['HS_SPILL_BATCH=1'])
        self.assertEqual(environment,{'unrelated':'retained','HS_SPILL_BATCH':'1'})
        self.assertEqual(record,{key:'1' if key=='HS_SPILL_BATCH' else None for key in CONTROLLED_DRIVER_OPTIONS})


if __name__=='__main__':unittest.main()
