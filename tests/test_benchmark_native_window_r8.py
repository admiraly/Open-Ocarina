# SPDX-License-Identifier: GPL-3.0-or-later
"""CPU fixtures for the r8 native-window collector. No game, device, or capture bytes."""
from __future__ import annotations
import hashlib
import subprocess
import sys
import struct
import importlib.util
from pathlib import Path
import tempfile
import unittest
ROOT=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('native_window_r8_checker',ROOT/'patches/hookshot/check_benchmark_native_window_r8.py')
checker=importlib.util.module_from_spec(spec);spec.loader.exec_module(checker)

class NativeWindowR8Tests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if hashlib.sha256(checker.HEADER.encode()).hexdigest()!=checker.HEADER_SHA:
            raise AssertionError('r8 patch header is not the executed header')
        cls.temporary=tempfile.TemporaryDirectory(prefix='oo-native-window-r8-')
        cls.directory=Path(cls.temporary.name)
        cls.driver,cls.command=checker.compile_fixture(cls.directory,'gcc')
        cls.reports={case:checker.run_case(cls.driver,case,cls.directory) for case in checker.CASES}
        cls.old=checker.prior_header_reproduces_pending_run_bug('gcc')
        cls.proof=checker.prove_reapplication()
        cls.watch=None
        if cls.proof.get('available'):
            replay=cls.proof.pop('replay')
            cls.watch=[checker.compile_watch(replay,'g++'),checker.compile_watch(replay,'clang++')]
    @classmethod
    def tearDownClass(cls):cls.temporary.cleanup()
    def test_legacy_fixture_has_unknown_lifecycle_and_is_not_a_settled_run(self):
        report=self.reports['startup']
        self.assertFalse(report['all_native_runs_settled'])
        self.assertEqual(report['native_run_lifecycle'],{})
        self.assertIn('native replay lifecycle unobserved for legacy fixture',report['unresolved'])
        self.assertEqual(report['lost'],0)
        self.assertLess(report['events'],30)
    def test_suspended_run_stays_pending_until_settle_and_links(self):
        report=self.reports['suspended-run']
        self.assertTrue(report['all_native_runs_settled'])
        self.assertFalse(report['structural_errors'])
        self.assertIn('begin/close are controller CPU boundaries, not a whole-frame or scene isolation guarantee',report['unresolved'])
        self.assertFalse((self.directory/'settled-missing-link.bin').exists())
    def test_drop_end_and_drop_start_are_not_settled(self):
        for case in ('midrun-gap','midrun-no-links','suspended-run'):
            self.assertEqual(self.reports[case]['reader_negatives'],['dropped_run_end','dropped_run_start'])
    def test_owned_bytes_above_one_mebibyte_stay_inside_the_same_aggregate(self):
        report=self.reports['large_owned']
        self.assertEqual(report['owned_streams'][0]['size'],1024*1024+64)
        self.assertFalse((self.directory/'aggregate_refuse.bin').exists())
    def test_resume_keeps_every_owned_part_link(self):
        self.assertEqual(self.reports['resumed_cover']['reader_negatives'],['dropped_first','foreign_part','mismatched_generation'])
        self.assertTrue(self.reports['shared_cover']['all_native_runs_settled'] is False or self.reports['shared_cover']['native_run_lifecycle']=={})
    def test_publication_failures_and_unknown_bits_stay_closed(self):
        for name in ('existing','symlink','sync_failure','close_failure','publish_failure','overflow','stuck_link'):
            if name in ('existing','symlink'):
                self.assertEqual((self.directory/(name+'.bin' if name=='existing' else 'symlink-target')).read_bytes(),b'original')
            else:
                self.assertFalse((self.directory/(name+'.bin')).exists())
        from tools.benchmark_native_bridge import SCOPE_UNKNOWN
        self.assertEqual(self.reports['startup']['flags'] & SCOPE_UNKNOWN, SCOPE_UNKNOWN)
    def test_prior_header_reproduces_both_pending_run_regressions(self):
        self.assertTrue(self.old['compiled'])
        self.assertEqual(self.old['failed_regressions'],['midrun-gap','midrun-no-links'])
    def test_reapplication_matches_executed_source_bytes(self):
        if not self.proof.get('available'):
            self.skipTest('pinned hookshot object store is not in this tree')
        self.assertTrue(self.proof['whitespace_nowarn_byte_identical'])
        self.assertTrue(self.proof['whitespace_error_check'])
        self.assertEqual(self.proof['files'],checker.EXECUTED)
        self.assertEqual(self.watch,[{'cases':8,'compiler':'g++'},{'cases':8,'compiler':'clang++'}])
    def test_reader_cli_runs_outside_the_repository_in_isolated_python(self):
        proc=subprocess.run([sys.executable,'-I',str(ROOT/'tools/benchmark_native_window_r8.py'),'--help'],cwd=self.directory,capture_output=True,text=True,timeout=10)
        self.assertEqual(proc.returncode,0,proc.stderr)
        self.assertIn('capture',proc.stdout)
    def test_checker_cli_runs_without_private_source_or_prior_patch(self):
        proc=subprocess.run([sys.executable,'-I',str(ROOT/'patches/hookshot/check_benchmark_native_window_r8.py'),'--compiler','clang'],cwd=self.directory,capture_output=True,text=True,timeout=45)
        self.assertEqual(proc.returncode,0,proc.stderr)
        import json
        report=json.loads(proc.stdout)
        self.assertEqual(len(report['cases']),41)
        self.assertTrue(report['prior_header_regressions']['compiled'])
        self.assertFalse(report['actual_forwarding'])
    def test_framing_rejects_truncation_oversize_and_cleared_scope(self):
        from tools import benchmark_native_window_r8 as reader
        original=(self.directory/'startup.bin').read_bytes()
        for data in (original[:-1], b'\0'+original[1:]):
            with self.assertRaises(ValueError):
                reader.decode(data)
        cleared=bytearray(original)
        struct.pack_into('<Q',cleared,24,0)
        with self.assertRaises(ValueError):
            reader.decode(bytes(cleared))
        oversize=bytearray(original)
        struct.pack_into('<I',oversize,56,4097)
        with self.assertRaises(ValueError):
            reader.decode(bytes(oversize))
    def test_reader_pin_holds(self):
        self.assertEqual(hashlib.sha256((ROOT/'tools/benchmark_native_window_r8.py').read_bytes()).hexdigest(),'ebf8f568af5a9d0d7c5117bdb41412228270119aeb90747910f488c4ef9940dc')

if __name__=='__main__':
    unittest.main()
