# D8 interval full-grid owned-output comparison

The A770 comparison passed all 23 windows covering the captured 705-group grid
exactly once. Each window ran two original baselines and the interval candidate: 69
serial mesh draws at actual width 32. Every complete capture byte matched both
originals. Native checks confirmed unchanged input/control buffers; capture checks
included counts, topology, attributes, writtenness, padding and guards. All owned
processes exited normally and were reaped; loaded driver identities and post-run
pins matched. The [observation](../research/grok-d8-interval-grid.json) records the
artifact identities and limits.

The planner prepares 23 windows that cover the captured 705-group grid exactly once.
Twenty-two windows contain 32 groups. The tail window starts at group 704 and
contains the remaining group. The frozen `d8-interval-replay-helper` is reused
with the same instrumented modules. Only the plan header and the initialized
capture extent change between windows. The readonly input payloads and the
12 descriptor rows stay byte-identical.

The already accepted origin-128 two-group capture remains an additional
reference. It is a different extent from the 32-group window that starts at
group 128, so it is not a substitute for this grid.

CPU preparation does not create a device, queue, or draw. A later parent lease
may run the private runner only after a root grant. That runner submits one
width-32 mesh draw at a time, compares every capture byte with both originals
in the same window, and stops on the first mismatch, timeout, or checker
error. It does not retry. The 2-second fence, 32-group window, and 1 GiB input
cap stay inside the unchanged helper. The parent pipe cap is 16 MiB and the
parent case limit is 180 seconds.

The Mesa shader cache for that lease is one private directory shared by the
serial processes so later cases can reuse compilation. It is not a timing
experiment. The runner checks the mapped `libvulkan_intel.so` path, inode, and
SHA-256 against the cache-safe E5 pin, with `HS_SPILL_BATCH`,
`HS_PUSH_SHADOW`, and `shader_spilling_rate` set and MAX left absent.

The match covers one frozen logical input state. Twenty-three separate windows
do not establish behavior across windows in a single 705-group submission.
Historical atomicity, general equivalence, warp32 composition, live integration
and frame rate remain open. As an additional check, the first two group records
and guards of the origin-128 window matched both earlier two-group references;
those earlier captures were not used as the full-window oracle.

```sh
python3 tools/grok_d8_interval_grid.py prepare \
  --source local/grok-d8-interval-replay-ready-01/inputs.bin \
  --output local/grok-d8-interval-grid-ready-01
python3 -m unittest tests.test_grok_d8_interval_grid -v
```

The parent command, after the root grant, is
`python3 local/grok-d8-interval-grid-root-01/run.py root-d8-full-grid-705-serial-01`.
