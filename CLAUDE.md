# CLAUDE.md — EU4 知識庫（eu4-kb）

基於 **RAG + MCP + Claude Agent SDK** 的 Europa Universalis IV 遊戲知識庫問答系統。兩個資料源
（官方 wiki + 遊戲安裝檔案）進同一個統一知識庫，混合檢索融合排序。架構大量複用姊妹專案
（作者先前的知識庫專案）的下游流水線，抽取層（wiki抓取 + 遊戲腳本解析）全新實作。

## 架構與連接埠

```
                                                          ┌─ Wiki (MediaWiki API, wikitext)
kb_ui.html ─→ 問答後端 :8781 (kb_answer_backend.py)         │  訂閱 OAuth token 認證(非 API key)
                   │  Claude Agent SDK，白名單 7 個 KB 工具   │
                   ▼                                       │
              KB MCP server :8766 (kb_mcp_server.py)        │
                   │  混合檢索：向量(LanceDB) + 英文BM25(RRF融合，可選reranker) │
                   ▼                                       │
              kbs/eu4/  單一統一知識庫                        │
                   每個 chunk: source=wiki|game_file          │
                              entity_category/wiki_category   │
                   ▲                                          │
        wiki_extract.py              game_extract.py          │
     (MediaWiki API 抓取+清洗)   (Clausewitz腳本解析+規則翻譯) ← 遊戲安裝目錄
```

## 套件結構

| 模組 | 職責 |
|------|------|
| `kb/common.py` | 環境穩定性修正、路徑/設定(複用自前身專案) |
| `kb/wiki_extract.py` + `wiki/` | MediaWiki API列舉/抓取(`mw_client.py`)、模板剝離+表格轉換(`wikitext_clean.py`/`wikitable.py`) |
| `kb/game_extract.py` + `clausewitz/` + `renderers/` | Clausewitz腳本解析(`tokenizer.py`/`parser.py`)、本地化(`localisation.py`)、宏展開(`macro_expand.py`)、trigger/effect規則翻譯(`translate_rules.py`)、實體渲染器 |
| `kb/build.py` | chunk(段落+標題感知切塊)/embed/lance/organize(分類統計+向量最近鄰)/index/tree |
| `kb/searcher.py` | 混合檢索器：向量+英文BM25→RRF→可選reranker→去重 |
| `kb_mcp_server.py` | 7個MCP工具 |
| `kb_answer_backend.py` | Claude Agent SDK問答後端 |

## 常用指令

```powershell
# 啟動全套：MCP(8766) + 問答後端(8781)
.\run_local.ps1

# 分階段建庫(可組合，見 kb/build_all.py 開頭docstring)
python -m kb.build_all --kb eu4 --stages wiki_extract                                    # 全站wiki(cache-based,快)
python -m kb.build_all --kb eu4 --stages game_extract --entity-types ideas,government_reforms,country_history --country-filter MNG --game-dir "C:\Program Files (x86)\Steam\steamapps\common\Europa Universalis IV"
python -m kb.build_all --kb eu4 --stages chunk,embed,lance,organize,index,tree            # 下游全量重建(embed慢,~40-50分鐘/37000+chunk)

# 不經MCP/Claude直接測檢索
.\.venv\Scripts\python.exe test_search.py

# 端到端測agent行為(需先啟後端；印工具軌跡+作答)
.\.venv\Scripts\python.exe ask_client.py "你的問題"

# 健檢
.\.venv\Scripts\python.exe check_kb.py --kb eu4
```

## 資料現狀（2026-07）

2597篇文檔（1883 wiki + 714 game_file）→ ~37300個chunk。game_file 是 **PoC範圍**：僅
ideas(25個理念組全量) + government_reforms(688個全量) + country_history(僅明朝Ming 1國)。
missions/events/decisions/province_history **完全未抽取**，是最大的已知覆蓋缺口，按規劃等
`feedback/turns.jsonl` 累積真實查詢日誌後再數據驅動決定優先級。

## ⚠ 已知坑（踩過的，務必先看）

- **標題正則的貪婪`\s*`bug**：`convert_headings()`裡若用`\s*$`當邊界，`\s`會吃掉換行，在`re.M`下
  貪婪比對會把標題後面所有空行一併吃進match，替換後標題跟緊接內容黏成一段、切塊器抓不到這層
  heading_path。教訓：正則邊界符號只能用`[ \t]*`(水平空白)，不能用`\s*`。**且不能指望來源wikitext
  本身有空行**——真實wikitext很常見「標題後緊接內容不留空行」(尤其標題後直接接清單)，`convert_headings()`
  現在會強制在標題轉換後補一個換行，不依賴來源格式。
- **嵌套MediaWiki表格**：cell內容裡又是一個完整`{|...|}`(常見於"modifier明細"這種collapsible子表格)，
  `wiki/wikitable.py` 用遞迴解析(深度上限`_MAX_DEPTH=4`)，子表格轉換後併入外層cell文字，保留行的
  主體上下文(如"這是哪個叛軍類型的修正")。超過遞迴深度才真的降級為純文字。
- **表格續塊需要補回表頭**：`kb/build.py`的`_soft_pieces()`切超預算的表格時，若整塊以「表頭+分隔列」
  開頭(`_TABLE_HEAD_RE`偵測)，每個續片都會補回表頭——否則續片只剩裸資料列，不知道各欄位代表什麼
  (schema collapse，業界公認的表格處理常見錯誤，見 memory: eu4-kb-industry-references)。
- **chunk階段透傳所有manifest欄位**：`run_chunk()`把manifest記錄(除`abs_path`/`extract_status`/`chars`
  等內部欄位)原樣複製進chunk dict，LanceDB建表自動跟著欄位走。新增資料源/新增分類欄位**不需要改
  chunk/lance/search任何程式碼**，只要extractor在manifest裡塞新欄位即可。
- **`organize`不用KMeans**：診斷過對這份已有清晰分類(entity_category/wiki_category)的資料，KMeans
  聚類等於花力氣重新(且更粗糙地)發現已知分類。改成確定性的 source×doc_type 分類統計，向量最近鄰
  (供`related_docs`)保留(這部分是獨立信號，仍有真實語義價值，如Ming的向量近鄰是Chinese Kingdom/
  Kongsi Federation這類東亞政府改革，語義上確實相關)。
- **Contextual Retrieval 未實現**：`context`欄位設計了(chunk schema裡有這一欄)但目前100%是空字串，
  因為沒有實現LLM文檔摘要生成(為每個chunk前綴一段文檔脈絡)這一步。Anthropic自己的研究稱此
  技術可降低49%檢索錯誤，是目前評估到投入產出比最高的未兌現功能。
- **反爬蟲層攔截 MediaWiki API**：`wiki/mw_client.py` 若用過於簡短的 User-Agent(無 Accept/
  Accept-Language)，會被站台反爬層攔截回傳HTML挑戰頁而非JSON(不是限速問題，是UA特徵判定)。現用
  瀏覽器風格 UA + 完整 Accept header 組合穩定拿到JSON，且JSON解碼失敗會自動重試(非單純網路錯誤才重試)。
- **`KB_USE_RERANK` 預設關閉**：手動起`kb_mcp_server.py`/`kb_answer_backend.py`測試時若不設這個環境
  變數，reranker不會啟用(只有`run_local.ps1`會設)，等於用"減配版"檢索(僅向量+BM25+RRF無cross-encoder精排)。
- **OAuth token**：`.env`放`CLAUDE_CODE_OAUTH_TOKEN`(sk-ant-oat01-開頭，
  `claude setup-token`取得)，`.env`已加進`.gitignore`。

## 已確認排除的方案

- 創意工坊「官方認可」中文化mod(`1999055990`/`2976470733`)：實測是位圖字體黑客編碼(BMFont自訂id
  對應貼圖字形，非標準Unicode)，需要OCR/字形匹配才能解碼，成本不划算，**不採用**。
- `paratranz/EU4-Chinese-Localisation`(GitHub，同步自ParaTranz眾包翻譯)：確認是標準UTF-8可用文字，
  key結構與官方英文本地化完全對應，**是v1.5中文化路線的可行資料源**，但v1階段不使用
  (先跑通純英文核心，中文靠LLM問答時現場翻譯)。
