# -*- coding: utf-8 -*-
r"""Record replayable demo traces.

Runs each question in demo/questions.json through the local answer backend's /ask_stream and
saves the full event stream (thinking / tool_use / tool_result / text / final), with the time of
each event, to demo/traces/<id>.json. kb_ui.html replays these files when no live backend is
available (e.g. on GitHub Pages).

Prerequisite: kb_mcp_server.py (:8766) and kb_answer_backend.py (:8781) running locally.
Usage:  python record_demo.py                 # record every question that has no trace yet
        python record_demo.py --force ID ...  # re-record the given ids
"""
import sys, json, time, os, argparse
from datetime import datetime, timezone

ROOT = os.path.dirname(os.path.abspath(__file__))
DEMO = os.path.join(ROOT, "demo")
TRACES = os.path.join(DEMO, "traces")
URL = os.environ.get("KB_ANSWER_URL", "http://127.0.0.1:8781")


def record(q):
    import httpx
    events = []
    t0 = time.monotonic()
    with httpx.stream("POST", URL + "/ask_stream", json={"question": q["question"], "lang": q["lang"]},
                      timeout=httpx.Timeout(600, connect=10)) as r:
        r.raise_for_status()
        for line in r.iter_lines():
            if not line.strip():
                continue
            ev = json.loads(line)
            ev.pop("turn_id", None)
            ev["t"] = int((time.monotonic() - t0) * 1000)
            events.append(ev)
    return events


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--force", nargs="*", default=None, help="re-record these ids (no ids = all)")
    args = ap.parse_args()
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    os.makedirs(TRACES, exist_ok=True)
    questions = json.load(open(os.path.join(DEMO, "questions.json"), encoding="utf-8"))
    health = __import__("httpx").get(URL + "/health", timeout=10).json()

    for q in questions:
        path = os.path.join(TRACES, q["id"] + ".json")
        forced = args.force is not None and (not args.force or q["id"] in args.force)
        if os.path.exists(path) and not forced:
            print(f"[skip] {q['id']}")
            continue
        print(f"[run ] {q['id']}: {q['question']}", flush=True)
        events = record(q)
        final = next((e for e in events if e["type"] == "final"), None)
        if not final or not final.get("answer"):
            print(f"[fail] {q['id']}: no final answer; not saved", flush=True)
            continue
        tools = [e["name"] for e in events if e["type"] == "tool_use" and e["name"] != "ToolSearch"]
        trace = {**q, "model": health.get("model"),
                 "recorded_at": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
                 "duration_ms": events[-1]["t"], "tool_calls": len(tools), "events": events}
        with open(path, "w", encoding="utf-8") as f:
            json.dump(trace, f, ensure_ascii=False, indent=1)
        print(f"[ok  ] {q['id']}: {len(tools)} tool calls, {events[-1]['t']/1000:.0f}s", flush=True)

    # index.json: what the UI lists, in questions.json order
    index = []
    for q in questions:
        path = os.path.join(TRACES, q["id"] + ".json")
        if os.path.exists(path):
            t = json.load(open(path, encoding="utf-8"))
            index.append({k: t[k] for k in ("id", "lang", "question", "shows", "model", "recorded_at",
                                            "duration_ms", "tool_calls")})
    with open(os.path.join(DEMO, "index.json"), "w", encoding="utf-8") as f:
        json.dump(index, f, ensure_ascii=False, indent=1)
    print(f"[done] {len(index)} traces indexed")


if __name__ == "__main__":
    main()
