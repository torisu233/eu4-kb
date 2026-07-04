# -*- coding: utf-8 -*-
"""Wiki extractor 入口：MediaWiki API 抓取 eu4.paradoxwikis.com 全站頁面 -> 清洗 -> docs/*.md + manifest.jsonl(source=wiki)。"""
import os, sys, json, re, hashlib
from . import common
from wiki.mw_client import MediaWikiClient, MediaWikiError
from wiki.wikitext_clean import clean_wikitext

_VERSION_STUB_RE = re.compile(r'^\d+(\.\d+)+$')   # 純版本號存根頁(如 "1.28")，對 RAG 問答價值低，過濾掉

def _guess_wiki_category(categories):
    """依頁面所屬 category(API 已回傳)粗分類；查無明確線索歸 misc。
    實測 EU4 wiki 最高頻的實質內容分類是 Countries/Events/Missions(遠高於泛用的
    mechanic 關鍵字猜測)，故直接對應這幾個高頻 category 而非只猜關鍵字。"""
    cats = [(c.get("*") or "").lower() for c in (categories or [])]
    joined = " ".join(cats)
    if "countries" in joined:
        return "country"
    if "missions" in joined:
        return "mission"
    if "events" in joined:
        return "event"
    if "disasters" in joined:
        return "disaster"
    if "achievements" in joined:
        return "achievement"
    if "disambiguation" in joined:
        return "disambiguation"
    if "patches" in joined:
        return "patch_notes"
    if any(k in joined for k in ("mechanic", "government", "idea group", "economy", "diplomacy",
                                  "military", "religion", "institution", "estate", "trade", "game_concepts")):
        return "mechanic"
    if "dlc" in joined or "expansion" in joined:
        return "dlc"
    return "misc"

def run(name, base_url="https://eu4.paradoxwikis.com", skip_cache=False, limit=None):
    P = common.kb_paths(name)
    os.makedirs(P["docs"], exist_ok=True)
    cache_dir = os.path.join(common.ROOT, "cache", "wiki_raw")
    client = MediaWikiClient(base_url, cache_dir)

    print("[wiki_extract] 列舉頁面...", flush=True)
    pages = []
    for pid, title in client.list_all_pages():
        if _VERSION_STUB_RE.match(title.strip()):
            continue
        pages.append((pid, title))
        if limit and len(pages) >= limit:
            break
    print(f"[wiki_extract] 共 {len(pages)} 頁待處理", flush=True)

    new_rows = []; failed = 0; residual_total = 0
    for i, (pid, title) in enumerate(pages, 1):
        try:
            data = client.fetch_page(pid, use_cache=not skip_cache)
            p = data["parse"]
            wt = p["wikitext"]
            wikitext = wt["*"] if isinstance(wt, dict) else wt
            revid = p.get("revid")
            categories = p.get("categories", [])
        except Exception as e:
            print(f"[wiki_extract] ⚠ FAILED pageid={pid} {title!r}: {e!r}", file=sys.stderr)
            failed += 1
            continue
        body_md, residual = clean_wikitext(wikitext)
        residual_total += residual
        wiki_category = _guess_wiki_category(categories)
        doc_id = "w-" + hashlib.sha1(title.encode("utf-8")).hexdigest()[:10]
        source_path = f"wiki/{wiki_category}/{title}"
        md_path = f"docs/{doc_id}.md"
        fm = {
            "doc_id": doc_id, "title": title, "system": "eu4", "doc_type": wiki_category,
            "lang": "en", "version": "", "source_hash": hashlib.sha1(wikitext.encode("utf-8")).hexdigest()[:12],
            "status": "active", "md_path": md_path, "source": "wiki", "wiki_category": wiki_category,
            "pageid": pid, "last_revision_id": revid, "source_url": f"{base_url}/{title.replace(' ', '_')}",
            "residual_template_count": residual, "source_path": source_path,
        }
        fm_yaml = "\n".join(f"{k}: {json.dumps(v, ensure_ascii=False)}" for k, v in fm.items())
        md = f"---\n{fm_yaml}\n---\n\n# {title}\n\n{body_md}\n"
        open(os.path.join(P["base"], md_path), "w", encoding="utf-8").write(md)
        new_rows.append(fm)
        if i % 100 == 0:
            print(f"[wiki_extract] {i}/{len(pages)}（失敗 {failed}，平均殘留模板 {residual_total/i:.1f}）", flush=True)

    # manifest.jsonl 合併：保留非 wiki 來源(game_file)的既有列，wiki 的列整批以本次結果取代
    existing = []
    if os.path.exists(P["manifest"]):
        for l in open(P["manifest"], encoding="utf-8"):
            if l.strip():
                m = json.loads(l)
                if m.get("source") != "wiki":
                    existing.append(m)
    with open(P["manifest"], "w", encoding="utf-8") as out:
        for m in existing + new_rows:
            out.write(json.dumps(m, ensure_ascii=False) + "\n")
    avg_res = residual_total / max(1, len(new_rows))
    print(f"[wiki_extract] DONE {len(new_rows)} 篇（失敗 {failed}，manifest 共 {len(existing)+len(new_rows)} 列，平均殘留模板數 {avg_res:.1f}）", flush=True)
    return len(new_rows)
