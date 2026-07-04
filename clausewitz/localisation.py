# -*- coding: utf-8 -*-
"""EU4 本地化索引：載入 localisation/*_l_english.yml，建立 key -> 顯示文本 的全域查找表。

不用標準 YAML 解析器：本地化檔案含 $VAR$ 佔位符與 §顏色碼§!，會讓標準 YAML parser 報錯/誤解析，
改用逐行正則解析(格式穩定：" key:version \"text\"")。

顏色碼已實測確認是標準 UTF-8（§ = U+00A7，即 \\xc2\\xa7 兩位元組），檔案帶 UTF-8 BOM，
不是位元組編碼問題，清洗只需正則 `§[A-Za-z!]` 即可(見 CLAUDE 計畫風險清單第4點的核實結論)。
"""
import os, re, glob

_LINE_RE = re.compile(r'^\s*([A-Za-z0-9_.\-]+)\s*:\s*(\d+)\s+"(.*)"\s*$')
_COLOR_RE = re.compile(r'§[A-Za-z!]')
_VAR_RE = re.compile(r'\$([A-Za-z0-9_]+)(\|[A-Za-z0-9]+)?\$')

def strip_color_codes(text):
    """移除 §R §! 這類顏色碼(§=U+00A7 + 一個字母，或 §!表示結束)。"""
    return _COLOR_RE.sub("", text or "")

def normalize_placeholders(text, style="brace"):
    """$WHERE|Y$ 這類動態佔位符：style='brace' 轉成 {WHERE}(去掉格式化後綴，標註是動態值)；
    style='keep' 原樣保留；用於降低下游閱讀者(LLM)對佔位符語法的誤解。"""
    if style == "keep":
        return text
    return _VAR_RE.sub(lambda m: "{" + m.group(1) + "}", text or "")

def clean_text(text):
    """本地化文本清洗的標準組合：去顏色碼 + 佔位符轉花括號。"""
    return normalize_placeholders(strip_color_codes(text))

def _title_case_fallback(key):
    """查無本地化時的兜底顯示名：snake_case -> Title Case。"""
    words = re.split(r'[_\-]+', key.strip())
    return " ".join(w.capitalize() for w in words if w)

class LocalisationIndex:
    def __init__(self):
        self._map = {}          # key(小寫) -> 原始文本(未清洗)
        self._loaded_files = 0
        self.miss_counter = {}  # 查無本地化的 key -> 命中次數(供品質抽查)

    def load_dir(self, loc_dir, lang="english", pattern=None):
        """載入 loc_dir 下所有符合 *_l_<lang>.yml 的檔案(預設 english)。同 key 以檔案內「最後一次出現」為準
        (與遊戲本體行為一致：同檔內重複定義時後者覆蓋前者)。"""
        pat = pattern or f"*_l_{lang}.yml"
        files = sorted(glob.glob(os.path.join(loc_dir, pat)))
        for fp in files:
            self.load_file(fp)
        return len(files)

    def load_file(self, path):
        with open(path, encoding="utf-8-sig", errors="replace") as f:
            for line in f:
                line = line.rstrip("\n").rstrip("\r")
                if not line.strip() or line.strip().endswith(":") and ":" not in line.strip()[:-1]:
                    continue  # 空行或 "l_english:" 語言標頭行
                m = _LINE_RE.match(line)
                if not m:
                    continue
                key, _ver, text = m.group(1), m.group(2), m.group(3)
                self._map[key.lower()] = text   # 後出現者覆蓋(檔內順序 = 遊戲載入順序)
        self._loaded_files += 1

    def raw(self, key):
        """查原始文本(未清洗顏色碼/佔位符)，查無回 None。"""
        return self._map.get((key or "").lower())

    def get(self, key, fallback_title_case=True):
        """查清洗後的顯示文本。查無時：fallback_title_case=True 回傳 snake_case->Title Case 兜底(並計入 miss_counter)；
        否則回傳 None。"""
        raw = self.raw(key)
        if raw is not None:
            return clean_text(raw)
        self.miss_counter[key] = self.miss_counter.get(key, 0) + 1
        return _title_case_fallback(key) if fallback_title_case else None

    def __len__(self):
        return len(self._map)

    def __contains__(self, key):
        return (key or "").lower() in self._map

    def top_misses(self, n=30):
        return sorted(self.miss_counter.items(), key=lambda kv: -kv[1])[:n]
