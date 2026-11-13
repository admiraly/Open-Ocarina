# Actual D8 interval pipeline compilation

The pinned original and interval candidate both created graphics pipelines on
the A770 under one common ordinary descriptor-set layout. Mesh executable width
was 32 in both, fragment width 8. This is compilation evidence: no queue was
retrieved, shader resources allocated, commands recorded, or draw submitted.
The [sanitized observation](../research/grok-d8-interval-compile-observation.json)
retains every native compiler statistic and exact helper/module/driver identities.

The [candidate](../research/grok-d8-interval.md) changes two reachable global-hit
load helpers and preserves the mesh entry, store helpers and original null miss
suffixes. The one-capture 400-byte record was not bound or read by this compile
host. Historical input atomicity and live record ownership are not established.

Both pipelines used 31 bindings containing 33 descriptor elements, including the
new mesh-only storage binding 30. The original ignores that extra binding.
The existing A770 push-descriptor limit is 32 **elements**, so counting binding
numbers would incorrectly admit this layout as push descriptors. This experiment
uses ordinary sets for both variants. A production second-set or other ABI still
needs separate design and validation. Push constants remain 88 bytes, mesh
subgroup requirement 32 with full subgroups off, fragment subgroup policy 0,
and the same diagnostic fixed state and feature thresholds apply to both.
The diagnostic color/depth formats and state do not certify a live draw's state.

| Mesh compiler statistic | Original | Interval candidate |
| --- | ---: | ---: |
| Instructions | 102,162 | 70,612 |
| Code bytes | 2,074,048 | 1,417,488 |
| Spills | 4,899 | 3,870 |
| Fills | 11,320 | 6,091 |
| SENDs | 1,504 | 2,058 |
| Scratch bytes | 32,768 | 32,768 |

All sixteen fragment statistics match exactly. The mesh results are mixed: the
smaller code and reduced spills/fills accompany more SEND instructions and the
same scratch allocation. Compiler `Cycles` is an estimate. Neither it nor process
or pipeline-creation wall time is measured shader execution time or an FPS gain.
These numbers should not be compared with older push-layout experiments as if
those used this same profile.

Each case had its own explicit root GPU lease, fresh empty writable shader cache,
180-second parent process bound and 16 MiB combined pipe bound. No child resource
limits changed and neither case retried. Both children exited normally and were
reaped before the next case. All sixteen frozen input pins matched at completion.
The actual mapped E5 library was inode/path/hash attested, rather than inferred
from an ICD filename. Native-safe controls were `HS_SPILL_BATCH=1`,
`HS_PUSH_SHADOW=1`, `shader_spilling_rate=1`; MAX and recorded unsafe overrides
were absent. Known game/helper process scans passed. Unknown or renamed
background GPU applications were not exhaustively excluded.

The private parent supervisor passed five CPU tests covering normal exit,
timeout, pipe overflow, a descendant holding a pipe open and preservation of
existing logs. It replaced the preparation's shell examples. Publication later
made those examples preserve existing caches and require an explicit driver ICD;
the original preparation sources remain private and unchanged as historical
inputs. The tested C helper, helper binary, layout and SPIR-V bytes did not change.
The plan's file digest and its semantic digest excluding the self-hash field are
recorded separately.

Reproduction requires the [CPU preparation](grok-d8-interval-compile.md), private
original shader inputs, a separately reviewed driver/device profile and a bounded
parent supervisor. The public repository contains authored tools, C source,
tests and metadata; shader binaries, records and raw outputs remain private.
The next runtime step is complete owned mesh-output replay against repeated
originals, followed by separately admitted timing and live integration. Rendering
quality, native-feature parity, gameplay stability and real 60 FPS remain open.
