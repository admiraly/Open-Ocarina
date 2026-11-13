# SPDX-License-Identifier: GPL-3.0-or-later
import struct
import unittest

from tools import task_broadcast_batch_fixture as fixture


class TaskBroadcastBatchTests(unittest.TestCase):
    def test_complete_oracle_and_source_identity(self):
        data = fixture.expected_output()
        self.assertEqual(len(data), 4096)
        words = struct.unpack("<1024I", data)
        for destination in range(32):
            for source in range(32):
                self.assertEqual(words[destination * 32 + source], 3 + 17 * source)
        self.assertEqual(len(set(words[:32])), 32)
        self.assertEqual(words[:32], words[-32:])

    def test_task_only_uniform_batch_and_distinct_lowering_operations(self):
        native, shared = fixture.assembly("native32"), fixture.assembly("shared32")
        for text in (native, shared):
            self.assertIn('OpEntryPoint TaskEXT %main "main"', text)
            self.assertIn("OpExecutionMode %main LocalSize 32 1 1", text)
            self.assertEqual(text.count("OpControlBarrier"), 2)
            self.assertIn("OpEmitMeshTasksEXT %c0 %c1 %c1", text)
            self.assertNotIn("OpBranch", text)
            self.assertNotIn("OpLoopMerge", text)
            self.assertNotIn("TaskPayload", text)
            self.assertNotIn("OpAtomic", text)
            self.assertLess(text.index("OpStore %publish %value"), text.index("OpControlBarrier"))
            self.assertLess(text.rindex("OpControlBarrier"), text.index("OpEmitMeshTasksEXT"))
        self.assertEqual(native.count("OpGroupNonUniformBroadcast"), 32)
        self.assertNotIn("OpGroup", shared)
        for lane in range(32):
            self.assertIn(f"OpGroupNonUniformBroadcast %uint %c3 %value %c{lane}\n", native)
            self.assertIn(f"%read{lane} = OpAccessChain %laneptr %scratch %c{lane}\n", shared)
            for text in (native, shared):
                self.assertIn(f"OpStore %output{lane} %value{lane}\n", text)
        with self.assertRaises(ValueError):
            fixture.assembly("arbitrary-target")

    def test_independent_shared_wave_order_model_checks_every_output(self):
        for width in (8, 16, 32):
            for reverse in (False, True):
                waves = [list(range(start, start + width)) for start in range(0, 32, width)]
                if reverse:
                    waves.reverse()
                shared = [None] * 32
                for wave in waves:
                    for lane in wave:
                        shared[lane] = lane * 17 + 3
                # Only after all publications may the uniform barrier complete.
                self.assertNotIn(None, shared)
                output = [None] * 1024
                for wave in waves:
                    for lane in wave:
                        for source in range(32):
                            output[lane * 32 + source] = shared[source]
                self.assertEqual(struct.pack("<1024I", *output), fixture.expected_output())


if __name__ == "__main__":
    unittest.main()
