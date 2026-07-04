# -*- coding: utf-8 -*-
"""Clausewitz 腳本詞法分析：# 注釋(不在字串內時到行尾)、雙引號字串、{ } = 結構符、裸詞(識別字/數字/日期)。"""

LBRACE, RBRACE, EQ, STRING, WORD = "LBRACE", "RBRACE", "EQ", "STRING", "WORD"

_SPECIAL = "{}=\"#"

class Token:
    __slots__ = ("type", "value", "line")
    def __init__(self, type_, value, line):
        self.type = type_; self.value = value; self.line = line
    def __repr__(self):
        return f"Token({self.type},{self.value!r})"

class TokenizeError(Exception):
    pass

def tokenize(text):
    """回傳 Token 串列。text 應已去除 BOM(建議用 encoding='utf-8-sig' 讀檔)。"""
    toks = []
    i = 0; n = len(text); line = 1
    while i < n:
        c = text[i]
        if c == "\n":
            line += 1; i += 1; continue
        if c.isspace():
            i += 1; continue
        if c == "#":                                   # 注釋到行尾(不在字串內時才會走到這，見下方字串分支)
            j = text.find("\n", i)
            i = n if j == -1 else j
            continue
        if c == "{":
            toks.append(Token(LBRACE, "{", line)); i += 1; continue
        if c == "}":
            toks.append(Token(RBRACE, "}", line)); i += 1; continue
        if c == "=":
            toks.append(Token(EQ, "=", line)); i += 1; continue
        if c == '"':                                    # 字串：到下一個未轉義的雙引號為止；字串內的 # { } = 均為字面字元
            j = i + 1; buf = []
            while j < n:
                cj = text[j]
                if cj == "\\" and j + 1 < n and text[j+1] in ('"', "\\"):  # 簡單轉義(EU4實務極少見，保守支援)
                    buf.append(text[j+1]); j += 2; continue
                if cj == '"':
                    break
                if cj == "\n": line += 1
                buf.append(cj); j += 1
            else:
                raise TokenizeError(f"未終止的字串(第 {line} 行起)")
            toks.append(Token(STRING, "".join(buf), line))
            i = j + 1; continue
        # 裸詞：吃到下個空白或特殊字元為止
        j = i
        while j < n and (not text[j].isspace()) and text[j] not in _SPECIAL:
            j += 1
        if j == i:                                       # 理論上不會發生(上面已處理所有特殊字元)，防禦性跳過
            i += 1; continue
        toks.append(Token(WORD, text[i:j], line))
        i = j
    return toks
