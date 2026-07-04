# -*- coding: utf-8 -*-
"""EU4 知識庫 MCP 後端：提供檢索/導覽工具給 Claude。

用法:
  python kb_mcp_server.py --kb eu4              # 單庫
  加 --http 以 HTTP 常駐(127.0.0.1:8766/mcp，避免與 前身專案 的 8765 衝突)

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
    KBS_HINT = ("本知識庫涵蓋多個系統/主題：" + "、".join(names) + "。") if multi else ("知識庫：" + names[0] + "。")

    @mcp.tool()
    def search_kb(query: str, kb: str = "", doc_id: str = "", path_prefix: str = "",
                  source: str = "", entity_category: str = "", top_k: int = 8) -> str:
        """做語意+關鍵字混合檢索，回傳最相關片段與出處(附 cosine 相關度)。內容為英文原文，請用中文綜合作答。
        doc_id 可選：只在「該單一文件」內檢索(大型文件找相關段用這個)；形如 庫名:doc_id。
        path_prefix 可選：只在「某資料夾子樹」內檢索(如 "game_file/idea_group")，先用 list_tree 看有哪些資料夾。
        source 可選：限定來源 "wiki" 或 "game_file"；預設不過濾(多數問題需要 wiki 策略說明 + 遊戲檔案精確數值融合檢索，
        只有明確只想看某一側時才過濾)。entity_category 可選：限定遊戲檔案的實體類別(如 idea_group/mission/decision)。
        每筆附「相關度」(0~1，cosine)；最佳相關度過低會明確提示「查無/可信度低」。"""
        f = {}
        if doc_id:                                   # 限定單一文件：拆出庫名縮範圍，用 doc_id 過濾 chunk
            if ":" in doc_id: kb, did = doc_id.split(":", 1); did = did.strip()
            else: did = doc_id.strip()
            f["doc_id"] = did
        if path_prefix: f["path_prefix"] = path_prefix.strip()   # 限定資料夾子樹
        if source: f["source"] = source.strip()
        if entity_category: f["entity_category"] = entity_category.strip()
        hits = S().search(query, kb=(kb or None), filters=(f or None), top_k=top_k)
        if not hits: return "（知識庫查無相關內容）"
        sims = [h["sim"] for h in hits if h.get("sim") is not None]
        best = max(sims) if sims else 0.0
        if best < SIM_FLOOR:                         # 相關性門檻：最佳 cosine 太低 → 知識庫多半沒有此主題
            return f"（知識庫查無相關內容；最佳相關度僅 {best:.2f}，可能沒有此主題，請勿據此臆測作答）"
        out = []
        for i, h in enumerate(hits, 1):
            ref = f"{h.get('kb','')}:{h['doc_id']}" if h.get("kb") else h["doc_id"]
            rel = f"{h['sim']:.2f}" if h.get("sim") is not None else "關鍵字命中"
            cs = h.get("char_start") or 0
            more = f"｜讀完整上下文: get_doc(\"{ref}\", offset={cs})" if cs else ""
            dups = h.get("dup_paths") or []
            dupnote = f"\n（相同內容另存於 {len(dups)} 處，如 {dups[0]}）" if dups else ""
            src = h.get("source") or ""
            out.append(f"[{i}] 《{h['title']}》（庫:{h.get('kb','')} · {h['doc_type']} · source={src}）相關度={rel}\n"
                       f"章節: {h['heading_path']}\n來源: {h['source_path']} (doc_id={ref}{more}){dupnote}\n內容: {h['text'][:600]}")
        body = "\n\n---\n\n".join(out)
        warn = (f"⚠ 最佳相關度偏低({best:.2f})，知識庫可能無此主題或本題超出範圍，請審慎判斷、必要時明說查無。\n\n"
                if best < SIM_WARN else "")
        return warn + body + "\n\n(內容為英文原文，請用中文綜合作答並標註《文件名》為出處；wiki 來源給策略/概念框架，"
        "game_file 來源給遊戲當前版本精確數值/觸發條件，兩者有出入時以 game_file 數值為準；"
        "相關度<0.5 的片段參考性低；不足時用 get_doc 看更完整內容；doc_id 形如 庫名:doc_id)"

    @mcp.tool()
    def grep_kb(pattern: str, kb: str = "", path_prefix: str = "", limit: int = 30) -> str:
        """精確字面/正則搜尋（遊戲內部 key、精確術語、trigger/modifier 名稱）。path_prefix 可選，限定資料夾子樹。"""
        hits = S().grep(pattern, kb=(kb or None), limit=limit, path_prefix=(path_prefix.strip() or None))
        if not hits: return "（查無符合的字面內容）"
        return "\n\n".join(f"《{h['title']}》(庫:{h.get('kb','')}) | {h['heading_path']}\n來源:{h['source_path']}\n…{h['snippet']}…" for h in hits)

    @mcp.tool()
    def get_doc(doc_id: str, section: int = -1, sections: str = "", offset: int = 0, max_chars: int = 12000) -> str:
        """取回結構化文件內容（大型文件用「大綱→章節」導覽，避免超過工具結果上限）。
        doc_id 形如 "庫名:doc_id"，也接受相對路徑(source_path)。
        用法：
        - 小文件：直接回整份。
        - 大文件且未指定 section/sections：回「文件大綱」(各章節編號與標題)。看過大綱後：
          · 只要 1 個章節 → section=K。
          · 從標題判斷有多個章節都可能相關 → **一次用 sections="2,6,17" 全部取回，不要分成好幾次呼叫**
            (每次呼叫都有成本，能一次拿到就不要分批)。
          · 找不準是哪幾節、或懷疑答案分散在多處 → 改用 search_kb(query, doc_id="庫名:doc_id") 直接語意搜本文件，
            往往比自己猜章節快，但注意它是相關度排序、標題語意不夠貼近查詢的章節可能排不進來，
            此時仍要搭配看大綱確認有沒有漏掉。
        - section=K：取第 K 章節內容(章節仍過長時，依結尾提示用 offset 續取)。
        - sections="K1,K2,...":一次取多個章節(各章節依總預算平均分配上限，內容較多時用 section=K 單獨續取)。
        - offset=N(不給 section)：從本文第 N 字元起讀(search_kb 結果會附此 offset，可直接跳到相關段的完整上下文)。
        - 遊戲檔案類文件末段通常有「原始腳本(附錄)」，需要核對精確數值/未翻譯的trigger時可讀該段。"""
        md = S().get_doc(doc_id)
        if not md:
            return f"（找不到 doc_id={doc_id}）"
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
                return f"（sections 參數格式錯誤，應為逗號分隔的章節編號，如 \"2,6,17\"）"
            per_cap = max(1500, cap // max(1, len(idxs)))
            parts = []
            for idx in idxs:
                if idx < 0 or idx >= len(secs):
                    parts.append(f"（章節[{idx}] 不存在，本文件只有 0–{len(secs)-1}）")
                    continue
                lvl, head, body = secs[idx]
                seg = body[:per_cap]
                more = (f"\n（本章節尚有後續，單獨續取：get_doc(\"{doc_id}\", section={idx}, offset={len(seg)})）"
                        if len(seg) < len(body) else "")
                parts.append(f"### 章節[{idx}] {head}\n{seg}{more}")
            return f"（doc_id={doc_id} 多章節合併回傳，共 {len(idxs)} 節）\n\n" + "\n\n---\n\n".join(parts)

        # 指定單一章節
        if section >= 0:
            secs = _split_sections(md)
            if not secs: return md[:cap]
            if section >= len(secs):
                return f"（doc_id={doc_id} 只有 {len(secs)} 個章節，編號 0–{len(secs)-1}；請先不帶 section 取大綱）"
            lvl, head, body = secs[section]
            off = min(max(0, offset), len(body)); seg = body[off:off+cap]; end = off+len(seg)
            info = f"（doc_id={doc_id} · 章節[{section}] {head} · 共 {len(body)} 字元，本段 {off}–{end}）\n\n"
            if end < len(body):
                tail = f"\n\n（本章節尚有後續，續取：get_doc(\"{doc_id}\", section={section}, offset={end})）"
            else:
                nxt = f"；下一章節 get_doc(\"{doc_id}\", section={section+1})" if section+1 < len(secs) else ""
                tail = f"\n\n（本章節結束{nxt}）"
            return info + seg + tail

        # offset 模式：對「去 frontmatter 的本文」切片，與 chunk 的 char_start 同基準對齊
        if offset > 0:
            body = common.strip_frontmatter(md); tb = len(body)
            off = min(offset, tb)
            # 本文若 ≤ 整份門檻(本可整份回) → 從 offset 一次給到文末，不分頁；大文件才套單段上限
            ocap = (tb - off) if tb <= DOC_WHOLE_RETURN else cap
            seg = body[off:off+ocap]; end = off+len(seg)
            tail = (f"\n\n（尚有後續：get_doc(\"{doc_id}\", offset={end})）" if end < tb else "\n\n（已到文件結尾）")
            return f"（doc_id={doc_id} 本文共 {tb} 字元，本段 {off}–{end}）\n\n" + seg + tail

        # 大文件、未指定 section → 回大綱
        secs = _split_sections(md)
        lines = [f"# 文件大綱  doc_id={doc_id}（共 {total} 字元、{len(secs)} 章節，內容過長未直接回傳）", ""]
        for k, (lvl, head, body) in enumerate(secs):
            indent = "  " * max(0, lvl-1)
            lines.append(f"[{k}] {indent}{head}  （{len(body)} 字元）")
            if sum(len(x)+1 for x in lines) > DOC_SECTION_CAP - 400:
                lines.append(f"… 章節過多，僅列前 {k+1} 個"); break
        lines += ["", "取用方式（擇一）：",
                  f"· 找特定資訊（推薦）：search_kb(\"你的問題\", doc_id=\"{doc_id}\") — 只在本文件內檢索相關段落",
                  f"· 讀單一章節：get_doc(\"{doc_id}\", section=K)",
                  f"· 看過上面章節標題後覺得有好幾節都可能相關 → 一次讀多節：get_doc(\"{doc_id}\", sections=\"2,6,17\")，"
                  "不要為每節分別呼叫"]
        return "\n".join(lines)

    @mcp.tool()
    def list_tree(path_prefix: str = "", depth: int = 1, kb: str = "") -> str:
        """瀏覽虛擬資料夾樹：頂層分 wiki/ 與 game_file/ 兩大子樹，列出某層的子資料夾(含子樹文件數)與檔案。
        path_prefix 空=從頂層開始(看到 wiki/ game_file/ 兩個分支)；depth 控制展開層數。
        想在某分類內找內容時，先用這個看路徑，再 search_kb(query, path_prefix="該路徑")。"""
        t = S().list_tree(path_prefix=path_prefix.strip(), depth=depth, kb=(kb or None))
        if not t["folders"] and not t["files"]:
            return f"（路徑「{t['prefix'] or '(頂層)'}」下無內容；可能 path_prefix 拼錯，先用 list_tree() 看頂層）"
        lines = [f"# 資料夾樹：{t['prefix'] or '(頂層)'}"]
        if t["folders"]:
            lines.append("\n## 子資料夾（含子樹文件數）")
            for name, cnt in t["folders"]:
                lines.append(f"- 📁 {name}/  （{cnt} 份）")
                if sum(len(x)+1 for x in lines) > DOC_SECTION_CAP - 400:
                    lines.append("… 資料夾過多，已截斷；用更深的 path_prefix 縮範圍"); break
        if t["files"]:
            lines.append("\n## 本層檔案")
            for f in t["files"]:
                ref = f"{f['kb']}:{f['doc_id']}"
                lines.append(f"- 📄 {f['name']}  (doc_id={ref})")
                if sum(len(x)+1 for x in lines) > DOC_SECTION_CAP - 200:
                    lines.append("… 檔案過多，已截斷"); break
        lines.append("\n用法：下鑽 list_tree(path_prefix=\"上面某資料夾\")；或 search_kb(query, path_prefix=\"…\") 在子樹內檢索")
        return "\n".join(lines)

    @mcp.tool()
    def list_index(kb: str = "", path_prefix: str = "", doc_type: str = "") -> str:
        """列出文件清單。建議用 path_prefix 限定某資料夾子樹(可靠)；doc_type 為粗分類(如 country/mechanic/idea_group)，僅供粗略參考。"""
        s = S(); targets = s._targets(kb or None); rows = []
        dt = doc_type.strip().lower(); pf = path_prefix.strip()
        for n in targets:
            for m in s.manifest(n):
                if dt and str(m.get("doc_type", "")).strip().lower() != dt: continue
                if pf and not KBSearcher._under_prefix(m.get("source_path"), pf): continue
                rows.append((n, m))
        if not rows: return "（無符合文件；path_prefix 可能拼錯，先用 list_tree 看路徑）"
        lines = [f"- {n}:{m['doc_id']} 《{m['title']}》 ‹{m.get('source_path','')}›" for n, m in rows[:400]]
        head = f"共 {len(rows)} 份（庫: {', '.join(targets)}{('；路徑='+pf) if pf else ''}）{'，僅列前 400' if len(rows)>400 else ''}：\n"
        return head + "\n".join(lines)

    @mcp.tool()
    def knowledge_map(kb: str = "") -> str:
        """分類地圖：知識庫各分類(來源×類型，如 wiki/country、game_file/idea_group)的文件量與代表文件。
        想看『資料夾結構』逐層瀏覽請改用 list_tree。"""
        s = S(); targets = s._targets(kb or None); out = ["# 知識庫分類地圖"]
        for n in targets:
            dm = s.s(n).doc_map; cats = dm.get("categories", []); titles = dm.get("titles", {}); nd = len(titles)
            out.append(f"\n## 庫「{n}」（{nd} 份文件）")
            if cats:
                for c in cats[:20]:
                    ex = [titles.get(d, "") for d in c["docs"][:3]]; ex = [e for e in ex if e]
                    extxt = ("　例：" + "、".join(f"《{e}》" for e in ex)) if ex else ""
                    out.append(f"- {c['key']}（{len(c['docs'])} 份){extxt}")
            else:
                out.append("（無分類資料）")
        return "\n".join(out)

    @mcp.tool()
    def related_docs(doc_id: str) -> str:
        """給 doc_id("庫名:doc_id")，回傳所屬分類、向量最相關文件。"""
        s = S(); n, did, dm = s.related(doc_id)
        if not dm: return f"（找不到 {doc_id} 的關聯）"
        titles = dm.get("titles", {})
        lines = [f"# {n}:{did} 《{titles.get(did, did)}》的關聯（庫:{n}）"]
        cat = next((c for c in dm.get("categories", []) if did in c["docs"]), None)
        if cat:
            lines.append(f"\n## 所屬分類「{cat['key']}」（共 {len(cat['docs'])} 份）")
            lines += [f"- {n}:{d} {titles.get(d, d)}" for d in cat["docs"] if d != did][:8]
        rel = dm.get("related", {}).get(did, [])
        lines.append("\n## 向量最相關")
        lines += ([f"- {n}:{d} {titles.get(d, d)}" for d in rel] or ["（無）"])
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
