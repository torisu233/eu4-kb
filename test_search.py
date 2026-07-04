# -*- coding: utf-8 -*-
"""本機直接檢索測試：不經 MCP / Claude，直接用 KBSearcher 對 eu4 庫查詢。"""
import sys, time
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
from kb.searcher import KBSearcher

t0 = time.time()
print("[1] loading KBSearcher('eu4') ...", flush=True)
s = KBSearcher("eu4")
print(f"    chunks loaded: {len(s.order)}  ({time.time()-t0:.1f}s)", flush=True)

queries = ["what does aristocracy ideas group give", "how does government reform work", "Ming initial government type"]
for q in queries:
    t = time.time()
    res = s.search(q, top_k=3)
    print(f"\n===== query: {q}  ({time.time()-t:.1f}s, {len(res)} hits) =====", flush=True)
    for i, r in enumerate(res, 1):
        print(f" [{i}] score={r['score']} sim={r['sim']} source={r.get('source')} | {r['title']}", flush=True)
        print(f"     heading: {r['heading_path']}", flush=True)
        print(f"     source_path : {r['source_path']}", flush=True)
        print(f"     text   : {r['text'][:100].replace(chr(10),' ')}", flush=True)

print(f"\n[done] total {time.time()-t0:.1f}s", flush=True)
