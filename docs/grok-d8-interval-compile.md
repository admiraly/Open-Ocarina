# D8 common-33 compile preparation

`tools/grok_d8_interval_compile.py` prepares one ordinary descriptor-set layout for the original mesh and the interval candidate. The native helper is `src/probes/grok_d8_interval_compile.c`. Preparation compiles that helper and does not run it.

`maxPushDescriptors` limits the sum of descriptor counts. The original mesh plus fragment layout is 30 bindings and 32 elements. The candidate adds NonWritable storage binding 30 and is 31 bindings and 33 elements. The prior A770 report of 32 is not enough for a 33-element push layout. Both modules therefore use the same 33-element set. Binding 30 is mesh-only. The original mesh does not reference it. Push constants stay 88 bytes. Mesh subgroup size stays 32 with full subgroups off. Fragment subgroup size stays 0. The diagnostic fixed state, robustness, required null descriptor, and device-limit thresholds are shared. Only the mesh SPIR-V differs.

The 21 direct loads in mesh entry 851 (binding 5 four times, binding 6 five times, binding 7 twelve times) stay on the original words. That classification is not a whole-shader rewrite. Functions 277, 390, 507, 738, and 851 stay word-identical, and functions 149 and 620 keep their miss suffixes. The 400-byte record is the pinned one-capture table, not a live certificate, and this compile profile does not bind it.

The sets layout is a diagnostic compile profile. A later renderer still needs a second set, buffer-device address, or another ABI if a benefit is measured. The helper declares a graphics queue family and never retrieves a queue, records commands, allocates shader resources, submits, or draws. Generated example runners have a wall-time bound, require an explicit driver ICD and a new cache directory, set the three native-safe compiler controls, and leave `HS_CS32_MAX_INSTS` unset. They refuse to start unless the parent sets `GROK_D8_COMPILE_ADMIT` to the plan digest. This step does not create that authorization. Native quality and real 60 FPS are not achieved here.

```sh
python3 tools/grok_d8_interval_compile.py prepare \
  --output local/grok-d8-interval-compile \
  --original local/graphics-targets/Hookshot-nightly-d8e59341cf4f629c-0.spv \
  --fragment local/graphics-targets/Hookshot-nightly-d8e59341cf4f629c-1.spv \
  --candidate local/grok-d8-interval/candidate.spv \
  --records local/grok-d8-interval/records.bin
python3 -m unittest tests.test_grok_d8_interval_compile -v
```

The [completed compile observation](grok-d8-interval-compile-observation.md)
uses a separately reviewed parent supervisor with combined pipe bounds and
loaded-driver attestation. The example shell runners are not that supervisor.
The publication revision preserves existing cache evidence instead of removing
it; this changes neither the tested C helper nor the common shader layout.
