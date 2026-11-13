# Owned compute interval experimental replay profile

`tools/compute_interval_replay.py` adds a separate CPU-prepared profile for the exact interval candidate. It does not change the original physical verifier, native-v5, their plans or historical evidence. Profile 02 completed separate baseline/candidate compile-only checks, two original baseline dispatches and one candidate dispatch. All three used actual subgroup width 32. The candidate matched both baselines across every complete original buffer and the guarded record. The [sanitized observation](../research/compute-interval-replay-observation.json) records frozen identities and evidence hashes.

The original module is `b9af52c0ba07f64eb8b0ca2905c432d094999fdf669aa95182ff7754b510e97b`; the candidate is `d5148342b4517882b25a48e63700cbfb8695454cc05d131da718509ac2563dc0`. The selected owned graph is the same manifest `4c4b5fcaf73318b8edc08406a58e39a2702544f86183e7d894455e7d363206b9`, with direct grid 2×1×1 and local size 64×1×1. Both experimental cases require actual width 32/full subgroups and the reviewed E5 driver. No alternative subgroup, NoOpt or global force setting is introduced.

## Admission rebuilt from actual inputs

Preparation hash-pins all reused tools before importing them. It invokes the unchanged full original graph validator, including backing provenance, physical page/owner spans and finite work. It then reruns the exact [lowering builder](../research/compute-interval-lowering.md) on the actual original module and manifest. The resulting record and candidate must match the independently frozen bundle byte-for-byte.

The source-order model reads the full captured descriptor-visible C0 range of 2224 bytes and the actual storage descriptor ranges/nulls. It does not consult guest pages or reconstruct later guest state. C0's owner must be absent from every admitted storage descriptor and physical table mapping. Its complete owner and visible range remain hash-pinned. The source model proves every uint64 root-selection cell independently for widths 4 and 16.

The adapter disassembles the actual candidate binary again and audits its bounded search recurrence. It independently rechecks binary-word preservation of the five other function bodies and both 166-word physical MISS suffixes. The original push layout, images, work/stores, physical-root offset 48 and all original input data remain unchanged. The actual frozen record header bounds each search to six rows, with at most three iterations; its start/count values index only the twelve complete rows inside the 592-byte range.

These are frozen owned-graph checks. The [live selector ownership contract](../research/live-selector-inputs.md) is separate and remains unimplemented here.

## Same union layout and independent record owner

Both baseline and candidate use the same union descriptor layout. The original fourteen descriptor elements remain exact, including binding-7 null entries and all sampled images/views/samplers. Set 0 binding 12 adds one storage descriptor. Extra unused layout bindings are intentional in the original baseline so layout changes do not confound its comparison with the candidate.

The read-only record is an independently allocated final owner, separate from the original 59 allocations totaling 1,163,919,360 bytes. It has:

| Field | Contract |
|---|---|
| Payload | 592 bytes; 16-byte header plus twelve 48-byte rows |
| Payload SHA-256 | `49da4bdc0c4542b4fef7b5dcbb631d41fb030d80803cde3bb37185722daf9770` |
| Descriptor | Binding 12, element 0, offset 4096, range 592 |
| Guards | 4096 bytes before and after payload; complete owner size 8784 |
| Addressability | No BDA usage/allocation flag and no physical-table mapping |
| Expected output | Every payload and guard byte must remain unchanged |

The candidate's constant typed roots still address original storage descriptors. The record variable is `NonWritable`; no pointer return, pointer phi or nonuniform descriptor feature is added. Native preflight rejects missing/duplicate/wrong descriptor shape, modified header/reserved fields, wrong widths, overlapping intervals, record aliases, physical mappings, and guard corruption before `vkCreateInstance`. The native runtime additionally checks fifteen push descriptors/resources, nine per-stage/set storage descriptors, two uniform buffers, four sampled images and samplers, required null descriptors, the 592-byte storage range and offset alignment **before** logical-device or pipeline creation. Those runtime gates passed for each completed profile-02 case.

Every original mapping, buffer owner, pushed byte, image subresource, sampler word and descriptor range is compared against the original encoded control. Only profile/module identity, the extra owner and binding 12 may differ. All nonzero original page entries are relocated through their original owners; the record is never inserted into that table.

## Independent derived helper and staged review

The adapter derives private native C from the exact original C digest using eighteen counted anchors and emits a complete formatted C diff. It creates a new control magic/version rather than teaching the original helper to accept a new schema. Native code is built with `-O2 -DNDEBUG -Wall -Wextra -Werror`.

The new helper supports three deliberately distinct phases:

1. `--check-only` parses the complete native frame and record contract, then returns before any Vulkan call.
2. `--compile-approved-plan` creates one pipeline and queries actual subgroup width plus structured executable statistics. Pipeline creation occurs before buffer/image allocation; this path never retrieves a queue, allocates a command pool/buffer, submits or dispatches. It cleans up without an idle wait.
3. `--execute-approved-plan` creates fresh owned resources and performs one direct dispatch with a two-second fence and a separately bounded process. There is no device-idle wait on failure. Every original output and the complete guarded record owner are saved.

All provided executable statistics are retained by name, description, format and value. This includes instruction/SEND/spill/fill/GRF/live-register fields when the driver provides them. Compiler cycle estimates are not GPU timings, and no instruction-count ratio is a speedup or FPS result.

The host wrapper preserves the old physical profile: `HS_SPILL_BATCH=1`, `shader_spilling_rate=1`, MAX/FORCE/SUBGROUP overrides absent, and runtime `HS_PUSH_SHADOW` absent. The renderer safe3 runtime profile remains separate. The selected driver library is pinned to `e5a1305ea3b423a18fccbb945aaedd32f8add303926fcc39276ee01032cd456f` and its ICD to `14f4eda1b6fd6cbf07d61edeeffdaaede166f4c09d165fec9f661e9f90556ad3`.

The review sequence is compile-only baseline/candidate inspection first, then separately authorized baseline width-32 execution, its repeat, and candidate width-32 execution. The byte verifier compares **all 59 complete original allocations plus all 8784 record/guard bytes**. Native expected-byte checking alone covers only the extra record, so it cannot certify the original outputs. A baseline comparison with the earlier E5 original reference is an additional explicitly scoped CPU check, rather than a claim that the old and new helper binaries are the same implementation.

## CPU preparation and regression checks

```sh
python3 tools/compute_interval_replay.py prepare \
  local/physical-replay/normalized-provenance-v1 \
  local/compute-interval-lowering-owned-01 \
  --driver local/mesa-cache-safe-v1/driver/intel_icd.json \
  --output local/compute-interval-replay-owned-03
python3 -m unittest discover -s tests -p test_compute_interval_replay.py -v
```

Preparation requires Python 3.11+, SPIRV-Tools, a C compiler, Vulkan development headers/libraries, pkg-config and clang-format. It performs no Vulkan API call. The reviewed second prepared profile is private under `local/compute-interval-replay-owned-02`; the initial `01` artifacts are preserved and superseded by explicit descriptor-capacity/null-feature gates. Its native source, complete diff, binary, proof report, frozen artifacts and two control hashes are reviewable there.

The fifteen CPU regression tests use independently authored records and framing words, never original instructions or assets. Native fixtures are explicitly framing-only and never submitted to Vulkan. They cover all original graph fields, record immutability/guards, missing or incorrect layout elements, physical/descriptor aliases, wrapped roots, width/grid/push changes, source-anchor identity, low-capacity/null-feature rejection, pre-Vulkan and compile-only boundaries, process admission rejection and structured statistics serialization.

## Completed profile-02 observation

The original baseline was dispatched twice in separate fresh processes; the candidate was dispatched once after both baselines agreed. Every case reported actual width 32. The baseline repeat and both candidate-to-baseline comparisons passed across all 59 complete original buffers (1,163,919,360 bytes) and every byte of the 8784-byte record owner. The complete 4 MiB writable owner also matched the preserved earlier E5 reference, SHA-256 `323334824a3f64ff80ba0cb8f8602c8f6dd7be3384a32353bbd8e1eb0628c7c2`. Record payload and both guards remained unchanged.

The separately compiled executable statistics are mixed:

| Driver statistic | Original | Candidate |
|---|---:|---:|
| Instructions | 144485 | 128800 |
| SENDs | 1559 | 1981 |
| Spills | 6690 | 9257 |
| Fills | 14943 | 10178 |
| Scratch bytes | 32768 | 32768 |
| Maximum live registers | 560 | 557 |
| Code bytes | 2889872 | 2557616 |

All sixteen driver statistics remain in the observation, including compiler cycle estimates. These are not measured GPU durations. No timing or speed ratio was measured. This establishes byte agreement for one owned input graph; live-renderer integration, rendered-image quality, historical snapshot atomicity and general equivalence remain separate. No normal-frame FPS claim follows. Profile 02 source, helper, ABI and plans remain frozen; any paired timing experiment requires a separate profile and review.
