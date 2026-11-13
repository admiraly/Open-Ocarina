#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""CPU-only explicit 6864 mesh output profile; no runtime admission changes.

Reuse the frozen, independently authored D8 auditor/generator with bounded,
counted public-source specializations. Never specialize private shader source.
The original module stays loaded unchanged, including its globals and policy.
"""
import argparse
import hashlib
import inspect
import json
from pathlib import Path
import struct
import subprocess
import sys
import tempfile

try:
    from . import mesh_output_instrument as frozen
except ImportError:
    import mesh_output_instrument as frozen

FROZEN_SHA256 = '69e97d192ca4607a2c7f50c4d92460c0467b5f0f076b9e3775dd04ac4fe960e7'
RESOURCE_AUDITOR_SHA256 = '91bb902ee11d37ed24cda2c74a086e6c53fb15405311adc1fdd6a38f905048f7'
ORIGINAL = '999c8511db93d486543421dde7350e3d0034faa834ef3edaf3553dcdc9b55d93'
# Filled only from CPU assembly of the frozen pure transform, never a runtime shader.
CONDITIONAL = 'a41c6df753f30f8c0646ffa45227304e994b3fbaccdcad64927cd57f5e2327e7'
PROFILE = '6864-position10-96/v1'
LOCATIONS = (0, 1, 2, 4, 5, 6, 7, 8, 11, 12)
RECORD_WORDS = 9216
RECORD_BYTES = RECORD_WORDS * 4
VALUE_SENTINEL, INNER_GUARD = frozen.VALUE_SENTINEL, frozen.INNER_GUARD
LAYOUT = dict(stride_bytes=RECORD_BYTES, stride_words=RECORD_WORDS,
              prefix_guard_offset=0, prefix_guard_bytes=64,
              lane_counts_offset=64, lane_count=32, lane_count_stride=8,
              position_offset=320, generic_offset=1856,
              generic_locations=list(LOCATIONS), generic_array_stride=1536,
              triangle_offset=17216, triangle_array_stride=16,
              writtenness_offset=18752, writtenness_words=4512,
              suffix_guard_offset=36800, suffix_guard_bytes=64,
              max_vertices=96, max_primitives=96,
              value_sentinel_word=VALUE_SENTINEL, inner_guard_word=INNER_GUARD,
              writtenness_initial_word=0, writtenness_written_word=1)


def _specialized(function, edits, namespace):
    """Counted textual edits of a hash-pinned public Python function only."""
    source = inspect.getsource(function)
    for old, new, expected in edits:
        if source.count(old) != expected:
            raise ValueError('Frozen specialization context changed')
        source = source.replace(old, new)
    exec(compile(source, str(Path(__file__)) + ':' + function.__name__, 'exec'), namespace)
    return namespace[function.__name__]


def _engine():
    if hashlib.sha256(Path(frozen.__file__).read_bytes()).hexdigest() != FROZEN_SHA256:
        raise ValueError('Frozen D8 infrastructure digest changed')
    resource_source = Path(inspect.getsourcefile(frozen.analyze_assembly))
    if hashlib.sha256(resource_source.read_bytes()).hexdigest() != RESOURCE_AUDITOR_SHA256:
        raise ValueError('Resource auditor digest changed')
    namespace = dict(vars(frozen))
    namespace.update(LOCATIONS=LOCATIONS, LAYOUT=LAYOUT,
                     RECORD_WORDS=RECORD_WORDS, RECORD_BYTES=RECORD_BYTES)
    audit = _specialized(frozen.audit, [
        ('128', '96', 4), ('range(9)', 'range(10)', 4),
        ('for binding in range(10)', 'for binding in range(9)', 1),
        ('binding < 5', 'binding < 4', 1),
        ('(0, 32, 64, 96)', '(0, 32, 64)', 2),
        ('("position", 0): 20, ("triangles", 0): 4',
         '("position", 0): 15, ("triangles", 0): 3', 1),
        ('("generic", index): 16', '("generic", index): 12', 1),
    ], namespace)
    instrument = _specialized(frozen.instrument_assembly, [
        ('5200 + component', '4304 + component', 1),
        ('5712 + 5120 + component', '4688 + 4224 + component', 1),
        ('592 + generic_index * 512', '464 + generic_index * 384', 1),
        ('5712 + ((generic_index + 1) * 512', '4688 + ((generic_index + 1) * 384', 1),
    ], namespace)
    fixture = _specialized(frozen.synthetic_fixture_assembly, [
        ('range(9)', 'range(10)', 2),
        ('["%ssbo0", "%ssbo1", "%ssbo2", "%ssbotable"]',
         '["%ssbo0", "%ssbo1", "%ssbo2", "%ssbo3", "%ssbotable"]', 2),
        ('range(5)', 'range(4)', 3),
        ('%ssboptr StorageBuffer" for index in range(3)',
         '%ssboptr StorageBuffer" for index in range(4)', 1),
        ('128', '96', 6), ('129', '97', 2),
        ('constants.update(range(40))', 'constants.update(range(44))', 1),
        ('(0, 32, 64, 96)', '(0, 32, 64)', 1),
        ('for field in range(10)', 'for field in range(11)', 1),
        ('for block in range(4)', 'for block in range(3)', 1),
        ('OpBitwiseAnd %u32 %tric{block}_{component} %k127',
         'OpUMod %u32 %tric{block}_{component} %k96', 1),
    ], namespace)
    return audit, instrument, fixture


def audit(assembly, source_kind):
    if source_kind not in {'original', 'conditional', 'synthetic-fixture'}:
        raise ValueError('Unknown source kind')
    return _engine()[0](assembly, source_kind)


def instrument_assembly(assembly, source_kind):
    # Same state and fail-closed audit as audit(); no production digest policy.
    if source_kind not in {'original', 'conditional', 'synthetic-fixture'}:
        raise ValueError('Unknown source kind')
    transformed, report = _engine()[1](assembly, source_kind)
    report.update(output_profile=PROFILE, frozen_infrastructure_sha256=FROZEN_SHA256,
                  resource_auditor_sha256=RESOURCE_AUDITOR_SHA256)
    return transformed, report


def synthetic_fixture_assembly():
    return _engine()[2]()


def initial_capture(group_count):
    if type(group_count) is not int or not 1 <= group_count <= 32:
        raise ValueError('Capture must contain one through 32 groups')
    words = [VALUE_SENTINEL] * (RECORD_WORDS * group_count)
    for group in range(group_count):
        base = group * RECORD_WORDS
        words[base:base + 16] = [INNER_GUARD] * 16
        words[base + 9200:base + 9216] = [INNER_GUARD] * 16
        words[base + 4688:base + 9200] = [0] * 4512
    return struct.pack(f'<{len(words)}I', *words)


def fixture_expected_capture(origin, groups):
    """Independent CPU oracle; record bodies only, excludes host outer guards."""
    if (not isinstance(origin, (tuple, list)) or not isinstance(groups, (tuple, list))
            or len(origin) != 3 or len(groups) != 3
            or any(type(v) is not int or not 0 <= v <= 0xffffffff for v in origin)
            or any(type(v) is not int or not 1 <= v <= 0xffffffff for v in groups)
            or any(o + n > 1 << 32 for o, n in zip(origin, groups))):
        raise ValueError('Expected bounded origin and positive XYZ window')
    data = bytearray(initial_capture(groups[0] * groups[1] * groups[2]))
    def word(base, offset, value):
        struct.pack_into('<I', data, 4 * (base + offset), value)
    for z in range(groups[2]):
        for y in range(groups[1]):
            for x in range(groups[0]):
                base = (x + groups[0] * (y + groups[1] * z)) * RECORD_WORDS
                tag = (origin[0] + x + 257 * (origin[1] + y) + 65537 * (origin[2] + z)) & 0xffffffff
                primitives = tag % 97
                for lane in range(32):
                    word(base, 16 + lane * 2, 96)
                    word(base, 17 + lane * 2, primitives)
                for vertex in range(96):
                    for field in range(11):
                        for component in range(4):
                            value = (tag + vertex * 8 + field * 4 + component
                                     + (1000 if field == 0 and component == 2 else 0)) & 0xffffffff
                            bits = struct.unpack('<I', struct.pack('<f', float(value)))[0]
                            offset = (80 if field == 0 else 464 + (field - 1) * 384) + vertex * 4 + component
                            word(base, offset, bits)
                            word(base, 4688 + field * 384 + vertex * 4 + component, 1)
                for primitive in range(primitives):
                    for component in range(3):
                        word(base, 4304 + primitive * 4 + component, (primitive * 3 + component) % 96)
                        word(base, 8912 + primitive * 3 + component, 1)
    return bytes(data)


def _prepare_bytes(data, output, source_kind):
    output = frozen.private_output(Path(output).resolve())
    if output.exists():
        raise ValueError('Choose a new private output directory')
    output.mkdir(parents=True)
    source, result = output / 'source.spv', output / 'instrumented.spv'
    source.write_bytes(data)
    validation = ['--target-env', 'vulkan1.3', '--uniform-buffer-standard-layout']
    frozen._tool('spirv-val', [*validation, str(source)])
    assembly = frozen._tool('spirv-dis', ['--raw-id', str(source)]).stdout
    instrumented, metadata = instrument_assembly(assembly, source_kind)
    assembly_path = output / 'instrumented.spvasm'
    assembly_path.write_text(instrumented)
    frozen._tool('spirv-as', ['--target-env', 'spv1.6', str(assembly_path), '-o', str(result)])
    frozen._tool('spirv-val', [*validation, str(result)])
    report = dict(schema='open-ocarina.mesh-output-profile96/v1',
                  source_module_sha256=hashlib.sha256(data).hexdigest(),
                  instrumented_sha256=hashlib.sha256(result.read_bytes()).hexdigest(),
                  instrumenter_source_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
                  source_filename=source.name, instrumented_filename=result.name,
                  structural_validation_passed=True, **metadata)
    (output / 'report.json').write_text(json.dumps(report, indent=2) + '\n')
    return report


def prepare(source, output):
    source = Path(source)
    if source.is_symlink() or not source.is_file() or not 20 <= source.stat().st_size <= 2 * 1024 * 1024:
        raise ValueError('Expected bounded regular module')
    with source.open('rb') as stream:
        data = stream.read(2 * 1024 * 1024 + 1)
    if not 20 <= len(data) <= 2 * 1024 * 1024 or len(data) % 4 or data[:4] != b'\x03\x02\x23\x07':
        raise ValueError('Invalid SPIR-V snapshot')
    kind = {ORIGINAL: 'original', CONDITIONAL: 'conditional'}.get(hashlib.sha256(data).hexdigest())
    if kind is None:
        raise ValueError('Module digest is outside the explicit 6864 CPU preparation profile')
    return _prepare_bytes(data, output, kind)


def prepare_fixture(output):
    output = frozen.private_output(Path(output).resolve())
    if output.exists():
        raise ValueError('Choose a new private output directory')
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='mesh96-fixture-', dir=output.parent) as tmp:
        assembly, binary = Path(tmp) / 'fixture.spvasm', Path(tmp) / 'fixture.spv'
        assembly.write_text(synthetic_fixture_assembly())
        frozen._tool('spirv-as', ['--target-env', 'spv1.6', str(assembly), '-o', str(binary)])
        return _prepare_bytes(binary.read_bytes(), output, 'synthetic-fixture')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('source', nargs='?', type=Path)
    parser.add_argument('--output', required=True, type=Path)
    parser.add_argument('--synthetic-fixture', action='store_true')
    args = parser.parse_args()
    try:
        if args.synthetic_fixture and args.source is None:
            report = prepare_fixture(args.output)
        elif args.source and not args.synthetic_fixture:
            report = prepare(args.source, args.output)
        else:
            raise ValueError('Supply one reviewed source or --synthetic-fixture')
        print(json.dumps(report, indent=2))
    except (OSError, ValueError, KeyError, StopIteration, subprocess.SubprocessError) as exc:
        print(str(exc), file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
