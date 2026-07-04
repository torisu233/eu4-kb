# -*- coding: utf-8 -*-
"""把 Block 重新序列化回 Clausewitz 腳本文字，供渲染器的「原始腳本(附錄)」段落使用。
不保留原始檔案的註解/排版(解析階段未追蹤位置)，但保留完整的鍵值結構與數值，
足以讓 agent/使用者核對精確數值——附錄要的是「結構忠實可核對」而非逐位元組還原。"""
import re
from .parser import Block

_BARE_OK_RE = re.compile(r'^[A-Za-z0-9_.\-]+$')

def _fmt_scalar(v):
    s = str(v)
    if _BARE_OK_RE.match(s):
        return s
    return '"' + s.replace('\\', '\\\\').replace('"', '\\"') + '"'

def serialize(value, indent=0):
    pad = "    " * indent
    if isinstance(value, Block):
        lines = []
        for k, v in value.items:
            if isinstance(v, Block):
                head = f"{pad}{k} = {{" if k is not None else f"{pad}{{"
                lines.append(head)
                inner = serialize(v, indent + 1)
                if inner:
                    lines.append(inner)
                lines.append(f"{pad}}}")
            else:
                lines.append(f"{pad}{k} = {_fmt_scalar(v)}" if k is not None else f"{pad}{_fmt_scalar(v)}")
        return "\n".join(lines)
    return pad + _fmt_scalar(value)
