#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Prepare CPU goldens and validated synthetic typed-pointer runtime fixtures.

No Vulkan device, pipeline or dispatch is created. A separately reviewed native
executor is still required. The expected-memory oracle does not run the interval
selector: it applies the fixture's explicit disjoint writes and known edge reads.
"""
import argparse
import hashlib
import importlib.util
import json
from pathlib import Path
import struct
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
FROZEN_GENERATOR_SHA = '0e4fc9d15438f078bf206e40d8838052a91a9e16af696f44cbb77a7b8a94838e'
LANES, ROWS, QUERIES, GUARD, DATA_BYTES = 64, 37, 11, 64, 8192
W0 = 0x1000000000
S, T, U, F = (W0 + offset for offset in (0x10000, 0x20000, 0x30000, 0x40000))
SEEDS = {'shared': 0x11000000, 'short': 0x22000000, 'zero_prefix': 0x33000000,
         'mapped_elsewhere': 0x44000000, 'physical': 0x55000000, 'ordinary_miss': 0x66000000}
CONTROLLED_OPTIONS = ('HS_CS32_MAX_INSTS', 'HS_SPILL_BATCH', 'HS_PUSH_SHADOW', 'shader_spilling_rate',
                     'INTEL_DEBUG', 'HS_SPILL_PCT', 'HS_RA_DEBUG', 'HS_FORCE_SIMD8', 'HS_LARGE_GRF',
                     'HS_SUBGROUP8', 'HS_LARGE_GRF_BIT', 'RX_VK_WARP64', 'RX_VK_NOOPT')


def canonical(data):
    return (json.dumps(data, sort_keys=True, indent=2) + '\n').encode()


def sha(data):
    return hashlib.sha256(data).hexdigest()


def frozen_generator():
    path = ROOT / 'tools/descriptor_array_pointer_experiment.py'
    if sha(path.read_bytes()) != FROZEN_GENERATOR_SHA:
        raise ValueError('Frozen pointer generator changed')
    spec = importlib.util.spec_from_file_location('runtime_fixture_pointer_dependency', path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def payload(name, size):
    seed = SEEDS[name]
    return b''.join(struct.pack('<I', seed ^ ((index * 0x01010101) & 0xFFFFFFFF)) for index in range((size + 3)//4))[:size]


def guarded(data):
    return bytes([0xBD])*GUARD + data + bytes([0xDB])*GUARD


def read_words(data, offset, width):
    return list(struct.unpack(f'<{width//4}I', data[offset:offset+width]))


def values(kind, width, lane, follow):
    base = {'storage': 0xA0000000, 'physical': 0xB0000000}[kind]
    return [base | ((width//4) << 24) | (component << 20) | (int(follow) << 16) | lane for component in range(width//4)]


def query_rows():
    rows = []
    for lane in range(LANES):
        offset = 256 + lane*64
        primary, alias = (S, T) if lane % 2 == 0 else (T, S)
        rows.append([primary+offset, primary+offset+8, primary+offset+16, alias+offset, F+offset,
                     S+4, U+4, S+4092, F+4092, T+128, 128])
    return rows


def memory_contract():
    """Four actual descriptor elements, including one explicit allocation alias."""
    return [{'binding': 0, 'element': 0, 'guest': S+12, 'size_word': 0, 'bound_bytes': 10, 'kind': 'single', 'allocation': 'short'},
            {'binding': 1, 'element': 0, 'guest': S, 'size_word': DATA_BYTES, 'bound_bytes': DATA_BYTES, 'kind': 'single', 'allocation': 'shared'},
            {'binding': 1, 'element': 1, 'guest': U+5, 'size_word': 0, 'bound_bytes': 32, 'kind': 'single', 'allocation': 'zero_prefix'},
            {'binding': 0, 'element': 1, 'guest': T, 'size_word': 0, 'bound_bytes': DATA_BYTES, 'kind': 'table', 'allocation': 'shared'}]


def ownership_proof():
    """Enumerate all written bytes and show no other lane reads those bytes."""
    owned = {}
    for lane in range(LANES):
        base = 256 + lane*64
        for allocation in ('shared', 'physical'):
            for offset, width in ((0, 4), (8, 8), (16, 16)):
                for byte in range(base+offset, base+offset+width):
                    key = allocation, byte
                    if key in owned:
                        raise ValueError('Fixture has overlapping stores')
                    owned[key] = lane
    # Shared edge reads, including typed floors of the cross-page operand.
    immutable = {'shared': [(0, 16), (128, 144), (4080, 4096)],
                 'physical': [(4092, 4096)], 'short': [(0, 8)],
                 'zero_prefix': [(0, 16)], 'ordinary_miss': [(8, 12)]}
    for allocation, ranges in immutable.items():
        for low, high in ranges:
            if any((allocation, byte) in owned for byte in range(low, high)):
                raise ValueError('Edge read overlaps another lane store')
    for lane, queries in enumerate(query_rows()):
        base=256+lane*64
        primary,alias=(S,T) if lane%2==0 else (T,S)
        expected=[primary+base,primary+base+8,primary+base+16,alias+base,F+base,S+4,U+4,S+4092,F+4092,T+128,128]
        if queries!=expected:
            raise ValueError('Query graph differs from proved disjoint recipe')
        for allocation in ('shared','physical'):
            for low,high in ((base,base+4),(base+8,base+16),(base+16,base+32)):
                if any(owned.get((allocation,byte))!=lane for byte in range(low,high)):
                    raise ValueError('Mutable read crosses lane ownership')
    # All mutable reads use the exact lane's own three spans; output rows have
    # disjoint [lane*ROWS,(lane+1)*ROWS) regions. No barriers between lanes needed.
    return {'lanes': LANES, 'per_lane_span_bytes': 64, 'per_lane_base': 256,
            'typed_store_spans': [[0, 4], [8, 16], [16, 32]],
            'disjoint_written_bytes_per_allocation': len(owned)//2,
            'immutable_shared_reads_disjoint_from_all_writes': True,
            'mutable_reads_use_own_lane_spans': True, 'output_rows_per_lane': ROWS,
            'cross_invocation_barrier_required': False}


def oracle(follow):
    """Independent known-operation oracle, without lookup/partition calls."""
    if type(follow) is not bool:
        raise ValueError('Explicit boolean follow-stores case required')
    initial = {name: payload(name, size) for name, size in
               (('shared', DATA_BYTES), ('short', 10), ('zero_prefix', 32),
                ('mapped_elsewhere', DATA_BYTES), ('physical', DATA_BYTES), ('ordinary_miss', 4096))}
    final = {name: bytearray(data) for name, data in initial.items()}
    records = []
    for lane in range(LANES):
        offset = 256 + lane*64
        rows = [[0xDA010000 | lane, lane, int(follow), ROWS],
                [2, 1, 0, DATA_BYTES//4], [DATA_BYTES//8, DATA_BYTES//16, 8, 4], [2, DATA_BYTES//4, DATA_BYTES//8, DATA_BYTES//16]]
        def emit(words):
            rows.append(words + [0]*(4-len(words)))
        for delta, width in ((0, 4), (8, 8), (16, 16)):
            emit(read_words(initial['shared'], offset+delta, width))
            words = values('storage', width, lane, follow)
            final['shared'][offset+delta:offset+delta+width] = struct.pack(f'<{width//4}I', *words)
        # Global reciprocal alias, direct A[1] alias, and direct B[0] alias.
        for _ in range(3):
            for width in (4, 8, 16):
                emit(values('storage', width, lane, follow))
        for delta, width in ((0, 4), (8, 8), (16, 16)):
            emit(read_words(initial['physical'], offset+delta, width))
        for delta, width in ((0, 4), (8, 8), (16, 16)):
            words = values('physical', width, lane, follow)
            if follow:
                final['physical'][offset+delta:offset+delta+width] = struct.pack(f'<{width//4}I', *words)
            emit(words if follow else read_words(initial['physical'], offset+delta, width))
        # The short first descriptor handles scalar/v2; v4 selects later B[0].
        emit(read_words(initial['short'], 4, 4))
        emit(read_words(initial['short'], 0, 8))
        emit(read_words(initial['shared'], 0, 16))
        emit([*read_words(initial['zero_prefix'], 4, 4), *read_words(initial['ordinary_miss'], 8, 4)])
        emit(read_words(initial['zero_prefix'], 0, 16))
        for width in (4, 8, 16):
            emit(read_words(initial['shared'], 128, width))
        for width in (4, 8, 16):
            emit(read_words(initial['shared'], (4092//width)*width, width))
        emit(read_words(initial['physical'], 4092, 4))
        emit([0, 0])
        emit([0, 0, 0, 0])
        emit([0, 0, 0, 0])
        if len(rows) != ROWS or any(len(row) != 4 for row in rows):
            raise ValueError('Oracle output field layout changed')
        records.extend(rows)
    output = b''.join(struct.pack('<4I', *row) for row in records)
    return initial, {name: bytes(data) for name, data in final.items()}, output


def verify_readback(directory, actual, device_addresses):
    """Exact complete-allocation comparison with independently relocated table.

    device_addresses are future host-owned VkBuffer base addresses, not values
    read from GPU output. This function alone does not validate a host executor.
    """
    directory = Path(directory)
    plan = json.loads((directory/'plan.json').read_text())
    expected_names = {item['name'] for item in plan['allocations']}
    if set(actual) != expected_names or set(device_addresses) != expected_names:
        raise ValueError('Every complete allocation and host device address is required')
    expected = {}
    for allocation in plan['allocations']:
        data = (directory/allocation['expected_file']).read_bytes()
        if len(data) != allocation['bytes'] or sha(data) != allocation['expected_sha256']:
            raise ValueError('Frozen expected allocation changed')
        expected[allocation['name']] = bytearray(data)
    for relocation in plan['page_table_relocations']:
        address = device_addresses[relocation['target_allocation']] + relocation['target_offset']
        if not 0 < address < 1 << 64 or address % 4:
            raise ValueError('Host physical target address must be nonzero and four-byte aligned')
        offset = relocation['write_offset']
        expected[relocation['allocation']][offset:offset+8] = struct.pack('<Q',address)
    failures=[]
    for name, reference in expected.items():
        observed=actual[name]
        if observed != reference:
            first=next((index for index,(a,b) in enumerate(zip(observed,reference)) if a!=b),min(len(observed),len(reference)))
            failures.append({'allocation':name,'first_mismatch_byte':first,'actual_bytes':len(observed),'expected_bytes':len(reference)})
    return {'match':not failures,'compared_allocations':len(expected),'compared_bytes':sum(map(len,expected.values())),'failures':failures}


def fixture_source():
    dependency = frozen_generator()
    source = dependency.glsl_source().split('void main() {', 1)[0]
    source += f'void emit_result(uint lane,uint slot,uvec4 value) {{ results.values[lane*{ROWS}u+slot]=value; }}\n'
    source += 'void main() {\n uint lane=gl_GlobalInvocationID.x;\n'
    for index in range(QUERIES):
        source += f' uint64_t q{index}=queries.addresses[lane*{QUERIES}u+{index}u];\n'
    source += ' uint relative=256u+lane*64u;\n'
    slot = 0
    def emit(expression):
        nonlocal source, slot
        source += f' emit_result(lane,{slot}u,{expression});\n'
        slot += 1
    def packed(expression, width):
        return {4: f'uvec4({expression},0u,0u,0u)', 8: f'uvec4({expression},0u,0u)', 16: expression}[width]
    def changed(kind, width):
        base = {'storage': 0xA0000000, 'physical': 0xB0000000}[kind]
        expressions = [f'(0x{base | ((width//4)<<24) | (component<<20):08x}u|((parameters.follow_stores&1u)<<16)|lane)' for component in range(width//4)]
        return expressions[0] if width == 4 else f'uvec{width//4}(' + ','.join(expressions) + ')'
    emit(f'uvec4(0xDA010000u|lane,lane,parameters.follow_stores,{ROWS}u)')
    emit('uvec4(length4(0u,0u),length8(0u,0u),length16(0u,0u),length4(1u,0u))')
    emit('uvec4(length8(1u,0u),length16(1u,0u),length4(1u,1u),length8(1u,1u))')
    emit('uvec4(length16(1u,1u),length4(0u,1u),length8(0u,1u),length16(0u,1u))')
    for index, width in enumerate((4, 8, 16)):
        emit(packed(f'load{width}(q{index})', width))
    for index, width in enumerate((4, 8, 16)):
        source += f' store{width}(q{index},{changed("storage", width)});\n'
    source += ' memoryBarrierBuffer();\n'
    for delta, width in ((0, 4), (8, 8), (16, 16)):
        emit(packed(f'load{width}(q3+uint64_t({delta}))', width))
    for root, element in (('a', 1), ('b', 0)):
        for delta, width in ((0, 4), (8, 8), (16, 16)):
            emit(packed(f'{root}{width}[{element}].values[(relative+{delta}u)>>{width.bit_length()-1}]', width))
    for delta, width in ((0, 4), (8, 8), (16, 16)):
        emit(packed(f'load{width}(q4+uint64_t({delta}))', width))
    for delta, width in ((0, 4), (8, 8), (16, 16)):
        source += f' store{width}(q4+uint64_t({delta}),{changed("physical", width)});\n'
    source += ' memoryBarrierBuffer();\n'
    for delta, width in ((0, 4), (8, 8), (16, 16)):
        emit(packed(f'load{width}(q4+uint64_t({delta}))', width))
    for width in (4, 8, 16):
        emit(packed(f'load{width}(q5)', width))
    emit('uvec4(load4(q6),load4(q6+uint64_t(4)),0u,0u)')
    emit('load16(q6)')
    for query in (9, 7, 8):
        for width in (4, 8, 16):
            emit(packed(f'load{width}(q{query})', width))
    emit('load16(q10)')
    if slot != ROWS:
        raise ValueError('Shader output field count differs')
    return source + '}\n'


def prepare(output):
    output = Path(output).resolve()
    if output.exists() or not output.is_relative_to(ROOT / 'local'):
        raise ValueError('Use a fresh private local output')
    proof = ownership_proof()
    dependency = frozen_generator()
    contract = memory_contract()
    descriptors = [dependency.Descriptor(**{key: view[key] for key in ('guest', 'size_word', 'bound_bytes', 'binding', 'element', 'kind')}) for view in contract]
    partitions = dependency.build_partitions(descriptors)
    starts, counts, rows = [], [], []
    for width in (4, 8, 16):
        starts.append(len(rows)); counts.append(len(partitions[width])); rows.extend(partitions[width])
    record = b''.join(struct.pack('<QQQII', row.low, row.high, row.base, row.binding, row.element) for row in rows)
    queries = b''.join(struct.pack('<Q', address) for row in query_rows() for address in row)
    output.mkdir(parents=True)
    glsl, original, assembly, binary = [output/name for name in ('runtime.comp', 'value.spv', 'pointer.spvasm', 'pointer.spv')]
    glsl.write_text(fixture_source())
    subprocess.run(['glslangValidator', '-V', '--target-env', 'vulkan1.2', '-S', 'comp', str(glsl), '-o', str(original)], check=True, capture_output=True, text=True, timeout=30)
    text = subprocess.run(['spirv-dis', str(original)], check=True, capture_output=True, text=True, timeout=15).stdout
    assembly.write_text(dependency.pointer_assembly(text))
    subprocess.run(['spirv-as', '--target-env', 'vulkan1.2', str(assembly), '-o', str(binary)], check=True, capture_output=True, text=True, timeout=15)
    for path in (original, binary):
        subprocess.run(['spirv-val', '--target-env', 'vulkan1.2', str(path)], check=True, capture_output=True, text=True, timeout=15)
    cases = []
    for follow in (False, True):
        directory = output / ('follow-on' if follow else 'follow-off'); directory.mkdir()
        initial, expected, results = oracle(follow)
        initial.update(records=record, queries=queries, results=bytes([0xE5])*(len(results)+64), page_table=bytes(66*8))
        expected.update(records=record, queries=queries, results=results+bytes([0xE5])*64, page_table=bytes(66*8))
        allocations = []
        for name, data in initial.items():
            before, after = guarded(data), guarded(expected[name])
            (directory/(name+'-initial.bin')).write_bytes(before)
            (directory/(name+'-expected.bin')).write_bytes(after)
            allocations.append({'name': name, 'bytes': len(before), 'payload_offset': GUARD, 'payload_bytes': len(data),
                'initial_file':name+'-initial.bin', 'initial_sha256':sha(before), 'expected_file':name+'-expected.bin', 'expected_sha256':sha(after),
                'comparison':'exact-all-bytes-after-host-relocation' if name=='page_table' else 'exact-all-bytes'})
        relocations = []
        for guest, allocation, pages in ((S,'mapped_elsewhere',2),(T,'mapped_elsewhere',2),(U,'ordinary_miss',1),(F,'physical',2)):
            for page in range(pages):
                relocations.append({'allocation':'page_table','write_offset':GUARD+((guest-W0)//4096+page)*8,
                    'target_allocation':allocation,'target_offset':GUARD+page*4096,'bytes':8})
        push = struct.pack('<4I4IQII', *starts, 0, *counts, 0, 0, int(follow), 0)
        (directory/'push-initial.bin').write_bytes(push)
        plan = {'schema':1,'profile':'typed-array-runtime-fixture/v1','synthetic':True,'prepare_only':True,'Vulkan_opened':False,
            'follow_stores':follow,'module_sha256':sha(binary.read_bytes()),'fixture_tool_sha256':sha(Path(__file__).read_bytes()),
            'frozen_pointer_dependency_sha256':FROZEN_GENERATOR_SHA,'workgroups':[1,1,1],'local_size':[64,1,1],
            'required_subgroup_size':32,'full_subgroups':True,'max_dispatches':1,'fence_timeout_ns':2000000000,'process_timeout_seconds':30,
            'descriptor_mode':'push','descriptor_roots':contract,'metadata_bindings':{'2':'records','3':'queries','4':'results'},
            'descriptor_offset':GUARD,'descriptor_counts':[2,2,1,1,1], 'typed_alias_widths':[4,8,16],
            'push_constant_bytes':48,'push_initial_sha256':sha(push),'push_root_relocation':{'offset':32,'target_allocation':'page_table','target_offset':GUARD},
            'interval_starts':starts,'interval_counts':counts,'allocations':allocations,'page_table_relocations':relocations,
            'queries_per_lane':QUERIES,'result_rows_per_lane':ROWS,'ownership_proof':proof,
            'required_features':['variablePointersStorageBuffer','shaderStorageBufferArrayNonUniformIndexing','shaderInt64','bufferDeviceAddress','robustBufferAccess','robustBufferAccess2','subgroupSizeControl','computeFullSubgroups'],
            'driver_profile':{'name':'cache-safe-e5','library_sha256':'e5a1305ea3b423a18fccbb945aaedd32f8add303926fcc39276ee01032cd456f',
                'icd_sha256':'14f4eda1b6fd6cbf07d61edeeffdaaede166f4c09d165fec9f661e9f90556ad3'},
            'compiler_policy':{'name':'native-safe','controlled_options':{key:'1' if key in ('HS_SPILL_BATCH','HS_PUSH_SHADOW','shader_spilling_rate') else None for key in CONTROLLED_OPTIONS},'disk_cache_disabled':True},
            'host_required_checks':['Every VkBuffer payload BDA is16-byte aligned and offset64 satisfies minStorageBufferOffsetAlignment.',
                'B0 and A1 descriptor aliases reference the same VkBuffer,offset64,range8192.',
                'Page-table/push relocations use only the named owned allocations; all ranges and offsets are checked.',
                'Complete initialized allocations and full relocated expected table are frozen before dispatch.',
                'Upload/compute/readback barriers and host memory flush/invalidate are explicit.',
                'Actual executable subgroup32 and both pointer features must be confirmed; unsupported stops.',
                'One dispatch, a2second fence and30second parent-bound process; failure stops without idle wait.'],
            'executor_implemented':False,'runtime_correctness_established':False,
            'limitations':['Independent explicit fixture oracle, not native renderer classification/replay.',
                           'Only first guest window exercised; no candidates/atomics/subword helpers.',
                           'Host must compare full relocated table, all guards and all allocations; no hash-only or XOR reduction.',
                           'B0 and A1 must use the same VkBuffer and offset, not copied equal bytes.']}
        data = canonical(plan); (directory/'plan.json').write_bytes(data)
        cases.append({'follow_stores':follow,'plan_sha256':sha(data),'directory':directory.name})
    report={'schema':1,'CPU_prepared':True,'Vulkan_opened':False,'pipeline_compiled':False,'dispatch_executed':False,
        'fixture_tool_sha256':sha(Path(__file__).read_bytes()),'source_sha256':sha(glsl.read_bytes()),'pointer_spirv_sha256':sha(binary.read_bytes()),
        'value_spirv_sha256':sha(original.read_bytes()),'assembly_sha256':sha(assembly.read_bytes()),'ownership_proof':proof,'cases':cases}
    report['tool_versions']={name:subprocess.run([tool,'--version'],check=True,capture_output=True,text=True,timeout=15).stdout.splitlines()[0]
        for name,tool in (('glslang','glslangValidator'),('spirv_tools','spirv-val'))}
    (output/'preparation.json').write_bytes(canonical(report))
    return report


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    try:
        print(json.dumps(prepare(args.output),indent=2))
    except (OSError,ValueError,subprocess.SubprocessError) as error:
        print(str(error))
        if isinstance(error,subprocess.CalledProcessError):print(error.stderr)
        return 1
    return 0


if __name__=='__main__':
    raise SystemExit(main())
