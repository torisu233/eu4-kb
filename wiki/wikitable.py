# -*- coding: utf-8 -*-
"""MediaWiki 表格語法({| ... |})→ Markdown 表格。非通用 wikitext 解析器，
針對 EU4 wiki 實測觀察到的表格模式(mildtable/wikitable class，!! 或 || 分隔 cell)做狀態機解析。

巢狀表格(cell 內又是一個完整 {| ... |})會遞迴轉換後併入外層 cell 的文字內容——
實測 EU4 wiki 常見「主表格每列的 Factors 欄位裡包一個 collapsible 子表格列修正明細」這種寫法，
若整塊放棄轉換(舊版做法)，會留下大量未剝除的 wikitext 標記符號(如 `style="..." |`)且失去列的
主體脈絡(如是哪個叛軍類型的修正)，讀起來比降級文字更難懂。遞迴深度上限防禦病態輸入。"""
import re

_ATTR_RE = re.compile(r'^\s*((?:[a-zA-Z-]+\s*=\s*(?:"[^"]*"|\'[^\']*\'|\S+)\s*)+)\|(?!\|)(.*)$', re.S)
_MAX_DEPTH = 4

def _split_cell_attrs(text):
    """回傳 (attrs_dict, content)。只在文字前綴看起來像『屬性列表』(key=value...)時才切開單一 pipe，
    避免誤切 [[Page|Display]] 這種連結內的 pipe(該類文字整體視為 content，attrs 為空)。"""
    m = _ATTR_RE.match(text)
    if not m:
        return {}, text
    attrs_str, content = m.group(1), m.group(2)
    attrs = dict(re.findall(r'([a-zA-Z-]+)\s*=\s*"([^"]*)"', attrs_str))
    if not attrs:
        attrs = dict(re.findall(r"([a-zA-Z-]+)\s*=\s*'([^']*)'", attrs_str))
    return attrs, content

def find_table_blocks(text):
    """回傳 [(start, end, nested)]：(start,end) 為最外層 {| ... |} 的完整區間(含頭尾記號)。
    nested 僅供資訊參考(是否含巢狀子表格)，轉換邏輯本身兩種情況都會處理(見 _parse_rows)。"""
    blocks = []
    i = 0; n = len(text)
    while True:
        s = text.find("{|", i)
        if s == -1:
            break
        depth = 1; j = s + 2; nested = False
        while j < n and depth > 0:
            if text.startswith("{|", j):
                depth += 1; nested = True; j += 2; continue
            if text.startswith("|}", j):
                depth -= 1; j += 2; continue
            j += 1
        blocks.append((s, j, nested))
        i = j
    return blocks

def _parse_rows(block_text, clean_cell_fn=None, _depth=0):
    """逐行狀態機解析一個(可能含巢狀子表格的)表格區塊，回傳 [[(is_header, text), ...], ...]。
    遇到巢狀 {| 時(depth-aware 收集到對應 |})，遞迴呼叫 convert_tables 轉換後，
    把結果併入「目前正在累積的 cell」文字內(而不是整個放棄)。"""
    lines = block_text.split("\n")
    if lines and lines[0].strip().startswith("{|"):
        lines = lines[1:]
    rows = []; cur_row = []
    i = 0; n = len(lines)
    while i < n:
        line = lines[i]
        s = line.strip()
        if not s:
            i += 1; continue
        if s.startswith("{|"):
            depth = 1; buf = [line]; j = i + 1
            while j < n and depth > 0:
                ls = lines[j].strip()
                if ls.startswith("{|"):
                    depth += 1
                elif ls.startswith("|}"):
                    depth -= 1
                buf.append(lines[j]); j += 1
            nested_text = "\n".join(buf)
            nested_md = (convert_tables(nested_text, clean_cell_fn=clean_cell_fn, _depth=_depth + 1)
                         if _depth < _MAX_DEPTH else nested_text)
            nested_md = nested_md.strip()
            if cur_row:
                is_h, txt = cur_row[-1]
                cur_row[-1] = (is_h, (txt + "\n" + nested_md).strip() if txt else nested_md)
            else:
                cur_row.append((False, nested_md))
            i = j; continue
        if s.startswith("|}"):
            i += 1; break
        if s.startswith("|+"):
            i += 1; continue                     # caption，略過
        if s.startswith("|-"):
            if cur_row:
                rows.append(cur_row); cur_row = []
            i += 1; continue
        if s.startswith("!") or s.startswith("|"):
            is_header = s.startswith("!")
            content = s[1:]
            cells_raw = re.split(r'!!|\|\|', content)
            for c in cells_raw:
                attrs, txt = _split_cell_attrs(c.strip())
                try:
                    span = max(1, int(attrs.get("colspan", "1")))
                except ValueError:
                    span = 1
                for _ in range(span):             # colspan 用重複內容降級處理(Markdown表格不支援合併儲存格)
                    cur_row.append((is_header, txt.strip()))
        elif cur_row:                              # 續行(上一個cell的延續文字)
            is_h, txt = cur_row[-1]
            cur_row[-1] = (is_h, (txt + " " + s).strip())
        i += 1
    if cur_row:
        rows.append(cur_row)
    return rows

def rows_to_markdown(rows, clean_cell_fn=None):
    if not rows:
        return ""
    clean = clean_cell_fn or (lambda t: t)
    header = [clean(txt).replace("\n", " ").strip() for _, txt in rows[0]]
    ncol = len(header)
    if ncol == 0:
        return ""
    out = ["| " + " | ".join(h.replace("|", "\\|") or " " for h in header) + " |",
           "| " + " | ".join(["---"] * ncol) + " |"]
    for row in rows[1:]:
        cells = [clean(txt).replace("\n", " ").strip() for _, txt in row]
        if len(cells) < ncol:
            cells += [""] * (ncol - len(cells))
        elif len(cells) > ncol:
            cells = cells[:ncol]
        out.append("| " + " | ".join(c.replace("|", "\\|") for c in cells) + " |")
    return "\n".join(out)

def convert_tables(text, clean_cell_fn=None, _depth=0):
    """把 text 中所有 {| ... |} 表格轉成 Markdown 表格(巢狀子表格遞迴轉換後併入外層 cell，
    見 _parse_rows)。遞迴深度超過上限才真的降級為剝除記號的純文字，避免病態輸入無限遞迴。"""
    blocks = find_table_blocks(text)
    if not blocks:
        return text
    out = []; pos = 0
    for s, e, nested in blocks:
        out.append(text[pos:s])
        block_text = text[s:e]
        if nested and _depth >= _MAX_DEPTH:
            plain = re.sub(r'^\s*[{|]\|.*$', '', block_text, flags=re.M)
            plain = re.sub(r'^\s*[!|]', '', plain, flags=re.M)
            out.append(plain)
        else:
            rows = _parse_rows(block_text, clean_cell_fn=clean_cell_fn, _depth=_depth)
            out.append(rows_to_markdown(rows, clean_cell_fn))
        pos = e
    out.append(text[pos:])
    return "".join(out)
