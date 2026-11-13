#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Reject common private artifacts and credentials in the Git index (not a full secret scanner)."""
import re
import subprocess
import sys
from pathlib import Path

ALLOWED = {".py", ".md", ".json", ".yml", ".yaml", ".patch", ".txt"}
NAMES = {"LICENSE", "LICENSE.MIT", ".gitignore"}
PRIVATE_PARTS = {"local", "work", "artifacts", "romfs", "exefs", ".hookshot_exefs", "saves", "user"}
SECRET = re.compile(rb"(?:gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,}|-----BEGIN (?:RSA |OPENSSH |EC )?PRIVATE KEY-----|/(?:ssoToken|refreshToken)=\S+)")


def audit(name, content, symlink=False):
    path = Path(name)
    errors = []
    if symlink:
        errors.append("symlink")
    scoped_source = ((path.suffix == ".java" and path.parts[:2] == ("tools", "ghidra_scripts"))
                     or (path.suffix == ".c" and path.parts[:2] == ("src", "probes"))
                     or name in {"src/probes/compute_interval_records.h",
                                 "src/probes/compute_selector_extract.h",
                                 "src/probes/grok_memory_mirror_plan.h",
                                 "tests/grok_replay_vertex_bind_harness.cpp",
                                 "tools/vulkan_compile_probe.c", "tools/vulkan_execution_probe.c",
                                 "tools/vulkan_graphics_compile_probe.c", "tools/vulkan_mesh_execution_probe.c",
                                 "tools/vulkan_physical_replay.c", "tools/vulkan_mesh_replay.c",
                                 "tools/vulkan_d8_interval_replay.c"})
    if set(path.parts) & PRIVATE_PARTS or (path.suffix not in ALLOWED and path.name not in NAMES and not scoped_source):
        errors.append("unapproved artifact path/type")
    if len(content) > 256 * 1024 or b"\0" in content:
        errors.append("large or binary content")
    if SECRET.search(content):
        errors.append("credential-like content")
    if re.search(rb"/(?:home|mnt)/[A-Za-z0-9]", content):
        errors.append("machine-specific absolute path")
    return errors


def main():
    root = Path(__file__).resolve().parents[1]
    entries = subprocess.check_output(["git", "ls-files", "--stage", "-z"], cwd=root).split(b"\0")
    failures = []
    for entry in filter(None, entries):
        metadata, raw_name = entry.split(b"\t", 1)
        mode, object_id, stage = metadata.split()
        name = raw_name.decode("utf-8")
        content = subprocess.check_output(["git", "cat-file", "blob", object_id.decode()], cwd=root)
        errors = audit(name, content, mode == b"120000")
        if stage != b"0":
            errors.append("unresolved merge")
        if errors:
            failures.append(f"{name}: {', '.join(errors)}")
    if failures:
        print("\n".join(failures), file=sys.stderr)
        return 1
    print("Public tree check passed (tracked/staged content only; review untracked files before adding).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
