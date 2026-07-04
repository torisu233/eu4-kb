# -*- coding: utf-8 -*-
"""Game file extractor 入口：解析 EU4 遊戲安裝目錄的 Clausewitz 腳本 -> 渲染 -> docs/*.md + manifest.jsonl(source=game_file)。

v1 PoC 範圍(見計畫)：ideas(全量) + government_reforms(全量) + country_history(需用 --country-filter 指定國家)。
missions/events/decisions/province_history 留待 v1.5 依效果評估再擴充(此處先不實作，entity_types 給了會報錯)。
"""
import os, sys, glob, json
from . import common
from clausewitz.parser import Block, parse_file
from clausewitz.localisation import LocalisationIndex
from clausewitz.macro_expand import MacroTable
from renderers import idea_group, government_reform, country_history

_IMPLEMENTED = {"ideas", "government_reforms", "country_history"}
_RENDERERS = {"ideas": idea_group, "government_reforms": government_reform, "country_history": country_history}

def _game_version(game_dir):
    p = os.path.join(game_dir, "eu4_branch.txt")
    if os.path.exists(p):
        return open(p, encoding="utf-8", errors="replace").read().strip()
    return ""

def _iter_idea_groups(game_dir):
    """common/ideas/*.txt 的每個頂層、含 category 子鍵的 Block 視為一個理念組。
    排除 zzz_default_idea.txt(範本)/zzzz_compatibility.txt(舊版相容映射)，對 RAG 無直接價值。"""
    d = os.path.join(game_dir, "common", "ideas")
    for fp in sorted(glob.glob(os.path.join(d, "*.txt"))):
        base = os.path.basename(fp).lower()
        if base in ("zzz_default_idea.txt", "zzzz_compatibility.txt"):
            continue
        top = parse_file(fp)
        rel = os.path.relpath(fp, game_dir).replace("\\", "/")
        for k, v in top.items:
            if k is not None and isinstance(v, Block) and v.get("category") is not None:
                yield k, v, rel

def _iter_government_reforms(game_dir):
    d = os.path.join(game_dir, "common", "government_reforms")
    for fp in sorted(glob.glob(os.path.join(d, "*.txt"))):
        top = parse_file(fp)
        rel = os.path.relpath(fp, game_dir).replace("\\", "/")
        for k, v in top.items:
            if k is not None and isinstance(v, Block):
                yield k, v, rel

def _find_country_file(game_dir, tag):
    d = os.path.join(game_dir, "history", "countries")
    matches = glob.glob(os.path.join(d, f"{tag} - *.txt"))
    return matches[0] if matches else None

def run(name, game_dir, entity_types=None, country_filter=None):
    if not game_dir or not os.path.isdir(game_dir):
        raise ValueError(f"game_dir 不存在或未指定: {game_dir}")
    entity_types = entity_types or ["ideas", "government_reforms"]  # country_history 需明確 --country-filter，預設不跑(避免誤跑975個檔案)
    bad = [e for e in entity_types if e not in _IMPLEMENTED]
    if bad:
        raise ValueError(f"尚未實作的 entity_types: {bad}；已實作: {sorted(_IMPLEMENTED)}（missions/events/decisions/province_history 留待 v1.5）")
    if "country_history" in entity_types and not country_filter:
        raise ValueError("entity_types 含 country_history 時必須指定 --country-filter(國家tag，逗號分隔)，v1 不支援全量975國一次跑完")

    P = common.kb_paths(name)
    os.makedirs(P["docs"], exist_ok=True)
    game_version = _game_version(game_dir)

    print(f"[game_extract] 載入本地化...", flush=True)
    loc = LocalisationIndex()
    nloc = loc.load_dir(os.path.join(game_dir, "localisation"), lang="english")
    print(f"[game_extract] 本地化 {nloc} 檔 / {len(loc)} keys", flush=True)

    print(f"[game_extract] 載入巨集表...", flush=True)
    mt = MacroTable()
    nmac = mt.load_dirs(os.path.join(game_dir, "common", "scripted_triggers"),
                         os.path.join(game_dir, "common", "scripted_effects"))
    print(f"[game_extract] 巨集 {nmac} 條", flush=True)

    new_rows = []
    if "ideas" in entity_types:
        n = 0
        for entity_id, blk, rel in _iter_idea_groups(game_dir):
            doc_id, title, source_path, cat, md, fm = idea_group.render(entity_id, blk, loc, mt, rel, game_version)
            open(os.path.join(P["base"], fm["md_path"]), "w", encoding="utf-8").write(md)
            new_rows.append({**fm, "source_path": source_path}); n += 1
        print(f"[game_extract] ideas: {n} 篇", flush=True)

    if "government_reforms" in entity_types:
        n = 0
        for entity_id, blk, rel in _iter_government_reforms(game_dir):
            doc_id, title, source_path, cat, md, fm = government_reform.render(entity_id, blk, loc, mt, rel, game_version)
            open(os.path.join(P["base"], fm["md_path"]), "w", encoding="utf-8").write(md)
            new_rows.append({**fm, "source_path": source_path}); n += 1
        print(f"[game_extract] government_reforms: {n} 篇", flush=True)

    if "country_history" in entity_types:
        n = 0
        for tag in country_filter:
            tag = tag.strip().upper()
            fp = _find_country_file(game_dir, tag)
            if not fp:
                print(f"[game_extract] ⚠ 找不到國家檔案: {tag}", file=sys.stderr); continue
            blk = parse_file(fp)
            rel = os.path.relpath(fp, game_dir).replace("\\", "/")
            doc_id, title, source_path, cat, md, fm = country_history.render(tag, blk, loc, mt, rel, game_version)
            open(os.path.join(P["base"], fm["md_path"]), "w", encoding="utf-8").write(md)
            new_rows.append({**fm, "source_path": source_path}); n += 1
        print(f"[game_extract] country_history: {n} 篇", flush=True)

    # manifest.jsonl 合併：保留非 game_file 來源(如 wiki)的既有列，game_file 的列整批以本次結果取代
    existing = []
    if os.path.exists(P["manifest"]):
        for l in open(P["manifest"], encoding="utf-8"):
            if l.strip():
                m = json.loads(l)
                if m.get("source") != "game_file":
                    existing.append(m)
    with open(P["manifest"], "w", encoding="utf-8") as out:
        for m in existing + new_rows:
            out.write(json.dumps(m, ensure_ascii=False) + "\n")
    print(f"[game_extract] DONE 新增/更新 {len(new_rows)} 篇 game_file 文件（manifest 共 {len(existing)+len(new_rows)} 列）", flush=True)
    return len(new_rows)
