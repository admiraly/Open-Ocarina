#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Stream Hookshot GPU timing tables into metadata-only optimization targets."""
import argparse
import hashlib
import json
import math
import re
import sys
from pathlib import Path

NUMBER = r"\d+(?:\.\d+)?"
HEADER = re.compile(r"GPU pass timing, (\d+) presents, per frame")
TOTAL = re.compile(rf"^({NUMBER}) ms a frame in all")
KIND = re.compile(rf"^([mu])\s+(\w+)\s+({NUMBER}) ms\s+({NUMBER})%\s+({NUMBER}) spans\s+({NUMBER}) items")
SPAN = re.compile(rf"^([mu])\s+({NUMBER}) ms\s+({NUMBER}) x\s+({NUMBER}) us\s+({NUMBER}) items\s+({NUMBER}) MB\s+(.+)$")


def parse(lines):
    blocks = []
    block = None
    for line_number, raw in enumerate(lines, 1):
        text = raw.split("vk:", 1)[-1].strip()
        header = HEADER.search(text)
        if header:
            block = {"source_line": line_number, "presents": int(header[1]),
                     "total_ms": None, "kinds": [], "spans": [], "expected_spans": None}
            blocks.append(block)
            continue
        if block is None:
            continue
        total = TOTAL.match(text)
        if total:
            block["total_ms"] = float(total[1])
        count = re.match(r"the (\d+) spans that took the most", text)
        if count:
            block["expected_spans"] = int(count[1])
            continue
        kind = KIND.match(text)
        if kind and block["expected_spans"] is None:
            block["kinds"].append({"queue": kind[1], "kind": kind[2],
                                    "ms": float(kind[3]), "spans_per_frame": float(kind[5]),
                                    "items_per_frame": float(kind[6])})
        span = SPAN.match(text)
        if span and block["expected_spans"] is not None and len(block["spans"]) < block["expected_spans"]:
            description = span[7]
            block["spans"].append({"source_line": line_number, "queue": span[1],
                "ms": float(span[2]), "runs_per_frame": float(span[3]),
                "items_per_frame": float(span[5]), "mb_per_frame": float(span[6]),
                "description": description,
                "program_hashes": re.findall(r"\bprog ([0-9a-fA-F]{16})\b", description)})
    return blocks


def summarize(block, minimum_dispatches=100):
    if not math.isfinite(minimum_dispatches) or minimum_dispatches < 0:
        raise ValueError("Dispatch threshold must be finite and nonnegative")
    if (not block["total_ms"] or not block["kinds"] or block["expected_spans"] is None
            or len(block["spans"]) != block["expected_spans"]):
        raise ValueError("Incomplete timing table; do not report a partially captured workload")
    dispatches = sum(row["items_per_frame"] for row in block["kinds"] if row["kind"] == "dispatch")
    if dispatches < minimum_dispatches:
        raise ValueError(f"Only {dispatches:g} dispatches/frame: likely loading or incomplete workload; "
                         "use --min-dispatches explicitly for another workload")
    result = {**block, "dispatches_per_frame": dispatches}
    result["kinds"] = [{**row, "share_of_total_pct": round(row["ms"] / block["total_ms"] * 100, 3)}
                       for row in block["kinds"]]
    result["spans"] = [{**row, "share_of_total_pct": round(row["ms"] / block["total_ms"] * 100, 3)}
                       for row in sorted(block["spans"], key=lambda item: item["ms"], reverse=True)]
    result["interpretation"] = [
        "GPU timestamps serialize preceding work; these spans are attribution, not normal frame latency.",
        "Shares use combined recording and upload time; source per-queue percentages have different denominators.",
        "Graphics first/last program hashes are endpoints; they do not identify every draw or its expensive stage.",
        "Shader algorithms and allocator records cannot be identified from chronology alone.",
        "Dispatch threshold is a configurable smoke check, not proof of a complete or equivalent scene."]
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("log", type=Path)
    parser.add_argument("--block", type=int, default=-1, help="Timing-table index; default last, including incomplete last tables")
    parser.add_argument("--min-dispatches", type=float, default=100)
    args = parser.parse_args()
    try:
        if args.min_dispatches < 0:
            raise ValueError("--min-dispatches must be nonnegative")
        with args.log.open(errors="replace") as source:
            blocks = parse(source)
        if not blocks:
            raise ValueError("No GPU timing tables found; collect with RX_VK_PASS_TIMING=1")
        report = summarize(blocks[args.block], args.min_dispatches)
        with args.log.open("rb") as source:
            report["source_sha256"] = hashlib.file_digest(source, "sha256").hexdigest()
        print(json.dumps(report, indent=2))
    except (OSError, ValueError, IndexError) as exc:
        print(str(exc), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
