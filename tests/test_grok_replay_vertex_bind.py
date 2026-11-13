#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Compile the patched replay callsites and compare them with a full-bind oracle."""

import os
import re
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PATCH = ROOT / "patches" / "hookshot" / "grok-replay-vertex-bind.patch"
HARNESS = ROOT / "tests" / "grok_replay_vertex_bind_harness.cpp"


def _added(patch_text: str) -> str:
    kept = []
    for line in patch_text.splitlines():
        if line.startswith("+++") or line.startswith("---") or line.startswith("@@"):
            continue
        if line.startswith("+"):
            kept.append(line[1:])
    return "\n".join(kept) + "\n"


def _extract_header(patch_text: str) -> str:
    marker = "+++ b/gpu/vk/grok-replay-bind.h"
    start = patch_text.find(marker)
    if start < 0:
        raise AssertionError("patch does not add grok-replay-bind.h")
    rest = patch_text[start:]
    nxt = len(rest)
    for marker in ("\ndiff --git ", "\n--- a/"):
        at = rest.find(marker)
        if at >= 0:
            nxt = min(nxt, at)
    block = rest[:nxt]
    lines = []
    for line in block.splitlines()[1:]:
        if line.startswith("@@"):
            continue
        if not line.startswith("+"):
            raise AssertionError(f"header hunk is not a pure add: {line!r}")
        lines.append(line[1:])
    text = "\n".join(lines) + "\n"
    if "namespace grok_replay" not in text:
        raise AssertionError("extracted header is not the replay helper")
    return text


def _between(text: str, begin: str, end: str) -> str:
    begin_at = text.find(begin)
    end_at = text.find(end, begin_at + len(begin) if begin_at >= 0 else 0)
    if begin_at < 0 or end_at < 0:
        raise AssertionError(f"missing site markers {begin}")
    body = text[begin_at + len(begin) : end_at]
    lines = []
    for line in body.splitlines():
        if line.startswith("+"):
            line = line[1:]
        lines.append(line)
    return "\n".join(lines)


class ReplayVertexBindTests(unittest.TestCase):
    def setUp(self):
        self.patch = PATCH.read_text()
        self.added = _added(self.patch)

    def test_checker_preserves_existing_work_tree(self):
        from tools import grok_replay_vertex_bind_check as checker
        with tempfile.TemporaryDirectory(prefix="oo-replay-preserve-") as tmp:
            work=Path(tmp);tree=work/"tree";tree.mkdir()
            marker=tree/"keep";marker.write_text("preserved")
            with self.assertRaisesRegex(ValueError,"fresh work directory"):
                checker.apply_fuzz0(work/"missing-draw.cpp",work)
            self.assertEqual(marker.read_text(),"preserved")

    def test_standalone_header_is_not_published(self):
        self.assertFalse((ROOT / "patches" / "hookshot" / "grok-replay-bind.h").exists())

    def test_controls_stay_opt_in(self):
        push = re.search(
            r"bool grok_replay_push_enabled\(\) \{\n\+    static const bool on = (.*);\n\+    return on;\n\+\}",
            self.patch,
        )
        bind = re.search(r"static const bool narrow = (.*);", self.patch)
        self.assertIsNotNone(push)
        self.assertIsNotNone(bind)
        self.assertIn('env_on("RX_VK_GROK_REPLAY_PUSH")', push.group(1))
        self.assertIn('env_on("RX_VK_GROK_REPLAY_BIND")', bind.group(1))
        self.assertNotIn("!", push.group(1))
        self.assertNotIn("!", bind.group(1))

    def test_observer_is_outside_push_elision(self):
        site = _between(self.patch, "GROK_REPLAY_PUSH_SITE\n", "GROK_REPLAY_PUSH_SITE_END")
        issue = site.find("push_should_issue")
        vk = site.find("vkCmdPushConstants")
        note = site.find("oo_dp::note_push")
        self.assertGreaterEqual(issue, 0)
        self.assertGreater(vk, issue)
        self.assertGreater(note, vk)
        between = site[site.find("if (grok_issue)") : note]
        self.assertNotIn("return", between)

    def test_fsr3_return_invalidates_before_every_result(self):
        removed = [ln for ln in self.patch.splitlines() if ln.startswith("-") and "return fsr3_upscale(" in ln]
        self.assertEqual(len(removed), 1)
        added = "\n".join(ln[1:] for ln in self.patch.splitlines() if ln.startswith("+"))
        call = added.find("const bool done = fsr3_upscale(")
        inv = added.find("grok_replay_push_invalidate()", call)
        ret = added.find("return done;", call)
        self.assertGreaterEqual(call, 0)
        self.assertGreater(inv, call)
        self.assertGreater(ret, inv)
        self.assertNotIn("return fsr3_upscale(", added)

    def test_direct_pushes_invalidate(self):
        # Every added vkCmdPushConstants except the full-range draw/dispatch push
        # is paired with an invalidation on the following added line.
        paired = 0
        raw = self.patch.splitlines()
        seen = 0
        for i, ln in enumerate(raw):
            if "vkCmdPushConstants(" not in ln or ln.startswith(("+++", "---", "@@", "+//", " //")):
                continue
            if ln.startswith("+") or ln.startswith(" "):
                seen += 1
            else:
                continue
            if "kPushSize" in ln:
                prev = raw[i - 1] if i else ""
                self.assertIn("grok_issue", prev)
                continue
            nxt = raw[i + 1]
            self.assertIn("grok_replay_push_invalidate()", nxt)
            paired += 1
        self.assertGreaterEqual(seen, 7)
        self.assertGreaterEqual(paired, 6)

    @unittest.skipUnless(shutil.which("g++"), "g++ unavailable")
    def test_extracted_callsites_match_full_oracle(self):
        header = _extract_header(self.patch)
        push_site = _between(self.patch, "// GROK_REPLAY_PUSH_SITE\n", "// GROK_REPLAY_PUSH_SITE_END")
        vb_site = _between(self.patch, "// GROK_REPLAY_VB_SITE\n", "// GROK_REPLAY_VB_SITE_END")
        # The patch stores these lines with a leading '+' only on the diff. _between
        # already stripped that. Drop a leading diff marker if the marker line itself
        # was included.
        push_site = "\n".join(ln[1:] if ln.startswith("+") else ln for ln in push_site.splitlines())
        vb_site = "\n".join(ln[1:] if ln.startswith("+") else ln for ln in vb_site.splitlines())
        self.assertIn("oo_dp::note_push", push_site)
        self.assertIn("vkCmdBindVertexBuffers2", vb_site)
        sites = r'''
#include "grok-replay-bind.h"
#include <cstdint>
#include <cstring>
#include <vector>

static int g_push_on = 1;
bool grok_replay_push_enabled() { return g_push_on != 0; }
static grok_replay::PushRecord grok_push;
constexpr uint32_t kPushSize = 32;

struct Prog { uint64_t layout; uint32_t push_stages; uint64_t key; uint64_t variant; };
namespace Shader { namespace Backend { namespace SPIRV { constexpr unsigned GUEST_PAGES_PUSH_OFFSET = 8; } } }

static int g_vk_push = 0;
static int g_notes = 0;
static uint8_t g_noted[32];

using VkCommandBuffer = uint64_t;
void vkCmdPushConstants(VkCommandBuffer, uint64_t, uint32_t, uint32_t, uint32_t, const void*) { g_vk_push++; }

namespace oo_dp {
inline bool enabled() { return true; }
inline void note_push(uint64_t, uint64_t, uint64_t, uint64_t, uint64_t, uint64_t, uint32_t, uint32_t, uint32_t,
                      const void* data, uint32_t) {
    g_notes++;
    std::memcpy(g_noted, data, 32);
}
}
namespace oo_identity { inline bool selected(uint64_t, uint64_t) { return true; } }
inline bool memory_pt_recording_info(uint64_t, uint64_t* part, uint64_t* logical) {
    *part = 1; *logical = 2; return true;
}

static void run_push_site(uint64_t cmd, Prog& p, uint8_t* data) {
PUSH_SITE
}

int run_extracted_push_cases() {
    Prog p{10, 1, 1, 1};
    uint8_t data[32]{};
    data[0] = 4;
    g_push_on = 1;
    run_push_site(1, p, data);
    run_push_site(1, p, data);
    if (g_vk_push != 1 || g_notes != 2) return 11;
    if (g_noted[0] != 4) return 12;
    p.layout = 11;
    run_push_site(1, p, data);
    if (g_vk_push != 2 || g_notes != 3) return 13;
    p.push_stages = 0x20;
    run_push_site(1, p, data);
    if (g_vk_push != 3) return 14;
    grok_replay::push_invalidate(grok_push);
    run_push_site(1, p, data);
    if (g_vk_push != 4 || g_notes != 5) return 15;
    g_push_on = 0;
    int before = g_vk_push;
    run_push_site(1, p, data);
    run_push_site(1, p, data);
    if (g_vk_push != before + 2) return 16;
    if (g_notes != 7) return 17;
    return 0;
}

struct LastVb { uint64_t b[16], o[16], z[16], s[16]; };
static uint64_t vbufs[16], voffs[16], vsizes[16], vstrides[16];
static LastVb last_vb{};
static bool last_vb_valid = false;
static bool narrow = true;
static uint64_t cmd = 1;
struct VbCall { uint32_t first, count; uint64_t buffer, offset, size, stride; };
static std::vector<VbCall> g_vb;

void vkCmdBindVertexBuffers2(uint64_t, uint32_t first, uint32_t count, uint64_t* b, uint64_t* o, uint64_t* z, uint64_t* st) {
    for (uint32_t i = 0; i < count; i++)
        g_vb.push_back({first + i, 1, b[i], o[i], z[i], st[i]});
}

static void run_vb_site() {
VB_SITE
}

static void fill(uint64_t buffer, uint64_t offset, uint64_t size, uint64_t stride, int index, uint64_t alt) {
    for (int i = 0; i < 16; i++) {
        vbufs[i] = buffer; voffs[i] = offset; vsizes[i] = size; vstrides[i] = stride;
    }
    if (index >= 0) {
        if (alt == 0) vbufs[index] = 9;
        if (alt == 1) voffs[index] = 9;
        if (alt == 2) vsizes[index] = 9;
        if (alt == 3) vstrides[index] = 9;
    }
}

int run_extracted_vertex_cases() {
    auto oracle_eq = []() {
        for (int i = 0; i < 16; i++) {
            uint64_t b = 0, o = 0, z = 0, s = 0;
            bool seen = false;
            for (const auto& c : g_vb) if ((int)c.first == i) { b = c.buffer; o = c.offset; z = c.size; s = c.stride; seen = true; }
            if (!seen && last_vb_valid) { b = last_vb.b[i]; o = last_vb.o[i]; z = last_vb.z[i]; s = last_vb.s[i]; seen = true; }
            if (!seen || b != vbufs[i] || o != voffs[i] || z != vsizes[i] || s != vstrides[i]) return false;
        }
        return true;
    };
    narrow = true;
    last_vb_valid = false;
    fill(1, 2, 3, 4, -1, 0);
    g_vb.clear();
    run_vb_site();
    if (g_vb.size() != 16 || !oracle_eq()) return 21;
    for (int i = 0; i < 16; i++) { last_vb.b[i] = vbufs[i]; last_vb.o[i] = voffs[i]; last_vb.z[i] = vsizes[i]; last_vb.s[i] = vstrides[i]; }
    last_vb_valid = true;
    for (uint64_t field = 0; field < 4; field++) {
        fill(1, 2, 3, 4, 7, field);
        g_vb.clear();
        run_vb_site();
        if (g_vb.size() != 1 || g_vb[0].first != 7 || !oracle_eq()) return 30 + (int)field;
        last_vb.b[7] = vbufs[7]; last_vb.o[7] = voffs[7]; last_vb.z[7] = vsizes[7]; last_vb.s[7] = vstrides[7];
    }
    fill(1, 2, 3, 4, -1, 0);
    for (int i : {0, 2, 4, 6}) vbufs[i] = 40 + i;
    g_vb.clear();
    run_vb_site();
    if (g_vb.size() != 16 || !oracle_eq()) return 40;
    narrow = false;
    g_vb.clear();
    run_vb_site();
    if (g_vb.size() != 16) return 41;
    last_vb_valid = false;
    narrow = true;
    g_vb.clear();
    run_vb_site();
    if (g_vb.size() != 16 || !oracle_eq()) return 42;
    return 0;
}
'''
        sites = sites.replace("PUSH_SITE", push_site).replace("VB_SITE", vb_site)
        with tempfile.TemporaryDirectory(prefix="oo-grok-replay-") as tmp:
            inc = Path(tmp)
            (inc / "grok-replay-bind.h").write_text(header)
            (inc / "grok_replay_sites.inc.cpp").write_text(sites)
            exe = inc / "harness"
            cmd = [
                "g++", "-std=c++20", "-Wall", "-Wextra", "-Werror", "-O2", "-DNDEBUG",
                "-I", str(inc), str(HARNESS), str(inc / "grok_replay_sites.inc.cpp"), "-o", str(exe),
            ]
            subprocess.run(cmd, check=True, capture_output=True, text=True, timeout=60)
            out = subprocess.run([str(exe)], check=True, capture_output=True, text=True, timeout=10)
            self.assertEqual(out.stdout.strip(), "ok")

    @unittest.skipUnless(shutil.which("g++"), "g++ unavailable")
    def test_env_on_expressions_compile_opt_in(self):
        push = re.search(
            r"bool grok_replay_push_enabled\(\) \{\n\+    static const bool on = (.*);\n\+    return on;\n\+\}",
            self.patch,
        )
        bind = re.search(r"static const bool narrow = (.*);", self.patch)
        src = """#include <cstdlib>
#include <cstring>
static bool env_on(const char *name) {
 const char *v=std::getenv(name);return v && *v && std::strcmp(v,"0");
}
int main() { bool p=PUSH_EXPR; bool b=BIND_EXPR;return p? (b?3:1):(b?2:0); }
""".replace("PUSH_EXPR", push.group(1)).replace("BIND_EXPR", bind.group(1))
        with tempfile.TemporaryDirectory(prefix="oo-replay-controls-") as tmp:
            build = Path(tmp) / "test.cpp"
            exe = Path(tmp) / "test"
            build.write_text(src)
            subprocess.run(
                ["g++", "-std=c++17", "-Wall", "-Wextra", "-Werror", str(build), "-o", str(exe)],
                check=True, capture_output=True, text=True, timeout=30,
            )
            for setting, expected in ((None, 0), ("0", 0), ("1", 3)):
                with self.subTest(setting=setting):
                    env = os.environ.copy()
                    for key in ("RX_VK_GROK_REPLAY_PUSH", "RX_VK_GROK_REPLAY_BIND"):
                        env.pop(key, None)
                        if setting is not None:
                            env[key] = setting
                    result = subprocess.run([str(exe)], env=env, timeout=5)
                    self.assertEqual(result.returncode, expected)


if __name__ == "__main__":
    unittest.main()
