# -*- coding: utf-8 -*-
"""國家歷史(history/countries/*)渲染器：無日期前綴的初始狀態設置 + 按日期排序的時間線。"""
from clausewitz.parser import Block, is_date_key
from clausewitz.macro_expand import expand_block
from clausewitz.translate_rules import translate_block, translate_block_md
from clausewitz.serialize import serialize
from .base import make_doc_id, source_hash, assemble_doc

_ROLE_LABEL = {"monarch": "即位为君主", "heir": "王储诞生", "queen": "王后/配偶"}

def _fmt_ruler(blk, role):
    name = blk.get("name", "?")
    dynasty = blk.get("dynasty")
    adm, dip, mil = blk.get("adm"), blk.get("dip"), blk.get("mil")
    dynasty_s = f"（{dynasty}王朝）" if dynasty else ""
    stats = f"（行政{adm}/外交{dip}/军事{mil}）" if any([adm, dip, mil]) else ""
    label = _ROLE_LABEL.get(role, role)
    return f"{name}{dynasty_s} {label}{stats}"

def _fmt_date_line(date_str, blk, loc):
    y, m, d = (date_str.split(".") + ["1", "1"])[:3]
    date_disp = f"{int(y):04d}-{int(m):02d}-{int(d):02d}"
    parts = []
    for role in ("monarch", "heir", "queen"):
        sub = blk.get(role)
        if isinstance(sub, Block):
            parts.append(_fmt_ruler(sub, role))
    leftover = Block([(k, v) for k, v in blk.items if k is not None and k.lower() not in ("monarch", "heir", "queen")])
    extra = translate_block(leftover, loc) if leftover.items else []
    head = f"- **{date_disp}**" + ("：" + "；".join(parts) if parts else "")
    lines = [head]
    lines += ["  " + e for e in extra]
    return lines

def render(entity_id, block, loc, macro_table, source_file, game_version=""):
    """entity_id: 國家 tag(如 MNG)；block: 該國歷史檔案的頂層 Block(整份檔案即一個 implicit block)。"""
    title = loc.get(entity_id) if loc else entity_id

    non_date = Block([(k, v) for k, v in block.items if k is not None and not is_date_key(k)])
    overview_lines = [f"Country: {title} (`{entity_id}`)."]
    overview_lines.append(translate_block_md(expand_block(non_date, macro_table), loc))

    dates = block.date_items()
    cond_lines = []
    if dates:
        cond_lines.append("### Timeline")
        cond_lines.append("")  # 標題與內文間需空行，切塊器才會把 heading_path 鑽到 Timeline 這層(見 idea_group.py 註解)
        for date_str, dblk in dates:
            cond_lines.extend(_fmt_date_line(date_str, dblk, loc))

    raw = serialize(block, 0)
    doc_id = make_doc_id("country_history", entity_id)
    source_path = f"game_file/country_history/{entity_id}"
    fm = {
        "doc_id": doc_id, "title": title, "system": "eu4", "doc_type": "country_history",
        "lang": "en", "version": game_version, "source_hash": source_hash(raw), "status": "active",
        "md_path": f"docs/{doc_id}.md", "source": "game_file", "entity_category": "country_history",
        "entity_id": entity_id, "source_file": source_file, "game_version": game_version,
    }
    md = assemble_doc(fm, title, "\n".join(overview_lines), "\n".join(cond_lines), raw)
    return doc_id, title, source_path, "country_history", md, fm
