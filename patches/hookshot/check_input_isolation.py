#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Game-free optimized policy tests and optional production mouse/control checks."""
import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile

PIN = "4cf785a0b143bb34026506c9a3fe4f4bb93c797d"
PATCH = Path(__file__).with_name("input-isolation.patch")


def header_from_patch():
    capture, lines = False, []
    for line in PATCH.read_text().splitlines(keepends=True):
        if line.startswith("diff --git "):
            capture = line.rstrip().endswith(" b/host/input_isolation.h")
        elif capture and line.startswith("+") and not line.startswith("+++"):
            lines.append(line[1:])
    result = "".join(lines)
    if "hos_script_exclusive_policy" not in result:
        raise ValueError("Input-isolation header missing from patch")
    return result


def function(source, name):
    start = source.index("int " + name + "(")
    opening = source.index("{", start)
    depth = 1
    for end in range(opening + 1, len(source)):
        if source[end] == "{":
            depth += 1
        elif source[end] == "}":
            depth -= 1
            if not depth:
                return source[start:end + 1]
    raise ValueError("Unterminated function")


POLICY_TEST = r'''
#include "input_isolation.h"
#include <stdio.h>
#define CHECK(x) do { if (!(x)) return __LINE__; } while (0)
int main(void) {
    CHECK(!hos_script_exclusive_policy(0,0,1,0,100));
    CHECK(!hos_script_exclusive_policy(0,1,1,0,100));
    CHECK(!hos_script_exclusive_policy(1,1,0,0,100));
    CHECK(!hos_script_exclusive_policy(1,0,-1,0,100));
    CHECK(hos_script_exclusive_policy(1,0,1,0,100));
    CHECK(hos_script_exclusive_policy(1,0,1,5099,100));
    CHECK(!hos_script_exclusive_policy(1,0,1,5100,100));
    CHECK(!hos_script_exclusive_policy(1,0,1,6000,100));
    CHECK(hos_script_exclusive_policy(1,1,1,6000,100));
    CHECK(hos_script_exclusive_policy(1,0,1,INT64_MAX,INT64_MAX));
    CHECK(!hos_script_exclusive_policy(1,1,1,0,-1));
    CHECK(!hos_control_exclusive_policy(0,1,-1,2,0));
    CHECK(!hos_control_exclusive_policy(1,0,-1,2,0));
    CHECK(!hos_control_exclusive_policy(1,1,0,0,0));
    CHECK(hos_control_exclusive_policy(1,1,-1,2,10));
    CHECK(hos_control_exclusive_policy(1,1,20,2,10));
    CHECK(hos_control_exclusive_policy(1,1,20,2,21));
    CHECK(!hos_control_exclusive_policy(1,1,20,2,22));
    CHECK(hos_control_exclusive_policy(1,1,INT64_MAX-3,8,INT64_MAX));
    puts("policy assertions passed"); return 0;
}
'''

HOS_H = r'''
#pragma once
#include <stdint.h>
#include <stddef.h>
#define HOOKSHOT_ENV_H 1
static inline int env_on(const char *name) { (void)name; return 0; }
extern uint8_t fake_heap[4096];
#define GP(a) ((void *)(fake_heap + (uint64_t)(a)))
void hos_log(const char *fmt, ...);
int64_t hos_now_ns(void);
'''

WINDOWS_H = r'''
#pragma once
#include <stdint.h>
typedef int32_t LONG;
typedef int64_t LONG64;
static inline LONG InterlockedExchange(volatile LONG *p,LONG v) { LONG old=*p;*p=v;return old; }
static inline LONG64 InterlockedExchange64(volatile LONG64 *p,LONG64 v) { LONG64 old=*p;*p=v;return old; }
static inline LONG64 InterlockedCompareExchange64(volatile LONG64 *p,LONG64 v,LONG64 c) {
 LONG64 old=*p;if(old==c)*p=v;return old;
}
'''

MOUSE_TEST = r'''
#include "hos.h"
#include <stdio.h>
#include <string.h>
#include <math.h>
uint8_t fake_heap[4096];
double hos_mouse_sens=1;
static int exclusive,native=1,takes;
static int32_t mx,my;
void hos_log(const char *format,...) { (void)format; }
int64_t hos_now_ns(void) { return 1000000000; }
int rx_option(int id,int otherwise) { return id==6?native:otherwise; }
int hos_input_player_exclusive(void) { return exclusive; }
int hos_mouse_take(int32_t *x,int32_t *y) { *x=mx;*y=my;mx=my=0;takes++;return 1; }
void hos_mouse_give_back(int32_t x,int32_t y) { mx+=x;my+=y; }
void hos_mouse_camera(uint64_t);
int hos_mouse_native(void);
static void setf(unsigned at,float value) { memcpy(fake_heap+at,&value,4); }
static float getf(unsigned at) { float value;memcpy(&value,fake_heap+at,4);return value; }
#define CAM 0x100
#define YAW (CAM+0x1a4)
#define PITCH (CAM+0x18c)
#define CHECK(x) do { if (!(x)) return __LINE__; } while (0)
int main(void) {
 int state=3;memcpy(fake_heap+CAM+0x204,&state,4);
 setf(CAM+0x178,-85);setf(CAM+0x17c,85);setf(YAW,10);setf(PITCH,5);
 mx=10;hos_mouse_camera(CAM);CHECK(fabsf(getf(YAW)-9)<0.001f);
 exclusive=1;mx=200;my=100;int before=takes;hos_mouse_camera(CAM);
 CHECK(takes==before+1 && mx==0 && my==0);CHECK(fabsf(getf(YAW)-9)<0.001f);
 setf(YAW,40);setf(PITCH,20);mx=99;hos_mouse_camera(0);CHECK(mx==0);
 mx=80;CHECK(hos_mouse_native()==0 && mx==0);
 exclusive=0;hos_mouse_camera(CAM);
 CHECK(fabsf(getf(YAW)-40)<0.001f && fabsf(getf(PITCH)-20)<0.001f);
 mx=10;hos_mouse_camera(CAM);CHECK(fabsf(getf(YAW)-39)<0.001f);
 native=0;mx=10;before=takes;hos_mouse_camera(CAM);CHECK(takes==before && mx==10);
 puts("production mouse isolation assertions passed");return 0;
}
'''

CONTROL_PREFIX = r'''
#include "input_isolation.h"
#include <stdint.h>
#include <stdio.h>
typedef struct { int used,exclusive; int64_t start,frames; } Input;
#define CTL_INPUTS 4
static int64_t frame=10;
static int64_t get_frame(void) { return frame; }
static struct { int64_t (*frame)(void); } game={get_frame};
static struct { int mu; typeof(game) *game; Input inputs[CTL_INPUTS]; } G={0,&game,{{0}}};
static int hos_control_on,locks;
static void ctl_lock(int *lock) { (void)lock;locks++; }
static void ctl_unlock(int *lock) { (void)lock;locks--; }
'''

CONTROL_TEST = r'''
#define CHECK(x) do { if (!(x)) return __LINE__; } while (0)
int main(void) {
 CHECK(!hos_control_input_exclusive());
 hos_control_on=1;G.inputs[0]=(Input){1,1,-1,4};
 CHECK(hos_control_input_exclusive() && G.inputs[0].start==-1 && frame==10 && locks==0);
 G.inputs[0].start=20;frame=23;CHECK(hos_control_input_exclusive());
 frame=24;CHECK(!hos_control_input_exclusive());
 CHECK(G.inputs[0].used && G.inputs[0].start==20 && G.inputs[0].frames==4 && locks==0);
 G.inputs[0].used=0;CHECK(!hos_control_input_exclusive());
 G.game=0;CHECK(!hos_control_input_exclusive());
 puts("read-only control lease assertions passed");return 0;
}
'''

SCRIPT_PREFIX = r'''
#include "input_isolation.h"
#include <stdint.h>
#include <stdio.h>
#include <string.h>
static int enabled,persistent,parse_count=1,npresses=-1,parses,bridge;
static int64_t elapsed,script_end_ms=100;
static int env_on(const char *name) { return !strcmp(name,"HOS_INPUT_EXCLUSIVE")?enabled:persistent; }
static void parse_script(void) { parses++;npresses=parse_count; }
static int64_t hos_now_ns(void) { return 0; }
static int64_t script_elapsed_ms(int64_t now) { (void)now;return elapsed; }
static int hos_control_input_exclusive(void) { return bridge; }
'''

SCRIPT_TEST = r'''
#define CHECK(x) do { if (!(x)) return __LINE__; } while (0)
int main(int argc,char **argv) {
 if(argc!=2)return 2;
 if(!strcmp(argv[1],"default")) {
   CHECK(!hos_input_player_exclusive() && parses==0);
   bridge=1;CHECK(hos_input_player_exclusive() && parses==0);
 } else if(!strcmp(argv[1],"window")) {
   enabled=1;elapsed=1000;CHECK(hos_input_player_exclusive() && parses==1);
   elapsed=5099;CHECK(hos_input_player_exclusive() && parses==1);
   elapsed=5100;CHECK(!hos_input_player_exclusive() && parses==1);
   bridge=1;CHECK(hos_input_player_exclusive());
 } else if(!strcmp(argv[1],"persistent")) {
   enabled=persistent=1;elapsed=INT64_MAX;CHECK(hos_input_player_exclusive() && parses==1);
   npresses=0;CHECK(!hos_input_player_exclusive());
 } else if(!strcmp(argv[1],"persistent-alone")) {
   persistent=1;CHECK(!hos_input_player_exclusive() && parses==0);
 } else if(!strcmp(argv[1],"empty")) {
   enabled=1;parse_count=0;CHECK(!hos_input_player_exclusive() && parses==1);
 } else return 3;
 puts("production script ownership assertions passed");return 0;
}
'''


def check(checkout=None, compiler=None):
    compiler = compiler or shutil.which("cc") or shutil.which("clang")
    if not compiler:
        raise ValueError("A C compiler is required")
    if checkout is not None:
        head = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=checkout, text=True).strip()
        if head != PIN:
            raise ValueError("Checkout is not the documented pin")
        subprocess.run(["git", "apply", "--cached", "--check", str(PATCH.resolve())], cwd=checkout, check=True)
    flags = ["-std=gnu11", "-O2", "-DNDEBUG", "-Wall", "-Wextra", "-Werror"]
    with tempfile.TemporaryDirectory(prefix="oo-input-") as temporary:
        work = Path(temporary)
        (work / "input_isolation.h").write_text(header_from_patch())
        source, executable = work / "policy.c", work / "policy"
        source.write_text(POLICY_TEST)
        subprocess.run([compiler, *flags, str(source), "-o", str(executable)], check=True, timeout=30)
        subprocess.run([str(executable)], check=True, timeout=5)
        if checkout is not None:
            (work / "hos.h").write_text(HOS_H)
            (work / "windows.h").write_text(WINDOWS_H)
            mouse = (checkout / "host/mouse_native.c").read_text()
            if "hos_input_player_exclusive()" not in mouse:
                raise ValueError("Apply input-isolation.patch before checking production source")
            (work / "mouse_native.c").write_text(mouse)
            source.write_text(MOUSE_TEST)
            subprocess.run([compiler, *flags, "-I", str(work), str(source), str(work / "mouse_native.c"),
                            "-lm", "-o", str(executable)], check=True, timeout=30)
            env = os.environ.copy()
            env.pop("HOS_MOUSE_NATIVE", None)
            env.pop("HOS_MOUSE_INVERT_Y", None)
            subprocess.run([str(executable)], env=env, check=True, timeout=5)
            control = function((checkout / "host/control.c").read_text(), "hos_control_input_exclusive")
            source.write_text(CONTROL_PREFIX + control + CONTROL_TEST)
            subprocess.run([compiler, *flags, "-I", str(work), str(source), "-o", str(executable)], check=True, timeout=30)
            subprocess.run([str(executable)], check=True, timeout=5)
            script = function((checkout / "host/nn_hid.c").read_text(), "hos_input_player_exclusive")
            source.write_text(SCRIPT_PREFIX + script + SCRIPT_TEST)
            subprocess.run([compiler, *flags, "-I", str(work), str(source), "-o", str(executable)], check=True, timeout=30)
            for case in ("default", "window", "persistent", "persistent-alone", "empty"):
                subprocess.run([str(executable), case], check=True, timeout=5)
    return {"optimized_policy_check": True, "pinned_apply_check": checkout is not None,
            "production_mouse_drain_and_release_check": checkout is not None,
            "read_only_control_lease_check": checkout is not None,
            "production_script_ownership_cases": 5 if checkout is not None else 0,
            "scope": "game-free tests; no live game, motion-control or complete camera isolation claim"}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--checkout", type=Path)
    parser.add_argument("--compiler")
    args = parser.parse_args()
    print(json.dumps(check(args.checkout, args.compiler), indent=2))


if __name__ == "__main__":
    main()
