# -*- coding: utf-8 -*-
"""政府改革(common/government_reforms/*)渲染器：trigger(解锁条件) + effect + modifier(效果表格化)。"""
from clausewitz.parser import Block
from clausewitz.macro_expand import expand_block
from clausewitz.translate_rules import translate_block_md
from clausewitz.serialize import serialize
from .base import make_doc_id, source_hash, assemble_doc

_SECTION_LABELS = {
    "trigger": "Trigger (unlock condition)",
    "effect": "Effect (applied once, when reform is picked)",
    "modifier": "Modifier",
    "modifiers": "Modifiers",
    "custom_attributes": "Custom Attributes",
    "government_names": "Government Names",
    # AI 選擇權重不是雜訊——玩家常會問"AI大概會怎麼選"，保留在獨立分區，
    # 一般查詢(如"這個改革给什么加成")不太会命中，但問AI決策時能被檢索到。
    "ai": "AI Weighting (how the game AI evaluates picking this reform)",
}
_KNOWN_SECTIONS = list(_SECTION_LABELS.keys())
_SKIP_KEYS = {"icon"}

def render(entity_id, block, loc, macro_table, source_file, game_version=""):
    title = loc.get(entity_id) if loc else entity_id
    overview_lines = [f"Government reform key: `{entity_id}`."]

    # 注意：切塊器(kb/build.py)只把「單獨成段(前後有空行)」的一行 #.. 文字視為標題並更新 heading_path，
    # 標題與內文之間務必插入空字串("" 佔位)造出空行分隔，否則標題會跟內文黏成同一段落，
    # heading_path 不會往下一層鑽(這是本專案渲染器踩過的真實bug，修過一次才發現要每個標題都補這個空行)。
    cond_lines = []
    for sec in _KNOWN_SECTIONS:
        sub = block.get(sec)
        if not sub:
            continue
        cond_lines.append(f"### {_SECTION_LABELS[sec]}")
        cond_lines.append("")
        cond_lines.append(translate_block_md(expand_block(sub, macro_table), loc))
        cond_lines.append("")

    handled = set(_KNOWN_SECTIONS) | _SKIP_KEYS
    leftover = Block([(k, v) for k, v in block.items if k is not None and k.lower() not in handled])
    if leftover.items:
        cond_lines.append("### Other")
        cond_lines.append("")
        cond_lines.append(translate_block_md(expand_block(leftover, macro_table), loc))

    raw = f"{entity_id} = {{\n{serialize(block, 1)}\n}}"
    doc_id = make_doc_id("government_reform", entity_id)
    source_path = f"game_file/government_reform/{entity_id}"
    fm = {
        "doc_id": doc_id, "title": title, "system": "eu4", "doc_type": "government_reform",
        "lang": "en", "version": game_version, "source_hash": source_hash(raw), "status": "active",
        "md_path": f"docs/{doc_id}.md", "source": "game_file", "entity_category": "government_reform",
        "entity_id": entity_id, "source_file": source_file, "game_version": game_version,
    }
    md = assemble_doc(fm, title, "\n".join(overview_lines), "\n".join(cond_lines), raw)
    return doc_id, title, source_path, "government_reform", md, fm
