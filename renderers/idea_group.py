# -*- coding: utf-8 -*-
"""理念組(common/ideas/*)渲染器：category/trigger/bonus + 7 個 idea slot。"""
from clausewitz.parser import Block
from clausewitz.macro_expand import expand_block
from clausewitz.translate_rules import translate_block_md
from clausewitz.serialize import serialize
from .base import make_doc_id, source_hash, assemble_doc

_SKIP_KEYS = {"category", "bonus", "trigger", "ai_will_do", "important"}
# ai_will_do 不是雜訊——保留在獨立分區(見下方渲染邏輯)，只是不跟一般理念清單混在一起
_CATEGORY_LABEL = {"mil": "Military (MIL)", "adm": "Administrative (ADM)", "dip": "Diplomatic (DIP)"}

def render(entity_id, block, loc, macro_table, source_file, game_version=""):
    """entity_id: 理念組 key(如 aristocracy_ideas)；block: 該理念組的原始(未展開) Block。
    回傳 (doc_id, title, source_path, entity_category, markdown_text)。"""
    title = loc.get(entity_id) if loc else entity_id
    category = block.get("category", "")
    cat_label = _CATEGORY_LABEL.get((category or "").lower(), category)

    overview_lines = [f"Idea group key: `{entity_id}`. Category: {cat_label}."]
    trigger = block.get("trigger")
    if trigger:
        overview_lines.append("")
        overview_lines.append("**Available only if:**")
        overview_lines.append(translate_block_md(expand_block(trigger, macro_table), loc))
    bonus = block.get("bonus")
    # 注意：切塊器(kb/build.py)只把「單獨成段(前後有空行)」的一行 #.. 文字視為標題並更新 heading_path，
    # 標題與內文之間務必插入空字串("" 佔位)造出空行分隔，否則標題會跟內文黏成同一段落，
    # heading_path 不會往下一層鑽(這是本專案渲染器踩過的真實bug，修過一次才發現要每個標題都補這個空行)。
    cond_lines = []
    if bonus:
        cond_lines.append("### Group completion bonus")
        cond_lines.append("")
        cond_lines.append(translate_block_md(expand_block(bonus, macro_table), loc))
        cond_lines.append("")

    idea_slots = [(k, v) for k, v in block.items if k is not None and k.lower() not in _SKIP_KEYS and isinstance(v, Block)]
    if idea_slots:
        cond_lines.append("### Ideas")
        cond_lines.append("")
        for i, (ik, iv) in enumerate(idea_slots, 1):
            iname = loc.get(ik) if loc else ik
            cond_lines.append(f"#### {i}. {iname} (`{ik}`)")
            cond_lines.append("")
            itrig = iv.get("trigger")
            body = Block([(k, v) for k, v in iv.items if k is not None and k.lower() != "trigger"])
            cond_lines.append(translate_block_md(expand_block(body, macro_table), loc))
            if itrig:
                cond_lines.append("")
                cond_lines.append("Conditions:")
                cond_lines.append("")
                cond_lines.append(translate_block_md(expand_block(itrig, macro_table), loc))
            cond_lines.append("")

    ai_will_do = block.get("ai_will_do")
    if ai_will_do:
        cond_lines.append("### AI Weighting (how the game AI evaluates picking this idea group)")
        cond_lines.append("")
        cond_lines.append(translate_block_md(expand_block(ai_will_do, macro_table), loc))

    raw = f"{entity_id} = {{\n{serialize(block, 1)}\n}}"
    doc_id = make_doc_id("idea_group", entity_id)
    source_path = f"game_file/idea_group/{entity_id}"
    fm = {
        "doc_id": doc_id, "title": title, "system": "eu4", "doc_type": "idea_group",
        "lang": "en", "version": game_version, "source_hash": source_hash(raw), "status": "active",
        "md_path": f"docs/{doc_id}.md", "source": "game_file", "entity_category": "idea_group",
        "entity_id": entity_id, "source_file": source_file, "game_version": game_version,
    }
    md = assemble_doc(fm, title, "\n".join(overview_lines), "\n".join(cond_lines), raw)
    return doc_id, title, source_path, "idea_group", md, fm
