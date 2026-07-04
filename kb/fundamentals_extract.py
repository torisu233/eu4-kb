# -*- coding: utf-8 -*-
"""Fundamentals extractor 入口：把手寫/LLM撰寫的「EU4基礎知識」源文件(`fundamentals_src/*.md`，
帶簡單 title/category frontmatter) 轉成 docs/*.md + manifest.jsonl(source=fundamentals)。

這批文檔解決的是"AI缺遊戲整體常識框架"問題(通用vs國家專屬內容分層判斷、君主點數/國家等級這類
被wiki埋沒或誤分類的基礎概念總覽)，走正常RAG檢索流程；核心的"回答策略"規則另外濃縮進
kb_answer_backend.py 的 SYSTEM 提示詞(不依賴檢索觸發)，這裡只負責把完整版本收進可檢索知識庫。

與 wiki_extract.py 對齊同一套 manifest 欄位約定，doc_id 前綴用 "f-" 以區分來源。"""
import os, sys, re, json, glob, hashlib
from . import common

_FM_RE = re.compile(r'^---\s*\n(.*?)\n---\s*\n(.*)$', re.S)

def _parse_source_md(text):
    """解析簡單的 title/category frontmatter(純文字 key: value，非JSON)，回 (meta_dict, body)。"""
    m = _FM_RE.match(text)
    if not m:
        return {}, text
    meta = {}
    for line in m.group(1).splitlines():
        if ":" in line:
            k, v = line.split(":", 1)
            meta[k.strip()] = v.strip()
    return meta, m.group(2)

def run(name, src_dir=None):
    P = common.kb_paths(name)
    os.makedirs(P["docs"], exist_ok=True)
    src_dir = src_dir or os.path.join(common.ROOT, "fundamentals_src")
    if not os.path.isdir(src_dir):
        raise ValueError(f"fundamentals 源目錄不存在: {src_dir}")

    files = sorted(glob.glob(os.path.join(src_dir, "*.md")))
    new_rows = []
    for fp in files:
        raw = open(fp, encoding="utf-8").read()
        meta, body = _parse_source_md(raw)
        title = meta.get("title") or os.path.splitext(os.path.basename(fp))[0]
        category = meta.get("category") or "misc"
        doc_id = "f-" + hashlib.sha1(os.path.basename(fp).encode("utf-8")).hexdigest()[:10]
        source_path = f"fundamentals/{category}/{title}"
        md_path = f"docs/{doc_id}.md"
        fm = {
            "doc_id": doc_id, "title": title, "system": "eu4", "doc_type": "fundamentals",
            "lang": "en", "version": "", "source_hash": hashlib.sha1(raw.encode("utf-8")).hexdigest()[:12],
            "status": "active", "md_path": md_path, "source": "fundamentals", "wiki_category": category,
            "source_path": source_path,
        }
        fm_yaml = "\n".join(f"{k}: {json.dumps(v, ensure_ascii=False)}" for k, v in fm.items())
        md = f"---\n{fm_yaml}\n---\n\n{body.strip()}\n"
        open(os.path.join(P["base"], md_path), "w", encoding="utf-8").write(md)
        new_rows.append(fm)
        print(f"[fundamentals_extract] {title} -> {doc_id}", flush=True)

    # manifest.jsonl 合併：保留非 fundamentals 來源的既有列，fundamentals 的列整批以本次結果取代
    existing = []
    if os.path.exists(P["manifest"]):
        for l in open(P["manifest"], encoding="utf-8"):
            if l.strip():
                m = json.loads(l)
                if m.get("source") != "fundamentals":
                    existing.append(m)
    with open(P["manifest"], "w", encoding="utf-8") as out:
        for m in existing + new_rows:
            out.write(json.dumps(m, ensure_ascii=False) + "\n")
    print(f"[fundamentals_extract] DONE {len(new_rows)} 篇（manifest 共 {len(existing)+len(new_rows)} 列）", flush=True)
    return len(new_rows)


def main():
    import argparse
    ap = argparse.ArgumentParser(description="Fundamentals 文檔抽取(獨立執行用，正式流程走 kb.build_all)")
    ap.add_argument("--kb", required=True)
    ap.add_argument("--src-dir", default=None)
    a = ap.parse_args()
    run(a.kb, src_dir=a.src_dir)

if __name__ == "__main__":
    main()
