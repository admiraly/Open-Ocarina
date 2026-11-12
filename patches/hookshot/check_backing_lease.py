#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Compile the exact backing-lease helper from the patch and exercise real host mappings.

No Vulkan device, game process, proprietary inputs or shared checkout edits.
"""
from __future__ import annotations

import argparse
import ast
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile

PIN = "4cf785a0b143bb34026506c9a3fe4f4bb93c797d"
PATCH = Path(__file__).with_name("backing-lease.patch")
CASES = ("basic", "alias", "discard", "failure", "replacement", "partial", "capacity",
         "events", "serial", "thread", "fd-reuse", "disabled", "default")
EXPORT_FLAG = "-Wl,--export-dynamic-symbol=hos_capture_backing_*"
EXPORTS = {"hos_capture_backing_acquire", "hos_capture_backing_validate", "hos_capture_backing_release", "hos_capture_backing_events"}


def helper_from_patch(patch=PATCH):
    lines = patch.read_text().splitlines(keepends=True)
    marker = "+++ b/host/capture_backing.h\n"
    try:
        start = lines.index(marker) + 1
    except ValueError as exc:
        raise ValueError("Patch does not add the production backing helper") from exc
    match = re.fullmatch(r"@@ -0,0 \+1,(\d+) @@\n", lines[start])
    if not match:
        raise ValueError("Unsupported new helper patch framing")
    payload = lines[start + 1:start + 1 + int(match[1])]
    if len(payload) != int(match[1]) or any(not line.startswith("+") for line in payload):
        raise ValueError("Truncated or malformed production helper")
    return "".join(line[1:] for line in payload)


def check_patch(checkout, patch=PATCH):
    """Apply only to a disposable index; the checkout's source and real index stay untouched."""
    checkout = checkout.resolve()
    with tempfile.TemporaryDirectory(prefix="oo-backing-index-") as temporary:
        index = Path(temporary) / "index"
        env = {**os.environ, "GIT_INDEX_FILE": str(index)}
        for args in (("read-tree", PIN), ("apply", "--cached", "--check", str(patch.resolve())),
                     ("apply", "--cached", str(patch.resolve()))):
            subprocess.run(["git", "-C", str(checkout), *args], env=env, check=True,
                           capture_output=True, text=True, timeout=30)
        source = subprocess.check_output(["git", "-C", str(checkout), "show", ":host/capture_backing.h"], env=env, text=True)
        if source != helper_from_patch(patch):
            raise ValueError("Applied helper differs from the production helper compiled by this checker")
        build = subprocess.check_output(["git", "-C", str(checkout), "show", ":nx2/build.py"], env=env, text=True)
        tree = ast.parse(build)
        linux = [node for node in ast.walk(tree) if isinstance(node, ast.If) and ast.unparse(node.test) == "a.standalone and (not vk_only) and LINUX"]
        # Python may omit parenthesis around `not` in the normalized expression.
        if not linux:
            linux = [node for node in ast.walk(tree) if isinstance(node, ast.If) and ast.unparse(node.test) == "a.standalone and not vk_only and LINUX"]
        if len(linux) != 1 or EXPORT_FLAG not in {node.value for statement in linux[0].body for node in ast.walk(statement) if isinstance(node, ast.Constant) and isinstance(node.value, str)}:
            raise ValueError("Production Linux standalone linker recipe does not export backing APIs")
    return {"base": PIN, "patch_applies": True, "shared_checkout_modified": False}


def export_flags_from_patch(patch=PATCH):
    added = "\n".join(line[1:] for line in patch.read_text().splitlines() if line.startswith("+") and not line.startswith("+++"))
    if added.count('"' + EXPORT_FLAG + '"') != 1:
        raise ValueError("Production backing API dynamic export rule is missing or ambiguous")
    return [EXPORT_FLAG]


def exported_apis(binary):
    nm = shutil.which("nm")
    if not nm:
        raise ValueError("Binutils nm is required to verify dynamic API exports")
    output = subprocess.check_output([nm, "-D", "--defined-only", str(binary)], text=True, timeout=15)
    names = {line.split()[-1] for line in output.splitlines() if line.split()}
    if not EXPORTS <= names:
        raise ValueError("Backing API dynamic exports are missing from the optimized executable")
    return sorted(EXPORTS)


HARNESS = r'''
#define HOS_CAPTURE_BACKING_IMPLEMENTATION
#include "capture_backing.h"
#include <stdio.h>
#include <sys/types.h>
#define CHECK(x) do { if(!(x)){fprintf(stderr,"check failed at line %d: %s\n",__LINE__,#x);return 3;} }while(0)
static size_t page;
static int fd;
static unsigned char* p;
static uint64_t guest=0;
static int fixture(void) {
    page=(size_t)sysconf(_SC_PAGESIZE);
    fd=memfd_create("oo-backing-test",MFD_CLOEXEC);
    CHECK(fd>=0&&ftruncate(fd,(off_t)(page*4))==0);
    p=mmap(NULL,page*2,PROT_READ|PROT_WRITE,MAP_SHARED,fd,0);CHECK(p!=MAP_FAILED);
    return 0;
}
static int acquire(uint64_t g,uint64_t n,HosCaptureBackingInfoV1* info){return hos_capture_backing_acquire(1,g,n,info,sizeof(*info));}
static int validate(uint64_t lease,HosCaptureBackingInfoV1* info){return hos_capture_backing_validate(1,lease,info,sizeof(*info));}
static void* discard_thread(void* argument){(void)argument;return (void*)(intptr_t)oo_backing_madvise(p,page,MADV_REMOVE);}
int main(int argc,char** argv) {
    CHECK(argc==2&&fixture()==0);const char* mode=argv[1];HosCaptureBackingInfoV1 a,b,c;memset(&a,0,sizeof(a));
    int registered=oo_backing_mapping_committed(guest,p,page*2,fd,0);
    if(!strcmp(mode,"disabled")||!strcmp(mode,"default")){
        CHECK(registered==HOS_BACKING_DISABLED);CHECK(acquire(guest,page,&a)==HOS_BACKING_DISABLED);
        p[0]=91;CHECK(oo_backing_madvise(p,page,MADV_REMOVE)==0&&p[0]==0);return 0;
    }
    CHECK(registered==HOS_BACKING_OK);CHECK(acquire(guest,page,&a)==HOS_BACKING_OK);
    CHECK(a.abi_version==1&&a.struct_size==sizeof(a)&&a.kind==HOS_BACKING_FILE&&a.backing_offset==0&&a.size==page);
    if(!strcmp(mode,"basic")){
        CHECK(validate(a.lease_id,&b)==HOS_BACKING_OK&&b.backing_id==a.backing_id);
        CHECK(hos_capture_backing_acquire(2,guest,page,&b,sizeof(b))==HOS_BACKING_ARGUMENT);
        CHECK(hos_capture_backing_acquire(1,guest,page,&b,sizeof(b)-1)==HOS_BACKING_ARGUMENT);
        CHECK(hos_capture_backing_acquire(1,UINT64_MAX,page,&b,sizeof(b))==HOS_BACKING_ARGUMENT);
        CHECK(hos_capture_backing_release(1,a.lease_id)==HOS_BACKING_OK);
        CHECK(validate(a.lease_id,&b)==HOS_BACKING_UNKNOWN);CHECK(hos_capture_backing_release(1,a.lease_id)==HOS_BACKING_UNKNOWN);return 0;
    }
    if(!strcmp(mode,"alias")||!strcmp(mode,"discard")){
        unsigned char* q=mmap(NULL,page,PROT_READ|PROT_WRITE,MAP_SHARED,fd,0);CHECK(q!=MAP_FAILED);
        CHECK(oo_backing_mapping_committed(page*8,q,page,fd,0)==HOS_BACKING_OK);
        CHECK(acquire(page*8,page,&b)==HOS_BACKING_OK&&b.backing_id==a.backing_id&&b.mapping_id!=a.mapping_id&&b.backing_offset==a.backing_offset);
        CHECK(acquire(page,page,&c)==HOS_BACKING_OK);
        if(!strcmp(mode,"alias")){CHECK(a.lease_id!=b.lease_id);return 0;}
        p[0]=83;CHECK(oo_backing_madvise(p,page,MADV_REMOVE)==0&&p[0]==0&&q[0]==0);
        CHECK(validate(a.lease_id,&a)==HOS_BACKING_STALE&&a.invalid_reason==HOS_BACKING_DISCARDED);
        CHECK(validate(b.lease_id,&b)==HOS_BACKING_STALE&&b.invalid_reason==HOS_BACKING_DISCARDED);
        CHECK(validate(c.lease_id,&c)==HOS_BACKING_OK&&c.current_generation>c.acquired_generation);
        HosCaptureBackingEventV1 events[8];uint64_t next=0;
        int n=hos_capture_backing_events(1,0,events,8,sizeof(*events),&next);CHECK(n==3&&events[n-1].kind==HOS_BACKING_DISCARD_COMMIT&&events[n-1].backing_offset==0&&events[n-1].size==page);return 0;
    }
    if(!strcmp(mode,"failure")){
        p[0]=77;errno=0;CHECK(oo_backing_madvise(p+1,page,MADV_REMOVE)==-1&&errno==EINVAL&&p[0]==77);
        CHECK(validate(a.lease_id,&a)==HOS_BACKING_STALE);CHECK(acquire(guest,page,&b)==HOS_BACKING_UNKNOWN);
        CHECK(hos_capture_backing_release(1,a.lease_id)==HOS_BACKING_OK);return 0;
    }
    if(!strcmp(mode,"replacement")||!strcmp(mode,"partial")){
        int other=memfd_create("oo-other-test",MFD_CLOEXEC);CHECK(other>=0&&ftruncate(other,(off_t)(page*2))==0);
        const size_t off=!strcmp(mode,"partial")?page:0,size=!strcmp(mode,"partial")?page:page*2;
        CHECK(mmap(p+off,size,PROT_READ|PROT_WRITE,MAP_SHARED|MAP_FIXED,other,0)==p+off);
        int result=oo_backing_mapping_committed(guest+off,p+off,size,other,0);
        CHECK(validate(a.lease_id,&a)==HOS_BACKING_STALE);
        if(off){CHECK(result==HOS_BACKING_UNKNOWN&&acquire(guest,page,&b)==HOS_BACKING_UNKNOWN);return 0;}
        CHECK(result==HOS_BACKING_OK&&a.invalid_reason==HOS_BACKING_REMAPPED);
        CHECK(acquire(guest,page,&b)==HOS_BACKING_OK&&b.backing_id!=a.backing_id&&b.mapping_id!=a.mapping_id);return 0;
    }
    if(!strcmp(mode,"capacity")){
        uint64_t leases[HOS_BACKING_MAX_LEASES];leases[0]=a.lease_id;
        for(unsigned i=1;i<HOS_BACKING_MAX_LEASES;++i){CHECK(acquire(guest,page,&b)==HOS_BACKING_OK);leases[i]=b.lease_id;}
        CHECK(acquire(guest,page,&b)==HOS_BACKING_CAPACITY);CHECK(validate(a.lease_id,&a)==HOS_BACKING_OK);
        CHECK(hos_capture_backing_release(1,leases[0])==HOS_BACKING_OK);CHECK(acquire(guest,page,&b)==HOS_BACKING_OK&&b.lease_id!=leases[0]);
        CHECK(validate(leases[0],&a)==HOS_BACKING_UNKNOWN);return 0;
    }
    if(!strcmp(mode,"events")){
        for(unsigned i=0;i<HOS_BACKING_MAX_EVENTS+1;++i)CHECK(oo_backing_madvise(p,page,MADV_REMOVE)==0);
        CHECK(validate(a.lease_id,&b)==HOS_BACKING_STALE&&acquire(guest,page,&c)==HOS_BACKING_UNKNOWN);return 0;
    }
    if(!strcmp(mode,"serial")){
        oo_backing_state->serial=UINT64_MAX;CHECK(acquire(guest,page,&b)==HOS_BACKING_UNKNOWN);
        CHECK(validate(a.lease_id,&c)==HOS_BACKING_STALE);return 0;
    }
    if(!strcmp(mode,"thread")){
        pthread_t t;void* result=(void*)1;CHECK(pthread_create(&t,NULL,discard_thread,NULL)==0);
        CHECK(pthread_join(t,&result)==0&&(intptr_t)result==0);CHECK(validate(a.lease_id,&b)==HOS_BACKING_STALE);return 0;
    }
    if(!strcmp(mode,"fd-reuse")){
        CHECK(close(fd)==0);int other=memfd_create("oo-reused-fd",MFD_CLOEXEC);CHECK(other>=0&&ftruncate(other,(off_t)(page*2))==0);
        unsigned char* q=mmap(NULL,page*2,PROT_READ|PROT_WRITE,MAP_SHARED,other,0);CHECK(q!=MAP_FAILED);
        CHECK(oo_backing_mapping_committed(page*8,q,page*2,other,0)==HOS_BACKING_OK);
        CHECK(acquire(page*8,page,&b)==HOS_BACKING_OK&&b.backing_id!=a.backing_id&&validate(a.lease_id,&a)==HOS_BACKING_OK);return 0;
    }
    return 4;
}
'''


def compile_helper(directory, compiler="cc", patch=PATCH):
    if not sys.platform.startswith("linux"):
        raise ValueError("Production backing observer tests require Linux")
    cc = shutil.which(compiler)
    if not cc:
        raise ValueError("A C11 compiler is required")
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "capture_backing.h").write_text(helper_from_patch(patch))
    (directory / "check.c").write_text(HARNESS)
    output = directory / "check-backing-lease"
    command = [cc, "-std=c11", "-D_GNU_SOURCE", "-O2", "-DNDEBUG", "-Wall", "-Wextra", "-Werror", "-pthread",
               "-ffunction-sections", "-Wl,--gc-sections", *export_flags_from_patch(patch),
               "-DHOS_BACKING_MAX_LEASES=4", "-DHOS_BACKING_MAX_EVENTS=8", str(directory / "check.c"), "-o", str(output)]
    subprocess.run(command, check=True, capture_output=True, text=True, timeout=60)
    exported_apis(output)
    return output


def run_case(binary, case):
    if case not in CASES:
        raise ValueError("Unknown backing observer test")
    env = dict(os.environ)
    env.pop("HOS_OO_BACKING_LEASE", None)
    if case != "default":
        env["HOS_OO_BACKING_LEASE"] = "0" if case == "disabled" else "1"
    result = subprocess.run([str(binary), case], env=env, capture_output=True, text=True, timeout=15)
    if result.returncode:
        raise ValueError(f"{case} failed: {result.stderr.strip()}")
    return {"case": case, "passed": True}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--checkout", type=Path)
    parser.add_argument("--compiler", default="cc")
    args = parser.parse_args()
    try:
        report = {"gpu_work": False, "full_host_build": False}
        if args.checkout:
            report["patch"] = check_patch(args.checkout)
        with tempfile.TemporaryDirectory(prefix="oo-backing-tests-") as temporary:
            binary = compile_helper(Path(temporary), args.compiler)
            report["optimized_production_helper_tests"] = [run_case(binary, case) for case in CASES]
            report["dynamic_exports"] = exported_apis(binary)
        report["scope"] = "Host backing observation and diagnostic lease invalidation; no renderer import leases, sparse bind ledger, CPU quiescence or replay claimed"
        print(json.dumps(report, indent=2))
        return 0
    except (OSError, ValueError, subprocess.SubprocessError) as exc:
        detail = exc.stderr if isinstance(exc, subprocess.CalledProcessError) else str(exc)
        print(detail, file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
