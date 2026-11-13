# Actual compute interval-load prototype

Status: CPU-only frozen-graph prototype. The original target and candidate both validate as Vulkan 1.3 SPIR-V with uniform-buffer-standard-layout enabled. No Vulkan API, pipeline compilation, shader execution, source-vendor edit or execution admission was performed in this lane.

## Exact target and available inputs

The original SPIR-V is pinned to `b9af52c0ba07f64eb8b0ca2905c432d094999fdf669aa95182ff7754b510e97b`, compute key `cc62764061c44f6a`, variant `095931037888515a`. This is the original owned graph already used by the physical-replay diagnostic, not a new live snapshot. Its historical CPU atomicity remains unknown.

`tools/compute_interval_lowering.py` derives the two reachable typed load helpers from the exact module and checks their storage-array types/strides, uniform-buffer layout, hit address and typed-index arithmetic, endpoint comparisons, five ordinary returns and one table-loop return. It also invokes the unchanged original physical-span/finite-work auditor (`f60dabf565b21b5b883d2784541682c27b88df8520bc16fffe5117d115d6ac33`).

The compiled helpers have **no emitted nearest-candidate phase**. That observation does not decode the original `Info::native_global_bounds` flag; it establishes the effective path of this exact module. The source-order single roots are bindings 2 through 6. Their selector byte offsets in UBO0 are respectively 192, 208, 256, 272 and 352. The three global-table entries belong to binding 7 and occupy UBO0 bytes 2176 through 2223, in 16-byte records. Each record provides a 64-bit unaligned guest base and the following low 32-bit size word.

All those bytes are present in the exact 2224-byte descriptor-visible UBO0 range of the owned graph. The builder hashes the complete captured UBO owner and derives the visible range from the canonical view plus the exact descriptor offset; it does not read later guest memory. It checks actual storage descriptor ranges and null entries. The writable binding-6 allocation must not alias the UBO0 allocation. This is not a proof that a live driver's CPU inputs can always be sampled atomically.

The original source rules were inspected in `EmitContext::DefineGlobalMemoryFunctions`, not guessed from buffer contents. `GPULoadU32` and `GPULoadU32x4` select:

1. The first ordinary single whose shader-computed range contains the start address.
2. Otherwise the first matching table entry, in element order 0, 1, 2.
3. Otherwise the original physical-page MISS path.

The prototype never replaces those hits with physical reads and never consults CPU `guest_pages[]` or substitutes pending host table state.

## Typed source semantics retained

The effective base is `guest & ~15`. For each width separately, `OpArrayLength` counts whole typed elements, and its byte-size left shift occurs in **uint32 arithmetic before conversion to uint64**. The typed end and NVN end use the source's uint64 arithmetic. An ordinary single uses the smaller NVN/typed end even when its size word is zero. A global-table entry alone substitutes its typed end for a zero size word.

This retains unintuitive cases: a zero-size unaligned ordinary single can claim an alignment prefix; a null table element has zero typed length even if stale UBO words name a guest range; vector views can select a later descriptor when an earlier scalar view would match. The final hit index remains `uint32(address - aligned_base) >> log2(width)`. It floors typed addresses exactly like the original; it does not turn a vector access into an exact-byte read.

The owned record has six disjoint intervals for width 4 and six for width 16. The current inputs happen to produce the same intervals, but they are built independently. The independent direct source-order walker compares **binding, array element and aligned base**, not payload bytes. Every original and transformed endpoint partitions the complete uint64 address domain into constant-selection cells. All 13 cells for each width agree, including gaps, alignment-prefix boundaries and both uint64 tails. On each cell, source membership and the selected root/base are constant; the transformed typed-index expression is then identical for every address. This is a complete root-selection partition for these frozen inputs, not a sampled-query search and not a whole-shader equivalence certificate.

## Emitted candidate

Only the selector prefixes of the two reachable load helpers are replaced. Each new prefix performs a bounded binary search of the width-specific interval partition, then routes to one of eight **constant typed descriptor roots**: five ordinary bindings and three literal table indices. The original typed `StorageBuffer` access chain/load remains a storage-buffer hit. The same underlying aliases and robustness behavior therefore remain in the same storage class. The helper returns the loaded value; it introduces no cross-binding pointer return or pointer phi and no nonuniform descriptor index.

No new variable-pointer or descriptor-indexing feature is requested. Existing capabilities and device requirements remain. A later executor still has to check the additional descriptor capacity and create an updated descriptor-set/pipeline layout; the old physical executor cannot run this candidate unchanged.

The private bundle's identities are:

- Candidate SPIR-V: `d5148342b4517882b25a48e63700cbfb8695454cc05d131da718509ac2563dc0`.
- Record bytes: 592, SHA-256 `49da4bdc0c4542b4fef7b5dcbb631d41fb030d80803cde3bb37185722daf9770`.
- Tool: `3176a7e0130b5d722a88fdb2d5ab9512fff4afecf78e10828eb6747b38ca00ca`.
- Owned manifest: `4c4b5fcaf73318b8edc08406a58e39a2702544f86183e7d894455e7d363206b9`.
- Descriptor-visible UBO0: `778a893b3523a792183d46f8d8e0e0d517e3957b808cdea2be97294b3b008bcf`.

Per-width 32/16/8 metadata is frozen in the private report, but all entries explicitly say not compiled and not executed. That metadata is not permission to use another policy.

## New record ABI, separate from the game ABI

One independently owned readonly storage buffer is added at set 0, binding 12. Its first 16 bytes are four uint32 fields: `count4`, `count16`, `start4`, `start16`. A runtime array of 48-byte rows follows at byte 16:

| Byte offset within row | Field |
|---|---|
| 0, 8, 16 | uint64 low, exclusive high, aligned guest base |
| 24, 28, 32 | uint32 original binding, array element, accessor width |
| 36 | uint32 route ID: 0–4 for bindings 2–6, 5–7 for binding-7 elements 0–2 |
| 40, 44 | Reserved uint32 zeroes |

This is a new prototype ABI, not a reinterpretation of the earlier synthetic experiment's 48-byte layout or the game's original push layout. No game push-constant bytes are added or changed. The original physical MISS instructions still load the original root member, so the transformer does not hardcode a new replacement for `GUEST_PAGES_PUSH_OFFSET`.

The record buffer must be immutable for the owned dispatch and must not alias any existing allocation. A future live builder must regenerate it at the actual descriptor/UBO snapshot boundary and bind matching range/layout/policy identities. That live boundary is not established by this prototype.

## Full instruction-difference and finite-work checks

Binary-word comparison establishes that all five other function bodies are identical, including main, image/work/output operations and all store helpers. Each modified helper's entire original physical MISS suffix remains exactly identical, 166 words each. Existing global capabilities, types, constants, decorations, memory model and execution modes are retained unchanged; only the new declarations/annotations and one appended entry-interface variable are added. Header changes are limited to assembler generator provenance and the larger ID bound; version and schema remain unchanged.

The candidate has two modified load-selector functions; their static SPIR-V bodies each contain 145 instructions versus 228 before. This is not a Mesa ISA statistic, compile-time gain or runtime measurement. Mesa's optimized code generation still decides whether the loop and root routing improve register pressure or execution.

The emitted-instruction auditor independently re-reads each new search: private uint32 lower/upper counters, initial low zero, high from the correct width-specific count, midpoint `low + ((high-low)>>1)`, left update `high=mid`, right update `low=mid+1`. It rejects other writers, differing counter arithmetic, or any remaining CFG cycle after removing the reviewed backedge. Under the valid frozen header, counts are at most 64 and the range strictly decreases on each non-hit iteration, so at most seven iterations are possible. The six-row owned partitions need at most three. Main and its already audited finite call sites remain unchanged.

Twenty regression tests cover typed floor/uint32/uint64 arithmetic, zero-size class differences, first-root identity, nulls, complete-domain gaps, record ABI, readonly selector ownership, bad artifact identities, synthetic scalar/vector assembly validation, counter-progress mutation, and rejection of changed main or MISS instructions. The full suite passed 647 tests with five optional skips at this handoff.

## Reproduce and next review

Requires Python 3.11+ and SPIRV-Tools. The command below never creates a Vulkan instance or device:

```sh
python3 tools/compute_interval_lowering.py --spirv local/shader-target.spv \
  --manifest local/physical-replay/normalized-provenance-v1/manifest.json \
  --output local/compute-interval-lowering-owned-02
python3 -m unittest discover -s tests -p test_compute_interval_lowering.py -v
```

The first frozen bundle is `local/compute-interval-lowering-owned-01`. Original/candidate code, visible selector contents, complete interval records and raw diagnostics stay private. The tracked [sanitized observation](compute-interval-lowering-observation.json) contains identities and structural evidence only.

Next review should verify the root-selection partition and record ABI, then add a separate CPU-only candidate-layout compilation path. Only after that review should a new executor layout/record binding and independently checked output comparison be considered. This prototype supplies no execution admission, native performance result, FPS improvement or general live-renderer equivalence.
