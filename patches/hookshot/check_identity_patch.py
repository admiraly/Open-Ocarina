#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Check the pinned metadata patch and compile its isolated, game-free helper."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import struct
import subprocess
import tempfile

PIN = "4cf785a0b143bb34026506c9a3fe4f4bb93c797d"
PATCH = Path(__file__).with_name("compute-identity.patch")


def header_from_patch():
    capture = False
    result = []
    for line in PATCH.read_text().splitlines(keepends=True):
        if line.startswith("diff --git "):
            capture = line.rstrip().endswith(" b/gpu/vk/compute_identity.h")
        elif capture and line.startswith("+") and not line.startswith("+++"):
            result.append(line[1:])
    if not result or "namespace mkwvk::oo_identity" not in "".join(result):
        raise ValueError("New identity header not found in patch")
    return "".join(result)


def require(condition, message):
    if not condition:
        raise ValueError(message)


def check(checkout=None, compiler=None):
    if checkout is not None:
        head = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=checkout, text=True).strip()
        if head != PIN:
            raise ValueError("Patch checkout is not the documented pinned revision")
        # Index check does not alter a working tree and works on the locally modified source too.
        subprocess.run(["git", "apply", "--cached", "--check", str(PATCH.resolve())], cwd=checkout, check=True)
    compiler = compiler or shutil.which("clang++") or shutil.which("g++")
    if not compiler:
        raise ValueError("A C++20 compiler is required for the standalone helper tests")
    with tempfile.TemporaryDirectory(prefix="oo-identity-") as temporary:
        work = Path(temporary)
        (work / "compute_identity.h").write_text(header_from_patch())
        source = work / "test.cpp"
        source.write_text(r'''#include "compute_identity.h"
#include <iostream>
int main(int argc,char** argv) {
    using namespace mkwvk::oo_identity;
    if(argc<2)return 2;
    if(std::strcmp(argv[1],"emit")) {
        std::string value = !std::strcmp(argv[1],"million") ? std::string(1000000,'a') : argv[1];
        std::cout << hex(sha256(reinterpret_cast<const uint8_t*>(value.data()),value.size())) << "\n";
        return 0;
    }
    Identity identity;
    const std::vector<uint32_t> words{0x07230203,0x00010600,0,1,0};
    note_stage(0xcc62764061c44f6aull,0x095931037888515aull,identity,words);
    const bool missing=argc>2;
    for(unsigned j=0;j<257;++j)
        emit(missing?Identity{}:identity,0xcc62764061c44f6aull,0x095931037888515aull,1,{64,1,1},
             2,3,4,j==1?0x1234:0,42,12,32,true,0x8086,0x56a0,1);
    return 0;
}
''')
        executable = work / "test"
        subprocess.run([compiler, "-std=c++20", "-O2", "-DNDEBUG", "-Wall", "-Wextra", "-Werror",
                        "-pthread", str(source), "-o", str(executable)], check=True, timeout=60)
        vectors = ["", "a", "abc", *("a" * size for size in (55, 56, 63, 64, 65)), "million"]
        for value in vectors:
            observed = subprocess.check_output([str(executable), value], text=True, timeout=10).strip()
            actual = b"a" * 1000000 if value == "million" else value.encode()
            if observed != hashlib.sha256(actual).hexdigest():
                raise ValueError("SHA-256 helper disagrees with hashlib")
        path = work / "events.jsonl"
        env = os.environ.copy()
        env["RX_VK_OO_IDENTITY_FILE"] = str(path)
        env.pop("RX_VK_OO_IDENTITY_PROGRAM", None)
        env.pop("RX_VK_OO_IDENTITY_VARIANT", None)
        subprocess.run([str(executable), "emit"], env=env, check=True, timeout=10)
        rows = [json.loads(line) for line in path.read_text().splitlines()]
        if len(rows) != 256:
            raise ValueError("Opt-in record bound failed")
        expected_digest = hashlib.sha256(struct.pack("<5I", 0x07230203, 0x00010600, 0, 1, 0)).hexdigest()
        require(rows[0]["pipeline"]["spirv"]["sha256"] == expected_digest, "Canonical SPIR-V digest mismatch")
        require(rows[0]["pipeline"]["subgroup"] == {"required_size": 32, "require_full_subgroups": True}, "Subgroup policy serialization mismatch")
        require(rows[0]["dispatch"]["group_count"] == [2, 3, 4], "Direct-grid metadata mismatch")
        require(rows[1]["dispatch"]["kind"] == "indirect" and rows[1]["dispatch"]["group_count"] is None, "Indirect arguments must remain explicitly unknown")
        require(all(row["producer"] == {"stage": "identity-only", "replay_complete": False} for row in rows), "Identity observations must not claim replay completeness")
        require(all(not any(row["coverage"].values()) for row in rows), "Resource coverage fabricated")
        require(len({row["event_id"] for row in rows}) == len(rows), "Duplicate event IDs")
        path.unlink()
        subprocess.run([str(executable), "emit", "missing"], env=env, check=True, timeout=10)
        missing = json.loads(path.read_text().splitlines()[0])
        require(missing["pipeline"]["spirv"]["sha256"] is None, "Missing digest must be null")
        path.unlink()
        env["RX_VK_OO_IDENTITY_PROGRAM"] = "cc627640"  # Prefixes must not match.
        subprocess.run([str(executable), "emit"], env=env, check=True, timeout=10)
        require(not path.exists(), "Disabled or invalid filter emitted output")
        env.pop("RX_VK_OO_IDENTITY_PROGRAM")
        env.pop("RX_VK_OO_IDENTITY_FILE")
        subprocess.run([str(executable), "emit"], env=env, check=True, timeout=10)
        require(not path.exists(), "Disabled or invalid filter emitted output")
    return {"pinned_apply_check": checkout is not None, "sha256_vectors": len(vectors),
            "standalone_optimized_compile": True, "json_record_limit": 256,
            "disabled_and_invalid_filter_no_output": True,
            "missing_digest_not_fabricated": True,
            "scope": "game-free helper and patch framing; no runtime capture or GPU execution tested"}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--checkout", type=Path)
    parser.add_argument("--compiler")
    args = parser.parse_args()
    try:
        print(json.dumps(check(args.checkout, args.compiler), indent=2))
    except (OSError, ValueError, subprocess.SubprocessError) as exc:
        parser.exit(1, str(exc) + "\n")


if __name__ == "__main__":
    main()
