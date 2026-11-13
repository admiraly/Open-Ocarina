// SPDX-License-Identifier: GPL-3.0-or-later
// Compiles the replay header extracted from grok-replay-vertex-bind.patch and
// the patched callsite bodies the Python test pastes in as grok_replay_sites.inc.
// Compares vertex and push results with an independent full-bind / always-push oracle.

#include "grok-replay-bind.h"

#include <cstdint>
#include <cstdio>
#include <cstring>
#include <vector>

int run_extracted_push_cases();
int run_extracted_vertex_cases();

namespace {

struct Slot {
    uint64_t buffer, offset, size, stride;
    bool operator==(const Slot& o) const {
        return buffer == o.buffer && offset == o.offset && size == o.size && stride == o.stride;
    }
};

enum class Kind { Draw, Barrier, Desc, ForeignPush, NewCmd };

struct Step {
    Kind kind;
    uint64_t cmd = 1;
    uint64_t layout = 10;
    uint32_t stages = 1;
    bool selected = true;
    uint8_t push[32]{};
    Slot slots[16]{};
};

struct Call {
    char type;  // B bind, P push, D draw, R barrier, S descriptor, O observer, F foreign
    int first = 0;
    int count = 0;
    uint64_t a = 0, b = 0;
    uint32_t stages = 0;
    uint8_t bytes[32]{};
};

struct Policy {
    grok_replay::PushRecord cache;
    bool vb_valid = false;
    grok_replay::VertexSlot prev[16]{};
    Slot bound[16]{};
    bool narrow = true;
    bool push_on = true;
    std::vector<Call> calls;

    void barrier() { calls.push_back({'R'}); }
    void desc(uint64_t set) { calls.push_back({'S', 0, 0, set}); }
    void draw_marker(int i) { calls.push_back({'D', 0, 0, (uint64_t)i}); }

    void foreign(uint64_t cmd, uint64_t layout, uint32_t stages) {
        Call c{'F'};
        c.a = cmd;
        c.b = layout;
        c.stages = stages;
        calls.push_back(c);
        grok_replay::push_invalidate(cache);
    }

    void new_cmd() {
        vb_valid = false;
        grok_replay::push_invalidate(cache);
        calls.push_back({'N'});
    }

    void vertex(const Slot* next_slots) {
        grok_replay::VertexSlot next[16];
        for (int i = 0; i < 16; i++)
            next[i] = {next_slots[i].buffer, next_slots[i].offset, next_slots[i].size, next_slots[i].stride};
        if (!vb_valid) {
            for (int i = 0; i < 16; i++) bound[i] = {};
        }
        grok_replay::issue_vertex_ranges(narrow, vb_valid, next, prev, [&](int first, int count) {
            Call c{'B'};
            c.first = first;
            c.count = count;
            calls.push_back(c);
            for (int i = 0; i < count; i++) bound[first + i] = next_slots[first + i];
        });
        for (int i = 0; i < 16; i++) prev[i] = next[i];
        vb_valid = true;
    }

    void push(uint64_t cmd, uint64_t layout, uint32_t stages, const uint8_t* data, bool selected) {
        const bool issue = grok_replay::push_should_issue(cache, push_on, cmd, layout, stages, data, 32);
        if (issue) {
            Call c{'P'};
            c.a = cmd;
            c.b = layout;
            c.stages = stages;
            std::memcpy(c.bytes, data, 32);
            calls.push_back(c);
        }
        if (selected) {
            Call c{'O'};
            c.a = cmd;
            c.b = layout;
            c.stages = stages;
            std::memcpy(c.bytes, data, 32);
            calls.push_back(c);
        }
    }
};

struct Oracle {
    bool vb_valid = false;
    Slot bound[16]{};
    bool push_defined = false;
    uint8_t push[32]{};
    uint64_t cmd = 0, layout = 0;
    uint32_t stages = 0;
    int draws = 0;
    int barriers = 0;
    int descs = 0;
    int observers = 0;
    std::vector<Call> calls;

    void run(const Step& s) {
        if (s.kind == Kind::Barrier) {
            barriers++;
            calls.push_back({'R'});
            return;
        }
        if (s.kind == Kind::Desc) {
            descs++;
            calls.push_back({'S'});
            return;
        }
        if (s.kind == Kind::NewCmd) {
            vb_valid = false;
            push_defined = false;
            calls.push_back({'N'});
            return;
        }
        if (s.kind == Kind::ForeignPush) {
            push_defined = false;
            calls.push_back({'F'});
            return;
        }
        draws++;
        calls.push_back({'D', 0, 0, (uint64_t)draws});
        bool changed = !vb_valid;
        for (int i = 0; i < 16 && !changed; i++) changed = !(bound[i] == s.slots[i]);
        if (changed) {
            Call c{'B'};
            c.first = 0;
            c.count = 16;
            calls.push_back(c);
            for (int i = 0; i < 16; i++) bound[i] = s.slots[i];
            vb_valid = true;
        }
        Call p{'P'};
        p.a = s.cmd;
        p.b = s.layout;
        p.stages = s.stages;
        std::memcpy(p.bytes, s.push, 32);
        calls.push_back(p);
        std::memcpy(push, s.push, 32);
        cmd = s.cmd;
        layout = s.layout;
        stages = s.stages;
        push_defined = true;
        if (s.selected) {
            observers++;
            calls.push_back({'O'});
        }
    }
};

int fail(const char* msg) {
    std::fprintf(stderr, "harness: %s\n", msg);
    return 1;
}

int stream_case(bool narrow, bool push_on, const std::vector<Step>& steps) {
    Policy policy;
    policy.narrow = narrow;
    policy.push_on = push_on;
    Oracle oracle;
    int draws = 0, barriers = 0, descs = 0;
    for (const Step& s : steps) {
        if (s.kind == Kind::Barrier) {
            policy.barrier();
            oracle.run(s);
            barriers++;
            continue;
        }
        if (s.kind == Kind::Desc) {
            policy.desc(1);
            oracle.run(s);
            descs++;
            continue;
        }
        if (s.kind == Kind::NewCmd) {
            policy.new_cmd();
            oracle.run(s);
            continue;
        }
        if (s.kind == Kind::ForeignPush) {
            policy.foreign(s.cmd, s.layout, s.stages);
            oracle.run(s);
            continue;
        }
        draws++;
        policy.draw_marker(draws);
        policy.vertex(s.slots);
        policy.push(s.cmd, s.layout, s.stages, s.push, s.selected);
        oracle.run(s);
    }
    int pd = 0, pr = 0, ps = 0, po = 0;
    for (const Call& c : policy.calls) {
        pd += c.type == 'D';
        pr += c.type == 'R';
        ps += c.type == 'S';
        po += c.type == 'O';
    }
    if (pd != draws || pr != barriers || ps != descs) return fail("forwarding dropped a draw, barrier, or descriptor");
    if (po != oracle.observers) return fail("observer count diverged from selected pushes");
    bool any = false;
    Slot expect[16]{};
    bool live = false;
    for (const Step& s : steps) {
        if (s.kind == Kind::NewCmd) {
            live = false;
            continue;
        }
        if (s.kind != Kind::Draw) continue;
        any = true;
        live = true;
        for (int i = 0; i < 16; i++) expect[i] = s.slots[i];
    }
    if (any && live) {
        for (int i = 0; i < 16; i++)
            if (!(policy.bound[i] == expect[i])) return fail("vertex state diverged from the full-bind oracle");
    }
    if (push_on) {
        // Every oracle push byte must match the policy cache after the stream, unless a foreign
        // push or a new command left it undefined and no later draw restored it.
        bool defined = false;
        uint8_t bytes[32]{};
        uint64_t cmd = 0, layout = 0;
        uint32_t stages = 0;
        for (const Step& s : steps) {
            if (s.kind == Kind::NewCmd || s.kind == Kind::ForeignPush) {
                defined = false;
                continue;
            }
            if (s.kind != Kind::Draw) continue;
            defined = true;
            std::memcpy(bytes, s.push, 32);
            cmd = s.cmd;
            layout = s.layout;
            stages = s.stages;
        }
        if (defined) {
            if (!policy.cache.valid || policy.cache.cmd != cmd || policy.cache.layout != layout ||
                policy.cache.stages != stages || std::memcmp(policy.cache.bytes, bytes, 32) != 0)
                return fail("push cache diverged from the always-push oracle");
            int issues = 0, oracle_pushes = 0;
            for (const Call& c : policy.calls)
                if (c.type == 'P') issues++;
            for (const Call& c : oracle.calls)
                if (c.type == 'P') oracle_pushes++;
            if (issues > oracle_pushes) return fail("elision issued more pushes than the oracle");
        }
    } else {
        int issues = 0, oracle_pushes = 0;
        for (const Call& c : policy.calls)
            if (c.type == 'P') issues++;
        for (const Call& c : oracle.calls)
            if (c.type == 'P') oracle_pushes++;
        if (issues != oracle_pushes) return fail("push switch off must forward every push");
    }
    if (!narrow) {
        for (const Call& c : policy.calls)
            if (c.type == 'B' && !(c.first == 0 && c.count == 16)) return fail("bind switch off must use the full 16");
    }
    return 0;
}

Step draw_at(int n, int dirty, uint64_t field, int which) {
    Step s;
    s.kind = Kind::Draw;
    s.layout = 10;
    s.stages = 0x1u;
    s.cmd = 1;
    for (int i = 0; i < 16; i++) s.slots[i] = {1, 0, 4, 12};
    if (dirty >= 0) {
        Slot& sl = s.slots[dirty];
        if (which == 0) sl.buffer = field;
        if (which == 1) sl.offset = field;
        if (which == 2) sl.size = field;
        if (which == 3) sl.stride = field;
    }
    for (int i = 0; i < 32; i++) s.push[i] = (uint8_t)(n + i);
    return s;
}

}  // namespace

int main() {
    if (int rc = run_extracted_push_cases()) return rc;
    if (int rc = run_extracted_vertex_cases()) return rc;

    std::vector<Step> mixed;
    mixed.push_back(draw_at(1, -1, 0, 0));
    mixed.push_back(draw_at(1, -1, 0, 0));  // identical: no bind, no push when enabled
    {
        Step b;
        b.kind = Kind::Barrier;
        mixed.push_back(b);
        Step d;
        d.kind = Kind::Desc;
        mixed.push_back(d);
    }
    mixed.push_back(draw_at(2, 3, 99, 0));
    mixed.push_back(draw_at(3, 4, 7, 1));
    mixed.push_back(draw_at(4, 5, 8, 2));
    mixed.push_back(draw_at(5, 6, 9, 3));
    {
        Step f;
        f.kind = Kind::ForeignPush;
        f.layout = 77;
        f.stages = 0x20;
        mixed.push_back(f);
    }
    mixed.push_back(draw_at(5, 6, 9, 3));  // same payload, must reissue after foreign layout
    {
        Step n;
        n.kind = Kind::NewCmd;
        mixed.push_back(n);
    }
    mixed.push_back(draw_at(6, 0, 3, 0));
    // four dirty runs -> full bind
    {
        Step s = draw_at(7, -1, 0, 0);
        for (int i : {0, 2, 4, 6}) s.slots[i].buffer = 50 + (uint64_t)i;
        mixed.push_back(s);
    }
    if (int rc = stream_case(true, true, mixed)) return rc;
    if (int rc = stream_case(false, false, mixed)) return rc;
    if (int rc = stream_case(true, false, mixed)) return rc;
    if (int rc = stream_case(false, true, mixed)) return rc;

    // Identical payload, different stage mask or layout, must both issue.
    {
        grok_replay::PushRecord rec;
        uint8_t data[32]{};
        data[0] = 1;
        if (!grok_replay::push_should_issue(rec, true, 1, 10, 1, data, 32)) return fail("first push");
        if (grok_replay::push_should_issue(rec, true, 1, 10, 1, data, 32)) return fail("same push reissued");
        if (!grok_replay::push_should_issue(rec, true, 1, 11, 1, data, 32)) return fail("layout change elided");
        if (!grok_replay::push_should_issue(rec, true, 1, 11, 0x20, data, 32)) return fail("stage change elided");
        if (!grok_replay::push_should_issue(rec, true, 2, 11, 0x20, data, 32)) return fail("cmd change elided");
        grok_replay::push_invalidate(rec);
        if (!grok_replay::push_should_issue(rec, true, 2, 11, 0x20, data, 32)) return fail("invalidate ignored");
        if (grok_replay::push_should_issue(rec, false, 2, 11, 0x20, data, 32) != true) return fail("off switch elided");
    }
    std::puts("ok");
    return 0;
}
