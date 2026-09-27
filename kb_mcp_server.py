# -*- coding: utf-8 -*-
"""EU4 知識庫 MCP 後端：提供檢索/導覽工具給 Claude。

用法:
  python kb_mcp_server.py --kb eu4              # 單庫
  加 --http 以 HTTP 常駐(127.0.0.1:8766/mcp)

工具: search_kb / grep_kb / get_doc / list_tree / list_index / knowledge_map / related_docs
路徑結構化：search_kb/grep_kb 可帶 path_prefix 限定資料夾子樹；list_tree 瀏覽資料夾樹
(wiki 文件用 "wiki/<wiki_category>/<title>" 虛擬路徑，遊戲檔案用 "game_file/<entity_category>/<entity_id>" 虛擬路徑)。
doc 參照用 "庫名:doc_id"(doc_id=路徑雜湊)。模型惰性/主執行緒預載(torch 在工作執行緒會 deadlock)。

知識庫內容為英文原文(wiki + 遊戲檔案皆英文)，search_kb 支援 source(wiki|game_file) 過濾，
但預設不過濾——多數真實問題需要 wiki 的策略框架與 game_file 的精確當前版本數值融合檢索。
"""
import argparse, os, sys, json, contextlib, re
import kb.common as common
from kb.multi import MultiKBSearcher
from kb.searcher import KBSearcher
from mcp.server.fastmcp import FastMCP

DOC_WHOLE_RETURN = 20000   # 文件 ≤ 此字數 → get_doc 直接回整份(Claude context 裝得下，省去大綱導航)
DOC_SECTION_CAP  = 12000   # 單次章節/分頁/大綱切片上限(避免單一 tool 回傳吃掉太多 context)
SIM_FLOOR = 0.35       # 最佳 cosine 相似度低於此 → 視為查無
SIM_WARN  = 0.45       # 低於此 → 結果可信度偏低，加註提醒

def _split_sections(md):
    """依 markdown 標題(#~######)切成章節，回 [(level, heading, text)]。標題前的前言併為第 0 節。"""
    lines = md.splitlines(keepends=True)
    secs = []; cur_head = "（前言）"; cur_lvl = 0; buf = []
    hre = re.compile(r'^(#{1,6})\s+(.*)$')
    for ln in lines:
        m = hre.match(ln)
        if m:
            if buf or secs: secs.append((cur_lvl, cur_head, "".join(buf)))
            cur_lvl = len(m.group(1)); cur_head = m.group(2).strip(); buf = [ln]
        else:
            buf.append(ln)
    secs.append((cur_lvl, cur_head, "".join(buf)))
    return [s for s in secs if s[2].strip()]

def build_server(names, label):
    mcp = FastMCP(f"kb-{label}")
    import threading
    _s = {"v": None}; _lock = threading.Lock()
    def S():
        if _s["v"] is None:
            with _lock:
                if _s["v"] is None: _s["v"] = MultiKBSearcher(names)
        return _s["v"]
    multi = len(names) > 1
    KBS_HINT = ("This knowledge base covers multiple systems/topics: " + ", ".join(names) + ".") if multi else ("Knowledge base: " + names[0] + ".")

    # 中文術語詞典(build 時由 paratranz 生成的 zh_glossary.json)：中文模式下給檢索結果標題加官方中文注解。
    def _load_gloss(n):
        p = common.kb_paths(n)["zh_glossary"]
        if os.path.exists(p):
            try:
                with open(p, encoding="utf-8") as f: return json.load(f)
            except Exception as e:
                sys.stderr.write(f"[gloss] load 失敗 {n}: {e!r}\n")
        return {"name2zh": {}, "key2zh": {}}
    GLOSS = {n: _load_gloss(n) for n in names}

    def _zh_gloss(kb, title, source_path, source):
        """回傳 title 的官方中文名(查無回 None)。game_file 用 entity_id(source_path 末段)查 key2zh，
        其餘(wiki)用英文名查 name2zh。"""
        g = GLOSS.get(kb or names[0]) or {}
        src = (source or "")
        if not src and source_path:
            src = source_path.split("/", 1)[0]        # "game_file/idea_group/x" -> "game_file"
        zh = None
        if src == "game_file" and source_path:
            eid = source_path.rstrip("/").split("/")[-1].strip().lower()
            zh = (g.get("key2zh") or {}).get(eid)
        if not zh and title:
            zh = (g.get("name2zh") or {}).get(title.strip().lower())
        return zh if (zh and zh != (title or "").strip()) else None

    def _title_zh(lang, kb, title, source_path, source):
        """中文模式下把 title 渲染成 'Title（中文）'，否則原樣。"""
        if (lang or "").strip().lower() == "zh":
            zh = _zh_gloss(kb, title, source_path, source)
            if zh: return f"{title}（{zh}）"
        return title

    @mcp.tool()
    def search_kb(query: str, kb: str = "", doc_id: str = "", path_prefix: str = "",
                  source: str = "", entity_category: str = "", top_k: int = 8, lang: str = "") -> str:
        """Hybrid semantic + keyword search; returns the most relevant snippets with sources (cosine relevance attached). Content is English.
        doc_id (optional): search within a single document only (use this to find relevant sections of a large doc); form "kb:doc_id".
        path_prefix (optional): search only within a folder subtree (e.g. "game_file/idea_group"); use list_tree first to see which folders exist.
        source (optional): restrict to "wiki" or "game_file"; unfiltered by default (most questions need wiki strategy + game_file exact values fused,
        filter only when you explicitly want one side). entity_category (optional): restrict game_file entity type (e.g. idea_group/mission/decision).
        lang (optional): pass "zh" when the user asked in Chinese — each result title then gets its official Chinese name appended as «Title（中文）»
        (from the game's Chinese localisation), so you can cite authoritative Chinese terms instead of inventing translations.
        Each hit carries a relevance score (0~1, cosine); if the best relevance is too low it will clearly say "not found / low confidence"."""
        f = {}
        if doc_id:                                   # 限定單一文件：拆出庫名縮範圍，用 doc_id 過濾 chunk
            if ":" in doc_id: kb, did = doc_id.split(":", 1); did = did.strip()
            else: did = doc_id.strip()
            f["doc_id"] = did
        if path_prefix: f["path_prefix"] = path_prefix.strip()   # 限定資料夾子樹
        if source: f["source"] = source.strip()
        if entity_category: f["entity_category"] = entity_category.strip()
        hits = S().search(query, kb=(kb or None), filters=(f or None), top_k=top_k)
        if not hits: return "(no relevant content in the knowledge base)"
        sims = [h["sim"] for h in hits if h.get("sim") is not None]
        best = max(sims) if sims else 0.0
        if best < SIM_FLOOR:                         # 相關性門檻：最佳 cosine 太低 → 知識庫多半沒有此主題
            return f"(no relevant content; best relevance only {best:.2f}, the topic is likely absent — do not speculate from this)"
        out = []
        for i, h in enumerate(hits, 1):
            ref = f"{h.get('kb','')}:{h['doc_id']}" if h.get("kb") else h["doc_id"]
            rel = f"{h['sim']:.2f}" if h.get("sim") is not None else "keyword match"
            cs = h.get("char_start") or 0
            more = f" | read full context: get_doc(\"{ref}\", offset={cs})" if cs else ""
            dups = h.get("dup_paths") or []
            dupnote = f"\n(same content also stored in {len(dups)} other place(s), e.g. {dups[0]})" if dups else ""
            src = h.get("source") or ""
            title_disp = _title_zh(lang, h.get('kb',''), h['title'], h.get('source_path'), src)
            out.append(f"[{i}] «{title_disp}» (kb:{h.get('kb','')} · {h['doc_type']} · source={src}) relevance={rel}\n"
                       f"Section: {h['heading_path']}\nSource: {h['source_path']} (doc_id={ref}{more}){dupnote}\nContent: {h['text'][:600]}")
        body = "\n\n---\n\n".join(out)
        warn = (f"⚠ Best relevance is low ({best:.2f}); the KB may lack this topic or the question is out of scope — judge carefully and say so if not found.\n\n"
                if best < SIM_WARN else "")
        return warn + body + "\n\n(Content is English; synthesize your answer and cite «Document Name» as the source; wiki gives strategy/conceptual framework, "
        "game_file gives the current version's exact values/trigger conditions, game_file wins on conflicts; "
        "snippets with relevance <0.5 are weak; use get_doc for fuller content; doc_id has the form kb:doc_id)"

    @mcp.tool()
    def grep_kb(pattern: str, kb: str = "", path_prefix: str = "", limit: int = 30, lang: str = "") -> str:
        """Exact literal/regex search (game-internal keys, exact terms, trigger/modifier names). path_prefix optional, restricts to a folder subtree.
        lang (optional): pass "zh" when the user asked in Chinese to append official Chinese names to result titles («Title（中文）»)."""
        hits = S().grep(pattern, kb=(kb or None), limit=limit, path_prefix=(path_prefix.strip() or None))
        if not hits: return "(no matching literal content)"
        return "\n\n".join(f"«{_title_zh(lang, h.get('kb',''), h['title'], h.get('source_path'), '')}» (kb:{h.get('kb','')}) | {h['heading_path']}\nSource:{h['source_path']}\n…{h['snippet']}…" for h in hits)

    @mcp.tool()
    def get_doc(doc_id: str, section: int = -1, sections: str = "", offset: int = 0, max_chars: int = 12000) -> str:
        """Retrieve structured document content (large docs use an "outline → section" navigation to avoid exceeding tool-result limits).
        doc_id has the form "kb:doc_id"; a relative path (source_path) is also accepted.
        Usage:
        - Small doc: returns the whole thing.
        - Large doc with no section/sections given: returns the "document outline" (section numbers and titles). After reading the outline:
          · Only one section → section=K.
          · Several sections look relevant from the titles → **fetch them all at once with sections="2,6,17", don't make several calls**
            (each call has a cost; grab them in one go).
          · Unsure which sections, or the answer seems scattered → use search_kb(query, doc_id="kb:doc_id") to semantically search this doc directly,
            often faster than guessing sections, but note it is relevance-ranked so sections whose titles don't match the query semantically may not surface —
            still cross-check the outline for anything missed.
        - section=K: fetch section K (if still too long, use offset per the end-of-output hint to continue).
        - sections="K1,K2,...": fetch multiple sections at once (each capped by an evenly-split budget; use section=K to continue a large one).
        - offset=N (no section): read from character N of the body (search_kb results carry this offset, so you can jump straight to the relevant section's full context).
        - game_file docs usually end with a "Raw Script (appendix)"; read it when you need exact values / unmapped triggers."""
        md = S().get_doc(doc_id)
        if not md:
            return f"(doc_id={doc_id} not found)"
        total = len(md)
        cap = min(max(1000, max_chars), DOC_SECTION_CAP)

        # 多數文件(≤門檻)：整份直接回，Claude context 裝得下、免大綱導航
        if total <= DOC_WHOLE_RETURN and section < 0 and not sections and offset <= 0:
            return md

        # 多章節一次取：把「大綱→逐節呼叫」的多輪工具呼叫壓成一次，預算平均分配給各章節
        if sections:
            secs = _split_sections(md)
            if not secs:
                return md[:cap]
            try:
                idxs = [int(x.strip()) for x in sections.split(",") if x.strip() != ""]
            except ValueError:
                return f"(bad 'sections' format; use comma-separated section numbers, e.g. \"2,6,17\")"
            per_cap = max(1500, cap // max(1, len(idxs)))
            parts = []
            for idx in idxs:
                if idx < 0 or idx >= len(secs):
                    parts.append(f"(section [{idx}] does not exist; this doc only has 0–{len(secs)-1})")
                    continue
                lvl, head, body = secs[idx]
                seg = body[:per_cap]
                more = (f"\n(section has more; continue separately: get_doc(\"{doc_id}\", section={idx}, offset={len(seg)}))"
                        if len(seg) < len(body) else "")
                parts.append(f"### Section [{idx}] {head}\n{seg}{more}")
            return f"(doc_id={doc_id} multiple sections combined, {len(idxs)} in total)\n\n" + "\n\n---\n\n".join(parts)

        # 指定單一章節
        if section >= 0:
            secs = _split_sections(md)
            if not secs: return md[:cap]
            if section >= len(secs):
                return f"(doc_id={doc_id} has only {len(secs)} sections, numbered 0–{len(secs)-1}; fetch the outline first without a section)"
            lvl, head, body = secs[section]
            off = min(max(0, offset), len(body)); seg = body[off:off+cap]; end = off+len(seg)
            info = f"(doc_id={doc_id} · section [{section}] {head} · {len(body)} chars total, this slice {off}–{end})\n\n"
            if end < len(body):
                tail = f"\n\n(section has more; continue: get_doc(\"{doc_id}\", section={section}, offset={end}))"
            else:
                nxt = f"; next section get_doc(\"{doc_id}\", section={section+1})" if section+1 < len(secs) else ""
                tail = f"\n\n(end of section{nxt})"
            return info + seg + tail

        # offset 模式：對「去 frontmatter 的本文」切片，與 chunk 的 char_start 同基準對齊
        if offset > 0:
            body = common.strip_frontmatter(md); tb = len(body)
            off = min(offset, tb)
            # 本文若 ≤ 整份門檻(本可整份回) → 從 offset 一次給到文末，不分頁；大文件才套單段上限
            ocap = (tb - off) if tb <= DOC_WHOLE_RETURN else cap
            seg = body[off:off+ocap]; end = off+len(seg)
            tail = (f"\n\n(more follows: get_doc(\"{doc_id}\", offset={end}))" if end < tb else "\n\n(end of document)")
            return f"(doc_id={doc_id} body is {tb} chars total, this slice {off}–{end})\n\n" + seg + tail

        # 大文件、未指定 section → 回大綱
        secs = _split_sections(md)
        lines = [f"# Document outline  doc_id={doc_id} ({total} chars, {len(secs)} sections; too long to return directly)", ""]
        for k, (lvl, head, body) in enumerate(secs):
            indent = "  " * max(0, lvl-1)
            lines.append(f"[{k}] {indent}{head}  ({len(body)} chars)")
            if sum(len(x)+1 for x in lines) > DOC_SECTION_CAP - 400:
                lines.append(f"… too many sections, showing only the first {k+1}"); break
        lines += ["", "How to fetch (pick one):",
                  f"· Find specific info (recommended): search_kb(\"your question\", doc_id=\"{doc_id}\") — search only within this doc",
                  f"· Read one section: get_doc(\"{doc_id}\", section=K)",
                  f"· If several section titles above look relevant → read them at once: get_doc(\"{doc_id}\", sections=\"2,6,17\"), "
                  "don't call once per section"]
        return "\n".join(lines)

    @mcp.tool()
    def list_tree(path_prefix: str = "", depth: int = 1, kb: str = "") -> str:
        """Browse the virtual folder tree: the top level splits into wiki/ and game_file/ subtrees; lists a level's subfolders (with subtree doc counts) and files.
        path_prefix empty = start from the top (you'll see the wiki/ and game_file/ branches); depth controls how many levels to expand.
        To find content within a category, use this to see the path first, then search_kb(query, path_prefix="that path")."""
        t = S().list_tree(path_prefix=path_prefix.strip(), depth=depth, kb=(kb or None))
        if not t["folders"] and not t["files"]:
            return f"(nothing under path \"{t['prefix'] or '(top)'}\"; path_prefix may be misspelled — use list_tree() to see the top level)"
        lines = [f"# Folder tree: {t['prefix'] or '(top)'}"]
        if t["folders"]:
            lines.append("\n## Subfolders (with subtree doc count)")
            for name, cnt in t["folders"]:
                lines.append(f"- 📁 {name}/  ({cnt} docs)")
                if sum(len(x)+1 for x in lines) > DOC_SECTION_CAP - 400:
                    lines.append("… too many folders, truncated; narrow with a deeper path_prefix"); break
        if t["files"]:
            lines.append("\n## Files at this level")
            for f in t["files"]:
                ref = f"{f['kb']}:{f['doc_id']}"
                lines.append(f"- 📄 {f['name']}  (doc_id={ref})")
                if sum(len(x)+1 for x in lines) > DOC_SECTION_CAP - 200:
                    lines.append("… too many files, truncated"); break
        lines.append("\nUsage: drill down with list_tree(path_prefix=\"a folder above\"); or search_kb(query, path_prefix=\"…\") to search within the subtree")
        return "\n".join(lines)

    @mcp.tool()
    def list_index(kb: str = "", path_prefix: str = "", doc_type: str = "") -> str:
        """List documents. Prefer path_prefix to restrict to a folder subtree (reliable); doc_type is a coarse category (e.g. country/mechanic/idea_group), rough reference only."""
        s = S(); targets = s._targets(kb or None); rows = []
        dt = doc_type.strip().lower(); pf = path_prefix.strip()
        for n in targets:
            for m in s.manifest(n):
                if dt and str(m.get("doc_type", "")).strip().lower() != dt: continue
                if pf and not KBSearcher._under_prefix(m.get("source_path"), pf): continue
                rows.append((n, m))
        if not rows: return "(no matching documents; path_prefix may be misspelled — use list_tree to see the paths)"
        lines = [f"- {n}:{m['doc_id']} «{m['title']}» ‹{m.get('source_path','')}›" for n, m in rows[:400]]
        head = f"{len(rows)} docs (kb: {', '.join(targets)}{('; path='+pf) if pf else ''}){', showing first 400' if len(rows)>400 else ''}:\n"
        return head + "\n".join(lines)

    @mcp.tool()
    def knowledge_map(kb: str = "") -> str:
        """Category map: for each KB category (source × type, e.g. wiki/country, game_file/idea_group), the doc count and representative docs.
        To browse the 'folder structure' level by level, use list_tree instead."""
        s = S(); targets = s._targets(kb or None); out = ["# Knowledge base category map"]
        for n in targets:
            dm = s.s(n).doc_map; cats = dm.get("categories", []); titles = dm.get("titles", {}); nd = len(titles)
            out.append(f"\n## KB \"{n}\" ({nd} docs)")
            if cats:
                for c in cats[:20]:
                    ex = [titles.get(d, "") for d in c["docs"][:3]]; ex = [e for e in ex if e]
                    extxt = ("  e.g. " + ", ".join(f"«{e}»" for e in ex)) if ex else ""
                    out.append(f"- {c['key']} ({len(c['docs'])} docs){extxt}")
            else:
                out.append("(no category data)")
        return "\n".join(out)

    @mcp.tool()
    def related_docs(doc_id: str) -> str:
        """Given a doc_id ("kb:doc_id"), returns its category and the most vector-similar documents."""
        s = S(); n, did, dm = s.related(doc_id)
        if not dm: return f"(no relations found for {doc_id})"
        titles = dm.get("titles", {})
        lines = [f"# Relations of {n}:{did} «{titles.get(did, did)}» (kb:{n})"]
        cat = next((c for c in dm.get("categories", []) if did in c["docs"]), None)
        if cat:
            lines.append(f"\n## Category \"{cat['key']}\" ({len(cat['docs'])} docs)")
            lines += [f"- {n}:{d} {titles.get(d, d)}" for d in cat["docs"] if d != did][:8]
        rel = dm.get("related", {}).get(did, [])
        lines.append("\n## Most vector-similar")
        lines += ([f"- {n}:{d} {titles.get(d, d)}" for d in rel] or ["(none)"])
        return "\n".join(lines)

    return mcp, S

def main():
    ap = argparse.ArgumentParser(description="EU4 知識庫 MCP 後端")
    ap.add_argument("--kb", required=True, help="庫名 / all / 逗號分隔多庫")
    ap.add_argument("--http", action="store_true")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8766)
    a = ap.parse_args()
    if a.kb.strip().lower() == "all":
        names = common.list_kbs()
    else:
        names = [x.strip() for x in a.kb.split(",") if x.strip()]
    if not names:
        print("找不到任何知識庫", file=sys.stderr); sys.exit(1)
    label = "all" if len(names) > 1 else names[0]
    mcp, S = build_server(names, label)
    # 主執行緒預載（torch 在工作執行緒初始化會 deadlock）
    try:
        S().warmup()
        print(f"[kb-{label}] warmup done, 庫: {names}", file=sys.stderr, flush=True)
    except Exception as e:
        print(f"[kb-{label}] warmup failed: {e!r}", file=sys.stderr, flush=True)
    if a.http:
        mcp.settings.host = a.host; mcp.settings.port = a.port
        try:
            from mcp.server.transport_security import TransportSecuritySettings
            mcp.settings.transport_security = TransportSecuritySettings(enable_dns_rebinding_protection=False)
        except Exception: pass
        print(f"[kb-{label}] HTTP MCP 常駐於 http://{a.host}:{a.port}/mcp", file=sys.stderr, flush=True)
        mcp.run(transport="streamable-http")
    else:
        mcp.run()

if __name__ == "__main__":
    main()
