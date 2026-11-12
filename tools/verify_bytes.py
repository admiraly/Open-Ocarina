#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Compare bounded byte ranges against a hash-pinned reference. Does not validate relocations."""
import argparse
import hashlib
import json
import re
import sys
from pathlib import Path


def verify(reference, candidate, reference_offset, candidate_offset, size, reference_sha256):
    if min(reference_offset, candidate_offset) < 0 or size <= 0:
        raise ValueError("Offsets must be nonnegative and size must be positive")
    if not re.fullmatch(r"[0-9a-fA-F]{64}", reference_sha256):
        raise ValueError("A full SHA-256 reference digest is required")
    with reference.open("rb") as expected, candidate.open("rb") as actual:
        if hashlib.file_digest(expected, "sha256").hexdigest() != reference_sha256.lower():
            raise ValueError("Reference hash mismatch; refusing comparison against a different revision")
        if reference_offset + size > reference.stat().st_size or candidate_offset + size > candidate.stat().st_size:
            raise ValueError("Comparison extends beyond an input file")
        expected.seek(reference_offset)
        actual.seek(candidate_offset)
        matched = 0
        while matched < size:
            count = min(size - matched, 1024 * 1024)
            left, right = expected.read(count), actual.read(count)
            if len(left) != count or len(right) != count:
                raise ValueError("Input changed or was truncated during comparison")
            if left != right:
                difference = next(index for index, pair in enumerate(zip(left, right)) if pair[0] != pair[1])
                return {"byte_match": False, "size": size, "first_difference": matched + difference,
                        "reference_sha256": reference_sha256.lower()}
            matched += count
    return {"byte_match": True, "size": size, "reference_sha256": reference_sha256.lower(),
            "scope": "specified bytes only; source provenance, boundaries, relocations and full-image identity not verified"}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--reference", type=Path, required=True)
    parser.add_argument("--candidate", type=Path, required=True)
    parser.add_argument("--reference-sha256", required=True)
    parser.add_argument("--reference-offset", type=lambda value: int(value, 0), default=0)
    parser.add_argument("--candidate-offset", type=lambda value: int(value, 0), default=0)
    parser.add_argument("--size", type=lambda value: int(value, 0), required=True)
    args = parser.parse_args()
    try:
        result = verify(args.reference, args.candidate, args.reference_offset, args.candidate_offset,
                        args.size, args.reference_sha256)
        print(json.dumps(result, indent=2))
        return 0 if result["byte_match"] else 1
    except (OSError, ValueError) as exc:
        print(str(exc), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
