#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Zero-fuzz apply of the grok replay vertex-bind patch, then an optional TU compile.

Reads a private vk_draw.cpp and writes only under --work. Does not modify the
source tree named by --draw. Prints SHA-256 of the patch, the tests, this tool,
the doc, and any object or linked library it produced. No frame time is measured.
"""

import argparse
import hashlib
import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PATCH = ROOT / "patches" / "hookshot" / "grok-replay-vertex-bind.patch"
DOC = ROOT / "docs" / "grok-replay-vertex-bind.md"
TEST = ROOT / "tests" / "test_grok_replay_vertex_bind.py"
HARNESS = ROOT / "tests" / "grok_replay_vertex_bind_harness.cpp"


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    h.update(path.read_bytes())
    return h.hexdigest()


def apply_fuzz0(draw: Path, work: Path) -> Path:
    tree = work / "tree"
    if tree.exists():
        raise ValueError("Choose a fresh work directory; existing tree is preserved")
    dest = tree / "gpu" / "vk"
    dest.mkdir(parents=True)
    shutil.copyfile(draw, dest / "vk_draw.cpp")
    subprocess.run(["patch", "--fuzz=0", "-p1", "-d", str(tree), "-i", str(PATCH)], check=True, timeout=15)
    return dest


def compile_tu(vk_dir: Path, out: Path) -> None:
    hookshot = os.environ.get("GROK_REPLAY_HOOKSHOT_VK")
    gen = os.environ.get("GROK_REPLAY_GEN")
    if not hookshot or not gen:
        raise SystemExit("compile needs GROK_REPLAY_HOOKSHOT_VK and GROK_REPLAY_GEN")
    cpm = os.environ.get("RX_CPM", "")
    includes = [
        "-I", str(vk_dir),
        "-I", hookshot,
        "-I", gen,
        "-I", str(Path(hookshot).parents[1] / "host" / "posix"),
        "-I", str(Path(hookshot) / "compat"),
        "-I", str(Path(hookshot).parent / "compat"),
        "-I", str(Path(hookshot) / "eden"),
        "-I", str(Path(hookshot).parent / "third_party" / "suyu"),
        "-I", str(Path(hookshot) / "sirit" / "include"),
        "-I", str(Path(hookshot) / "sirit" / "src"),
    ]
    if cpm:
        for name, extra in (
            ("vulkanheaders", "include"),
            ("spirv-headers", "include"),
            ("vulkanmemoryallocator", "include"),
        ):
            roots = sorted(p for p in Path(cpm).glob(name + "/*") if (p / extra).is_dir())
            if not roots:
                raise SystemExit(f"missing {name} under RX_CPM")
            includes += ["-I", str(roots[-1] / extra)]
        spirv = [p for p in Path(cpm).glob("spirv-headers/*") if (p / "include" / "spirv" / "unified1").is_dir()]
        if spirv:
            includes += ["-I", str(spirv[-1] / "include" / "spirv" / "unified1")]
    cmd = [
        os.environ.get("CXX", "clang++"),
        "-std=c++20", "-O2", "-g0", "-DNDEBUG", "-march=x86-64-v3", "-ffp-contract=off",
        "-fPIC", "-D_GNU_SOURCE", "-Wno-everything",
        "-include", "algorithm", "-include", "cstdio", "-include", "cstdlib",
        "-include", "cstring", "-include", "functional", "-include", "string", "-include", "vector",
        "-DVK_NO_PROTOTYPES", "-DMKWVK_BUILDING", "-DHOOKSHOT_RELEASE", "-DMKWVK_INCLUDES=2",
        *includes,
        "-c", str(vk_dir / "vk_draw.cpp"),
        "-o", str(out),
    ]
    subprocess.run(cmd, check=True, timeout=90)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--draw", type=Path, help="vk_draw.cpp to copy and patch with --fuzz=0")
    parser.add_argument("--work", type=Path, required=True, help="private work directory")
    parser.add_argument("--compile", action="store_true")
    parser.add_argument("--link-rsp", type=Path, help="frozen vk_objs.rsp; the vk_draw object is replaced")
    args = parser.parse_args()
    args.work = args.work.resolve()
    if args.work.is_relative_to(ROOT) and args.work.relative_to(ROOT).parts[:1] not in (("local",),("work",),("artifacts",)):
        raise ValueError("Use an ignored private work directory")
    if args.work.exists() and any(args.work.iterdir()):
        raise ValueError("Work directory must be fresh or empty")
    args.work.mkdir(parents=True, exist_ok=True)
    rows = {
        "patch": sha256(PATCH),
        "test": sha256(TEST),
        "harness": sha256(HARNESS),
        "tool": sha256(Path(__file__).resolve()),
        "doc": sha256(DOC),
    }
    if args.draw:
        vk = apply_fuzz0(args.draw, args.work)
        rows["patched_vk_draw"] = sha256(vk / "vk_draw.cpp")
        rows["patched_header"] = sha256(vk / "grok-replay-bind.h")
        if args.compile:
            obj = args.work / "vk_draw.o"
            compile_tu(vk, obj)
            rows["vk_draw_o"] = sha256(obj)
            if args.link_rsp:
                text = args.link_rsp.read_text()
                repl = '"' + str(obj) + '"'
                lines = []
                found = False
                for line in text.splitlines():
                    if "vk_vk_draw.o" in line:
                        lines.append(repl)
                        found = True
                    else:
                        lines.append(line)
                if not found:
                    raise SystemExit("rsp has no vk_vk_draw.o")
                rsp = args.work / "vk_objs.rsp"
                rsp.write_text("\n".join(lines) + "\n")
                so = args.work / "hookshot_vk.so"
                subprocess.run(
                    ["clang++", "-fuse-ld=lld", "-shared", "-o", str(so),
                     "-Wl,-Bsymbolic", "-Wl,--gc-sections", "-static-libstdc++", "-static-libgcc",
                     "@" + str(rsp), "-ldl", "-lpthread", "-lm"],
                    check=True, timeout=60,
                )
                rows["hookshot_vk_so"] = sha256(so)
    for name, digest in rows.items():
        print(f"{name} {digest}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
