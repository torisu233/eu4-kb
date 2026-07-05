# -*- coding: utf-8 -*-
"""共用基礎：環境修正(避免本機原生庫 segfault)、路徑、設定、長路徑工具。
重要：本模組在 import torch/sentence-transformers 之前先設好環境並載入 pyarrow，
任何入口程式都應『最先 import 本模組』。"""
import os, sys, json

# --- 本機原生庫穩定性修正（順序與環境變數缺一不可，否則無 GPU CPU 會 segfault）---
os.environ.setdefault("KMP_DUPLICATE_LIB_OK", "TRUE")
os.environ.setdefault("OMP_NUM_THREADS", "4")
os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")
os.environ.setdefault("HF_HUB_DISABLE_PROGRESS_BARS", "1")
os.environ.setdefault("TRANSFORMERS_VERBOSITY", "error")
os.environ.setdefault("HF_HUB_DISABLE_TELEMETRY", "1")
import pyarrow  # noqa: F401  # 必須在 torch 之前載入

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # ...\eu4-kb
# 部署場景(Cloud Run)可能把 kbs/ 換成掛載的 GCS bucket 路徑(如 /mnt/kb-data)，
# 用環境變數覆蓋、預設值不變，本機/現有流程無感。
KBS_DIR = os.environ.get("EU4_KBS_DIR") or os.path.join(ROOT, "kbs")
DEFAULT_EMBED_MODEL = "paraphrase-multilingual-MiniLM-L12-v2"

def kb_path(name): return os.path.join(KBS_DIR, name)
def kb_paths(name):
    base = kb_path(name)
    return {
        "base": base,
        "docs": os.path.join(base, "docs"),
        "manifest": os.path.join(base, "manifest.jsonl"),
        "chunks": os.path.join(base, "chunks.jsonl"),
        "emb": os.path.join(base, "emb"),
        "lancedb": os.path.join(base, "lancedb"),
        "doc_map": os.path.join(base, "doc_map.json"),
        "tree": os.path.join(base, "tree.json"),
        "index_md": os.path.join(base, "INDEX.md"),
        "config": os.path.join(base, "config.json"),
        "zh_glossary": os.path.join(base, "zh_glossary.json"),  # 英文名/key → 官方中文(paratranz)，供中文模式術語注解
    }

def strip_frontmatter(text):
    """去掉開頭的 YAML frontmatter，回傳純內文(body)。與 build._parse_fm 的 body 取法一致，
    使 chunk 的 char_start(對 body 計)與 get_doc 的 offset 切片對齊。"""
    if text.startswith("---"):
        e = text.find("\n---", 3)
        if e != -1:
            return text[e + 4:].lstrip("\n")
    return text

def load_config(name):
    p = kb_paths(name)["config"]
    return json.load(open(p, encoding="utf-8")) if os.path.exists(p) else {}

def save_config(name, cfg):
    p = kb_paths(name)
    os.makedirs(p["base"], exist_ok=True)
    json.dump(cfg, open(p["config"], "w", encoding="utf-8"), ensure_ascii=False, indent=1)

def list_kbs():
    if not os.path.isdir(KBS_DIR): return []
    return [d for d in os.listdir(KBS_DIR) if os.path.isfile(os.path.join(KBS_DIR, d, "config.json"))]

def LP(p):
    """Windows 長路徑前綴，供 .NET 風格長路徑檔案存取（python open 也吃 \\?\）。"""
    p = os.path.abspath(p)
    return p if p.startswith("\\\\?\\") else "\\\\?\\" + p
