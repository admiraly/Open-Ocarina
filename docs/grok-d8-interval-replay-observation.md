# D8 interval owned-output observation

The interval candidate reproduced every byte of the original mesh output on
the Arc A770 for one frozen two-workgroup window. Two fresh original runs and
one candidate run each passed the complete output verifier. Each new capture
also matched both earlier original captures for this same window. The
[machine-readable observation](../research/grok-d8-interval-replay-observation.json)
contains the exact helper, input-plan, driver and result identities.

The window starts at group 128 in the captured 705-group grid. Each group
emitted 128 vertices and 32 primitives. All 89,984 capture bytes matched,
including attributes, topology, per-lane counts, writtenness, untouched padding
and inner/outer guards. The native helper also compared all ten complete input
buffers and the origin-control buffer after the draw; every byte was unchanged.
That includes the 400-byte interval record and its two 64-byte guards.

All cases used the same owned input graph, ordinary descriptor sets with 14
elements, robust buffer access and the same two null table entries. The native
executable query reported width 32 for each case. The loaded E5 driver was
checked against its file hash, mapped path and inode. Each owned process exited
normally, was reaped and was gone before the next case started. Parent time
and pipe bounds were used without child address-space or file-size limits.

This tests one logical readonly capture. Historical writer quiescence and
imported-memory alias ownership remain unknown. The unchanged entry still has
21 direct storage loads outside the two rewritten helpers. The fixed record
does not authorize live integration, and interval-plus-warp32 composition has
not been tested. No GPU timestamps, gameplay FPS, image-quality result or
native-feature restoration were measured.

The [preparation tool](grok-d8-interval-replay.md) remains CPU-only. Its native
helper was executed separately by the parent after review. Publication added
optional dependency checks to its tests and an exact source-path allowance;
the executed C source, helper, instrumented modules and input plan were unchanged.
