# -*- coding: utf-8 -*-
"""生成中文術語詞典 artifact（`kbs/<kb>/zh_glossary.json`）。

用途：知識庫語料是英文（來源真相），但中文用戶想看到權威中文術語。此步在 build 時（有遊戲檔案時）
把 **遊戲英文 localisation(key→英文)** 與 **paratranz 中文 localisation(key→中文，同 key 結構)** 按共享
key join，產出兩張查找表隨數據發布，供 MCP serve 時給檢索結果加中文注解：

  { "name2zh": { <英文名小寫>: <中文> },   # 給 wiki 標題/一般顯示名注解
    "key2zh":  { <key小寫>:    <中文> } }   # 給 game_file 用 entity_id(=source_path末段) 直接注解

為什麼要 build 時做：雲端容器沒有 Steam 遊戲檔案，英文 loc 只在 build 時有；serve 時只讀 kbs 數據。
中文來源 = paratranz/EU4-Chinese-Localisation（CC BY-NC-SA 4.0，非商用；本項目個人非商用，需署名）。
"""
import os, sys, json, argparse
from . import common
from clausewitz.localisation import LocalisationIndex

DEFAULT_ZH_DIR = os.path.join(common.ROOT, "cache", "paratranz_zh", "localisation")


def run(name, game_dir, zh_loc_dir=None):
    zh_loc_dir = zh_loc_dir or DEFAULT_ZH_DIR
    if not game_dir or not os.path.isdir(os.path.join(game_dir, "localisation")):
        raise ValueError(f"game_dir 無效(缺 localisation): {game_dir}")
    if not os.path.isdir(zh_loc_dir):
        raise ValueError(f"中文 loc 目錄不存在: {zh_loc_dir}（先 clone paratranz/EU4-Chinese-Localisation）")

    gloc = LocalisationIndex(); ng = gloc.load_dir(os.path.join(game_dir, "localisation"), lang="english")
    zloc = LocalisationIndex(); nz = zloc.load_dir(zh_loc_dir, lang="english")  # paratranz 檔名亦為 _l_english
    print(f"[zh_glossary] 游戏英文 {ng}檔/{len(gloc)}key  paratranz中文 {nz}檔/{len(zloc)}key", flush=True)

    # 只保留「短的、像術語名」的值：注解是要給標題加一個名字，不是塞一段事件/tooltip 描述。
    # 過濾掉含換行、含佔位符($/§/[)、或過長(>24中文字)的值，大幅縮小 artifact 並去噪。
    def _is_term(zh):
        if not zh or "\n" in zh or len(zh) > 24:
            return False
        if any(c in zh for c in "$§[{"):
            return False
        return True

    key2zh = {}
    for key in zloc._map:                                   # paratranz key→中文(僅術語名)
        zh = zloc.get(key, fallback_title_case=False)
        if zh: zh = zh.strip()
        if _is_term(zh):
            key2zh[key.lower()] = zh

    name2zh = {}
    for key in gloc._map:                                   # 共享 key join：英文名→中文(僅術語名)
        en = gloc.get(key, fallback_title_case=False)
        zh = key2zh.get(key.lower())
        if en and zh and en.strip():
            name2zh.setdefault(en.strip().lower(), zh)      # 同英文名多 key 時保留首個(穩定)

    P = common.kb_paths(name)
    os.makedirs(P["base"], exist_ok=True)
    with open(P["zh_glossary"], "w", encoding="utf-8") as f:
        json.dump({"name2zh": name2zh, "key2zh": key2zh}, f, ensure_ascii=False)
    print(f"[zh_glossary] DONE name2zh={len(name2zh)} key2zh={len(key2zh)} -> {P['zh_glossary']}", flush=True)
    return len(name2zh), len(key2zh)


def main():
    ap = argparse.ArgumentParser(description="生成中文術語詞典 zh_glossary.json")
    ap.add_argument("--kb", required=True)
    ap.add_argument("--game-dir", required=True, help="EU4 遊戲安裝目錄")
    ap.add_argument("--zh-loc-dir", default=None, help=f"paratranz 中文 loc 目錄(預設 {DEFAULT_ZH_DIR})")
    a = ap.parse_args()
    run(a.kb.strip(), a.game_dir, a.zh_loc_dir)


if __name__ == "__main__":
    main()
