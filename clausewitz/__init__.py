# -*- coding: utf-8 -*-
"""Clausewitz 腳本(EU4遊戲資料格式)解析基礎設施：tokenizer -> parser -> AST(Block)，
外加本地化索引(localisation)、宏展開(macro_expand)、trigger/effect 規則模板翻譯(translate_rules)。
純標準庫實作，不依賴任何第三方套件。"""
