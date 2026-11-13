#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""CPU-only interval lowering for the private d8 mesh module.

The transformation reads one pinned SPIR-V binary, proves which functions are
global-hit load cascades, and replaces only those reachable cascades with a
width-specific interval-record binary search. Miss paths, store helpers,
unreachable phases, and every other function stay binary-word identical.
No Vulkan, device, pipeline, or game call is made.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import struct
import subprocess
import sys
from dataclasses import asdict, dataclass
from pathlib import Path

TARGET = "835aa59799c6ecb621034d946e2fa4eadb1aa2407d293321ae3e3052882bc9b3"
PROGRAM = "d8e59341cf4f629c"
U32 = (1 << 32) - 1
U64 = (1 << 64) - 1
RECORD = struct.Struct("<QQQIIIIII")

OP_ENTRY = 15
OP_TYPE_VOID = 19
OP_TYPE_BOOL = 20
OP_TYPE_INT = 21
OP_TYPE_VECTOR = 23
OP_TYPE_ARRAY = 28
OP_TYPE_RUNTIME_ARRAY = 29
OP_TYPE_STRUCT = 30
OP_TYPE_POINTER = 32
OP_CONSTANT = 43
OP_CONSTANT_NULL = 46
OP_FUNCTION = 54
OP_PARAMETER = 55
OP_FUNCTION_END = 56
OP_FUNCTION_CALL = 57
OP_VARIABLE = 59
OP_LOAD = 61
OP_STORE = 62
OP_ACCESS_CHAIN = 65
OP_ARRAY_LENGTH = 68
OP_DECORATE = 71
OP_MEMBER_DECORATE = 72
OP_COMPOSITE_EXTRACT = 81
OP_BITCAST = 124
OP_IADD = 128
OP_ISUB = 130
OP_SHIFT_RIGHT = 194
OP_SHIFT_LEFT = 196
OP_BITWISE_AND = 199
OP_LOGICAL_AND = 167
OP_IEQUAL = 170
OP_INOT_EQUAL = 171
OP_UGE = 174
OP_ULT = 176
OP_SELECT = 169
OP_LOOP_MERGE = 246
OP_SELECTION_MERGE = 247
OP_LABEL = 248
OP_BRANCH = 249
OP_BRANCH_COND = 250
OP_SWITCH = 251
OP_RETURN = 253
OP_RETURN_VALUE = 254

FUNCTION_STORAGE = 7
UNIFORM = 2
STORAGE_BUFFER = 12
DECOR_BLOCK = 2
DECOR_ARRAY_STRIDE = 6
DECOR_BINDING = 33
DECOR_SET = 34
DECOR_OFFSET = 35
DECOR_NONWRITABLE = 24


class LoweringError(ValueError):
    pass


def need(condition, message):
    if not condition:
        raise LoweringError(message)


@dataclass(frozen=True)
class Root:
    binding: int
    element: int
    kind: str
    variable: int
    pointer_type: int
    address_byte: int
    size_byte: int


@dataclass(frozen=True)
class Helper:
    function: int
    parameter: int
    entry: int
    miss_label: int
    result_type: int
    width: int
    shift: int
    calls: int
    roots: tuple
    start: int
    miss_at: int
    end: int


@dataclass(frozen=True)
class View:
    guest: int
    size_word: int
    bound_bytes: int
    binding: int
    element: int
    kind: str


@dataclass(frozen=True)
class Row:
    low: int
    high: int
    base: int
    binding: int
    element: int
    route: int


def source_interval(view, width):
    need(width in (4, 8, 16) and width & (width - 1) == 0, "Unsupported typed width")
    need(view.kind in ("single", "table"), "Unproved descriptor class")
    need(0 <= view.guest <= U64 and 0 <= view.size_word <= U32 and view.bound_bytes >= 0, "Invalid descriptor")
    shift = width.bit_length() - 1
    base = view.guest & (U64 ^ 15)
    elements = (view.bound_bytes // width) & U32
    typed_bytes = (elements << shift) & U32
    typed_end = (base + typed_bytes) & U64
    nvn_end = (view.guest + view.size_word) & U64
    if view.kind == "table" and view.size_word == 0:
        end = typed_end
    else:
        end = typed_end if typed_end < nvn_end else nvn_end
    return base, end


def legacy_identity(views, address, width):
    need(0 <= address <= U64, "Address out of range")
    for view in views:
        aligned, end = source_interval(view, width)
        if aligned <= address < end:
            return view.binding, view.element, aligned
    return None


def build_rows(views, width):
    claims = []
    for route, view in enumerate(views):
        low, high = source_interval(view, width)
        if low < high:
            claims.append((low, high, view, route))
    points = sorted({point for low, high, _view, _route in claims for point in (low, high)})
    rows = []
    for left, right in zip(points, points[1:]):
        owners = [item for item in claims if item[0] <= left < item[1]]
        if not owners:
            continue
        _low, _high, view, route = owners[0]
        base = view.guest & (U64 ^ 15)
        row = Row(left, right, base, view.binding, view.element, route)
        if rows and rows[-1].high == left and (
            rows[-1].base, rows[-1].binding, rows[-1].element, rows[-1].route
        ) == (row.base, row.binding, row.element, row.route):
            previous = rows[-1]
            rows[-1] = Row(previous.low, right, previous.base, previous.binding, previous.element, previous.route)
        else:
            rows.append(row)
    need(len(rows) <= 64, "Interval count can wrap the uint32 search counter")
    for index, row in enumerate(rows):
        need(0 <= row.low < row.high <= U64 and row.base <= row.low, "Invalid interval")
        if index:
            need(rows[index - 1].high <= row.low, "Intervals overlap")
    return rows


def binary_identity(rows, address):
    low, high, steps = 0, len(rows), 0
    while low < high:
        steps += 1
        need(steps <= 7, "Binary search exceeded the finite bound")
        middle = low + ((high - low) >> 1)
        row = rows[middle]
        if address < row.low:
            high = middle
        elif address >= row.high:
            low = middle + 1
        else:
            return (row.binding, row.element, row.base), steps
    return None, steps


def certify(views, partitions, widths):
    certificates = {}
    for width in widths:
        rows = partitions[width]
        endpoints = {0, 1 << 64}
        for view in views:
            endpoints.update(source_interval(view, width))
        for row in rows:
            endpoints.update((row.low, row.high))
        endpoints = sorted(endpoints)
        maximum = 0
        for left, right in zip(endpoints, endpoints[1:]):
            need(right > left, "Empty selection cell")
            for address in (left, right - 1):
                expected = legacy_identity(views, address, width)
                selected, steps = binary_identity(rows, address)
                need(selected == expected, "Binary search changes a proved root")
                maximum = max(maximum, steps)
        certificates[width] = {
            "selection_cells": len(endpoints) - 1,
            "rows": len(rows),
            "maximum_binary_steps": maximum,
        }
    return certificates


class Module:
    def __init__(self, data):
        need(len(data) % 4 == 0 and len(data) >= 20, "SPIR-V is truncated")
        words = struct.unpack(f"<{len(data) // 4}I", data)
        need(words[0] == 0x07230203 and words[4] == 0, "Unexpected SPIR-V magic or schema")
        self.words = words
        self.bound = words[3]
        self.instructions = []
        at = 5
        while at < len(words):
            count = words[at] >> 16
            opcode = words[at] & 0xFFFF
            need(count and at + count <= len(words), "Broken SPIR-V instruction")
            self.instructions.append(list(words[at:at + count]))
            at += count
        result_at_second = {OP_CONSTANT, OP_CONSTANT_NULL, OP_FUNCTION, OP_PARAMETER, OP_VARIABLE, OP_LOAD,
                            OP_ACCESS_CHAIN, OP_ARRAY_LENGTH, OP_BITCAST, OP_IADD, OP_ISUB, OP_SHIFT_RIGHT,
                            OP_SHIFT_LEFT, OP_BITWISE_AND, OP_LOGICAL_AND, OP_IEQUAL, OP_INOT_EQUAL, OP_UGE,
                            OP_ULT, OP_SELECT, 113}
        self.by_id = {}
        for inst in self.instructions:
            opcode = inst[0] & 0xFFFF
            if opcode in (OP_TYPE_VOID, OP_TYPE_BOOL, OP_LABEL):
                result = inst[1]
            elif 21 <= opcode <= 38 or opcode in result_at_second:
                result = inst[2] if opcode in result_at_second else inst[1]
            else:
                result = None
            if result is not None:
                self.by_id[result] = inst
        self.binding = {}
        self.descriptor_set = {}
        self.array_stride = {}
        self.member_offset = {}
        for inst in self.instructions:
            opcode = inst[0] & 0xFFFF
            if opcode == OP_DECORATE and len(inst) >= 4 and inst[2] == DECOR_BINDING:
                self.binding[inst[1]] = inst[3]
            elif opcode == OP_DECORATE and len(inst) >= 4 and inst[2] == DECOR_SET:
                self.descriptor_set[inst[1]] = inst[3]
            elif opcode == OP_DECORATE and len(inst) >= 4 and inst[2] == DECOR_ARRAY_STRIDE:
                self.array_stride[inst[1]] = inst[3]
            elif opcode == OP_MEMBER_DECORATE and len(inst) >= 5 and inst[3] == DECOR_OFFSET:
                self.member_offset[(inst[1], inst[2])] = inst[4]

    def opcode(self, inst):
        return inst[0] & 0xFFFF

    def const_u32(self, ident):
        inst = self.by_id.get(ident)
        need(inst is not None and self.opcode(inst) == OP_CONSTANT and inst[1] == self.uint(32), "Expected a uint32 constant")
        return inst[3]

    def uint(self, bits):
        for inst in self.instructions:
            if self.opcode(inst) == OP_TYPE_INT and inst[2] == bits and inst[3] == 0:
                return inst[1]
        raise LoweringError(f"Missing uint{bits}")

    def vector(self, element, count):
        for inst in self.instructions:
            if self.opcode(inst) == OP_TYPE_VECTOR and inst[2] == element and inst[3] == count:
                return inst[1]
        return None

    def functions(self):
        groups = []
        current = None
        for index, inst in enumerate(self.instructions):
            opcode = self.opcode(inst)
            if opcode == OP_FUNCTION:
                current = [index]
            elif opcode == OP_FUNCTION_END and current is not None:
                current.append(index)
                groups.append(tuple(current))
                current = None
        need(current is None, "Unclosed function")
        return groups

    def calls_of(self, function):
        count = 0
        for inst in self.instructions:
            if self.opcode(inst) == OP_FUNCTION_CALL and inst[3] == function:
                count += 1
        return count

    def byte_index(self, variable, index_id):
        inst = self.by_id[variable]
        need(self.opcode(inst) == OP_VARIABLE and inst[3] == UNIFORM, "Selector is not a uniform variable")
        pointer = self.by_id[inst[1]]
        need(self.opcode(pointer) == OP_TYPE_POINTER and pointer[2] == UNIFORM, "Selector pointer class differs")
        struct = self.by_id[pointer[3]]
        need(self.opcode(struct) == OP_TYPE_STRUCT and len(struct) == 3, "Selector block is not one member")
        need(self.member_offset.get((struct[1], 0)) == 0, "Selector member offset is not zero")
        array = self.by_id[struct[2]]
        need(self.opcode(array) == OP_TYPE_ARRAY, "Selector member is not an array")
        stride = self.array_stride.get(array[1])
        need(stride in (1, 4, 8, 16), "Unproved selector stride")
        need(self.descriptor_set.get(variable) == 0 and variable in self.binding, "Selector binding is not set 0")
        return self.binding[variable], self.const_u32(index_id) * stride, stride


def _result(inst):
    return inst[2]


class Matcher:
    def __init__(self, module, insts):
        self.module = module
        self.insts = insts
        self.at = 0

    def peek(self):
        need(self.at < len(self.insts), "Helper ended early")
        return self.insts[self.at]

    def take(self, opcode):
        inst = self.peek()
        need(self.module.opcode(inst) == opcode, "Helper opcode sequence is not the proved cascade")
        self.at += 1
        return inst

    def id_of(self, inst):
        return inst[2] if self.module.opcode(inst) != OP_LABEL else inst[1]


def prove_load(module, start, end):
    insts = module.instructions[start:end + 1]
    cursor = Matcher(module, insts)
    function = cursor.take(OP_FUNCTION)
    parameter = cursor.take(OP_PARAMETER)
    entry = cursor.take(OP_LABEL)
    u32 = module.uint(32)
    u64 = module.uint(64)
    need(parameter[1] == u64 and len(function) >= 4, "Load helper parameter is not uint64")
    result_type = function[1]
    if result_type == u32:
        width = 4
    else:
        vector = module.by_id.get(result_type)
        need(vector is not None and module.opcode(vector) == OP_TYPE_VECTOR and vector[2] == u32 and vector[3] in (2, 4),
             "Load helper result is not a uint32 scalar or vector")
        width = 4 * vector[3]
    shift = width.bit_length() - 1
    counter_var = cursor.take(OP_VARIABLE)
    need(module.by_id[counter_var[1]][2] == FUNCTION_STORAGE and module.by_id[counter_var[1]][3] == u32,
         "Table counter is not function-local uint32")
    mask = None
    roots = []

    def take_single():
        nonlocal mask
        address = cursor.take(OP_ACCESS_CHAIN)
        size = cursor.take(OP_ACCESS_CHAIN)
        loaded_address = cursor.take(OP_LOAD)
        guest = cursor.take(OP_BITCAST)
        aligned = cursor.take(OP_BITWISE_AND)
        loaded_size = cursor.take(OP_LOAD)
        wide_size = cursor.take(113)  # OpUConvert
        nvn_end = cursor.take(OP_IADD)
        length = cursor.take(OP_ARRAY_LENGTH)
        shifted = cursor.take(OP_SHIFT_LEFT)
        wide_length = cursor.take(113)
        typed_end = cursor.take(OP_IADD)
        compare = cursor.take(OP_ULT)
        minimum = cursor.take(OP_SELECT)
        lower = cursor.take(OP_UGE)
        upper = cursor.take(OP_ULT)
        both = cursor.take(OP_LOGICAL_AND)
        cursor.take(OP_SELECTION_MERGE)
        branch = cursor.take(OP_BRANCH_COND)
        hit = cursor.take(OP_LABEL)
        delta = cursor.take(OP_ISUB)
        truncated = cursor.take(113)
        index = cursor.take(OP_SHIFT_RIGHT)
        chain = cursor.take(OP_ACCESS_CHAIN)
        value = cursor.take(OP_LOAD)
        cursor.take(OP_RETURN_VALUE)
        cursor.take(OP_LABEL)
        need(loaded_address[3] == _result(address) and guest[3] == _result(loaded_address), "Guest bitcast source differs")
        need(guest[1] == u64 and aligned[3] == _result(guest), "Aligned base is not the guest")
        and_mask = module.by_id[aligned[4]]
        need(module.opcode(and_mask) == OP_CONSTANT and and_mask[1] == u64, "Alignment mask is not uint64")
        mask_value = and_mask[3] | (and_mask[4] << 32)
        need(mask_value == (U64 ^ 15), "Alignment mask is not ~15")
        mask = mask_value
        binding, address_byte, address_stride = module.byte_index(address[3], address[5])
        size_binding, size_byte, size_stride = module.byte_index(size[3], size[5])
        need(binding == size_binding == 0 and address_stride == 8 and size_stride == 4, "Single selector layout is not the proved UBO0 pair")
        need(loaded_address[1] == module.vector(u32, 2), "Guest load is not one uint32 vector")
        need(loaded_size[3] == _result(size) and loaded_size[1] == u32, "Size word is not one uint32")
        need(wide_size[3] == _result(loaded_size) and nvn_end[3] == _result(guest) and nvn_end[4] == _result(wide_size),
             "NVN end is not guest + uint32 size")
        need(length[3] == chain[3] and length[4] == 0, "Array length root differs from the hit root")
        need(module.const_u32(shifted[4]) == shift and index[4] == shifted[4], "Typed shift is not log2(width)")
        need(compare[3] == _result(typed_end) and compare[4] == _result(nvn_end), "Single end is not min(typed, NVN)")
        need(minimum[3] == _result(compare) and upper[4] == _result(minimum), "Single upper bound differs")
        need(lower[3] == parameter[2] and lower[4] == _result(aligned), "Single lower bound is not the aligned base")
        need(upper[3] == parameter[2], "Single compare does not use the address")
        need(both[3] == _result(lower) and branch[1] == _result(both) and branch[2] == hit[1], "Single hit edge differs")
        need(delta[3] == parameter[2] and delta[4] == _result(aligned), "Typed index does not subtract the aligned base")
        need(truncated[1] == u32 and module.const_u32(chain[4]) == 0, "Single member index is not zero")
        need(chain[5] == _result(index) and value[3] == _result(chain) and value[1] == result_type, "Hit load type differs")
        variable = chain[3]
        need(module.descriptor_set.get(variable) == 0 and variable in module.binding, "Hit root has no set-0 binding")
        need(module.by_id[variable][3] == STORAGE_BUFFER, "Hit root is not StorageBuffer")
        return Root(module.binding[variable], 0, "single", variable, chain[1], address_byte, size_byte)

    while module.opcode(cursor.peek()) == OP_ACCESS_CHAIN:
        roots.append(take_single())
    need(roots, "No single storage claims were proved")
    stored = cursor.take(OP_STORE)
    need(stored[1] == counter_var[2] and module.const_u32(stored[2]) == 0, "Table counter does not start at zero")
    cursor.take(OP_BRANCH)
    header = cursor.take(OP_LABEL)
    merge = cursor.take(OP_LOOP_MERGE)
    need(merge[3] == 2, "Table loop is not DontUnroll")
    cursor.take(OP_BRANCH)
    check = cursor.take(OP_LABEL)
    counter = cursor.take(OP_LOAD)
    below = cursor.take(OP_ULT)
    cursor.take(OP_BRANCH_COND)
    body = cursor.take(OP_LABEL)
    scaled = cursor.take(OP_SHIFT_LEFT)
    base_index = cursor.take(OP_IADD)
    address = cursor.take(OP_ACCESS_CHAIN)
    size_index = cursor.take(OP_IADD)
    size_chain = cursor.take(OP_ACCESS_CHAIN)
    loaded_address = cursor.take(OP_LOAD)
    guest = cursor.take(OP_BITCAST)
    aligned = cursor.take(OP_BITWISE_AND)
    loaded_size = cursor.take(OP_LOAD)
    extracted = cursor.take(OP_COMPOSITE_EXTRACT)
    wide_size = cursor.take(113)
    nvn_end = cursor.take(OP_IADD)
    element = cursor.take(OP_ACCESS_CHAIN)
    length = cursor.take(OP_ARRAY_LENGTH)
    shifted = cursor.take(OP_SHIFT_LEFT)
    wide_length = cursor.take(113)
    typed_end = cursor.take(OP_IADD)
    less = cursor.take(OP_ULT)
    minimum = cursor.take(OP_SELECT)
    zero = cursor.take(OP_IEQUAL)
    selected = cursor.take(OP_SELECT)
    lower = cursor.take(OP_UGE)
    upper = cursor.take(OP_ULT)
    both = cursor.take(OP_LOGICAL_AND)
    cursor.take(OP_SELECTION_MERGE)
    cursor.take(OP_BRANCH_COND)
    cursor.take(OP_LABEL)
    delta = cursor.take(OP_ISUB)
    truncated = cursor.take(113)
    index = cursor.take(OP_SHIFT_RIGHT)
    chain = cursor.take(OP_ACCESS_CHAIN)
    value = cursor.take(OP_LOAD)
    returned = cursor.take(OP_RETURN_VALUE)
    cursor.take(OP_LABEL)
    cursor.take(OP_BRANCH)
    continue_label = cursor.take(OP_LABEL)
    incremented = cursor.take(OP_IADD)
    cursor.take(OP_STORE)
    cursor.take(OP_BRANCH)
    miss = cursor.take(OP_LABEL)
    need(counter[3] == stored[1] and below[3] == _result(counter), "Table counter reload differs")
    trip = module.const_u32(below[4])
    need(1 <= trip <= 64, "Table trip count is outside the proved finite bound")
    need(module.const_u32(scaled[4]) == 1, "Table selector stride shift is not 1")
    need(module.const_u32(size_index[4]) == 1, "Table size slot is not one byte after its address slot")
    need(module.opcode(module.by_id[base_index[3]]) == OP_CONSTANT, "Table base is not a constant")
    base_binding, base_byte, stride = module.byte_index(address[3], base_index[3])
    need(base_binding == 0 and stride == 8 and loaded_address[1] == module.vector(u32, 2),
         "Table selector is not the UBO0 uint32-vector array")
    need(size_chain[3] == address[3], "Table size uses a different uniform")
    need(extracted[4] == 0 and zero[3] == _result(extracted) and module.const_u32(zero[4]) == 0,
         "Table zero test is not the low uint32")
    need(selected[3] == _result(zero) and selected[4] == _result(typed_end) and selected[5] == _result(minimum),
         "Zero-size table entry does not keep the typed end")
    need(element[3] == chain[3] and length[3] == _result(element) and length[4] == 0, "Table length element differs")
    need(chain[4] == _result(counter) and module.const_u32(chain[5]) == 0 and chain[6] == _result(index),
         "Table hit indexing differs")
    need(value[1] == result_type and returned[1] == _result(value), "Table hit does not return its load")
    need(module.const_u32(shifted[4]) == shift and delta[4] == _result(aligned), "Table typed shift or base differs")
    need(lower[4] == _result(aligned) and upper[4] == _result(selected), "Table range does not use the selected end")
    variable = chain[3]
    need(module.binding.get(variable) is not None and module.descriptor_set.get(variable) == 0, "Table binding missing")
    need(merge[1] == miss[1] and merge[2] == continue_label[1], "Loop merge is not the miss suffix")
    need(cursor.at < len(insts), "Miss suffix is empty")
    for element_index in range(trip):
        address_byte = base_byte + element_index * (stride << 1)
        roots.append(Root(module.binding[variable], element_index, "table", variable, chain[1],
                          address_byte, address_byte + stride))
    helper = Helper(
        function=function[2], parameter=parameter[2], entry=entry[1], miss_label=miss[1],
        result_type=result_type, width=width, shift=shift, calls=module.calls_of(function[2]),
        roots=tuple(roots), start=start, miss_at=start + cursor.at - 1, end=end)
    need(helper.miss_at == start + next(offset for offset, inst in enumerate(insts) if inst is miss), "Miss cursor drifted")
    return helper


def discover(module):
    proved, rejected = [], []
    for start, end in module.functions():
        function = module.instructions[start][2]
        try:
            helper = prove_load(module, start, end)
        except LoweringError as error:
            stores = sum(1 for inst in module.instructions[start:end + 1] if module.opcode(inst) == OP_STORE)
            returns = sum(1 for inst in module.instructions[start:end + 1] if module.opcode(inst) == OP_RETURN_VALUE)
            rejected.append({
                "function": function,
                "reason": str(error),
                "stores": stores,
                "return_values": returns,
                "calls": module.calls_of(function),
                "rewritten": False,
            })
            continue
        if helper.calls == 0:
            rejected.append({
                "function": function,
                "reason": "Nearest typed phase matches the load cascade but has no call from the module",
                "width": helper.width,
                "calls": 0,
                "rewritten": False,
            })
            continue
        proved.append(helper)
    need(proved, "No reachable global-hit load cascade was proved")
    reference = [(root.binding, root.element, root.kind, root.address_byte, root.size_byte) for root in proved[0].roots]
    for helper in proved[1:]:
        current = [(root.binding, root.element, root.kind, root.address_byte, root.size_byte) for root in helper.roots]
        need(current == reference, "Reachable load helpers disagree on selector order")
    return proved, rejected


class Emitter:
    def __init__(self, module, helpers, binding):
        self.module = module
        self.helpers = helpers
        self.binding = binding
        self.next_id = module.bound
        self.constants = {}
        self.prefix = []
        self.annotations = []
        self.u32 = module.uint(32)
        self.u64 = module.uint(64)
        self.bool = next(inst[1] for inst in module.instructions if module.opcode(inst) == OP_TYPE_BOOL)
        self.widths = tuple(sorted({helper.width for helper in helpers}))
        self.function_pointer = self._function_pointer()

    def new(self):
        ident = self.next_id
        self.next_id += 1
        return ident

    def words(self, opcode, operands):
        return [(len(operands) + 1) << 16 | opcode, *operands]

    def _function_pointer(self):
        for inst in self.module.instructions:
            if self.module.opcode(inst) == OP_TYPE_POINTER and inst[2] == FUNCTION_STORAGE and inst[3] == self.u32:
                return inst[1]
        raise LoweringError("Missing function-local uint32 pointer")

    def const(self, value, wide=False):
        key = (value, wide)
        if key not in self.constants:
            ident = self.new()
            self.constants[key] = ident
            if wide:
                self.prefix.append(self.words(OP_CONSTANT, [self.u64, ident, value & U32, (value >> 32) & U32]))
            else:
                need(0 <= value <= U32, "uint32 constant out of range")
                self.prefix.append(self.words(OP_CONSTANT, [self.u32, ident, value]))
        return self.constants[key]

    def layout(self):
        row = self.new()
        array = self.new()
        block = self.new()
        block_pointer = self.new()
        self.u64_pointer = self.new()
        self.u32_pointer = self.new()
        self.variable = self.new()
        members = [self.u64, self.u64, self.u64, self.u32, self.u32, self.u32, self.u32, self.u32, self.u32]
        header = [self.u32] * (len(self.widths) * 2)
        self.prefix.extend([
            self.words(OP_TYPE_STRUCT, [row, *members]),
            self.words(OP_TYPE_RUNTIME_ARRAY, [array, row]),
            self.words(OP_TYPE_STRUCT, [block, *header, array]),
            self.words(OP_TYPE_POINTER, [block_pointer, STORAGE_BUFFER, block]),
            self.words(OP_TYPE_POINTER, [self.u64_pointer, STORAGE_BUFFER, self.u64]),
            self.words(OP_TYPE_POINTER, [self.u32_pointer, STORAGE_BUFFER, self.u32]),
            self.words(OP_VARIABLE, [block_pointer, self.variable, STORAGE_BUFFER]),
        ])
        self.annotations.extend([
            self.words(OP_DECORATE, [array, DECOR_ARRAY_STRIDE, 48]),
            self.words(OP_DECORATE, [block, DECOR_BLOCK]),
            self.words(OP_DECORATE, [self.variable, DECOR_SET, 0]),
            self.words(OP_DECORATE, [self.variable, DECOR_BINDING, self.binding]),
            self.words(OP_DECORATE, [self.variable, DECOR_NONWRITABLE]),
        ])
        for index, offset in enumerate((0, 8, 16, 24, 28, 32, 36, 40, 44)):
            self.annotations.append(self.words(OP_MEMBER_DECORATE, [row, index, DECOR_OFFSET, offset]))
        array_member = len(self.widths) * 2
        for index in range(array_member):
            self.annotations.append(self.words(OP_MEMBER_DECORATE, [block, index, DECOR_OFFSET, index * 4]))
        self.annotations.append(self.words(OP_MEMBER_DECORATE, [block, array_member, DECOR_OFFSET, array_member * 4]))
        self.array_member = array_member

    def op(self, body, opcode, *operands, result_type=None):
        if result_type is None:
            body.append(self.words(opcode, list(operands)))
            return None
        ident = self.new()
        body.append(self.words(opcode, [result_type, ident, *operands]))
        return ident

    def helper_body(self, helper):
        body = []
        zero = self.const(0)
        one = self.const(1)
        lower = self.new()
        upper = self.new()
        found = self.new()
        for variable in (lower, upper, found):
            body.append(self.words(OP_VARIABLE, [self.function_pointer, variable, FUNCTION_STORAGE]))
        width_index = self.widths.index(helper.width)

        def header(member):
            pointer = self.op(body, OP_ACCESS_CHAIN, self.variable, self.const(member), result_type=self.u32_pointer)
            return self.op(body, OP_LOAD, pointer, result_type=self.u32)

        count = header(width_index)
        start = header(width_index + len(self.widths))
        self.op(body, OP_STORE, lower, zero)
        self.op(body, OP_STORE, upper, count)
        self.op(body, OP_STORE, found, self.const(U32))
        loop, check, block, left, right_test, right, hit, continuation, done = [self.new() for _ in range(9)]
        self.op(body, OP_BRANCH, loop)
        body.append(self.words(OP_LABEL, [loop]))
        body.append(self.words(OP_LOOP_MERGE, [done, continuation, 2]))
        self.op(body, OP_BRANCH, check)
        body.append(self.words(OP_LABEL, [check]))
        low = self.op(body, OP_LOAD, lower, result_type=self.u32)
        high = self.op(body, OP_LOAD, upper, result_type=self.u32)
        active = self.op(body, OP_ULT, low, high, result_type=self.bool)
        self.op(body, OP_BRANCH_COND, active, block, done)
        body.append(self.words(OP_LABEL, [block]))
        distance = self.op(body, OP_ISUB, high, low, result_type=self.u32)
        half = self.op(body, OP_SHIFT_RIGHT, distance, one, result_type=self.u32)
        middle = self.op(body, OP_IADD, low, half, result_type=self.u32)
        row_index = self.op(body, OP_IADD, start, middle, result_type=self.u32)

        def field(index, row, wide=False):
            pointer_type = self.u64_pointer if wide else self.u32_pointer
            value_type = self.u64 if wide else self.u32
            pointer = self.op(body, OP_ACCESS_CHAIN, self.variable, self.const(self.array_member), row, self.const(index),
                              result_type=pointer_type)
            return self.op(body, OP_LOAD, pointer, result_type=value_type)

        row_low = field(0, row_index, True)
        row_high = field(1, row_index, True)
        below = self.op(body, OP_ULT, helper.parameter, row_low, result_type=self.bool)
        decide = self.new()
        body.append(self.words(OP_SELECTION_MERGE, [decide, 0]))
        self.op(body, OP_BRANCH_COND, below, left, right_test)
        body.append(self.words(OP_LABEL, [left]))
        self.op(body, OP_STORE, upper, middle)
        self.op(body, OP_BRANCH, decide)
        body.append(self.words(OP_LABEL, [right_test]))
        beyond = self.op(body, OP_UGE, helper.parameter, row_high, result_type=self.bool)
        right_merge = self.new()
        body.append(self.words(OP_SELECTION_MERGE, [right_merge, 0]))
        self.op(body, OP_BRANCH_COND, beyond, right, hit)
        body.append(self.words(OP_LABEL, [right]))
        after = self.op(body, OP_IADD, middle, one, result_type=self.u32)
        self.op(body, OP_STORE, lower, after)
        self.op(body, OP_BRANCH, right_merge)
        body.append(self.words(OP_LABEL, [hit]))
        self.op(body, OP_STORE, found, row_index)
        self.op(body, OP_BRANCH, done)
        body.append(self.words(OP_LABEL, [right_merge]))
        self.op(body, OP_BRANCH, decide)
        body.append(self.words(OP_LABEL, [decide]))
        self.op(body, OP_BRANCH, continuation)
        body.append(self.words(OP_LABEL, [continuation]))
        self.op(body, OP_BRANCH, loop)
        body.append(self.words(OP_LABEL, [done]))
        chosen = self.op(body, OP_LOAD, found, result_type=self.u32)
        exists = self.op(body, OP_INOT_EQUAL, chosen, self.const(U32), result_type=self.bool)
        route_block, lookup_merge, route_merge = [self.new() for _ in range(3)]
        body.append(self.words(OP_SELECTION_MERGE, [lookup_merge, 0]))
        self.op(body, OP_BRANCH_COND, exists, route_block, lookup_merge)
        body.append(self.words(OP_LABEL, [route_block]))
        base = field(2, chosen, True)
        route = field(6, chosen, False)
        delta = self.op(body, OP_ISUB, helper.parameter, base, result_type=self.u64)
        truncated = self.op(body, 113, delta, result_type=self.u32)
        index = self.op(body, OP_SHIFT_RIGHT, truncated, self.const(helper.shift), result_type=self.u32)
        cases = [self.new() for _ in helper.roots]
        literals = []
        for number, label in enumerate(cases):
            literals.extend((number, label))
        body.append(self.words(OP_SELECTION_MERGE, [route_merge, 0]))
        body.append(self.words(OP_SWITCH, [route, route_merge, *literals]))
        zero_id = self.const(0)
        for number, (label, root) in enumerate(zip(cases, helper.roots)):
            body.append(self.words(OP_LABEL, [label]))
            if root.kind == "single":
                operands = [root.variable, zero_id, index]
            else:
                operands = [root.variable, self.const(root.element), zero_id, index]
            pointer = self.op(body, OP_ACCESS_CHAIN, *operands, result_type=root.pointer_type)
            loaded = self.op(body, OP_LOAD, pointer, result_type=helper.result_type)
            self.op(body, OP_RETURN_VALUE, loaded)
        body.append(self.words(OP_LABEL, [route_merge]))
        self.op(body, OP_BRANCH, lookup_merge)
        body.append(self.words(OP_LABEL, [lookup_merge]))
        self.op(body, OP_BRANCH, helper.miss_label)
        return body


def free_binding(module, layout_bindings):
    used = set(module.binding.values()) | set(layout_bindings)
    binding = 0
    while binding in used:
        binding += 1
    need(binding <= 255, "No descriptor binding remains")
    return binding


def transform(module, helpers, binding):
    emitter = Emitter(module, helpers, binding)
    emitter.layout()
    bodies = {helper.function: emitter.helper_body(helper) for helper in helpers}
    replaced = {helper.function: helper for helper in helpers}
    output = []
    annotations_placed = False
    prefix_placed = False
    skip_until = None
    type_opcodes = set(range(19, 39)) | {OP_CONSTANT, OP_CONSTANT_NULL, OP_VARIABLE}
    for index, inst in enumerate(module.instructions):
        if skip_until is not None:
            if index < skip_until:
                continue
            skip_until = None
        opcode = module.opcode(inst)
        if not annotations_placed and opcode in type_opcodes:
            output.extend(emitter.annotations)
            annotations_placed = True
        if not prefix_placed and opcode == OP_FUNCTION:
            output.extend(emitter.prefix)
            prefix_placed = True
        if opcode == OP_FUNCTION and inst[2] in replaced and index == replaced[inst[2]].start:
            helper = replaced[inst[2]]
            output.append(inst)
            output.append(module.instructions[index + 1])
            output.append(module.instructions[index + 2])
            output.extend(bodies[helper.function])
            skip_until = helper.miss_at
            continue
        if opcode == OP_ENTRY:
            output.append(inst + [emitter.variable])
            continue
        output.append(inst)
    need(annotations_placed and prefix_placed, "SPIR-V layout insertion point was missing")
    header = [module.words[0], module.words[1], module.words[2], emitter.next_id, 0]
    blob = struct.pack(f"<{len(header)}I", *header)
    for inst in output:
        packed = list(inst)
        packed[0] = (len(packed) << 16) | (packed[0] & 0xFFFF)
        blob += struct.pack(f"<{len(packed)}I", *packed)
    return blob, emitter


def function_bodies(data):
    words = struct.unpack(f"<{len(data) // 4}I", data)
    at = 5
    functions = {}
    current = None
    body = []
    while at < len(words):
        count = words[at] >> 16
        opcode = words[at] & 0xFFFF
        row = tuple(words[at:at + count])
        if opcode == OP_FUNCTION:
            current = row[2]
            body = []
        if current is not None:
            body.append(row)
        if opcode == OP_FUNCTION_END and current is not None:
            functions[current] = tuple(body)
            current = None
        at += count
    return functions


def suffix(body, label):
    flat = [word for inst in body for word in inst]
    at = 0
    while at < len(flat):
        count = flat[at] >> 16
        opcode = flat[at] & 0xFFFF
        if opcode == OP_LABEL and flat[at + 1] == label:
            return tuple(flat[at:])
        at += count
    raise LoweringError("Miss label is absent from the function")


def preservation(original, candidate, helpers):
    left = function_bodies(original)
    right = function_bodies(candidate)
    need(set(left) == set(right), "Function inventory changed")
    modified = {helper.function for helper in helpers}
    untouched = []
    for function, body in left.items():
        if function in modified:
            continue
        need(body == right[function], "An unreplaced function word changed")
        raw = struct.pack(f"<{sum(len(inst) for inst in body)}I", *[word for inst in body for word in inst])
        untouched.append({"function": function, "words": sum(len(inst) for inst in body), "sha256": hashlib.sha256(raw).hexdigest()})
    suffixes = []
    for helper in helpers:
        old = suffix(left[helper.function], helper.miss_label)
        new = suffix(right[helper.function], helper.miss_label)
        need(old == new, "Original miss suffix changed")
        raw = struct.pack(f"<{len(old)}I", *old)
        suffixes.append({"function": helper.function, "width": helper.width, "words": len(old), "sha256": hashlib.sha256(raw).hexdigest()})
    return {"untouched_functions": untouched, "miss_suffixes": suffixes,
            "modified_functions": sorted(modified), "other_function_words_identical": True}


def pack_records(partitions, widths):
    counts = [len(partitions[width]) for width in widths]
    starts = []
    cursor = 0
    for count in counts:
        starts.append(cursor)
        cursor += count
    blob = bytearray(struct.pack(f"<{len(counts) + len(starts)}I", *counts, *starts))
    for width in widths:
        for row in partitions[width]:
            blob.extend(RECORD.pack(row.low, row.high, row.base, row.binding, row.element, width, row.route, 0, 0))
    return bytes(blob)


def capture_views(capture_json, roots):
    directory = capture_json.resolve().parent
    document = json.loads(capture_json.read_text())
    need(document.get("schema") == "open-ocarina.mesh-logical-input/v1", "Unexpected mesh capture schema")
    event = document["event"]
    need(event["program"] == PROGRAM and event["spirv_sha256"] == TARGET, "Capture is a different mesh module")
    layout = [row["binding"] for row in document["descriptor_layout"]]
    need(layout == list(range(len(layout))), "Reflected bindings are not a dense prefix")
    bindings = {}
    for row in document["bindings"]:
        key = (row["binding"], row["element"])
        need(key not in bindings, "Duplicate captured descriptor")
        bindings[key] = row
    ubo = bindings[(0, 0)]
    snapshot = ubo["snapshot"]
    path = directory / snapshot["path"]
    payload = path.read_bytes()
    need(len(payload) == snapshot["size"] == ubo["range_bytes"], "UBO0 extent differs from its snapshot")
    need(hashlib.sha256(payload).hexdigest() == snapshot["sha256"], "UBO0 snapshot digest differs")
    views = []
    for root in roots:
        row = bindings[(root.binding, root.element)]
        if row.get("null_descriptor") is True:
            need(row.get("snapshot") is None, "Null descriptor carries a payload")
            bound = 0
        else:
            need(type(row.get("range_bytes")) is int and row["range_bytes"] >= 0, "Storage range is not a concrete byte count")
            bound = row["range_bytes"]
        need(root.address_byte + 8 <= len(payload) and root.size_byte + 4 <= len(payload), "Selector exceeds captured UBO0")
        guest = struct.unpack_from("<Q", payload, root.address_byte)[0]
        size_word = struct.unpack_from("<I", payload, root.size_byte)[0]
        views.append(View(guest, size_word, bound, root.binding, root.element, root.kind))
    return views, {
        "capture_json_sha256": hashlib.sha256(capture_json.read_bytes()).hexdigest(),
        "ubo0_sha256": snapshot["sha256"],
        "ubo0_bytes": len(payload),
        "reflected_bindings": layout,
        "ownership": "Logical mesh-input capture. CPU-writer quiescence and import-alias ownership are not certified by this file.",
    }


def validate(path):
    run = subprocess.run(["spirv-val", "--target-env", "vulkan1.3", "--uniform-buffer-standard-layout", str(path)],
                         capture_output=True, text=True, timeout=60)
    need(run.returncode == 0, "spirv-val rejected the candidate")


def build(spirv_path, capture_path, output):
    data = spirv_path.read_bytes()
    need(hashlib.sha256(data).hexdigest() == TARGET, "Original module digest differs")
    module = Module(data)
    helpers, rejected = discover(module)
    views, frozen = capture_views(capture_path, helpers[0].roots)
    binding = free_binding(module, frozen["reflected_bindings"])
    need(all(view.binding != binding for view in views), "Record binding collides with a proved root")
    widths = tuple(sorted(helper.width for helper in helpers))
    partitions = {width: build_rows(views, width) for width in widths}
    certificate = certify(views, partitions, widths)
    records = pack_records(partitions, widths)
    candidate, _emitter = transform(module, helpers, binding)
    output.mkdir(parents=True, exist_ok=False)
    candidate_path = output / "candidate.spv"
    record_path = output / "records.bin"
    candidate_path.write_bytes(candidate)
    record_path.write_bytes(records)
    validate(spirv_path)
    validate(candidate_path)
    proof = preservation(data, candidate, helpers)
    report = {
        "schema": "open-ocarina.grok-d8-interval/v1",
        "execution_admitted": False,
        "instruction_timing_measured": False,
        "program": PROGRAM,
        "original_sha256": TARGET,
        "candidate_sha256": hashlib.sha256(candidate).hexdigest(),
        "record_sha256": hashlib.sha256(records).hexdigest(),
        "record_bytes": len(records),
        "tool_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "added_binding": {"set": 0, "binding": binding, "storage": "StorageBuffer", "nonwritable": True},
        "widths": list(widths),
        "helpers": [{
            "function": helper.function, "width": helper.width, "shift": helper.shift, "calls": helper.calls,
            "roots": [asdict(root) for root in helper.roots],
        } for helper in helpers],
        "rejected": rejected,
        "frozen": frozen,
        "views": [asdict(view) for view in views],
        "intervals": {str(width): [asdict(row) for row in partitions[width]] for width in widths},
        "selection": {str(width): certificate[width] for width in widths},
        "preservation": proof,
        "missing_producer_input": None,
    }
    (output / "report.json").write_text(json.dumps(report, indent=2) + "\n")
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--spirv", type=Path, required=True)
    parser.add_argument("--capture", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    try:
        report = build(args.spirv, args.capture, args.output)
    except (LoweringError, OSError, KeyError, subprocess.TimeoutExpired, json.JSONDecodeError) as error:
        print(str(error), file=sys.stderr)
        return 1
    print(json.dumps({
        "candidate_sha256": report["candidate_sha256"],
        "record_sha256": report["record_sha256"],
        "binding": report["added_binding"]["binding"],
        "execution_admitted": False,
    }, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
