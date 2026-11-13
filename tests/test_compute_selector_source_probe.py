# SPDX-License-Identifier: GPL-3.0-or-later
from pathlib import Path
import hashlib
import tempfile
import unittest

from tools import compute_selector_source_probe as probe


class SelectorSourceProbeTests(unittest.TestCase):
    def test_frozen_core_dependency(self):
        self.assertEqual(probe.sha(probe.ROOT/'src/probes/compute_interval_records.h'),probe.CORE_SHA)

    def test_nested_actual_struct_extraction(self):
        self.assertEqual(probe.body('prefix struct A { struct B { int a; }; int b; }; tail','struct A {'),
                         'struct A { struct B { int a; }; int b; }')
        with self.assertRaises(ValueError):probe.body('missing','struct A {')

    def test_patch_copies_authored_headers_only(self):
        text=probe.PATCH.read_text();names=[]
        for line in text.splitlines():
            if line.startswith('diff --git '):names.append(line.split(' b/',1)[1])
        self.assertEqual(names,['gpu/vk/compute_interval_records.h','gpu/vk/compute_selector_extract.h'])
        self.assertNotIn('diff --git a/gpu/vk/vk_draw.cpp',text)
        for filename in ('compute_interval_records.h','compute_selector_extract.h'):
            active=False;lines=[]
            for line in text.splitlines(keepends=True):
                if line.startswith('diff --git '):active=line.rstrip().endswith(' b/gpu/vk/'+filename)
                elif active and line.startswith('+')and not line.startswith('+++'):lines.append(line[1:])
            self.assertEqual(''.join(lines),(probe.ROOT/'src/probes'/filename).read_text())

    def test_unreviewed_source_rejected_before_compilation(self):
        with tempfile.TemporaryDirectory(dir=probe.ROOT/'local') as tmp:
            root=Path(tmp);source=root/'source/gpu/vk';source.mkdir(parents=True)
            (source/'vk_draw.cpp').write_text('changed')
            with self.assertRaisesRegex(ValueError,'Unreviewed renderer source'):
                probe.run(root/'source',root/'output')
            self.assertFalse((root/'output').exists())

    def test_api_keeps_live_core_boundary_and_has_no_gpu_calls(self):
        text=(probe.ROOT/'src/probes/compute_selector_extract.h').read_text()
        self.assertIn('in.mode=ci::SourceMode::LiveAsSubmitted',text)
        self.assertNotIn('OwnedFrozenGraph',text)
        for forbidden in ('vkCmd','vkQueue','vkCreate','vmaGet','system(','popen('):self.assertNotIn(forbidden,text)
        self.assertIn('const ci::Input& input() const',text)


if __name__=='__main__':unittest.main()
