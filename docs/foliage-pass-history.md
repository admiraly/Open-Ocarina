# Bounded foliage pass history

The metadata-only diagnostic now completed one reviewed A770 gameplay frame: 55
raster-pass rows and 104 attachment rows, with zero pass readbacks. Begin/end
compiler observations were idle and unchanged. Two control screenshots were
reviewed, all settings and dependency pins matched, and the owned process was
stopped and reaped with no process-group survivors. The
[observation](../research/foliage-pass-history-observation.json) pins this run.
This identifies ordered pass candidates; the card producer is still unknown.

The tool prepares a metadata-first baseline diagnostic. The existing dark cards occur in the original V3/E5 baseline as well
as both mesh candidates. The reference image is identified by its hash; a manual
region around the left card supplies a visual question, not a shader/material
identity. The next run must review its own screenshot because particles, temporal
state and the camera are not deterministic from the available save/script.

The original renderer `629addc2…` cannot safely admit a complete pass-image
history under the requested bounds. `RX_VK_DUMP_STEPS` counts global attempts
before the width filter, `RX_VK_DUMP_PROGS` overrides that step filter, and depth
copies retain full resolution regardless of `RX_VK_DUMP_W`. The audited E5
driver source advertises a 16,384-pixel maximum 2D dimension: one 2,048-wide
32-bit depth image could therefore require 128 MiB. A three-step global window
can bound those payloads, but can miss every scene pass and is not a complete
history. The original bounded `PASS_TRACE` ring holds eight frames and at most
6,000 entries per frame; its periodic logging trigger is not a one-shot capture.
Neither original mechanism exposes complete compiler readiness: `jobs_pending`
excludes Intel's serial compiler queue.

The separate [diagnostic patch](../patches/hookshot/bounded-pass-history.patch)
starts from the exact frozen12 source for that renderer, with its existing
backing ABI header supplied separately. It leaves the signedness candidate07
unchanged. The overlay is off unless `RX_VK_OO_PASS_HISTORY=1` is set. Its first
reviewed phase sets `RX_VK_OO_PASS_PIXELS=0`: it records ordered raster passes
and attachment/written-image metadata at widths 1,536 and 2,048, including
dimensions, formats, texture IDs and source-reported program IDs. The first/last
program of a multi-draw pass does not identify which draw produced a card.
Metadata observes CPU command-recording order; it does not prove that every
recorded pass executed or identify the card's first pixel producer.

The native observer permits one journal per process, at most 512 metadata rows
plus a summary, and at most 256 KiB. Rows retain the encounter order even when
pixels are denied. Reaching a cap, unstable readiness, missing data or an IO
failure latches an incomplete diagnostic. File writes count partial writes too.
The observer bounds its snapshot retries and treats unstable/saturated snapshots
as unready. None of those outcomes skips a game draw, dispatch or pipeline.

Pixel mode exists for a later separately reviewed plan. Before any diagnostic
allocation or copy it reserves logical buffer bytes, intermediate-image payload
bytes and image-to-buffer copy bytes against one 384 MiB aggregate limit; at
most 128 image attempts can be admitted. It records successful resource-request
and copy sizes separately. These are logical Vulkan resource/copy payload bounds;
VMA padding and the game's ordinary memory are outside that accounting. Automatic
mesh-indirect buffer diagnostics are suppressed in observer mode, and unexpected
buffer-dump requests fail the diagnostic. Sampled-texture, SSBO, raw-float,
every-draw, footprint and repeated/modulo dumps are absent from the reviewed plan.

The metadata-only phase records zero pass readbacks. Its two control screenshots
can copy at most 18 MiB of RGBA payload in total. The native PNG writer uses RGB
scanlines and stored deflate blocks, so its output bound follows directly from
dimensions. Even the future native byte cap plus two screenshots stays below
512 MiB of diagnostic copy/resource payload. The journal, capped 16 MiB process
log, maps/options/receipts and dimension-bounded screenshots also fit a separate
512 MiB diagnostic-disk budget. Static binaries/assets, saves and ordinary game
caches are separately inventoried; there is no whole-install or process-RSS cap.
The launcher keeps draining a capped log so closing a pipe cannot interrupt
rendering.

Normal queued/running jobs and serial outstanding/active jobs are explicitly
observed. A saturating monotonic epoch changes on queue/completion events.
Admission requires three identical periodic native observations with zero work,
120 sampled nonloading gameplay frames, matching loaded binary/driver identities
and unchanged baseline settings. The selected frame's begin/end observations must
also agree on the epoch, translation/failure/pipeline/skip counters and abandoned
worker state. Python recomputes that comparison from the journal. This covers the
instrumented source queues and counters; quiet logs or guessed `state=play`
alone never establish readiness.

The [launcher](../tools/pass_history_capture.py) prepares a new install, save,
cache and observer directory from the prior immutable seeds. Shared assets are
read-only inputs; the original working install is untouched. The host remains
`4eb32b56…`, the driver E5, the MAX control absent, frame generation off and mesh
transforms zero. The old cache policy label says `native-safe-v2`; its exact
preserved controls are the same three required safe controls, with MAX absent.
The plan hashes every save/cache file and pins the baseline image/shader inputs.
No camera-transform producer or particle-RNG seed exists, so those remain explicit
gaps rather than inferred equality.

At runtime, a separate parent lease and exact frozen-plan hash are required.
After readiness, one control screenshot is written and the process awaits a
review certificate bound to that image's SHA-256. Only then can one trigger file
arm one frame. The trigger is polled every ten presents; the source requests the
following frame, so request time is not the capture frame. A second screenshot
and the journal bracket the diagnostic without claiming temporal equivalence.
The run has a 240-second wall bound, no retries, and cleanup signals only the
owned PID after checking its process-start identity. A failed cleanup never
claims that the GPU lease is released.

CPU validation compiles the exact authored policy and added native helper bodies
against independent texture/queue/IO stubs. It exercises byte/step/row caps,
overflow-sized requests, metadata-only zero copying, serial work, epoch/counter
changes, partial writes, default-off behavior, journal checks and owned-process
negative cases. The real modified renderer units also compile and link with
optimized release flags. These checks authorize review of a diagnostic, not a
runtime launch, native-feature restoration, card attribution, console equivalence
or FPS acceptance. The [preparation metadata](../research/foliage-pass-history-preparation.json)
pins the exact prepared plan, patch, source and binary. The later observation
identifies the executed fresh plan and wrapper separately. A publication-only
wrapper fix resolves relative plan paths before changing child cwd; the runtime
used the earlier frozen wrapper with an absolute path. A regression covers this
resolution. No renderer or observation was relabeled.

The published patch removes the space prefix from otherwise empty diff-context
lines. Applying it with zero fuzz produces byte-identical source files to the
compiled overlay; the executed and published patch hashes are recorded separately.
