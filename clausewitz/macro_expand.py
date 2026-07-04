# -*- coding: utf-8 -*-
"""scripted_triggers/scripted_effects 宏展開：載入巨集定義表，把 trigger/effect Block 內對巨集的呼叫
原地展開成巨集定義體(遞迴展開巨集內部再引用的巨集，深度上限防循環)。

呼叫慣例(依真實檔案 common/scripted_triggers/00_scripted_triggers.txt 實測歸納)：巨集名即 key，
值若為 Block 且含 key=value 參數(如 has_advisor_of_type_and_level = { type = adm level = 3 })，
則以該參數名對巨集定義體內的 $參數名$ 做替換(含 key 本身，因為存在 has_$type$_advisor_of_level 這種
巨集名帶佔位符、需先替換才能解出真正巨集名的 meta-巨集寫法)；值若為純量(通常是 yes)則視為無參數呼叫，
直接展開巨集定義體。"""
import os, glob, re
from .parser import Block, parse_file

_VAR_TOKEN_RE = re.compile(r'\$([A-Za-z0-9_]+)\$')

class MacroTable:
    def __init__(self):
        self.macros = {}  # name(小寫) -> Block(巨集定義體)

    def load_dir(self, dir_path):
        n = 0
        if not os.path.isdir(dir_path):
            return 0
        for fp in sorted(glob.glob(os.path.join(dir_path, "*.txt"))):
            blk = parse_file(fp)
            for k, v in blk.items:
                if k is not None and isinstance(v, Block):
                    self.macros[k.lower()] = v; n += 1
        return n

    def load_dirs(self, *dir_paths):
        return sum(self.load_dir(d) for d in dir_paths)

    def __contains__(self, name):
        return (name or "").lower() in self.macros

    def __len__(self):
        return len(self.macros)


def _sub_token(s, param_map):
    def repl(m):
        name = m.group(1)
        v = param_map.get(name)
        return m.group(0) if v is None else str(v)
    return _VAR_TOKEN_RE.sub(repl, s)

def _substitute(value, param_map):
    """遞迴把字串葉節點(含 key)內的 $參數名$ 替換為呼叫時提供的實際值；未提供的參數名原樣保留(供人工核查)。"""
    if isinstance(value, Block):
        new = Block()
        for k, v in value.items:
            nk = _sub_token(k, param_map) if k is not None else None
            new.append(nk, _substitute(v, param_map))
        return new
    if isinstance(value, str):
        return _sub_token(value, param_map)
    return value


def expand_block(block, macro_table, max_depth=5, _depth=0, _stack=frozenset()):
    """回傳一個新 Block：巨集呼叫節點(key 命中 macro_table)的 value 被替換為「參數替換後、遞迴展開過」的
    巨集定義體；其餘節點的巢狀 Block 也遞迴走訪(巨集可能藏在更深層的 trigger/effect 巢狀結構裡)。
    深度超過 max_depth 或偵測到巨集自我循環引用時，停止展開、保留呼叫原樣(不強行展開避免無限遞迴)。"""
    out = Block()
    for k, v in block.items:
        kl = k.lower() if k is not None else None
        if kl is not None and kl in macro_table.macros and _depth < max_depth and kl not in _stack:
            macro_def = macro_table.macros[kl]
            param_map = {pk: pv for pk, pv in v.items if pk is not None} if isinstance(v, Block) else {}
            substituted = _substitute(macro_def, param_map)
            expanded = expand_block(substituted, macro_table, max_depth, _depth + 1, _stack | {kl})
            out.append(k, expanded)
        elif isinstance(v, Block):
            out.append(k, expand_block(v, macro_table, max_depth, _depth, _stack))
        else:
            out.append(k, v)
    return out
