# D8 interval owned mesh-output replay preparation

The separate [owned-output observation](grok-d8-interval-replay-observation.md)
now records three parent-executed cases. This page describes the CPU-only
preparation command, which still does not execute the replay.

`tools/grok_d8_interval_replay.py` prepares a mesh-only comparison of the pinned original module and the interval candidate. It compiles `tools/vulkan_d8_interval_replay.c` and runs that helper only with `--validate`. Validation returns before `vkCreateInstance`. A run without the admit token returns before instance creation as well. No queue, device, draw, or timestamp is taken by this step.

The common graphics set is 31 bindings and 33 elements, which exceeds the observed A770 push-descriptor limit of 32. This profile does not use that set or push descriptors. It counts elements: set 0 has bindings 0–8 plus NonWritable storage binding 30 (12 elements), and set 1 has the capture and origin-control buffers (2 elements). The owned total is 14 elements. Both modules receive the same descriptor writes, including binding 30, the two null table slots, and every other view’s `VkBuffer`, offset, and range.

The 400-byte record is copied between two 64-byte guards (`0xd8a1c0de`) and bound at offset 64 with range 400. The historical logical capture is not treated as a writer or alias certificate. The owned plan records zero writers and keeps `historical_atomicity_certified` false. Output comparison, when later admitted, is the entire capture buffer against two independent original32 runs: counts, topology, attributes, writtenness, and guards, with raw bits and no sampled subrange. Timing stays unadmitted.

The original module is instrumented by the existing mesh-output instrumenter and must keep derivative SHA-256 `747185e77510c027a0f23e4d598292c3d1bf47d3c95a145204497d439fae2002`. The candidate is instrumented only after a word check that entry 851 and every function except 149 and 620 match, and that those two helpers keep their five-word miss suffix. Binding 30 must be set 0 and NonWritable. The warp32 hash guard is not relaxed, and interval-plus-warp32 composition is not admitted.

```sh
python3 tools/grok_d8_interval_replay.py prepare \
  --output local/grok-d8-interval-replay \
  --capture local/runtime-mesh-input-02/capture/mesh-inputs.json \
  --original local/grok-d8-interval-compile/original.spv \
  --candidate local/grok-d8-interval/candidate.spv \
  --records local/grok-d8-interval/records.bin \
  --origin 128,0,0 --groups 2,1,1
python3 -m unittest tests.test_grok_d8_interval_replay -v
```
