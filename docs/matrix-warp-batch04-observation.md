# Revision 04 matrix batch observation

All 16 exact safe cases matched their software references. Three of 48 diagnostic cases differed: 128 negative-zero signs changed, and two normal half values moved to adjacent encodings. This establishes the tested completion and finite-layout result; it does not establish general arithmetic equivalence or justify a production native-matrix bypass.

The [sanitized observation](../research/matrix-warp-batch04-observation.json) pins the unchanged plan, helper, module, seven source dependencies, exact E5/native-safe policy, three runtime reports and three complete guarded readbacks. The frozen [protocol](matrix-warp-batch.md) and [preparation receipt](../research/matrix-warp-batch04-preparation.json) remain unchanged. Revision 03 remains preserved and was never dispatched.

One authorized `[64,1,1]` dispatch ran with local size `[32,1,1]` and actual compute subgroup width 32. All 4,096 unique completion markers passed. All three buffers' outer guards, all per-group input/output/marker guards and every readonly input byte passed before numerical classification. The complete captured frames were 74,240 input bytes and 25,088 bytes each for output and markers. Independent Python validation and both native CPU recheck modes verified the actual readbacks after the GPU process terminated.

Every one of the 4,096 packed words and 8,192 half elements was compared. Safe cases accounted for 1,024 exact packed words. Diagnostics had 66 packed-word differences, covering 130 half elements, in these groups:

| Group and case | Location | Software reference | Native result |
| --- | --- | --- | --- |
| 17: zero-padding signed zero | All 128 half elements | Negative zero, `0x8000` | Positive zero, `0x0000` |
| 36: finite exponent/mantissa pattern | Row 1, column 4; lane 6, D0 low half | 4812, `0x6cb3` | 4808, `0x6cb2` |
| 58: low-exponent finite bits | Row 13, column 7; lane 23, D1 high half | 0.0011930465698242188, `0x14e3` | 0.0011920928955078125, `0x14e2` |

The two normal differences each have finite encoding distance one. That description supplies no accepted tolerance. The whole-bridge outputs do not isolate their causes or establish a driver bug. All 252 reference subnormals and all 1,989 reference infinities matched exactly at their original locations and bits. Neither reference nor native output contained NaNs in this corpus; NaN payload/signaling behavior remains uncovered.

All 64 case results are retained as hashes, classes and mismatch counts. There were 61 bit-exact cases overall: all 16 safe cases and 45 diagnostics. No complete private payload is published. Runtime processes reached terminal success, CPU rechecks passed, the exclusive GPU lease was released, and frozen artifact/driver/predecessor hashes remained unchanged. No retry, adaptive input, source change or renderer-policy change occurred.

No machine IR was captured, no original neural network was restored, and no scene-quality, hair-antialiasing or FPS improvement is established. Process completion times are excluded from GPU performance evidence.
