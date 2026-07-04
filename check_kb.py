# -*- coding: utf-8 -*-
r"""知識庫健檢：驗證資料完整性並輸出統計。可抓出「docs/ 與 manifest/chunks 失同步」
（症狀：search_kb 正常但 get_doc 大量 No such file or directory）。

用法：
  .\.venv\Scripts\python.exe check_kb.py --kb eu4
  .\.venv\Scripts\python.exe check_kb.py --kb eu4 --show-missing 20   # 列出前 N 個缺檔

檢查項：
  ① manifest 每個 md_path 的 .md 是否存在於磁碟（失同步 → get_doc 會失敗）
  ② chunks 數、context 覆蓋率、chunk 的 doc_id 是否都在 manifest（孤兒偵測）
  ③ doc_type / lang / source(wiki|game_file) 分布
  ④ 下游產物齊全度（lancedb / doc_map / tree / INDEX）
"""
import argparse, os, sys, json, collections
import kb.common as common

def main():
    ap = argparse.ArgumentParser(description="知識庫健檢與統計")
    ap.add_argument("--kb", required=True)
    ap.add_argument("--show-missing", type=int, default=0, help="列出前 N 個缺 .md 的文件")
    a = ap.parse_args()

    P = common.kb_paths(a.kb)
    if not os.path.exists(P["manifest"]):
        print(f"[X] 找不到 {P['manifest']}（庫名對嗎？）", file=sys.stderr); sys.exit(2)

    man = [json.loads(l) for l in open(P["manifest"], encoding="utf-8") if l.strip()]
    have = [m for m in man if m.get("md_path")]
    print(f"=== 知識庫 {a.kb} ===")
    print(f"manifest 文件數: {len(man)}  (有內文 md_path: {len(have)})")

    # ① docs/ 同步檢查（抓 get_doc 會失敗的文件）
    missing = [m for m in have
               if not os.path.exists(os.path.join(P["base"], m["md_path"].replace("\\", "/")))]
    okn = len(have) - len(missing)
    pct = okn * 100 // max(1, len(have))
    print(f"\n① docs/ 完整性: {okn}/{len(have)} 的 .md 存在 ({pct}%)  "
          + ("✓ 同步" if not missing else f"⚠ 失同步！get_doc 會對 {len(missing)} 份失敗"))
    docs_on_disk = len([f for f in os.listdir(P["docs"]) if f.endswith(".md")]) if os.path.isdir(P["docs"]) else 0
    print(f"   docs/ 實際 .md 檔數: {docs_on_disk}")
    if missing and a.show_missing:
        for m in missing[:a.show_missing]:
            print(f"    缺: {m['md_path']}  ‹{m.get('source_path', '')}›")

    # ② chunks / context / 孤兒
    if os.path.exists(P["chunks"]):
        n = ctx = orphan = 0
        man_ids = {m["doc_id"] for m in man}
        for l in open(P["chunks"], encoding="utf-8"):
            if not l.strip():
                continue
            c = json.loads(l); n += 1
            if (c.get("context") or "").strip():
                ctx += 1
            if c.get("doc_id") not in man_ids:
                orphan += 1
        print(f"\n② chunks: {n}  context 覆蓋: {ctx} ({ctx * 100 // max(1, n)}%)  "
              f"孤兒(doc_id 不在 manifest): {orphan}")
    else:
        print("\n② chunks.jsonl 不存在（尚未 chunk）")

    # ③ 分布
    print("\n③ doc_type:", dict(collections.Counter(m.get("doc_type", "") for m in man).most_common()))
    print("   source  :", dict(collections.Counter(m.get("source", "") for m in man).most_common()))
    print("   lang    :", dict(collections.Counter(m.get("lang", "") for m in man)))

    # ④ 下游產物
    print("\n④ 產物存在:", {k: os.path.exists(P[k]) for k in ["lancedb", "doc_map", "tree", "index_md"]})

if __name__ == "__main__":
    main()
