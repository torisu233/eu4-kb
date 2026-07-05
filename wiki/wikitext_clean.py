# -*- coding: utf-8 -*-
"""Wikitext 清洗：模板剝離/選擇性展開、wiki連結轉純文字、標題/粗斜體轉 Markdown、表格轉換。
規則覆蓋高頻的二三十種模板(圖示/顏色/版本/DLC/navbox等)，未知模板走兜底(取最後一個參數當顯示文字)。
不可能100%覆蓋所有模板，殘留的 {{ 數量會回報供品質抽查(見 wiki_extract.py 的 residual_template_count)。"""
import re
from .wikitable import convert_tables

# (a) 直接丟棄：版本/DLC標籤、排版控制、導覽框
_DISCARD_NAMES = {
    "version", "sversion", "toc", "__toc__", "clear", "contents", "reflist",
    "main", "see also", "colorize", "anchor", "cite", "citation needed",
}
# (b) 取第一參數(如 {{flag|Ming}} -> "Ming")；注意："country" 不可放這裡——{{Country|...}} 是多行
# infobox(如 {{Country\n|Government=Free City\n|Culture=...\n}})，args 全是 key=value，取第一個會拿到
# "Government=Free City" 這種殘渣，交給下面的兜底規則(自動跳過 key=value 參數)處理才對。
_FIRST_PARAM_NAMES = {"flag", "flagcountry"}
# (c) 取最後一個參數(顏色/圖示包裝，內容本身有意義，模板只是外殼)
_LAST_PARAM_NAMES = {"green", "red", "yellow", "dlc-only", "dlc", "tooltip", "icon2", "color"}
# (d) 整體刪除(純裝飾圖示，無文字可保留)
_DROP_ENTIRELY_NAMES = {"icon", "icon24", "iconify"}
# (e) 保留全部參數並用空白接合(版面包裝模板，內容才是重點)
_JOIN_PARAMS_NAMES = {"plainlist", "multicolumn", "box wrapper", "columns"}
# (f) 多具名字段模板：模板名(小寫) -> [(欄位名小寫, 顯示標籤), ...]，只列有敘述性內容的欄位
# (跳過 version/id/collapse/mtth/map 等純技術/排版欄位)。2026-07 審計發現：這類模板(Decision/
# Event/Option/Country)原本走下面的兜底規則，會被誤判成「大多數欄位是自由文本」而只留1個欄位、
# 或「全部是key=value」而整個丟棄——不管哪種都會把 potential/allow/trigger 這類真正回答「怎麼做/
# 何時觸發」的核心內容悄悄丟掉(見 memory: eu4-kb-project-status「怎麼成立莫臥兒」bug 的診斷記錄)。
_KEEP_FIELDS_NAMES = {
    "decision": [("decision_name", "Decision"), ("potential", "Potential"), ("allow", "Allow"), ("effect", "Effect")],
    "country":  [("government", "Government"), ("culture", "Culture"), ("religion", "Religion"),
                 ("capital", "Capital"), ("tech", "Tech"), ("rank", "Rank")],
    "event":    [("event_name", "Event"), ("event_text", "Description"), ("trigger", "Trigger"),
                 ("effect", "Effect"), ("options", "Options")],
    "option":   [("option_text", "Option"), ("effect", "Effect")],
}

_TEMPLATE_INNER_RE = re.compile(r'\{\{([^{}]*)\}\}')
# 注意：邊界只能用 [ \t]*(水平空白)，不能用 \s*——\s 會吃掉換行，貪婪比對在 re.M 下會把標題後面
# 所有空行一併吃進 match，替換後標題會跟緊接內容黏成一段(切塊器就抓不到這個標題層級)。
# 這個 bug 曾經讓幾乎全部 1883 篇 wiki 文件的標題都失效，教訓：正則邊界符號要謹慎選字元類。
_HEADING_RE = re.compile(r'^(={2,6})[ \t]*(.*?)[ \t]*\1[ \t]*$', re.M)
_LINK_RE = re.compile(r'\[\[([^\]|]+)(?:\|([^\]]+))?\]\]')
# 注意：一定要有 re.S(DOTALL)——Decision/Event 這類模板的 potential=/allow=/trigger= 欄位值本身是
# 多行清單，沒有 re.S 時 "." 不跨行，導致這些多行欄位被誤判成「不是 key=value」而走進兜底的錯誤分支
# (2026-07 審計發現的真實bug，見上面 _KEEP_FIELDS_NAMES 的說明)。
_KV_ARG_RE = re.compile(r'^[a-zA-Z_][a-zA-Z0-9_ \-]*\s*=\s*.*$', re.S)   # 形如 "government=Free City"、"link=on" 的具名參數(非顯示文字)
_KV_SPLIT_RE = re.compile(r'^([a-zA-Z_][a-zA-Z0-9_ \-]*?)\s*=\s*(.*)$', re.S)
_HTML_WRAP_RE = re.compile(r'</?(?:div|span)\b[^>]*>', re.I)       # 排版用 HTML 包裝標籤(保留內容、丟標籤)


def _parse_kv(a):
    """把 "Government=Indian Sultanate" 解析成 ("government", "Indian Sultanate")；非 kv 形式回傳 None。"""
    m = _KV_SPLIT_RE.match(a)
    return (m.group(1).strip().lower(), m.group(2)) if m else None

def _split_template(inner):
    """按頂層 | 切參數——注意 [[連結|顯示文字]] 內部的 | 不算分隔符，否則 potential=/allow= 這類
    欄位值裡只要出現一個 wiki 連結(如 [[end-game_tag|end-game nation]])就會被錯誤地切成兩段參數，
    field 內容和邊界全部亂掉(2026-07 審計發現的真實bug)。"""
    parts = []; buf = []; depth = 0; i = 0; n = len(inner)
    while i < n:
        two = inner[i:i+2]
        if two == '[[':
            depth += 1; buf.append(two); i += 2; continue
        if two == ']]' and depth > 0:
            depth -= 1; buf.append(two); i += 2; continue
        if inner[i] == '|' and depth == 0:
            parts.append(''.join(buf)); buf = []; i += 1; continue
        buf.append(inner[i]); i += 1
    parts.append(''.join(buf))
    return parts[0].strip(), [p.strip() for p in parts[1:]]

def _process_template(name, args):
    nl = name.lower()
    if nl.startswith("#lst:"):                              # Labeled Section Transclusion：v1 不做真跨頁轉錄，降級為說明性佔位文字
        target = name.split(":", 1)[1] if ":" in name else (args[0] if args else "?")
        return f'[See "{target}" article for details]'
    if nl in _DISCARD_NAMES or "navbox" in nl or nl.endswith(" navbox"):
        return ""
    if nl in _DROP_ENTIRELY_NAMES:
        return ""
    if nl in _FIRST_PARAM_NAMES:
        return args[0] if args else ""
    if nl in _LAST_PARAM_NAMES:
        return args[-1] if args else ""
    if nl in _JOIN_PARAMS_NAMES:
        return " ".join(a for a in args if a)
    if nl in _KEEP_FIELDS_NAMES:
        found = {}
        for a in args:
            kv = _parse_kv(a)
            if kv and kv[1].strip():
                found[kv[0]] = kv[1].strip()
        parts = [f"{label}: {found[key]}" for key, label in _KEEP_FIELDS_NAMES[nl] if key in found]
        return "\n".join(parts)
    # 兜底：未知模板 -> 由後往前找第一個「非 key=value 形式」的參數當顯示文字(多數展示型模板的慣例是
    # 最後一個位置參數才是要顯示的文字，key=value 形式的通常是版面/連結控制參數，如 link=on、size=24px)；
    # 全部參數都是 key=value(如 infobox 類模板)或完全無參數 -> 整個丟棄，不留生硬的 "key=value" 殘渣。
    for a in reversed(args):
        if a and not _KV_ARG_RE.match(a):
            return a
    return ""

def strip_templates(text, max_rounds=8):
    """逐輪剝離最內層(不含巢狀 {{)的模板，直到無法再剝或達輪數上限(防禦異常輸入)。
    回傳 (清洗後文字, 殘留的 {{ 數量)——殘留數是清洗品質的量化指標，非0代表有未覆蓋的模板寫法。"""
    for _ in range(max_rounds):
        if "{{" not in text:
            break
        new_text = _TEMPLATE_INNER_RE.sub(lambda m: _process_template(*_split_template(m.group(1))), text)
        if new_text == text:
            break
        text = new_text
    return text, text.count("{{")

def _replace_link(m):
    target, display = m.group(1), m.group(2)
    tl = target.strip().lower()
    if tl.startswith(("category:", "file:", "image:")):
        return ""
    return (display or target).strip()

def strip_links(text):
    return _LINK_RE.sub(_replace_link, text)

def convert_headings(text):
    """轉換 ==H2==→## H2 等，並在標題後強制補一個換行(=製造空行)。真實 wikitext 常見「標題後緊接內容
    不留空行」(尤其標題後直接接清單)，若不強制補空行，切塊器(kb/build.py)的段落判定會把標題和緊接內容
    黏成一段，heading_path 抓不到這層——這點不能指望來源 wikitext 本身有空行。"""
    return _HEADING_RE.sub(lambda m: "#" * len(m.group(1)) + " " + m.group(2).strip() + "\n", text)

def convert_emphasis(text):
    text = re.sub(r"'''(.*?)'''", r"**\1**", text)
    text = re.sub(r"''(.*?)''", r"*\1*", text)
    return text

def clean_inline(text):
    """表格 cell 內文清洗：模板+連結+粗斜體(不處理標題，cell 內不會有 wiki 標題)。"""
    text, _ = strip_templates(text)
    text = strip_links(text)
    text = convert_emphasis(text)
    return text.strip()

def clean_wikitext(wikitext):
    """回傳 (清洗後的 Markdown 正文, residual_template_count)。"""
    text = wikitext
    text = re.sub(r'<!--.*?-->', '', text, flags=re.S)                       # HTML 註解
    text = re.sub(r'<ref[^>]*/>', '', text)                                   # 自閉合 <ref/>
    text = re.sub(r'<ref[^>]*>.*?</ref>', '', text, flags=re.S)              # <ref>...</ref>
    text = re.sub(r'<references\s*/?>', '', text, flags=re.I)
    text = re.sub(r'__[A-Z_]+__', '', text)                                   # __TOC__ / __NOTOC__ 等
    text = re.sub(r'<br\s*/?>', '\n', text, flags=re.I)
    text = re.sub(r'<math>(.*?)</math>', r'\1', text, flags=re.S)            # 公式：保留內容(純文字)、丟標籤
    text = re.sub(r'<section\s+(?:begin|end)=[^>]*/>', '', text, flags=re.I) # LST 區段標記(<section begin=x/>)，純標記無文字資訊
    text = _HTML_WRAP_RE.sub('', text)                                        # <div style="..">/</div>/<span..> 等排版包裝標籤(保留內容)
    text = convert_tables(text, clean_cell_fn=clean_inline)                   # 先轉表格(cell內文自帶模板/連結清洗)
    text, residual = strip_templates(text)                                    # 表格外的模板
    text = strip_links(text)
    text = convert_headings(text)
    text = convert_emphasis(text)
    text = re.sub(r'[ \t]{2,}', ' ', text)                                    # 模板移除後留下的連續空格收斂
    text = re.sub(r'\n{3,}', '\n\n', text)                                    # 多餘空行收斂
    return text.strip(), residual
