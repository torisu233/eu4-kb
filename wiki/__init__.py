# -*- coding: utf-8 -*-
"""EU4 wiki (MediaWiki) 抽取層：mw_client(API存取+快取) / wikitext_clean(模板剝離) / wikitable(表格轉換)。
標準庫實作(urllib)，不額外依賴 requests，降低與 kb/ 檢索層依賴的耦合。"""
