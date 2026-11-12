# SPDX-License-Identifier: GPL-3.0-or-later
import copy
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

SCRIPT = Path(__file__).resolve().parents[1] / 'patches/hookshot/check_binding_provenance.py'
SPEC = importlib.util.spec_from_file_location('binding_provenance_check', SCRIPT)
CHECK = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(CHECK)


def event(sequence, kind, **fields):
    return dict(sequence=sequence, kind=kind, **dict(dict(object=0, related=0, offset=0, size=0,
                memory_offset=0, flags=0, result=0), **fields))


class BindingProvenanceTests(unittest.TestCase):
    def setUp(self):
        self.document = {
            'schema': 'open-ocarina.binding-provenance/v1', 'replay_complete': False,
            'cpu_writers_quiesced': False, 'backing_provenance_complete': True,
            'registry_start_sequence': 0, 'selected_ledger_sequence': 6,
            'recording_part': 2, 'logical_value': 3, 'recording_sequence': 4,
            'completed_recording_sequence': 4, 'selected_occurrence': 64,
            'program': '1' * 16, 'variant': '2' * 16, 'spirv_sha256': '3' * 64,
            'host_event_endpoint': 0, 'host_event_history_complete': True, 'host_events': [],
            'objects': [{'catalog_id': 0, 'kind': 'buffer', 'handle': 'a' * 16, 'tracked': True,
                'lifetime_id': 3, 'created_sequence': 4, 'size': 1024, 'allocation_lifetime_id': 0,
                'edges': [{'resource_offset': 0, 'size': 1024, 'memory_lifetime_id': 1,
                    'memory_offset': 128, 'flags': 0, 'bind_sequence': 5, 'sparse': False,
                    'fence_completion_sequence': 0}]}],
            'memories': [{'lifetime_id': 1, 'handle': 'a' * 16, 'size': 4096, 'kind': 'vma', 'created_sequence': 2}],
            'events': [event(2, 'memory_create', object=1, size=4096),
                event(4, 'resource_create', object=3, size=1024),
                event(5, 'ordinary_bind_commit', object=3, related=1, size=1024, memory_offset=128),
                event(6, 'capture_selected')], 'gaps': [],
            'remaining_gates': ['cpu_writer_epoch', 'temporal_state', 'canonical_alias_payload_agreement', 'independent_output_comparison']}

    def test_nonzero_bind_offset_is_complete_but_not_replay(self):
        result = CHECK.validate_document(self.document)
        self.assertTrue(result['backing_provenance_complete'])
        self.assertFalse(result['replay_complete'])

    def test_optimized_real_production_helper(self):
        result = CHECK.check_helper()
        self.assertEqual(len(result['cases']), 21)
        self.assertFalse(result['gpu_execution'])

    def test_exact_completion_required(self):
        for name, value in [('recording_sequence', 0), ('completed_recording_sequence', 3),
                            ('recording_part', True), ('logical_value', 1 << 64)]:
            d = copy.deepcopy(self.document);d[name] = value
            with self.subTest(field=name), self.assertRaises(ValueError):
                CHECK.validate_document(d)

    def test_lifetime_and_bind_events_must_agree(self):
        for seq, field in [(2, 'object'), (4, 'size'), (5, 'memory_offset')]:
            d = copy.deepcopy(self.document)
            next(e for e in d['events'] if e['sequence'] == seq)[field] += 1
            with self.subTest(sequence=seq), self.assertRaises(ValueError):
                CHECK.validate_document(d)

    def test_destroyed_or_freed_selected_lifetime_cannot_be_complete(self):
        for kind, lifetime in [('resource_destroy', 3), ('memory_free', 1)]:
            d = copy.deepcopy(self.document)
            d['events'][-1] = event(6, kind, object=lifetime)
            d['events'].append(event(7, 'capture_selected'))
            d['selected_ledger_sequence'] = 7
            with self.subTest(kind=kind), self.assertRaisesRegex(ValueError, 'destroyed or freed'):
                CHECK.validate_document(d)

    def test_bind_cannot_precede_resource_creation(self):
        d = copy.deepcopy(self.document)
        d['events'][2]['sequence'] = 3
        d['objects'][0]['edges'][0]['bind_sequence'] = 3
        d['events'].sort(key=lambda row: row['sequence'])
        with self.assertRaisesRegex(ValueError, 'Binding must follow'):
            CHECK.validate_document(d)

    def test_bind_cannot_precede_memory_creation(self):
        d = copy.deepcopy(self.document)
        d['events'][0]['sequence'] = 4
        d['memories'][0]['created_sequence'] = 4
        d['events'][1]['sequence'] = 2
        d['objects'][0]['created_sequence'] = 2
        d['events'][2]['sequence'] = 3
        d['objects'][0]['edges'][0]['bind_sequence'] = 3
        d['events'].sort(key=lambda row: row['sequence'])
        with self.assertRaisesRegex(ValueError, 'Binding must follow'):
            CHECK.validate_document(d)

    def test_bind_gaps_overlaps_overflow_and_unknown_memory(self):
        for name, value in [('resource_offset', 1), ('size', (1 << 64) - 1),
                            ('memory_offset', 4000), ('memory_lifetime_id', 999)]:
            d = copy.deepcopy(self.document);d['objects'][0]['edges'][0][name] = value
            with self.subTest(field=name), self.assertRaises(ValueError):
                CHECK.validate_document(d)

    def test_unknown_owner_and_empty_catalog_cannot_be_complete(self):
        for objects in [[], [{'catalog_id': 0, 'handle': '0' * 16, 'kind': 'buffer', 'tracked': False}]]:
            d = copy.deepcopy(self.document);d['objects'] = objects
            with self.assertRaises(ValueError):CHECK.validate_document(d)
        self.document['objects'] = [{'catalog_id': 0, 'handle': '0' * 16, 'kind': 'buffer', 'tracked': False}]
        self.document['backing_provenance_complete'] = False
        self.document['gaps'] = ['untracked_resource_owner']
        self.assertFalse(CHECK.validate_document(self.document)['backing_provenance_complete'])

    def test_duplicate_resource_or_memory_lifetimes_rejected(self):
        for name in ['objects', 'memories']:
            d = copy.deepcopy(self.document);d[name].append(copy.deepcopy(d[name][0]))
            with self.subTest(name=name), self.assertRaises(ValueError):CHECK.validate_document(d)

    def test_missing_selected_event_and_future_events_rejected(self):
        self.document['events'].pop()
        with self.assertRaises(ValueError):CHECK.validate_document(self.document)
        self.document['events'].append(event(7, 'capture_selected'))
        with self.assertRaises(ValueError):CHECK.validate_document(self.document)

    def test_sparse_requires_successful_original_queue_and_fence(self):
        d = self.document;d['events'][-2:] = [event(5, 'sparse_queue_result', object=3),
            event(6, 'sparse_fence_result', related=5),event(7, 'sparse_bind_commit', object=3, related=1,
                size=1024, memory_offset=128, flags=9),event(8, 'capture_selected')]
        d['selected_ledger_sequence'] = 8
        edge = d['objects'][0]['edges'][0];edge.update(sparse=True, flags=9, bind_sequence=7, fence_completion_sequence=6)
        self.assertTrue(CHECK.validate_document(d)['backing_provenance_complete'])
        d['events'][3]['result'] = -4
        with self.assertRaises(ValueError):CHECK.validate_document(d)

    def test_sparse_queue_cannot_precede_resource_creation(self):
        d = self.document
        d['events'][-2:] = [event(3, 'sparse_queue_result', object=3),
            event(6, 'sparse_fence_result', related=3),
            event(7, 'sparse_bind_commit', object=3, related=1, size=1024, memory_offset=128),
            event(8, 'capture_selected')]
        d['events'].sort(key=lambda row: row['sequence'])
        d['selected_ledger_sequence'] = 8
        d['objects'][0]['edges'][0].update(sparse=True, bind_sequence=7, fence_completion_sequence=6)
        with self.assertRaisesRegex(ValueError, 'Sparse submission precedes'):
            CHECK.validate_document(d)

    def test_replay_writer_or_hidden_gap_claim_rejected(self):
        for name, value in [('replay_complete', True), ('cpu_writers_quiesced', True),
                            ('registry_start_sequence', 1), ('gaps', ['hidden'])]:
            d = copy.deepcopy(self.document);d[name] = value
            with self.subTest(field=name), self.assertRaises(ValueError):CHECK.validate_document(d)

    def test_host_lease_immutable_fields_and_status_checked(self):
        memory = self.document['memories'][0];memory['kind'] = 'dma-buf'
        info = dict(abi_version=1, struct_size=88, kind=2, invalid_reason=0, lease_id=7, backing_id=8,
            mapping_id=9, backing_offset=128, size=4096, acquired_generation=2, current_generation=2,
            acquired_event=1, invalidated_event=0)
        memory.update(acquire_result=1, acquired=info, validation_result=1, validated=copy.deepcopy(info))
        self.document['host_event_endpoint'] = 1
        self.document['host_events'] = [dict(event_id=1, kind=1, result_errno=0, backing_id=8, mapping_id=9, backing_offset=0, size=8192, generation=1)]
        memory['validated']['current_generation'] = 3
        self.assertTrue(CHECK.validate_document(self.document)['backing_provenance_complete'])
        for name, value in [('invalid_reason', 1), ('backing_offset', 129), ('acquired_generation', 3), ('kind', 1)]:
            d = copy.deepcopy(self.document);d['memories'][0]['validated'][name] = value
            with self.subTest(field=name), self.assertRaises(ValueError):CHECK.validate_document(d)

    def test_host_event_endpoint_is_checked(self):
        self.document['host_event_endpoint'] = 7
        with self.assertRaises(ValueError):CHECK.validate_document(self.document)

    def test_original_mapping_must_cover_the_whole_retained_lease(self):
        d = self.document
        info = dict(abi_version=1, struct_size=88, kind=2, invalid_reason=0, lease_id=7, backing_id=8,
            mapping_id=9, backing_offset=128, size=4096, acquired_generation=2, current_generation=2,
            acquired_event=1, invalidated_event=0)
        d['memories'][0].update(kind='dma-buf', acquire_result=1, acquired=info,
            validation_result=1, validated=copy.deepcopy(info))
        d['host_event_endpoint'] = 1
        d['host_events'] = [dict(event_id=1, kind=1, result_errno=0, backing_id=8, mapping_id=9,
            backing_offset=0, size=8192, generation=1)]
        self.assertTrue(CHECK.validate_document(d)['backing_provenance_complete'])
        for field, value in [('size', 1), ('backing_offset', 129), ('size', (1 << 64) - 1)]:
            bad = copy.deepcopy(d)
            bad['host_events'][0][field] = value
            if value == (1 << 64) - 1:
                bad['host_events'][0]['backing_offset'] = 1
            with self.subTest(field=field, value=value), self.assertRaises(ValueError):
                CHECK.validate_document(bad)

    def test_symlink_artifact_and_malformed_header_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary);source = root / 'source.json';source.write_text(json.dumps(self.document))
            (root / 'binding-provenance.json').symlink_to(source)
            with self.assertRaises(ValueError):CHECK.validate_capture(root)
            bad = root / 'bad.patch';bad.write_text('+++ b/gpu/vk/binding_provenance.h\n@@ -0,0 +1,2 @@\n+truncated\n')
            with self.assertRaises(ValueError):CHECK.header_from_patch(bad)

    def test_capture_associates_exact_recording_and_catalog(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / 'binding-provenance.json').write_text(json.dumps(self.document))
            table = {'schema': 'open-ocarina.gpu-page-table/v1', 'pipeline': {k: self.document[k] for k in ('program', 'variant', 'spirv_sha256')},
                     'submission': {k: self.document[k] for k in ('recording_part', 'logical_value', 'recording_sequence', 'completed_recording_sequence')}}
            resources = {k: self.document[k] for k in ('program', 'variant', 'spirv_sha256', 'recording_part', 'recording_sequence', 'completed_recording_sequence', 'selected_occurrence')}
            resources.update(schema='open-ocarina.gpu-allocations/v2', buffers=[{'buffer': 'a' * 16, 'size': 1024}])
            (root / 'manifest.json').write_text(json.dumps(table))
            (root / 'allocations.json').write_text(json.dumps(resources))
            self.assertTrue(CHECK.validate_capture(root)['same_capture_catalog_checked'])
            resources['selected_occurrence'] = 65
            (root / 'allocations.json').write_text(json.dumps(resources))
            with self.assertRaises(ValueError):CHECK.validate_capture(root)
