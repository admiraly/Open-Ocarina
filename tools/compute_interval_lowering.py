#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""CPU-only, exact-module interval-load prototype for one frozen owned graph.

No Vulkan API is used. Output is private analysis material, not an execution
admission. Live descriptor/UBO snapshot equivalence is deliberately unsupported.
"""
from __future__ import annotations
import argparse
from bisect import bisect_right
from collections import Counter
from dataclasses import asdict, dataclass
import hashlib
import json
from pathlib import Path
import re
import shlex
import struct
import subprocess
import sys

try:
    from .compile_probe import private_output
    from .physical_span_contract import inspect_module
except ImportError:
    from compile_probe import private_output
    from physical_span_contract import inspect_module

TARGET = "b9af52c0ba07f64eb8b0ca2905c432d094999fdf669aa95182ff7754b510e97b"
U32, U64 = (1 << 32) - 1, (1 << 64) - 1
WIDTHS = (4, 16)
RECORD_BINDING = 12
RECORD = struct.Struct("<QQQIIIIII")


def need(condition, message):
    if not condition:
        raise ValueError(message)


@dataclass(frozen=True)
class View:
    guest: int
    size_word: int
    bound_bytes: int
    binding: int
    element: int
    kind: str
    selector_byte_offset: int = 0


@dataclass(frozen=True)
class Row:
    low: int
    high: int
    base: int
    binding: int
    element: int


def source_interval(view, width, alignment=16):
    need(width in WIDTHS and alignment == 16, "Unsupported target typed/alignment policy")
    need(view.kind in ("single", "table"), "No candidate phase is emitted in this exact target")
    need(type(view.guest) is int and 0 <= view.guest <= U64 and
         type(view.size_word) is int and 0 <= view.size_word <= U32 and
         type(view.bound_bytes) is int and view.bound_bytes >= 0, "Invalid source descriptor")
    base = view.guest & (U64 ^ 15)
    array_length32 = (view.bound_bytes // width) & U32
    typed_bytes32 = (array_length32 << (width.bit_length() - 1)) & U32
    typed_end = (base + typed_bytes32) & U64
    nvn_end = (view.guest + view.size_word) & U64
    end = typed_end if view.kind == "table" and view.size_word == 0 else min(nvn_end, typed_end)
    return base, end


def legacy_identity(views, address, width):
    """Independent direct source-order walk; do not call the partition builder."""
    need(0 <= address <= U64 and width in WIDTHS, "Invalid typed address query")
    for view in views:
        need(view.kind in ("single", "table"), "Unreviewed descriptor class")
        aligned = view.guest - view.guest % 16
        count = (view.bound_bytes // width) % (1 << 32)
        bound_end = (aligned + ((count * width) % (1 << 32))) % (1 << 64)
        cap = (view.guest + view.size_word) % (1 << 64)
        selected_end = bound_end if view.kind == "table" and view.size_word == 0 else min(cap, bound_end)
        if aligned <= address < selected_end:
            return view.binding, view.element, aligned
    return None


def build_partitions(views):
    result = {}
    for width in WIDTHS:
        claims = [(source_interval(view, width), view) for view in views]
        boundaries = sorted({point for (low, high), _ in claims if low < high for point in (low, high)})
        rows = []
        for low, high in zip(boundaries, boundaries[1:]):
            winners = [(bounds, view) for bounds, view in claims if bounds[0] <= low < bounds[1]]
            if not winners:
                continue
            (base, _), view = winners[0]
            row = Row(low, high, base, view.binding, view.element)
            if rows and rows[-1].high == low and (rows[-1].base, rows[-1].binding, rows[-1].element) == (row.base, row.binding, row.element):
                old = rows[-1]; rows[-1] = Row(old.low, high, old.base, old.binding, old.element)
            else:
                rows.append(row)
        need(len(rows) <= 64, "Owned graph exceeds reviewed interval capacity")
        result[width] = rows
    return result


def interval_identity(rows, address):
    index = bisect_right([row.low for row in rows], address) - 1
    if index < 0 or not address < rows[index].high:
        return None
    row = rows[index]
    return row.binding, row.element, row.base


def binary_identity(rows, address):
    low, high, steps = 0, len(rows), 0
    while low < high:
        steps += 1
        need(steps <= 7, "Lookup exceeds reviewed finite-work bound")
        middle = low + ((high - low) >> 1)
        row = rows[middle]
        if address < row.low:
            high = middle
        elif address >= row.high:
            low = middle + 1
        else:
            return (row.binding, row.element, row.base), steps
    return None, steps


def certify_partitions(views, partitions):
    """Exhaust the complete constant-selection cells, not a sampled query grid.

    Source root selection depends only on interval membership in this target.
    All source and transformed endpoints partition the entire uint64 domain;
    selection identity is constant in each cell. Same root/base then implies
    the identical uint32 truncation and typed-element flooring for every byte.
    """
    certificates = {}
    for width in WIDTHS:
        rows = partitions[width]
        need(all(0 <= row.low < row.high <= U64 and row.base <= row.low for row in rows), "Invalid interval record")
        need(all(rows[index - 1].high <= row.low for index, row in enumerate(rows) if index), "Intervals overlap or are unsorted")
        endpoints = {0, 1 << 64}
        for view in views:
            endpoints.update(source_interval(view, width))
        for row in rows:
            endpoints.update((row.low, row.high))
        endpoints = sorted(endpoints)
        maximum_steps = 0
        for low, high in zip(endpoints, endpoints[1:]):
            need(legacy_identity(views, low, width) == interval_identity(rows, low),
                 f"Interval selection differs from source identity at a width-{width} domain cell")
            need(high > low, "Empty domain cell")
            for address in (low, high - 1):
                selected, steps = binary_identity(rows, address)
                need(selected == legacy_identity(views, address, width), "Binary search changes original typed-root selection")
                maximum_steps = max(maximum_steps, steps)
        certificates[width] = {"complete_uint64_selection_domain": True,
                               "selection_cells": len(endpoints) - 1,
                               "maximum_binary_search_iterations": maximum_steps,
                               "identity_compared": ["StorageBuffer binding", "descriptor array element", "aligned guest base"],
                               "typed_index_rule": "uint32(address - aligned_base) >> log2(width)"}
    return certificates


def route(binding, element):
    need((2 <= binding <= 6 and element == 0) or (binding == 7 and 0 <= element < 3), "Unknown target descriptor root")
    return binding - 2 if binding < 7 else 5 + element


def pack_records(partitions):
    scalar, vector = partitions[4], partitions[16]
    blob = bytearray(struct.pack("<IIII", len(scalar), len(vector), 0, len(scalar)))
    for width in WIDTHS:
        for row in partitions[width]:
            blob.extend(RECORD.pack(row.low, row.high, row.base, row.binding, row.element,
                                    width, route(row.binding, row.element), 0, 0))
    return bytes(blob)


class Assembly:
    def __init__(self, text):
        self.lines = text.splitlines()
        self.definitions, self.functions, self.decorations = {}, {}, {}
        self.rows, current = [], None
        for number, line in enumerate(self.lines):
            parts = shlex.split(line, comments=True)
            if not parts:
                continue
            result = parts[0] if len(parts) > 2 and parts[1] == "=" else None
            opcode, args = (parts[2], parts[3:]) if result else (parts[0], parts[1:])
            row = (result, opcode, args, number)
            self.rows.append(row)
            if result:
                need(result not in self.definitions, "Duplicate SPIR-V ID")
                self.definitions[result] = row
            if opcode == "OpFunction":
                current = result; self.functions[current] = []
            if current is not None:
                self.functions[current].append(row)
            if opcode == "OpFunctionEnd":
                current = None
            if opcode == "OpDecorate" and len(args) == 3:
                self.decorations.setdefault(args[0], {})[args[1]] = args[2]

    def args(self, result, opcode=None):
        need(result in self.definitions, "Missing definition")
        _, actual, args, _ = self.definitions[result]
        need(opcode is None or opcode == actual, f"Expected {opcode}, saw {actual}")
        return args

    def constant(self, result):
        args = self.args(result, "OpConstant")
        return int(args[1], 0)

    def binding(self, variable):
        metadata = self.decorations.get(variable, {})
        need(metadata.get("DescriptorSet") == "0" and "Binding" in metadata, "Unexpected descriptor root")
        return int(metadata["Binding"])

    def uniform_layout(self, variable, stride):
        pointer, storage = self.args(variable, "OpVariable")
        pointer_storage, structure = self.args(pointer, "OpTypePointer")
        need(storage == pointer_storage == "Uniform", "Selector uses a non-uniform-buffer root")
        members = self.args(structure, "OpTypeStruct")
        need(len(members) == 1 and any(op == "OpMemberDecorate" and args == [structure, "0", "Offset", "0"]
             for _, op, args, _ in self.rows), "Uniform member layout differs")
        array = self.args(members[0], "OpTypeArray")
        need(array[0] == ("%8" if stride == 8 else "%7") and
             self.decorations.get(members[0], {}).get("ArrayStride") == str(stride), "Uniform element stride/type differs")


def discover_target(text):
    module = Assembly(text)
    need(module.args("%7", "OpTypeInt") == ["32", "0"] and
         module.args("%23", "OpTypeInt") == ["64", "0"] and
         module.args("%10", "OpTypeVector") == ["%7", "4"], "Reviewed scalar/vector type identity differs")
    helpers = []
    for function, rows in module.functions.items():
        physical = [row for row in rows if row[1] == "OpConvertUToPtr"]
        if not physical:
            continue
        parameters = [row for row in rows if row[1] == "OpFunctionParameter"]
        if len(parameters) != 1:
            continue  # Unreachable store helpers are retained byte-exact.
        result_type = module.args(function, "OpFunction")[0]
        if result_type not in ("%7", "%10"):
            continue  # Unreachable two-word load helper is retained byte-exact.
        width = 4 if result_type == "%7" else 16
        parameter = parameters[0][0]
        roots, selectors = [], []
        for result, opcode, args, number in rows:
            if opcode != "OpReturnValue":
                continue
            returned = args[0]
            if module.definitions[returned][1] != "OpLoad":
                continue
            loaded = module.args(returned, "OpLoad")
            pointer = module.args(loaded[1], "OpAccessChain")
            variable = pointer[1]
            if module.definitions.get(variable, (None, None))[1] != "OpVariable":
                continue  # Physical miss load is retained separately.
            binding = module.binding(variable)
            variable_type, storage = module.args(variable, "OpVariable")
            pointer_storage, structure = module.args(variable_type, "OpTypePointer")
            need(storage == pointer_storage == "StorageBuffer", "Hit changes storage class")
            if binding == 7:
                structure, array_count = module.args(structure, "OpTypeArray")
                need(module.constant(array_count) == 3, "Descriptor table count differs")
            members = module.args(structure, "OpTypeStruct")
            need(len(members) == 1 and module.args(members[0], "OpTypeRuntimeArray") == [result_type] and
                 module.decorations.get(members[0], {}).get("ArrayStride") == str(width), "Typed storage-array extent differs")
            need(any(op == "OpMemberDecorate" and a == [structure, "0", "Offset", "0"]
                     for _, op, a, _ in module.rows), "Storage array has a nonzero member offset")
            typed_index = module.args(pointer[-1], "OpShiftRightLogical")
            need(typed_index[0] == "%7" and module.constant(typed_index[2]) == width.bit_length() - 1, "Typed index shift differs")
            converted = module.args(typed_index[1], "OpUConvert")
            delta = module.args(converted[1], "OpISub")
            need(converted[0] == "%7" and delta[0] == "%23" and delta[1] == parameter, "Hit does not truncate/floor the requested guest address")
            base = module.args(delta[2], "OpBitwiseAnd")
            need(module.constant(base[2]) == U64 - 15, "Storage alignment mask differs")
            address_load = module.args(module.args(base[1], "OpBitcast")[1], "OpLoad")
            uniform_access = module.args(address_load[1], "OpAccessChain")
            module.uniform_layout(uniform_access[1], 8)
            need(module.binding(uniform_access[1]) == 0 and module.constant(uniform_access[2]) == 0,
                 "Selector does not come from captured UBO0")
            if binding == 7:
                word = module.args(uniform_access[-1], "OpIAdd")
                byte_offset = module.constant(word[1]) * 8
                stride = module.args(word[2], "OpShiftLeftLogical")
                need(module.constant(stride[2]) == 1 and len(pointer) == 5, "Table selector/index structure differs")
                element = pointer[2]
                counter = module.args(element, "OpLoad")[1]
                need(module.args(stride[1], "OpLoad")[1] == counter, "Table read and descriptor use different counters")
                kind = "table"
            else:
                need(len(pointer) == 4 and module.constant(pointer[2]) == 0, "Single typed root structure differs")
                byte_offset = module.constant(uniform_access[-1]) * 8
                kind = "single"
            # Reconstruct the exact source-side typed endpoint and class rule,
            # rather than inferring it from a descriptor's binding number.
            hit_label = next(row[0] for row in reversed(rows) if row[3] < number and row[1] == "OpLabel")
            branches = [row for row in rows if row[1] == "OpBranchConditional" and row[2][1] == hit_label]
            need(len(branches) == 1, "Hit lacks a unique source-order condition")
            condition = module.args(branches[0][2][0], "OpLogicalAnd")
            lower = module.args(condition[1], "OpUGreaterThanEqual")
            upper = module.args(condition[2], "OpULessThan")
            need(lower[1:] == [parameter, delta[2]] and upper[1] == parameter, "Hit range bounds differ")
            endpoint = upper[2]
            if kind == "table":
                selected = module.args(endpoint, "OpSelect")
                zero_test = module.args(selected[1], "OpIEqual")
                need(module.constant(zero_test[2]) == 0, "Table zero-size test differs")
                bound_endpoint, endpoint = selected[2:]
            minimum = module.args(endpoint, "OpSelect")
            comparison = module.args(minimum[1], "OpULessThan")
            need(comparison[1:] == minimum[2:], "Source endpoint is not min(NVN end, typed bound)")
            bound_end, native_end = minimum[2:]
            if kind == "table":
                need(bound_endpoint == bound_end, "Zero-size table does not select typed bound")
            bound_add = module.args(bound_end, "OpIAdd")
            need(bound_add[1] == delta[2], "Typed bound uses a different aligned base")
            extended = module.args(bound_add[2], "OpUConvert")
            shifted_length = module.args(extended[1], "OpShiftLeftLogical")
            need(shifted_length[0] == "%7" and module.constant(shifted_length[2]) == width.bit_length() - 1,
                 "ArrayLength byte shift is not uint32 arithmetic")
            array_length = module.args(shifted_length[1], "OpArrayLength")
            need(array_length[0] == "%7" and array_length[2] == "0", "Typed length rule differs")
            if kind == "single":
                need(array_length[1] == variable, "Length is queried from a different typed root")
            else:
                structure_pointer = module.args(array_length[1], "OpAccessChain")
                need(structure_pointer[1:] == [variable, element], "Table length uses a different element")
            native_add = module.args(native_end, "OpIAdd")
            need(native_add[1] == base[1], "NVN endpoint does not use the unaligned guest base")
            size32 = module.args(native_add[2], "OpUConvert")[1]
            if kind == "single":
                size_pointer = module.args(module.args(size32, "OpLoad")[1], "OpAccessChain")
                module.uniform_layout(size_pointer[1], 4)
                need(module.binding(size_pointer[1]) == 0 and module.constant(size_pointer[-1]) * 4 == byte_offset + 8,
                     "Ordinary size word does not follow its guest pointer")
            else:
                extracted = module.args(size32, "OpCompositeExtract")
                need(extracted[2] == "0" and zero_test[1] == size32, "Table size word is not the low uint32 component")
                size_pointer = module.args(module.args(extracted[1], "OpLoad")[1], "OpAccessChain")
                module.uniform_layout(size_pointer[1], 8)
                size_word = module.args(size_pointer[-1], "OpIAdd")
                need(size_pointer[1] == uniform_access[1] and size_word[1] == uniform_access[-1] and
                     module.constant(size_word[2]) == 1, "Table size pair is not adjacent to its address pair")
            roots.append({"binding": binding, "variable": variable, "element_pointer_type": pointer[0]})
            selectors.append({"binding": binding, "byte_offset": byte_offset, "kind": kind})
        need([root["binding"] for root in roots] == [2, 3, 4, 5, 6, 7], "Expected five ordinary singles then one table")
        need([row["kind"] for row in selectors] == ["single"] * 5 + ["table"] and selectors[-1]["byte_offset"] == 2176,
             "Unexpected source descriptor classification")
        merges = [row for row in rows if row[1] == "OpLoopMerge"]
        need(len(merges) == 1, "Expected one original table loop")
        miss_label = merges[0][2][0]
        start = rows[0][3]; end = rows[-1][3] + 1
        miss_line = module.definitions[miss_label][3]
        helpers.append({"function": function, "parameter": parameter, "entry": next(row[0] for row in rows if row[1] == "OpLabel"),
                        "return_type": result_type, "width": width, "roots": roots, "selectors": selectors,
                        "start_line": start, "end_line": end, "miss_line": miss_line, "miss_label": miss_label,
                        "function_header": module.lines[start], "parameter_line": module.lines[parameters[0][3]]})
    need(sorted(helper["width"] for helper in helpers) == list(WIDTHS), "Reviewed reachable load helper set differs")
    need(helpers[0]["selectors"] == helpers[1]["selectors"], "Typed load helpers disagree on descriptor selector metadata")
    return module, sorted(helpers, key=lambda helper: helper["width"])


class Emitter:
    def __init__(self, module):
        self.next_id = max(int(key[1:]) for key in module.definitions) + 1
        self.constants, self.globals, self.annotations = {}, [], []

    def new(self):
        value = f"%{self.next_id}"; self.next_id += 1
        return value

    def constant(self, value):
        if value not in self.constants:
            result = self.new(); self.constants[value] = result
            self.globals.append(f"{result} = OpConstant %7 {value}")
        return self.constants[value]

    def layout(self):
        row, array, block, block_pointer, self.record_variable = [self.new() for _ in range(5)]
        self.u64_pointer = self.new()
        self.globals.extend([f"{row} = OpTypeStruct %23 %23 %23 %7 %7 %7 %7 %7 %7",
                             f"{array} = OpTypeRuntimeArray {row}",
                             f"{block} = OpTypeStruct %7 %7 %7 %7 {array}",
                             f"{block_pointer} = OpTypePointer StorageBuffer {block}",
                             f"{self.u64_pointer} = OpTypePointer StorageBuffer %23",
                             f"{self.record_variable} = OpVariable {block_pointer} StorageBuffer"])
        self.annotations.extend([f"OpDecorate {array} ArrayStride 48", f"OpDecorate {block} Block",
                                 f"OpDecorate {self.record_variable} DescriptorSet 0",
                                 f"OpDecorate {self.record_variable} Binding {RECORD_BINDING}",
                                 f"OpDecorate {self.record_variable} NonWritable"])
        for index, offset in enumerate((0, 8, 16, 24, 28, 32, 36, 40, 44)):
            self.annotations.append(f"OpMemberDecorate {row} {index} Offset {offset}")
        for index, offset in enumerate((0, 4, 8, 12, 16)):
            self.annotations.append(f"OpMemberDecorate {block} {index} Offset {offset}")

    def helper(self, original, helper):
        lines = [helper["function_header"], helper["parameter_line"], f"{helper['entry']} = OpLabel"]
        def op(opcode, *args, result=False):
            identifier = self.new() if result else None
            lines.append((identifier + " = " if identifier else "") + opcode + " " + " ".join(map(str, args)))
            return identifier
        zero, one = self.constant(0), self.constant(1)
        lower, upper, found = [self.new() for _ in range(3)]
        for variable in (lower, upper, found):
            lines.append(f"{variable} = OpVariable %110 Function")
        width_index = 0 if helper["width"] == 4 else 1
        def header(index):
            pointer = op("OpAccessChain", "%54", self.record_variable, self.constant(index), result=True)
            return op("OpLoad", "%7", pointer, result=True)
        count, start = header(width_index), header(width_index + 2)
        op("OpStore", lower, zero); op("OpStore", upper, count); op("OpStore", found, self.constant(U32))
        loop, check, body, left, right_test, right, hit, continuation, done = [self.new() for _ in range(9)]
        op("OpBranch", loop)
        lines.append(f"{loop} = OpLabel"); op("OpLoopMerge", done, continuation, "DontUnroll"); op("OpBranch", check)
        lines.append(f"{check} = OpLabel")
        lo = op("OpLoad", "%7", lower, result=True); hi = op("OpLoad", "%7", upper, result=True)
        active = op("OpULessThan", "%2", lo, hi, result=True); op("OpBranchConditional", active, body, done)
        lines.append(f"{body} = OpLabel")
        distance = op("OpISub", "%7", hi, lo, result=True)
        half = op("OpShiftRightLogical", "%7", distance, one, result=True)
        middle = op("OpIAdd", "%7", lo, half, result=True)
        row_index = op("OpIAdd", "%7", start, middle, result=True)
        def field(index, row, wide=False):
            pointer = op("OpAccessChain", self.u64_pointer if wide else "%54", self.record_variable,
                         self.constant(4), row, self.constant(index), result=True)
            return op("OpLoad", "%23" if wide else "%7", pointer, result=True)
        low, high = field(0, row_index, True), field(1, row_index, True)
        below = op("OpULessThan", "%2", helper["parameter"], low, result=True)
        decide_merge = self.new()
        op("OpSelectionMerge", decide_merge, "None"); op("OpBranchConditional", below, left, right_test)
        lines.append(f"{left} = OpLabel"); op("OpStore", upper, middle); op("OpBranch", decide_merge)
        lines.append(f"{right_test} = OpLabel")
        beyond = op("OpUGreaterThanEqual", "%2", helper["parameter"], high, result=True)
        right_merge = self.new()
        op("OpSelectionMerge", right_merge, "None"); op("OpBranchConditional", beyond, right, hit)
        lines.append(f"{right} = OpLabel")
        after = op("OpIAdd", "%7", middle, one, result=True)
        op("OpStore", lower, after); op("OpBranch", right_merge)
        lines.append(f"{hit} = OpLabel"); op("OpStore", found, row_index); op("OpBranch", done)
        lines.append(f"{right_merge} = OpLabel"); op("OpBranch", decide_merge)
        lines.append(f"{decide_merge} = OpLabel"); op("OpBranch", continuation)
        lines.append(f"{continuation} = OpLabel"); op("OpBranch", loop)
        lines.append(f"{done} = OpLabel")
        chosen = op("OpLoad", "%7", found, result=True)
        exists = op("OpINotEqual", "%2", chosen, self.constant(U32), result=True)
        route_block, lookup_merge, route_merge = [self.new() for _ in range(3)]
        op("OpSelectionMerge", lookup_merge, "None"); op("OpBranchConditional", exists, route_block, lookup_merge)
        lines.append(f"{route_block} = OpLabel")
        base, selected_route = field(2, chosen, True), field(6, chosen)
        delta = op("OpISub", "%23", helper["parameter"], base, result=True)
        truncated = op("OpUConvert", "%7", delta, result=True)
        index = op("OpShiftRightLogical", "%7", truncated, self.constant(helper["width"].bit_length() - 1), result=True)
        cases = [self.new() for _ in range(8)]
        op("OpSelectionMerge", route_merge, "None")
        op("OpSwitch", selected_route, route_merge, *[part for number, label in enumerate(cases) for part in (number, label)])
        for number, label in enumerate(cases):
            root = helper["roots"][number] if number < 5 else helper["roots"][-1]
            lines.append(f"{label} = OpLabel")
            operands = [root["variable"], zero, index] if number < 5 else [root["variable"], self.constant(number - 5), zero, index]
            pointer = op("OpAccessChain", root["element_pointer_type"], *operands, result=True)
            value = op("OpLoad", helper["return_type"], pointer, result=True)
            op("OpReturnValue", value)
        lines.append(f"{route_merge} = OpLabel"); op("OpBranch", lookup_merge)
        lines.append(f"{lookup_merge} = OpLabel"); op("OpBranch", helper["miss_label"])
        lines.extend(original.lines[helper["miss_line"]:helper["end_line"]])
        return lines


def transform_assembly(text):
    module, helpers = discover_target(text)
    emitter = Emitter(module); emitter.layout()
    replacements = {helper["start_line"]: (helper["end_line"], emitter.helper(module, helper)) for helper in helpers}
    removed = set()
    for helper in helpers:
        keep = {helper["function"], helper["parameter"], helper["entry"]}
        removed.update(result for result, _, _, line in module.functions[helper["function"]]
                       if helper["start_line"] <= line < helper["miss_line"] and result not in keep and result is not None)
    lines, index = [], 0
    annotation_insert = next(number for _, opcode, _, number in module.rows if opcode.startswith("OpType"))
    global_insert = min(row[3] for rows in module.functions.values() for row in rows)
    while index < len(module.lines):
        if index == annotation_insert:
            lines.extend(emitter.annotations)
        if index == global_insert:
            lines.extend(emitter.globals)
        if index in replacements:
            index, replacement = replacements[index]
            lines.extend(replacement); continue
        line = module.lines[index]
        identifiers = set(re.findall(r"%\d+", line))
        if identifiers & removed:
            need(line.startswith(("OpName ", "OpDecorate ")), "Removed helper value escapes its original function")
        else:
            if line.startswith("OpEntryPoint GLCompute "):
                line += " " + emitter.record_variable
            lines.append(line)
        index += 1
    return "\n".join(lines) + "\n", helpers


def function_words(data):
    words = struct.unpack(f"<{len(data) // 4}I", data)
    functions, current, body = {}, None, []
    at = 5
    while at < len(words):
        count, opcode = words[at] >> 16, words[at] & 65535
        need(count and at + count <= len(words), "Invalid binary instruction framing")
        row = words[at:at + count]
        if opcode == 54:
            current, body = row[2], []
        if current is not None:
            body.extend(row)
        if opcode == 56:
            functions[current] = tuple(body); current = None
        at += count
    return functions


def verify_preservation(original, candidate, helpers):
    old_header, new_header = struct.unpack_from("<5I", original), struct.unpack_from("<5I", candidate)
    need(old_header[0:2] == new_header[0:2] and old_header[4] == new_header[4], "SPIR-V version/magic/schema changed")
    left, right = function_words(original), function_words(candidate)
    modified = {int(helper["function"][1:]) for helper in helpers}
    need(set(left) == set(right), "Function inventory changed")
    preserved = []
    for function in left.keys() - modified:
        need(left[function] == right[function], "A store, main, work, image or untouched helper instruction changed")
        preserved.append({"function_id": function,
                          "sha256": hashlib.sha256(struct.pack(f"<{len(left[function])}I", *left[function])).hexdigest()})
    suffixes = []
    for helper in helpers:
        function, label = int(helper["function"][1:]), int(helper["miss_label"][1:])
        def suffix(words):
            at = 0
            while at < len(words):
                count, op = words[at] >> 16, words[at] & 65535
                if op == 248 and words[at + 1] == label:
                    return words[at:]
                at += count
            raise ValueError("Original physical MISS label disappeared")
        need(suffix(left[function]) == suffix(right[function]), "Original physical MISS path changed")
        suffixes.append({"width": helper["width"], "unchanged_binary_words": len(suffix(left[function]))})
    def global_rows(data):
        words = struct.unpack(f"<{len(data) // 4}I", data); at = 5; rows = []
        while at < len(words):
            count, opcode = words[at] >> 16, words[at] & 65535
            if opcode == 54:
                break
            rows.append(tuple(words[at:at + count])); at += count
        return rows, words[3]
    old_globals, original_bound = global_rows(original)
    new_globals, _ = global_rows(candidate)
    extra, retained = [], []
    for row in new_globals:
        opcode = row[0] & 65535
        result = row[1] if 19 <= opcode <= 38 else row[2] if opcode in (41, 42, 43, 44, 59) else None
        annotation_target = row[1] if opcode in (5, 71, 72) else None
        if (result is not None and result >= original_bound) or (annotation_target is not None and annotation_target >= original_bound):
            extra.append(opcode)
        else:
            retained.append(row)
    need(len(old_globals) == len(retained), "Original global instruction inventory changed")
    entry_changes = 0
    for old, new in zip(old_globals, retained):
        if old == new:
            continue
        need(old[0] & 65535 == 15 and new[0] & 65535 == 15 and old[1:] == new[1:-1] and new[-1] >= original_bound,
             "Original capability/type/decoration/execution mode/global changed")
        entry_changes += 1
    need(entry_changes == 1, "Expected one added readonly entry interface")
    return {"untouched_functions": sorted(preserved, key=lambda row: row["function_id"]),
            "physical_miss_suffixes": suffixes,
            "modified_functions": sorted(modified),
            "header_changes": {"generator_provenance_changed": old_header[2] != new_header[2],
                               "id_bound": [old_header[3], new_header[3]], "version_and_schema_unchanged": True},
            "added_global_instruction_opcodes": dict(sorted(Counter(extra).items())),
            "original_globals_preserved_except_added_entry_interface": True,
            "categories": ["two load-selector prefixes replaced", "one readonly binding12 added",
                "new independent record types/constants/annotations", "record interface added to main entry declaration"],
            "all_other_function_instruction_words_identical": True}


def audit_lookup_shape(text, helpers, partitions):
    """Re-read emitted instructions and establish the bounded-search recurrence."""
    module = Assembly(text)
    proofs = []
    for helper in helpers:
        rows = module.functions[helper["function"]]
        blocks, block = {}, None
        for row in rows:
            if row[1] == "OpFunctionEnd":
                continue
            if row[1] == "OpLabel":
                block = row[0]; blocks[block] = []
            if block is not None:
                blocks[block].append(row)
        loops = [(label, row) for label, content in blocks.items() for row in content if row[1] == "OpLoopMerge"]
        need(len(loops) == 1, "Candidate does not contain one bounded lookup loop")
        header, merge_row = loops[0]; merge, continuation, control = merge_row[2]
        need(control == "DontUnroll", "Candidate search unroll policy differs")
        header_branch = blocks[header][-1]
        need(header_branch[1] == "OpBranch", "Lookup header is not a single edge")
        check = header_branch[2][0]; branch = blocks[check][-1]
        need(branch[1] == "OpBranchConditional" and branch[2][2] == merge, "Lookup false edge does not leave loop")
        condition = module.args(branch[2][0], "OpULessThan")
        need(condition[0] == "%2", "Lookup bound comparison type differs")
        low_load, high_load = condition[1:]
        low_pointer = module.args(low_load, "OpLoad")[1]
        high_pointer = module.args(high_load, "OpLoad")[1]
        for variable in (low_pointer, high_pointer):
            need(module.args(variable, "OpVariable") == ["%110", "Function"], "Search counter is not private uint32 storage")
        middle_candidates = []
        for result, op, args, _ in rows:
            if op != "OpIAdd" or args[0] != "%7" or args[1] != low_load:
                continue
            _, half = args[1:]
            shifted = module.args(half, "OpShiftRightLogical")
            need(shifted[0] == "%7" and module.constant(shifted[2]) == 1, "Search midpoint is not a halved uint32 interval")
            need(module.args(shifted[1], "OpISub") == ["%7", high_load, low_load], "Midpoint span differs from high-low")
            middle_candidates.append(result)
        need(len(middle_candidates) == 1, "Search midpoint is not unique")
        middle = middle_candidates[0]
        stores = {variable: [row[2][1] for row in rows if row[1] == "OpStore" and row[2][0] == variable]
                  for variable in (low_pointer, high_pointer)}
        need(len(stores[low_pointer]) == len(stores[high_pointer]) == 2, "Counter has unexpected writers")
        initial_low, next_low = stores[low_pointer]
        initial_high, next_high = stores[high_pointer]
        need(module.constant(initial_low) == 0 and next_high == middle, "Search initialization/left update differs")
        increment = module.args(next_low, "OpIAdd")
        need(increment[0:2] == ["%7", middle] and module.constant(increment[2]) == 1, "Right update is not mid+1")
        count_pointer = module.args(initial_high, "OpLoad")[1]
        count_access = module.args(count_pointer, "OpAccessChain")
        index = 0 if helper["width"] == 4 else 1
        need(module.binding(count_access[1]) == RECORD_BINDING and module.constant(count_access[2]) == index,
             "Initial high does not use the width-specific frozen count")
        need(len(partitions[helper["width"]]) <= 64, "Search counter bound can wrap")
        graph = {}
        for label, content in blocks.items():
            _, terminal, args, _ = content[-1]
            if terminal == "OpBranch":
                graph[label] = args
            elif terminal == "OpBranchConditional":
                graph[label] = args[1:3]
            elif terminal == "OpSwitch":
                graph[label] = [args[1], *args[3::2]]
            else:
                need(terminal in ("OpReturn", "OpReturnValue", "OpKill", "OpUnreachable"), "Unknown candidate control-flow terminator")
                graph[label] = []
        need(graph[continuation] == [header], "Search backedge differs")
        graph[continuation] = []
        incoming = {label: 0 for label in graph}
        for targets in graph.values():
            for target in targets:
                need(target in incoming, "Unknown search control target"); incoming[target] += 1
        pending = [label for label, count in incoming.items() if count == 0]; consumed = 0
        while pending:
            label = pending.pop(); consumed += 1
            for target in graph[label]:
                incoming[target] -= 1
                if incoming[target] == 0:
                    pending.append(target)
        need(consumed == len(graph), "Candidate has a cycle outside the reviewed binary-search recurrence")
        proofs.append({"width": helper["width"], "initial_low": 0,
                       "initial_high": len(partitions[helper["width"]]), "counter_bits": 32,
                       "midpoint": "low + ((high-low)>>1)", "left_update": "high=mid", "right_update": "low=mid+1",
                       "other_control_flow_cycles": False})
    return proofs


def build(spirv, manifest, output):
    data = spirv.read_bytes(); need(hashlib.sha256(data).hexdigest() == TARGET, "Unreviewed original module digest")
    original_access_contract = inspect_module(data)
    output = private_output(output); output.mkdir(parents=True)
    def command(name, args):
        run = subprocess.run(args, capture_output=True, text=True, timeout=60)
        (output / (name + ".stdout")).write_text(run.stdout)
        (output / (name + ".stderr")).write_text(run.stderr)
        need(run.returncode == 0, f"{name} failed; inspect private diagnostics")
        return run.stdout
    command("original-val", ["spirv-val", "--target-env", "vulkan1.3", "--uniform-buffer-standard-layout", str(spirv)])
    assembly = command("original-dis", ["spirv-dis", "--raw-id", "--no-indent", str(spirv)])
    candidate, helpers = transform_assembly(assembly)
    views, frozen = frozen_views(manifest, helpers[0]["selectors"])
    partitions = build_partitions(views)
    certificate = certify_partitions(views, partitions)
    records = pack_records(partitions)
    (output / "records.bin").write_bytes(records)
    (output / "original.spv").write_bytes(data)
    (output / "candidate.spvasm").write_text(candidate)
    command("candidate-as", ["spirv-as", "--target-env", "vulkan1.3", "--preserve-numeric-ids", str(output / "candidate.spvasm"), "-o", str(output / "candidate.spv")])
    command("candidate-val", ["spirv-val", "--target-env", "vulkan1.3", "--uniform-buffer-standard-layout", str(output / "candidate.spv")])
    candidate_data = (output / "candidate.spv").read_bytes()
    preservation = verify_preservation(data, candidate_data, helpers)
    finite_proof = audit_lookup_shape(candidate, helpers, partitions)
    candidate_digest = hashlib.sha256(candidate_data).hexdigest()
    report = {"schema": "open-ocarina.compute-interval-lowering/v1", "execution_admitted": False,
        "original_sha256": TARGET, "candidate_sha256": candidate_digest,
        "original_access_contract": original_access_contract,
        "tool_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "source_auditor_sha256": hashlib.sha256(Path(inspect_module.__code__.co_filename).read_bytes()).hexdigest(),
        "record_sha256": hashlib.sha256(records).hexdigest(), "record_bytes": len(records),
        "frozen_inputs": frozen, "views": [asdict(view) for view in views],
        "intervals": {str(width): [asdict(row) for row in partitions[width]] for width in WIDTHS},
        "selection_proof": certificate, "preservation": preservation,
        "layout_plan": {"added_binding": {"set": 0, "binding": RECORD_BINDING, "type": "readonly StorageBuffer", "count": 1},
            "header": {"bytes": 16, "u32_fields": ["count4", "count16", "start4", "start16"]},
            "row_stride": 48, "row_fields": ["low:u64", "high:u64", "base:u64", "binding:u32", "element:u32", "width:u32", "route_id:u32", "reserved0:u32", "reserved1:u32"],
            "existing_push_bytes_unchanged": True, "new_push_bytes": 0,
            "pointer_return_or_phi_added": False, "nonuniform_descriptor_index_added": False,
            "requires_additional_device_features": False, "additional_descriptor_capacity_check_required": True,
            "record_buffer_must_not_alias_existing_graph": True},
        "finite_lookup": {"maximum_rows_per_width": 64, "captured_counts": {str(width): len(partitions[width]) for width in WIDTHS},
            "emitted_instruction_audit": finite_proof,
            "method": "lower/upper binary search; left=mid or low=mid+1, high-low strictly decreases on non-hit iterations",
            "maximum_iterations_under_valid_header": 7, "valid_header_frozen_by_record_digest": True},
        "width_policy_metadata": {str(width): {"spirv_sha256": candidate_digest, "requested_subgroup_size": width, "compiled": False, "executed": False} for width in (32, 16, 8)},
        "limitations": ["Owned frozen graph only; no live as-submitted descriptor atomicity claim", "No Vulkan calls, pipeline compilation, shader execution or performance measurement", "Old replay executor does not support the added binding/layout; this bundle is not execution admission", "All original store/main/image/work instructions and physical MISS suffixes remain binary-word identical"]}
    (output / "report.json").write_text(json.dumps(report, indent=2) + "\n")
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--spirv", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    try:
        report = build(args.spirv, args.manifest, args.output)
        print(json.dumps({"candidate_sha256": report["candidate_sha256"], "record_sha256": report["record_sha256"],
                          "record_bytes": report["record_bytes"], "execution_admitted": False}, indent=2))
    except (ValueError, OSError, KeyError, IndexError, subprocess.TimeoutExpired) as exc:
        print(str(exc), file=sys.stderr); return 1
    return 0


def artifact(directory, record):
    need(isinstance(record, dict) and set(record) == {"path", "size", "sha256"}, "Incomplete frozen artifact identity")
    name = Path(record["path"])
    need(not name.is_absolute() and ".." not in name.parts, "Artifact path escapes owned graph")
    path = directory / name
    need(not path.is_symlink() and path.is_file() and path.resolve().is_relative_to(directory.resolve()), "Artifact is not a contained ordinary file")
    need(path.stat().st_size == record["size"], "Frozen artifact extent differs")
    with path.open("rb") as source:
        need(hashlib.file_digest(source, "sha256").hexdigest() == record["sha256"], "Frozen artifact digest differs")
    return path


def frozen_views(manifest_path, selectors):
    directory = manifest_path.resolve().parent
    def unique(pairs):
        result = {}
        for key, value in pairs:
            need(key not in result, "Duplicate frozen manifest key")
            result[key] = value
        return result
    manifest = json.loads(manifest_path.read_text(), object_pairs_hook=unique)
    need(manifest.get("schema") == "open-ocarina.physical-compute-replay/v1" and
         manifest.get("kind") == "target" and manifest.get("mode") == "frozen-diagnostic", "Only normalized owned frozen target graphs are supported")
    pipeline = manifest["pipeline"]
    need((pipeline["program"], pipeline["variant"], pipeline["spirv"]["sha256"]) ==
         ("cc62764061c44f6a", "095931037888515a", TARGET), "Frozen target identity differs")
    allocations = {row["id"]: row for row in manifest["allocations"]}
    views = {row["id"]: row for row in manifest["views"]}
    need(len(allocations) == len(manifest["allocations"]) and len(views) == len(manifest["views"]), "Duplicate allocation/view identity")
    bindings = {(row["binding"], row["element"]): row for row in manifest["bindings"]}
    need(len(bindings) == len(manifest["bindings"]) and (RECORD_BINDING, 0) not in bindings, "Duplicate or conflicting record binding")
    ubo = bindings[(0, 0)]; need(ubo["type"] == 6 and ubo["range"] == 2224 and ubo["view"] is not None, "Complete 2224-byte UBO0 is required")
    view = views[ubo["view"]]; allocation = allocations[view["allocation"]]
    need(0 <= view["offset"] and 0 < view["size"] and view["offset"] + view["size"] <= allocation["size"] and
         0 <= ubo["offset"] and allocation["size"] <= 16 << 20, "Unsupported UBO owner extent")
    need(view["offset"] + ubo["offset"] + ubo["range"] <= allocation["size"] and ubo["offset"] + ubo["range"] <= view["size"], "UBO0 range exceeds its captured owner")
    path = artifact(directory, allocation["initial"])
    payload = path.read_bytes()
    need(len(payload) == allocation["size"] and hashlib.sha256(payload).hexdigest() == allocation["initial"]["sha256"], "UBO owner changed during read")
    offset = view["offset"] + ubo["offset"]; words = payload[offset:offset + ubo["range"]]
    written = bindings[(6, 0)]
    need(written["view"] is not None and views[written["view"]]["allocation"] != view["allocation"],
         "Readonly interval selectors may alias this shader's writable allocation")
    result = []
    for selector in selectors:
        count = 3 if selector["kind"] == "table" else 1
        for element in range(count):
            offset = selector["byte_offset"] + 16 * element
            need(offset + 12 <= len(words), "Selector is outside captured UBO0")
            guest, size_word = struct.unpack_from("<QI", words, offset)
            binding = bindings[(selector["binding"], element)]
            need(binding["type"] == 7 and type(binding["range"]) is int and binding["range"] >= 0,
                 "Unknown/non-storage descriptor range")
            if binding["view"] is None:
                need(binding["range"] == 0 and binding["offset"] == 0, "Null binding has a nonempty range")
            else:
                bound_view = views[binding["view"]]
                need(binding["offset"] >= 0 and binding["offset"] + binding["range"] <= bound_view["size"], "Storage descriptor exceeds its exact VkBuffer view")
                need(0 <= bound_view["offset"] and bound_view["offset"] + bound_view["size"] <= allocations[bound_view["allocation"]]["size"],
                     "Storage descriptor view exceeds its canonical allocation")
            result.append(View(guest, size_word, binding["range"], selector["binding"], element, selector["kind"], offset))
    return result, {"manifest_sha256": hashlib.sha256(manifest_path.read_bytes()).hexdigest(),
                    "ubo0_allocation_sha256": allocation["initial"]["sha256"],
                    "ubo0_visible_sha256": hashlib.sha256(words).hexdigest(),
                    "ubo0_visible_bytes": len(words),
                    "storage_binding_records": [bindings[(row.binding, row.element)] for row in result],
                    "scope": "Frozen owned descriptor-visible bytes/ranges only; no claim of live as-submitted atomicity"}


if __name__ == "__main__":
    raise SystemExit(main())
