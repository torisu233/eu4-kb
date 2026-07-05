# -*- coding: utf-8 -*-
"""渲染器共用工具：doc_id 產生、frontmatter 組裝、三段式文件組版。"""
import hashlib, json

def make_doc_id(entity_category, entity_id):
    h = hashlib.sha1(f"{entity_category}:{entity_id}".encode("utf-8")).hexdigest()[:10]
    return f"g-{h}"

def source_hash(raw_text):
    return hashlib.sha1((raw_text or "").encode("utf-8")).hexdigest()[:12]

def build_frontmatter(fields):
    lines = ["---"]
    for k, v in fields.items():
        lines.append(f"{k}: {json.dumps(v, ensure_ascii=False)}")
    lines.append("---")
    return "\n".join(lines)

def assemble_doc(frontmatter_fields, title, overview_md, conditions_md, raw_script):
    fm = build_frontmatter(frontmatter_fields)
    parts = [fm, "", f"# {title}", "",
             "## Overview", "", (overview_md or "").strip() or "(none)", "",
             "## Conditions and Effects", "", (conditions_md or "").strip() or "(no additional conditions/effects)", "",
             "## Raw Script (appendix)", "", "```", (raw_script or "").strip(), "```", ""]
    return "\n".join(parts)

def pct(value):
    """0.15 -> '+15%'；-0.2 -> '-20%'。非數值回 None。"""
    try:
        v = float(value) * 100
    except (TypeError, ValueError):
        return None
    s = str(int(round(v))) if abs(v - round(v)) < 1e-6 else f"{v:g}"
    return f"{'+' if v >= 0 else ''}{s}%"
