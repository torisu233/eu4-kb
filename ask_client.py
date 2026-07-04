# -*- coding: utf-8 -*-
r"""問答後端測試客戶端：POST /ask_stream，印出 agent 的完整行為軌跡
（思考 / 呼叫哪個工具+參數 / 檢索回什麼 / 最終作答）。

前置：先啟動問答後端（run_local.ps1，:8781）。
用法：  .\.venv\Scripts\python.exe ask_client.py "你的問題"
"""
import sys, json

def main():
    if len(sys.argv) < 2:
        sys.exit('用法: python ask_client.py "問題"')
    try:
        import httpx
    except ImportError:
        sys.exit("需要 httpx（服務環境已內建）")
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    q = sys.argv[1]
    url = "http://127.0.0.1:8781/ask_stream"
    print("Q:", q); print("=" * 70)
    try:
        with httpx.stream("POST", url, json={"question": q}, timeout=300) as r:
            for line in r.iter_lines():
                if not line.strip():
                    continue
                ev = json.loads(line); t = ev.get("type")
                if t == "thinking":
                    th = (ev.get("text") or "").strip().replace("\n", " ")
                    if th:
                        print(f"\n💭 {th[:200]}")
                elif t == "tool_use":
                    inp = ev.get("input") or {}
                    args = "  ".join(f"{k}={v!r}" for k, v in inp.items() if v not in ("", None))
                    print(f"\n🔧 {ev.get('name')}（{args}）")
                elif t == "tool_result":
                    c = (ev.get("content") or "").strip().replace("\n", " ")
                    print(f"   ↩ {c[:180]}{'…' if ev.get('truncated') else ''}")
                elif t == "final":
                    print("\n" + "=" * 70 + "\n【答案】\n" + (ev.get("answer") or ""))
                    print("\n[工具序列]",
                          " → ".join(x.replace("mcp__eu4-kb__", "") for x in ev.get("tools_used", [])))
                elif t == "error":
                    print("❌", ev.get("error"))
    except Exception as e:
        sys.exit(f"[X] 連線/串流失敗（後端啟動了嗎？）: {e!r}")

if __name__ == "__main__":
    main()
