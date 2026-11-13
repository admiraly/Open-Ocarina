#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""CPU-only HMMA checkpoint/layout oracle and synthetic KHR shader preparation.

No Vulkan loader, device, pipeline or dispatch is used. The oracle models the
inspected software lowering, not an independently certified console operation.
"""
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import struct
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
QNaN32 = 0x7FC00000


def _round_shift(value, shift):
    if shift <= 0:
        return value << -shift
    quotient, remainder = divmod(value, 1 << shift)
    midpoint = 1 << (shift - 1)
    return quotient + (remainder > midpoint or (remainder == midpoint and quotient & 1))


def _encode(coefficient, power, sign, exponent_bits, fraction_bits):
    """Round exact nonnegative coefficient*2**power to binary IEEE RNE."""
    sign_word = sign << (exponent_bits + fraction_bits)
    if coefficient == 0:
        return sign_word
    bias = (1 << (exponent_bits - 1)) - 1
    minimum, maximum = 1 - bias, bias
    exponent = coefficient.bit_length() - 1 + power
    if exponent < minimum:
        quantum = minimum - fraction_bits
        significand = _round_shift(coefficient, quantum - power)
        return sign_word | significand
    shift = coefficient.bit_length() - 1 - fraction_bits
    significand = _round_shift(coefficient, shift)
    if significand == 1 << (fraction_bits + 1):
        significand >>= 1
        exponent += 1
    if exponent > maximum:
        return sign_word | (((1 << exponent_bits) - 1) << fraction_bits)
    return sign_word | ((exponent + bias) << fraction_bits) | (significand - (1 << fraction_bits))


def _finite(word, exponent_bits, fraction_bits):
    exponent = (word >> fraction_bits) & ((1 << exponent_bits) - 1)
    fraction = word & ((1 << fraction_bits) - 1)
    bias = (1 << (exponent_bits - 1)) - 1
    coefficient = fraction if exponent == 0 else (1 << fraction_bits) | fraction
    power = (1 - bias if exponent == 0 else exponent - bias) - fraction_bits
    return coefficient, power


def _classify(word, exponent_bits, fraction_bits):
    maximum = (1 << exponent_bits) - 1
    exponent = (word >> fraction_bits) & maximum
    fraction = word & ((1 << fraction_bits) - 1)
    return "nan" if exponent == maximum and fraction else "inf" if exponent == maximum else "zero" if not exponent and not fraction else "finite"


def half_to_float32(word):
    if not isinstance(word, int) or not 0 <= word <= 0xFFFF:
        raise ValueError("Expected a 16-bit half encoding")
    kind, sign = _classify(word, 5, 10), word >> 15
    if kind == "nan":
        return QNaN32 | sign << 31
    if kind == "inf":
        return 0x7F800000 | sign << 31
    coefficient, power = _finite(word, 5, 10)
    return _encode(coefficient, power, sign, 8, 23)


def float32_to_half(word):
    if not isinstance(word, int) or not 0 <= word <= 0xFFFFFFFF:
        raise ValueError("Expected a 32-bit float encoding")
    kind, sign = _classify(word, 8, 23), word >> 31
    if kind == "nan":
        return 0x7E00 | sign << 15
    if kind == "inf":
        return 0x7C00 | sign << 15
    coefficient, power = _finite(word, 8, 23)
    return _encode(coefficient, power, sign, 5, 10)


def fma32(a, b, c):
    """One exact finite fused operation rounded once to f32; canonical NaNs."""
    if any(not isinstance(word, int) or not 0 <= word <= 0xFFFFFFFF for word in (a, b, c)):
        raise ValueError("FMA inputs must be 32-bit encodings")
    kinds = [_classify(word, 8, 23) for word in (a, b, c)]
    if "nan" in kinds or (kinds[0] == "inf" and kinds[1] == "zero") or (kinds[1] == "inf" and kinds[0] == "zero"):
        return QNaN32
    product_sign = (a ^ b) >> 31
    if "inf" in kinds[:2]:
        if kinds[2] == "inf" and c >> 31 != product_sign:
            return QNaN32
        return product_sign << 31 | 0x7F800000
    if kinds[2] == "inf":
        return c
    ac, ap = _finite(a, 8, 23)
    bc, bp = _finite(b, 8, 23)
    cc, cp = _finite(c, 8, 23)
    pp = ap + bp
    power = min(pp, cp)
    product = ac * bc * (-1 if product_sign else 1)
    accumulator = cc * (-1 if c >> 31 else 1)
    total = (product << (pp - power)) + (accumulator << (cp - power))
    if total == 0:
        # Exact cancellation is +0 at RNE; -0 + -0 remains -0.
        return 0x80000000 if product == 0 and accumulator == 0 and product_sign and c >> 31 else 0
    return _encode(abs(total), power, int(total < 0), 8, 23)


def half(value):
    """Fixture convenience only; arithmetic above never uses host floats."""
    try:
        return int.from_bytes(struct.pack(">e", value), "big")
    except OverflowError:
        return 0xFC00 if value < 0 else 0x7C00


def dot16(a, b, c=0, *, checkpoint=8, zero_pad=False, order=None):
    if len(a) != 16 or len(b) != 16 or checkpoint not in (8, 16):
        raise ValueError("Expected K16 inputs and K8 or final-only checkpoint")
    permutation = list(range(16)) if order is None else list(order)
    if sorted(permutation) != list(range(16)):
        raise ValueError("Order must be a permutation of all 16 terms")
    accumulator = half_to_float32(c)
    for start in range(0, 16, checkpoint):
        for index in permutation[start:start + checkpoint]:
            accumulator = fma32(half_to_float32(a[index]), half_to_float32(b[index]), accumulator)
        if zero_pad:
            for _ in range(16 - checkpoint):
                accumulator = fma32(0, 0, accumulator)
        accumulator = half_to_float32(float32_to_half(accumulator))
    return float32_to_half(accumulator)


def _matrix(value, rows, columns):
    if len(value) != rows or any(len(row) != columns for row in value):
        raise ValueError(f"Expected a {rows}x{columns} half-word matrix")
    if any(not isinstance(word, int) or not 0 <= word <= 0xFFFF for row in value for word in row):
        raise ValueError("Matrix elements must be 16-bit encodings")


def logical_hmma(a, b, c):
    _matrix(a, 16, 16)
    _matrix(b, 16, 8)
    _matrix(c, 16, 8)
    return [[dot16(a[row], [b[k][column] for k in range(16)], c[row][column]) for column in range(8)] for row in range(16)]


def pack_fragments(a, b, c):
    """Original warp32 logical fragments; not Intel cooperative element order."""
    _matrix(a, 16, 16)
    _matrix(b, 16, 8)
    _matrix(c, 16, 8)
    packed = {"A": [], "B": [], "C": []}
    pair = lambda first, second: first | second << 16
    for lane in range(32):
        row, quarter = divmod(lane, 4)
        k, column = 2 * quarter, 2 * quarter
        packed["A"].append([pair(a[row][k], a[row][k + 1]), pair(a[row + 8][k], a[row + 8][k + 1]),
                            pair(a[row][k + 8], a[row][k + 9]), pair(a[row + 8][k + 8], a[row + 8][k + 9])])
        packed["B"].append([pair(b[k][row], b[k + 1][row]), pair(b[k + 8][row], b[k + 9][row])])
        packed["C"].append([pair(c[row][column], c[row][column + 1]), pair(c[row + 8][column], c[row + 8][column + 1])])
    return packed


def unpack_fragments(packed):
    if set(packed) != {"A", "B", "C"} or any(len(packed[name]) != 32 for name in packed):
        raise ValueError("Expected A/B/C warp32 fragments")
    if any(len(words) != (4 if name == "A" else 2) or any(not isinstance(w, int) or not 0 <= w <= 0xFFFFFFFF for w in words)
           for name, lanes in packed.items() for words in lanes):
        raise ValueError("Invalid fragment word counts or encodings")
    a, b, c = [[0] * 16 for _ in range(16)], [[0] * 8 for _ in range(16)], [[0] * 8 for _ in range(16)]
    for lane in range(32):
        row, quarter = divmod(lane, 4)
        k = 2 * quarter
        for register, (arow, ak) in enumerate(((row, k), (row + 8, k), (row, k + 8), (row + 8, k + 8))):
            a[arow][ak:ak + 2] = [packed["A"][lane][register] & 0xFFFF, packed["A"][lane][register] >> 16]
        for register, bk in enumerate((k, k + 8)):
            b[bk][row], b[bk + 1][row] = packed["B"][lane][register] & 0xFFFF, packed["B"][lane][register] >> 16
        for register, crow in enumerate((row, row + 8)):
            c[crow][k:k + 2] = [packed["C"][lane][register] & 0xFFFF, packed["C"][lane][register] >> 16]
    return a, b, c


def tile_inputs(a, b, c):
    """Four M8/K8 padded A tiles, two B tiles, two initial C tiles, row-major."""
    _matrix(a, 16, 16)
    _matrix(b, 16, 8)
    _matrix(c, 16, 8)
    aa, bb, cc = [], [], []
    for m in range(2):
        for group in range(2):
            aa.extend(a[m * 8 + row][group * 8:group * 8 + 8] + [0] * 8 for row in range(8))
        cc.extend(list(row) for row in c[m * 8:m * 8 + 8])
    for group in range(2):
        bb.extend([list(row) for row in b[group * 8:group * 8 + 8]] + [[0] * 8 for _ in range(8)])
    return {"A_rows": aa, "B_rows": bb, "C_rows": cc,
            "shape": {"M": 8, "N": 8, "K": 16},
            "tiles_per_logical_matrix": 4,
            "limitation": "Padding and geometry only; native summation/rounding/fragment layout remain unverified"}


def fixture_report():
    zero, one = half(0.0), half(1.0)
    a = [one, half(2 ** -11)] + [zero] * 14
    a[8] = half(-1.0)
    b = [one] * 16
    negative_zero = [0x8000] * 16
    cancellation = [half(65504.0), one, half(-65504.0)] + [zero] * 13
    weights = [half(65504.0), one, half(65504.0)] + [zero] * 13
    return {"schema": 1, "execution": "CPU integer arithmetic only", "modeled_schedule": "ordered f32 fused operations; half-RNE after K8 and K16",
        "counterexamples": {
            "final_half_cast_is_not_checkpoint_equivalent": {
                "checkpoint_half": dot16(a, b), "final_only_half": dot16(a, b, checkpoint=16)},
            "positive_zero_padding_changes_negative_zero": {
                "checkpoint_half": dot16(negative_zero, b, 0x8000),
                "padded_sequential_half": dot16(negative_zero, b, 0x8000, zero_pad=True)},
            "native_reduction_order_cannot_be_assumed": {
                "ordered_half": dot16(cancellation, weights),
                "reordered_half": dot16(cancellation, weights, order=[0, 2, 1] + list(range(3, 16)))}},
        "limitations": ["This is the inspected software-lowering oracle, not certified original-console arithmetic.",
                           "Finite arithmetic is exact RNE; NaNs are canonicalized for classification and do not certify payload/signaling behavior.",
                           "No KHR matrix's internal operation order, precision, subgroup acceptance or GPU results are inferred."]}


GLSL = """#version 460
#extension GL_KHR_cooperative_matrix : require
#extension GL_KHR_memory_scope_semantics : require
#extension GL_EXT_shader_explicit_arithmetic_types_float16 : require
#extension GL_EXT_shader_16bit_storage : require
#extension GL_KHR_shader_subgroup_basic : require
layout(local_size_x=32, local_size_y=1, local_size_z=1) in;
layout(set=0,binding=0,std430) readonly buffer AInput { float16_t elements[]; } a;
layout(set=0,binding=1,std430) readonly buffer BInput { float16_t elements[]; } b;
layout(set=0,binding=2,std430) readonly buffer CInput { float elements[]; } c;
layout(set=0,binding=3,std430) writeonly buffer DOutput { float elements[]; } d;
void main() {
    coopmat<float16_t, gl_ScopeSubgroup, 8, 16, gl_MatrixUseA> ma;
    coopmat<float16_t, gl_ScopeSubgroup, 16, 8, gl_MatrixUseB> mb;
    coopmat<float, gl_ScopeSubgroup, 8, 8, gl_MatrixUseAccumulator> mc;
    uint group = gl_WorkGroupID.x * gl_NumSubgroups + gl_SubgroupID;
    coopMatLoad(ma, a.elements, group * 128u, 16u, gl_CooperativeMatrixLayoutRowMajor);
    coopMatLoad(mb, b.elements, group * 128u, 8u, gl_CooperativeMatrixLayoutRowMajor);
    coopMatLoad(mc, c.elements, group * 64u, 8u, gl_CooperativeMatrixLayoutRowMajor);
    mc = coopMatMulAdd(ma, mb, mc);
    coopMatStore(mc, d.elements, group * 64u, 8u, gl_CooperativeMatrixLayoutRowMajor);
}
"""

# Independent finite-RNE checkpoint, expressed as integer conversion to avoid an
# optimizer eliminating a float->half->float cast pair. NaN payload is outside
# this fixture's certified contract. The native matrix's reduction remains opaque.
CHECKPOINT_GLSL = GLSL[:GLSL.index("void main() ")] + """
uint rne_shift(uint value, uint shift) {
    if (shift == 0u) return value;
    if (shift > 31u) return 0u;
    uint quotient = value >> shift;
    uint remainder = value & ((1u << shift) - 1u);
    uint midpoint = 1u << (shift - 1u);
    return quotient + uint(remainder > midpoint || (remainder == midpoint && (quotient & 1u) != 0u));
}
float half_checkpoint(float value) {
    uint word = floatBitsToUint(value);
    uint sign = (word >> 16u) & 0x8000u;
    uint exponent = (word >> 23u) & 255u;
    uint fraction = word & 0x7fffffu;
    if (exponent == 255u) return uintBitsToFloat(fraction == 0u ? word : 0x7fc00000u);
    uint result = sign;
    int unbiased = int(exponent) - 127;
    if (exponent != 0u) {
        if (unbiased > 15) result |= 0x7c00u;
        else if (unbiased >= -14) {
            uint significand = rne_shift(0x800000u | fraction, 13u);
            if (significand == 2048u) { significand = 1024u; unbiased++; }
            result |= unbiased > 15 ? 0x7c00u : (uint(unbiased + 15) << 10u) | (significand - 1024u);
        } else result |= rne_shift(0x800000u | fraction, uint(-14 - unbiased + 13));
    }
    return unpackHalf2x16(result).x;
}
void main() {
    coopmat<float16_t, gl_ScopeSubgroup, 8, 16, gl_MatrixUseA> ma;
    coopmat<float16_t, gl_ScopeSubgroup, 16, 8, gl_MatrixUseB> mb;
    coopmat<float, gl_ScopeSubgroup, 8, 8, gl_MatrixUseAccumulator> mc;
    uint group = gl_WorkGroupID.x * gl_NumSubgroups + gl_SubgroupID;
    for (uint m = 0u; m < 2u; m++) {
        coopMatLoad(mc, c.elements, group * 128u + m * 64u, 8u, gl_CooperativeMatrixLayoutRowMajor);
        for (uint k = 0u; k < 2u; k++) {
            coopMatLoad(ma, a.elements, group * 512u + (m * 2u + k) * 128u, 16u, gl_CooperativeMatrixLayoutRowMajor);
            coopMatLoad(mb, b.elements, group * 256u + k * 128u, 8u, gl_CooperativeMatrixLayoutRowMajor);
            mc = coopMatMulAdd(ma, mb, mc);
            for (int element = 0; element < mc.length(); element++) mc[element] = half_checkpoint(mc[element]);
        }
        coopMatStore(mc, d.elements, group * 128u + m * 64u, 8u, gl_CooperativeMatrixLayoutRowMajor);
    }
}
"""


def private_output(output):
    output = Path(output).resolve()
    if output.is_relative_to(ROOT) and (output == ROOT or output.relative_to(ROOT).parts[0] not in {"local", "work", "artifacts"}):
        raise ValueError("Outputs inside the repository must be under local/, work/ or artifacts/")
    if output.exists():
        raise ValueError("Choose a new output directory")
    return output


def prepare(output, prepare_shaders=False, compiler="glslangValidator", validator="spirv-val"):
    output = private_output(output)
    report = fixture_report()
    report["tool_sha256"] = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    report["shader_prepared"] = False
    report["gpu_pipeline_compiled"] = False
    report["gpu_executed"] = False
    if prepare_shaders:
        compiler_path, validator_path = shutil.which(compiler), shutil.which(validator)
        if not compiler_path or not validator_path:
            raise ValueError("GLSL preparation requires glslangValidator and spirv-val")
        compiler_version = subprocess.run([compiler_path, "--version"], check=True, capture_output=True, text=True, timeout=10).stdout
        validator_version = subprocess.run([validator_path, "--version"], check=True, capture_output=True, text=True, timeout=10).stdout
    output.mkdir(parents=True)
    if prepare_shaders:
        shaders = {}
        for name, text in (("khr_m8n8k16", GLSL), ("khr_m16_checkpoints_padded", CHECKPOINT_GLSL)):
            source, module = output / (name + ".comp"), output / (name + ".spv")
            source.write_text(text)
            result = subprocess.run([compiler_path, "-V", "--target-env", "vulkan1.3", "-Os", "-o", str(module), str(source)],
                                    capture_output=True, text=True, timeout=30)
            (output / (name + "-compiler.log")).write_text(result.stdout + result.stderr)
            if result.returncode:
                raise ValueError("GLSL preparation failed; inspect the private compiler log")
            result = subprocess.run([validator_path, "--target-env", "vulkan1.3", str(module)], capture_output=True, text=True, timeout=15)
            (output / (name + "-validator.log")).write_text(result.stdout + result.stderr)
            if result.returncode:
                raise ValueError("SPIR-V validation failed; inspect the private validator log")
            shaders[name] = {"source_sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
                             "spirv_sha256": hashlib.sha256(module.read_bytes()).hexdigest(),
                             "spirv_bytes": module.stat().st_size}
        report["shader_prepared"] = True
        report["shader"] = {"fixtures": shaders, "compiler_version": compiler_version.strip(),
                            "validator_version": validator_version.strip(), "flags": ["-V", "--target-env", "vulkan1.3", "-Os"],
                            "scope": "independently authored advertised-shape and padded-checkpoint fixtures; no native reduction/order/layout equivalence implied"}
    (output / "report.json").write_text(json.dumps(report, indent=2) + "\n")
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out-dir", type=Path, required=True)
    parser.add_argument("--prepare-shaders", action="store_true")
    parser.add_argument("--compiler", default="glslangValidator")
    parser.add_argument("--validator", default="spirv-val")
    args = parser.parse_args()
    try:
        print(json.dumps(prepare(args.out_dir, args.prepare_shaders, args.compiler, args.validator), indent=2))
    except (ValueError, OSError, subprocess.SubprocessError) as exc:
        print(str(exc), file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
