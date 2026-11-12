# Physical-load span certificate for the first compute replay

Date: 2026-11-13. This CPU-only audit closes the full-byte-span question for the exact target SPIR-V and the reviewed page-table/allocation snapshot. It does not execute Vulkan, change renderer sources, or certify historical snapshot atomicity or race freedom.

## Exact module and proof method

The reviewed shader digest is `b9af52c0ba07f64eb8b0ca2905c432d094999fdf669aa95182ff7754b510e97b`. `tools/physical_span_contract.py` refuses another digest and independently decodes the SPIR-V binary. It does not consume a producer's `coverage_complete` or `load_spans_safe` claim.

The tool walks the selected compute entry's call graph and identifies all reachable physical-storage loads. It checks types, address expressions, comparison constants, push-member offsets, control-flow edges, and dominance of the **true guard edge**, including alternate paths and loops. Removing a required edge must make the load unreachable. An alignment operand on `OpLoad` is recorded as a requirement, not treated as a proof of pointer alignment.

## Universal load extents

There are four reachable physical loads, arranged as two helper pairs:

| Helper | Page-entry load | Data load | Dominating guest-offset limit |
|---|---|---|---|
| Scalar read | 8 bytes, `Aligned 8` | 4 bytes, `Aligned 4` | `(guest_address & 4095) <= 4092` |
| Four-word read | 8 bytes, `Aligned 8` | 16 bytes, `Aligned 4` | `(guest_address & 4095) <= 4080` |

The data address is the loaded page base plus the same masked guest offset. Both physical data loads are additionally dominated by a nonzero loaded page base. Consequently neither data load can extend past its 4096-byte page: the largest possible final byte is page offset 4095. This holds for **all helper arguments**, not only the captured main invocation arguments or UBO contents. Dynamic address evaluation or fabricated page-tail bytes are unnecessary for this span proof.

The table root comes from push-constant member offset 48. Root loads are guarded by nonzero root and either of two unsigned address-window comparisons. The entry index is the selected unsigned window delta shifted right by 12, plus the second window's first-entry index when applicable. The tool derives the two disjoint, whole-page windows from the module and checks their contiguous index domains. They require exactly 3,145,728 entries, or 25,165,824 bytes. The largest reachable entry offset is 25,165,816, so its 8-byte read fits exactly at the table end.

## Original owner evidence

For the reviewed later-occurrence image/dispatch capture, the actual table artifact has SHA-256 `25bf632b461c023da452ec36f6ca6fbab62a34afa5bfdadbae828564323d99d8`. Independent scanning found **285,696 nonzero entries**. Every nonzero entry is 16-byte aligned and has complete 4096-byte containment in every captured VkBuffer owner interval containing that entry's start. The smallest remaining owner extent is exactly 4096 bytes. No short final-page owner was observed.

The owner interval digest is `eb3b56c46738a9f0a216be04ff5a5dd08983872ece2f485d6a07ff3dc832995b`. The CLI additionally verifies the root value in actual captured push bytes, table digest, and ordinary BEFORE/AFTER payload-file extents. Payload hashes and original backing provenance remain the replay validator's responsibility; span proof concerns extents, not payload authenticity.

A page-table entry merely landing inside an owner would be insufficient. For example, a 4095-byte owner at a page base would pass start-address coverage but could not hold a 16-byte load beginning at offset 4080. A regression test rejects this case. A complete overlapping owner cannot conceal another short owner and leave the later relocation mapper free to select it: the validator requires every containing original owner to cover the complete page.

## Replay integration requirements

Call `validate_target_access_spans(spirv_bytes, original_table_bytes, original_buffer_owners)` on verified original inputs; owners carry actual original buffer `address` and `size`, not canonical allocation booleans. Do not admit an arbitrary precomputed certificate JSON without reproducing the checks.

The executor must additionally:

- Bind that exact table to the reviewed push root and preserve the root member's position.
- Check the queried new table BDA is at least 8-byte aligned.
- Preserve each original owner/canonical-offset relationship and the complete mapped 4096-byte extent.
- Preserve data alignment by checking rebased page addresses and canonical offsets. The current executor's 16-byte checks are stronger than the data loads' 4-byte requirement and table loads' 8-byte requirement.
- Reject original or rebased address arithmetic overflow; never invent owner-tail bytes.

The helper bounds prove byte spans even for an unaligned guest offset. They do **not** establish that the original application fulfilled its `Aligned 4` promise. Alignment-preserving page rebasing preserves valid original accesses; original alignment validity is a separate property. Likewise this certificate does not prove alias normalization, historical CPU quiescence, complete sampled state, or output equivalence.

## Finite control flow

The same binary audit independently checks finite control-flow work. The selected entry reaches three functions, without recursion. Main has 829 basic blocks and an acyclic CFG: there are no main backedges. Its 72 static helper calls can each be reached at most once per invocation.

The two reachable helpers each have exactly one descriptor-search loop. Each counter is private 32-bit function storage, initialized once to zero, compared unsigned against constant three, and incremented by exactly one at its unique continuation/backedge. No other writes or escaping counter pointers are accepted. Removing those two reviewed backedges leaves all reachable function CFGs acyclic; no residual cycle is unresolved. Early returns only reduce work.

For the captured direct grid 2 × 1 × 1 and local size 64 × 1 × 1, there are 128 invocations with finite shader control flow. This is not a time estimate, a GPU scheduling guarantee, or evidence inferred from a ten-second fence timeout.

## Reproduce

```sh
python3 tools/physical_span_contract.py --spirv local/shader-target.spv \
  --capture local/runtime-image-dispatch-01/capture \
  > local/runtime-image-dispatch-01/load-span-certificate.json
python3 -m unittest discover -s tests -p test_physical_span_contract.py -v
```

Original shader bytes, table bytes, allocation contents and full private certificate stay in ignored local storage. The tracked tool and tests use independently authored logic and synthetic adversarial intervals/graphs only.
