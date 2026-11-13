# SPDX-License-Identifier: GPL-3.0-or-later
"""Independent CPU fixtures for the target-pinned native SPIR-V writer patch."""
import hashlib
from pathlib import Path
import random
import shutil
import struct
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
PATCH = ROOT / "patches/hookshot/compute-interval-native-writer.patch"
DEPENDENCY = ROOT / "patches/hookshot/compute-identity.patch"
DEPENDENCY_SHA = "2f14873edfdb47b1f915e75bc4bbe6683ef6632bc66fcbfa1d918aa1c176f018"
HEADERS = Path("/usr/include/spirv/unified1/spirv.hpp").is_file()
COMPILER = shutil.which("c++")


def added(patch, name):
    active, text = False, []
    for line in patch.read_text().splitlines(keepends=True):
        if line.startswith("diff --git "):
            active = line.rstrip().endswith(" b/" + name)
        elif active and line.startswith("+") and not line.startswith("+++"):
            text.append(line[1:])
    result = "".join(text)
    if not result or "SPDX-License-Identifier" not in result:
        raise ValueError("Missing authored patch file")
    return result


HARNESS = r'''
#include "gpu/vk/compute_interval_spirv_writer.h"
#include <iostream>
int main(int argc,char** argv) {
    try {
        std::vector<uint8_t> data;
        for(int c=std::cin.get();c!=std::char_traits<char>::eof();c=std::cin.get()) {
            if(data.size()>=2097152)throw std::runtime_error("Transport input cap");
            data.push_back(uint8_t(c));
        }
        const std::string mode=argc==1?"target":argv[1];
        if(mode=="hash") {
            std::cout<<mkwvk::oo_identity::hex(mkwvk::oo_identity::sha256(data.data(),data.size()));return 0;
        }
        if(data.size()%4)throw std::runtime_error("Unaligned binary bytes");
        open_ocarina::interval_spirv::Words w;
        for(size_t n=0;n<data.size();n+=4)w.push_back(uint32_t(data[n])|uint32_t(data[n+1])<<8|uint32_t(data[n+2])<<16|uint32_t(data[n+3])<<24);
        if(mode=="parse") {open_ocarina::interval_spirv::Module module(w);std::cout<<"parsed";return 0;}
        open_ocarina::interval_spirv::Result result;
        if(mode=="target")result=open_ocarina::interval_spirv::write_target(w);
#ifdef OPEN_OCARINA_INTERVAL_WRITER_TESTING
        else if(mode=="fixture")result=open_ocarina::interval_spirv::write_authored_fixture(w);
#endif
        else throw std::runtime_error("Unknown mode");
        const auto b=open_ocarina::interval_spirv::bytes(result.words);
        std::cout.write(reinterpret_cast<const char*>(b.data()),static_cast<std::streamsize>(b.size()));return 0;
    }catch(const std::exception& e){std::cerr<<e.what()<<'\n';return 2;}
}
'''


def fixture_assembly():
    """Authored seven-function module; no instructions extracted from a game."""
    serial = 5000
    constants = {}

    def fresh():
        nonlocal serial
        serial += 1
        return f"%{serial}"

    def constant(value, wide=False):
        key = value, wide
        if key not in constants:
            constants[key] = fresh()
        return constants[key]

    zero, one, three = constant(0), constant(1), constant(3)
    mask = constant((1 << 64) - 16, True)
    count = constant(278)
    annotations = ["OpDecorate %4302 Block", "OpMemberDecorate %4302 0 Offset 0",
                   "OpDecorate %4303 Block", "OpMemberDecorate %4303 0 Offset 0",
                   "OpDecorate %4000 ArrayStride 4", "OpDecorate %4001 Block",
                   "OpMemberDecorate %4001 0 Offset 0", "OpDecorate %4003 DescriptorSet 0", "OpDecorate %4003 Binding 0",
                   "OpDecorate %4007 ArrayStride 8", "OpDecorate %4008 Block",
                   "OpMemberDecorate %4008 0 Offset 0", "OpDecorate %4010 DescriptorSet 0", "OpDecorate %4010 Binding 0"]
    storage = []
    for width, start, first in [(4, 4020, 4100), (16, 4030, 4110)]:
        scalar = "%7" if width == 4 else "%10"
        annotations += [f"OpDecorate %{start} ArrayStride {width}", f"OpDecorate %{start+1} Block",
                        f"OpMemberDecorate %{start+1} 0 Offset 0"]
        storage += [f"%{start} = OpTypeRuntimeArray {scalar}", f"%{start+1} = OpTypeStruct %{start}",
                    f"%{start+2} = OpTypePointer StorageBuffer %{start+1}", f"%{start+3} = OpTypeArray %{start+1} {three}",
                    f"%{start+4} = OpTypePointer StorageBuffer %{start+3}"]
        for i in range(6):
            annotations += [f"OpDecorate %{first+i} DescriptorSet 0", f"OpDecorate %{first+i} Binding {i+2}"]
            storage += [f"%{first+i} = OpVariable %{start+4 if i==5 else start+2} StorageBuffer"]

    def helper(width):
        function, parameter, entry = (107, 108, 109) if width == 4 else (850, 851, 852)
        result = "%7" if width == 4 else "%10"
        pointer = "%54" if width == 4 else "%79"
        first, structure_pointer = (4100, "%4022") if width == 4 else (4110, "%4032")
        lines = [f"%{function} = OpFunction {result} None %{4200 if width==4 else 4201}",
                 f"%{parameter} = OpFunctionParameter %23", f"%{entry} = OpLabel"]
        counter = fresh(); lines.append(f"{counter} = OpVariable %110 Function")
        shift = constant(2 if width == 4 else 4)

        def op(code, *a):
            out = fresh(); lines.append(out + " = " + code + " " + " ".join(a)); return out

        def hit(root, selector, element=None):
            word = constant(selector // 8)
            if element:
                word = op("OpIAdd", "%7", word, op("OpShiftLeftLogical", "%7", element, one))
            address = op("OpBitcast", "%23", op("OpLoad", "%8", op("OpAccessChain", "%4011", "%4010", zero, word)))
            base = op("OpBitwiseAnd", "%23", address, mask)
            structure = root if not element else op("OpAccessChain", structure_pointer, root, element)
            length = op("OpArrayLength", "%7", structure, "0")
            bound = op("OpIAdd", "%23", base, op("OpUConvert", "%23", op("OpShiftLeftLogical", "%7", length, shift)))
            if element:
                pair = op("OpLoad", "%8", op("OpAccessChain", "%4011", "%4010", zero, op("OpIAdd", "%7", word, one)))
                size = op("OpCompositeExtract", "%7", pair, "0")
            else:
                size = op("OpLoad", "%7", op("OpAccessChain", "%4004", "%4003", zero, constant((selector + 8) // 4)))
            native = op("OpIAdd", "%23", address, op("OpUConvert", "%23", size))
            end = op("OpSelect", "%23", op("OpULessThan", "%2", bound, native), bound, native)
            if element:
                end = op("OpSelect", "%23", op("OpIEqual", "%2", size, zero), bound, end)
            condition = op("OpLogicalAnd", "%2", op("OpUGreaterThanEqual", "%2", f"%{parameter}", base),
                           op("OpULessThan", "%2", f"%{parameter}", end))
            return base, condition

        def returned(root, base, element=None):
            index = op("OpShiftRightLogical", "%7", op("OpUConvert", "%7", op("OpISub", "%23", f"%{parameter}", base)), shift)
            indices = (element, zero, index) if element else (zero, index)
            value = op("OpLoad", result, op("OpAccessChain", pointer, root, *indices))
            lines.append("OpReturnValue " + value)

        for i, selector in enumerate((192, 208, 256, 272, 352)):
            root = f"%{first+i}"; base, condition = hit(root, selector)
            match, next_block = fresh(), fresh()
            lines.extend([f"OpSelectionMerge {next_block} None", f"OpBranchConditional {condition} {match} {next_block}", f"{match} = OpLabel"])
            returned(root, base); lines.append(f"{next_block} = OpLabel")
        header, check, body, continuation, miss = [fresh() for _ in range(5)]
        lines += [f"OpStore {counter} {zero}", f"OpBranch {header}", f"{header} = OpLabel",
                  f"OpLoopMerge {miss} {continuation} DontUnroll", f"OpBranch {check}", f"{check} = OpLabel"]
        element = op("OpLoad", "%7", counter)
        condition = op("OpULessThan", "%2", element, three)
        lines += [f"OpBranchConditional {condition} {body} {miss}", f"{body} = OpLabel"]
        base, condition = hit(f"%{first+5}", 2176, element)
        matched, unmatched = fresh(), fresh()
        lines += [f"OpSelectionMerge {unmatched} None", f"OpBranchConditional {condition} {matched} {unmatched}", f"{matched} = OpLabel"]
        returned(f"%{first+5}", base, element)
        lines += [f"{unmatched} = OpLabel", f"OpBranch {continuation}", f"{continuation} = OpLabel"]
        after = op("OpIAdd", "%7", op("OpLoad", "%7", counter), one)
        lines += [f"OpStore {counter} {after}", f"OpBranch {header}", f"{miss} = OpLabel"]
        physical = op("OpConvertUToPtr", "%4300" if width == 4 else "%4301", f"%{parameter}")
        physical_element = op("OpAccessChain", "%4304" if width == 4 else "%4305", physical, zero)
        loaded = op("OpLoad", result, physical_element, "Aligned", "4")
        lines += ["OpReturnValue " + loaded, "OpFunctionEnd"]
        return lines

    scalar, vector = helper(4), helper(16)
    stubs = {f: [f"%{f} = OpFunction %1 None %4202", f"{fresh()} = OpLabel", "OpReturn", "OpFunctionEnd"]
             for f in (332, 491, 691, 1050)}
    main = ["%1209 = OpFunction %1 None %4202", f"{fresh()} = OpLabel",
            f"{fresh()} = OpFunctionCall %7 %107 {constant(0, True)}",
            f"{fresh()} = OpFunctionCall %10 %850 {constant(0, True)}", "OpReturn", "OpFunctionEnd"]
    primitives = ["%1 = OpTypeVoid", "%2 = OpTypeBool", "%7 = OpTypeInt 32 0", "%23 = OpTypeInt 64 0",
                  "%8 = OpTypeVector %7 2", "%10 = OpTypeVector %7 4", "%54 = OpTypePointer StorageBuffer %7",
                  "%79 = OpTypePointer StorageBuffer %10", "%110 = OpTypePointer Function %7",
                  "%4200 = OpTypeFunction %7 %23", "%4201 = OpTypeFunction %10 %23", "%4202 = OpTypeFunction %1",
                  "%4302 = OpTypeStruct %7", "%4303 = OpTypeStruct %10",
                  "%4300 = OpTypePointer PhysicalStorageBuffer %4302", "%4301 = OpTypePointer PhysicalStorageBuffer %4303",
                  "%4304 = OpTypePointer PhysicalStorageBuffer %7", "%4305 = OpTypePointer PhysicalStorageBuffer %10"]
    declarations = [f"{identity} = OpConstant {'%23' if wide else '%7'} {value}" for (value, wide), identity in constants.items()]
    uniforms = [f"%4000 = OpTypeArray %7 {count}", "%4001 = OpTypeStruct %4000", "%4002 = OpTypePointer Uniform %4001",
                "%4003 = OpVariable %4002 Uniform", "%4004 = OpTypePointer Uniform %7",
                f"%4007 = OpTypeArray %8 {count}", "%4008 = OpTypeStruct %4007", "%4009 = OpTypePointer Uniform %4008",
                "%4010 = OpVariable %4009 Uniform", "%4011 = OpTypePointer Uniform %8"]
    interfaces = "%4003 %4010 " + " ".join(f"%{first+i}" for first in (4100, 4110) for i in range(6))
    return "\n".join(["OpCapability Shader", "OpCapability Int64", "OpCapability PhysicalStorageBufferAddresses",
        'OpExtension "SPV_KHR_physical_storage_buffer"', "OpMemoryModel PhysicalStorageBuffer64 GLSL450",
        'OpEntryPoint GLCompute %1209 "main" ' + interfaces, "OpExecutionMode %1209 LocalSize 64 1 1",
        *annotations, *primitives, *declarations, *uniforms, *storage, *scalar, *stubs[332], *stubs[491],
        *stubs[691], *vector, *stubs[1050], *main]) + "\n"


def binary(words):
    return struct.pack(f"<{len(words)}I", *words)


def instructions(words):
    at = 5
    while at < len(words):
        count, opcode = words[at] >> 16, words[at] & 65535
        yield at, opcode, words[at + 1:at + count]
        at += count


@unittest.skipUnless(COMPILER and HEADERS, "Optional C++17 and public SPIRV-Headers check")
class ComputeIntervalNativeWriterTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory(); cls.directory = Path(cls.temp.name)
        if hashlib.sha256(DEPENDENCY.read_bytes()).hexdigest() != DEPENDENCY_SHA:
            raise AssertionError("Reviewed identity dependency changed")
        target = cls.directory / "gpu/vk"; target.mkdir(parents=True)
        (target / "compute_identity.h").write_text(added(DEPENDENCY, "gpu/vk/compute_identity.h"))
        (target / "compute_interval_spirv_writer.h").write_text(added(PATCH, "gpu/vk/compute_interval_spirv_writer.h"))
        (cls.directory / "harness.cpp").write_text(HARNESS)
        cls.production, cls.fixture_writer = cls.directory / "production", cls.directory / "fixture"
        for output, flags in [(cls.production, []), (cls.fixture_writer, ["-DOPEN_OCARINA_INTERVAL_WRITER_TESTING"])]:
            result = subprocess.run([COMPILER, "-std=c++17", "-O2", "-Wall", "-Wextra", "-Werror", "-pedantic", *flags,
                                     str(cls.directory / "harness.cpp"), "-o", str(output)], capture_output=True, text=True, timeout=60)
            if result.returncode:
                cls.temp.cleanup(); raise AssertionError(result.stderr)
        cls.original = None
        if shutil.which("spirv-as"):
            source = cls.directory / "original.spvasm"; source.write_text(fixture_assembly())
            binary_path = source.with_suffix(".spv")
            result = subprocess.run(["spirv-as", "--target-env", "vulkan1.3", "--preserve-numeric-ids", str(source), "-o", str(binary_path)], capture_output=True, text=True)
            if result.returncode:
                raise AssertionError(result.stderr)
            cls.original = binary_path.read_bytes()

    @classmethod
    def tearDownClass(cls):
        cls.temp.cleanup()

    def call(self, data, mode="fixture", production=False, error=None):
        result = subprocess.run([str(self.production if production else self.fixture_writer), mode], input=data, capture_output=True, timeout=10)
        if error:
            self.assertEqual(result.returncode, 2); self.assertEqual(result.stdout, b"")
            self.assertIn(error, result.stderr.decode())
        else:
            self.assertEqual(result.returncode, 0, result.stderr.decode()); return result.stdout

    def require_fixture(self):
        if self.original is None:
            self.skipTest("Optional authored fixture needs SPIRV-Tools assembler")
        return self.original

    def test_dependency_sha_padding_and_multiblock_lengths(self):
        rng = random.Random(2917)
        for n in (0, 1, 3, 55, 56, 57, 63, 64, 65, 119, 120, 127, 128, 129, 1024, 65535, 65536, 1000000):
            data = bytes(rng.randrange(256) for _ in range(n))
            with self.subTest(length=n):
                self.assertEqual(self.call(data, "hash", production=True).decode(), hashlib.sha256(data).hexdigest())

    def test_production_default_has_no_authored_fixture_entry(self):
        self.call(b"", "fixture", production=True, error="Unknown mode")
        source = self.directory / "forbidden.cpp"
        source.write_text('#include "gpu/vk/compute_interval_spirv_writer.h"\nint main(){open_ocarina::interval_spirv::write_authored_fixture({});}\n')
        result = subprocess.run([COMPILER, "-std=c++17", "-fsyntax-only", str(source)], capture_output=True, timeout=30)
        self.assertNotEqual(result.returncode, 0)

    def test_production_rejects_authored_module_by_internal_sha(self):
        self.call(self.require_fixture(), "target", production=True, error="SHA-256 differs")

    def test_framing_result_ids_and_reference_ids_fail_closed(self):
        base = [0x07230203, 0x00010600, 0, 8, 0, (2 << 16) | 19, 1, (2 << 16) | 20, 2, (4 << 16) | 21, 7, 32, 0]
        self.assertEqual(self.call(binary(base), "parse"), b"parsed")
        cases = []
        w = base.copy(); w[0] = 0; cases.append((w, "header/extent"))
        w = base.copy(); w[3] = 7; cases.append((w, "result ID"))
        w = base.copy(); w[4] = 1; cases.append((w, "header/extent"))
        w = base.copy(); w[5] = 19; cases.append((w, "framing"))
        w = base.copy(); w[5] = (999 << 16) | 19; cases.append((w, "framing"))
        w = base.copy(); w[6] = 0; cases.append((w, "result ID"))
        w = base.copy(); w[8] = 1; cases.append((w, "result ID"))
        w = base.copy(); w[5] = (2 << 16) | 65535; cases.append((w, "Unknown SPIR-V"))
        w = base + [(4 << 16) | 32, 6, 12, 5]; cases.append((w, "Undefined/out-of-bound"))
        w = base + [(3 << 16) | 5, 1, 0xffffffff]; cases.append((w, "Unterminated literal"))
        w = base + [(3 << 16) | 5, 1, 0x410041]; cases.append((w, "Nonzero string padding"))
        w = base + [(4 << 16) | 61, 7, 6, 5]; cases.append((w, "Undefined/out-of-bound"))
        for w, error in cases:
            with self.subTest(error=error): self.call(binary(w), "parse", error=error)
        self.call(b"x", "parse", error="Unaligned binary")

    def test_authored_module_preserves_five_functions_and_physical_suffixes(self):
        from tools.compute_interval_lowering import function_words
        original = self.require_fixture(); candidate = self.call(original)
        left, right = function_words(original), function_words(candidate)
        self.assertEqual(set(left), set(right))
        for identity in set(left) - {107, 850}:
            self.assertEqual(left[identity], right[identity])
        for identity in (107, 850):
            def physical_suffix(w):
                block = None
                for at, opcode, operands in instructions([0] * 5 + list(w)):
                    if opcode == 248: block = at - 5
                    if opcode == 120: return w[block:]
                raise AssertionError("Fixture lacks physical suffix")
            self.assertEqual(physical_suffix(left[identity]), physical_suffix(right[identity]))

    def test_authored_native_and_python_emit_identical_instruction_words(self):
        if not shutil.which("spirv-dis") or not shutil.which("spirv-as"):
            self.skipTest("Optional independent Python/SPIRV-Tools comparison")
        from tools.compute_interval_lowering import transform_assembly
        original = self.require_fixture(); native = self.call(original)
        source = self.directory / "python-source.spv"; source.write_bytes(original)
        result = subprocess.run(["spirv-dis", "--raw-id", "--no-indent", str(source)], capture_output=True, text=True, check=True)
        transformed, _ = transform_assembly(result.stdout)
        asm = self.directory / "python.spvasm"; asm.write_text(transformed); output = asm.with_suffix(".spv")
        subprocess.run(["spirv-as", "--target-env", "vulkan1.3", "--preserve-numeric-ids", str(asm), "-o", str(output)], check=True, capture_output=True)
        python = bytearray(output.read_bytes()); python[8:12] = native[8:12]
        self.assertEqual(native, python)

    def test_authored_original_and_candidate_validate(self):
        if not shutil.which("spirv-val"):
            self.skipTest("Optional SPIRV-Tools validation")
        original = self.require_fixture()
        for name, data in (("original", original), ("native", self.call(original))):
            path = self.directory / f"validate-{name}.spv"; path.write_bytes(data)
            result = subprocess.run(["spirv-val", "--target-env", "vulkan1.3", "--uniform-buffer-standard-layout", str(path)], capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)

    def test_selector_root_counter_and_bound_mutations_fail_closed(self):
        original = self.require_fixture(); words = list(struct.unpack(f"<{len(original)//4}I", original))
        constants = {a[2]: a[1] for _, op, a in instructions(words) if op == 43 and a[0] == 7}
        by_id = {a[1]: (at, op, a) for at, op, a in instructions(words) if op in (61, 128, 176, 169, 68)}
        cases = []
        at = next(at for at, op, a in instructions(words) if op == 71 and a[1:] == [33, 2])
        w = words.copy(); w[at+3] = 12; cases.append((w, "already occupied"))
        at = next(at for at, op, a in instructions(words) if op == 43 and a == [7, constants[24], 24])
        w = words.copy(); w[at+3] = 25; cases.append((w, "adjacent word"))
        at = next(at for at, op, a in instructions(words) if op == 169)
        w = words.copy(); w[at+4], w[at+5] = w[at+5], w[at+4]; cases.append((w, "source min"))
        loop_counter = next(a[0] for _, op, a in instructions(words) if op == 62)
        counter_store = [a for _, op, a in instructions(words) if op == 62 and a[0] == loop_counter][-1]
        increment_at, _, _ = by_id[counter_store[1]]
        w = words.copy(); w[increment_at+4] = constants[0]; cases.append((w, "unit-progress"))
        test_at = next(at for at, op, a in instructions(words) if op == 176 and a[-1] == constants[3])
        w = words.copy(); w[test_at+4] = constants[4]; cases.append((w, "counter bound"))
        for w, error in cases:
            with self.subTest(error=error): self.call(binary(w), error=error)

    def test_short_known_operands_and_nonpower_alignment_are_rejected(self):
        base = [0x07230203, 0x00010600, 0, 8, 0, (2 << 16) | 19, 1, (2 << 16) | 20, 2, (4 << 16) | 21, 7, 32, 0]
        for extra, error in (([(3 << 16) | 23, 6, 7], "operand count"),
                             ([(2 << 16) | 128, 7], "result operand"),
                             ([(3 << 16) | 246, 1, 2], "operand count"),
                             ([(6 << 16) | 61, 7, 6, 1, 2, 3], "alignment literal"),
                             ([(6 << 16) | 61, 7, 6, 1, 2, 0], "alignment literal"),
                             ([(5 << 16) | 62, 1, 2, 2, 3], "alignment literal")):
            with self.subTest(error=error): self.call(binary(base + extra), "parse", error=error)

    def test_function_scope_missing_function_and_suffix_escape_are_rejected(self):
        original = self.require_fixture(); words = list(struct.unpack(f"<{len(original)//4}I", original))
        rows = list(instructions(words))
        # One instruction now reads a private address parameter from another helper.
        at = next(at for at, op, a in rows if op == 120)
        w = words.copy(); w[at+3] = 851
        self.call(binary(w), error="Function-local ID escapes")
        # A preserved MISS suffix cannot secretly depend on removed selector SSA.
        base = next(a[1] for _, op, a in reversed(rows[:next(i for i, row in enumerate(rows) if row[0] == at)]) if op == 199)
        w = words.copy(); w[at+3] = base
        self.call(binary(w), error="Undefined/out-of-bound")
        # Delete one unreferenced stub without touching any other word.
        begin = next(at for at, op, a in rows if op == 54 and a[1] == 332)
        end = next(at + 1 for at, op, _ in rows if at > begin and op == 56)
        w = words[:begin] + words[end:]
        self.call(binary(w), error="seven-function inventory")

    def test_patch_has_no_gpu_execution_process_or_runtime_control_path(self):
        source = added(PATCH, "gpu/vk/compute_interval_spirv_writer.h")
        for forbidden in ("vkCmd", "vkQueue", "vkCreate", "getenv(", "system(", "popen("):
            self.assertNotIn(forbidden, source)
        self.assertIn('#include "compute_identity.h"', source)
