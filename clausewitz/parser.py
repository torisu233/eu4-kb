# -*- coding: utf-8 -*-
"""Clausewitz 腳本語法分析：token 串流 -> 嵌套 AST(Block)。

關鍵設計：Block 內容用「有序鍵值對列表」而非 dict，因為真實資料大量存在重複 key
（同一天多條歷史記錄、多個 option={}、多個 if={} 等）；用 dict 會丟資料。
裸值(無 key=前綴，如 core = { FRA ENG } 內的 FRA/ENG)以 key=None 的項目表示。
"""
import re
from .tokenizer import tokenize, LBRACE, RBRACE, EQ, STRING, WORD

_DATE_RE = re.compile(r'^\d{1,4}\.\d{1,2}\.\d{1,2}$')

def is_date_key(k):
    return isinstance(k, str) and bool(_DATE_RE.match(k))

class ParseError(Exception):
    pass

class Block:
    """有序鍵值對容器。items: List[(key: str|None, value: str|Block)]。"""
    __slots__ = ("items",)
    def __init__(self, items=None):
        self.items = items if items is not None else []

    def append(self, key, value):
        self.items.append((key, value))

    def get(self, key, default=None):
        """回傳第一個符合 key 的 value（大小寫不敏感）；查無回 default。"""
        kl = key.lower()
        for k, v in self.items:
            if k is not None and k.lower() == kl:
                return v
        return default

    def get_all(self, key):
        """回傳所有符合 key 的 value 列表(供重複 key 情境使用，如多個 option={})。"""
        kl = key.lower()
        return [v for k, v in self.items if k is not None and k.lower() == kl]

    def keys(self):
        """去重後的 key 列表，保留首次出現順序。"""
        seen = []; seenset = set()
        for k, _ in self.items:
            if k is not None and k.lower() not in seenset:
                seenset.add(k.lower()); seen.append(k)
        return seen

    def values(self):
        return [v for _, v in self.items]

    def bare_values(self):
        """回傳所有裸值(key=None 的項目)，用於純列表情境如 core = { FRA ENG }。"""
        return [v for k, v in self.items if k is None]

    def date_items(self):
        """回傳 (date_str, Block) 的列表，僅限 key 符合日期格式者，依原始順序(history 檔案用)。"""
        return [(k, v) for k, v in self.items if is_date_key(k)]

    def __len__(self):
        return len(self.items)

    def __iter__(self):
        return iter(self.items)

    def __bool__(self):
        return bool(self.items)

    def __repr__(self):
        return f"Block({len(self.items)} items)"


def _parse_items(tokens, pos, end_at_rbrace, line_hint=0):
    block = Block()
    n = len(tokens)
    while pos < n:
        tok = tokens[pos]
        if end_at_rbrace and tok.type == RBRACE:
            return block, pos + 1
        if tok.type in (STRING, WORD):
            key_val = tok.value
            if pos + 1 < n and tokens[pos + 1].type == EQ:
                pos += 2
                if pos >= n:
                    raise ParseError(f"第 {tok.line} 行：'{key_val} =' 之後缺少值")
                val_tok = tokens[pos]
                if val_tok.type == LBRACE:
                    sub, pos = _parse_items(tokens, pos + 1, True, val_tok.line)
                    block.append(key_val, sub)
                elif val_tok.type in (STRING, WORD):
                    block.append(key_val, val_tok.value)
                    pos += 1
                else:
                    raise ParseError(f"第 {val_tok.line} 行：'{key_val} =' 之後出現非預期符號 {val_tok.type}")
            else:
                block.append(None, key_val)              # 裸值(列表項)
                pos += 1
        elif tok.type == LBRACE:                          # 匿名子區塊當裸值(較少見，但語法上允許)
            sub, pos = _parse_items(tokens, pos + 1, True, tok.line)
            block.append(None, sub)
        elif tok.type == RBRACE:
            raise ParseError(f"第 {tok.line} 行：多餘的 '}}'（未配對）")
        else:
            raise ParseError(f"第 {tok.line} 行：非預期符號 {tok.type}")
    if end_at_rbrace:
        raise ParseError(f"第 {line_hint} 行起的區塊缺少對應的 '}}'")
    return block, pos


def parse(text):
    """解析整份腳本文字，回傳頂層 Block(視同無外層大括號的隱式區塊)。"""
    tokens = tokenize(text)
    block, pos = _parse_items(tokens, 0, end_at_rbrace=False)
    return block


def parse_file(path, encoding="utf-8-sig"):
    with open(path, encoding=encoding, errors="replace") as f:
        return parse(f.read())
