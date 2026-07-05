# CLAUDE.md — EU4 知識庫（eu4-kb）

基於 **RAG + MCP + Claude Agent SDK** 的 Europa Universalis IV 遊戲知識庫問答系統。兩個資料源
（官方 wiki + 遊戲安裝檔案）進同一個統一知識庫，混合檢索融合排序。架構大量複用姊妹專案
（作者先前的知識庫專案）的下游流水線，抽取層（wiki抓取 + 遊戲腳本解析）全新實作。

## 架構與連接埠

```
                                                          ┌─ Wiki (MediaWiki API, wikitext)
kb_ui.html ─→ 問答後端 :8781 (kb_answer_backend.py)         │  訂閱 OAuth token 認證(非 API key)
                   │  Claude Agent SDK，白名單 7 個 KB 工具   │  SYSTEM提示詞含「EU4整體遊戲地圖」(Tier 0)+「內容分層判斷規則」濃縮版
                   ▼                                       │
              KB MCP server :8766 (kb_mcp_server.py)        │
                   │  混合檢索：向量(LanceDB) + 英文BM25(RRF融合，可選reranker) │
                   ▼                                       │
              kbs/eu4/  單一統一知識庫                        │
                   每個 chunk: source=wiki|game_file|fundamentals │
                              entity_category/wiki_category   │
                   ▲                                          │            ┌ fundamentals_src/*.md
        wiki_extract.py              game_extract.py          │            │ (手寫遊戲常識框架文檔)
     (MediaWiki API 抓取+清洗)   (Clausewitz腳本解析+規則翻譯) ← 遊戲安裝目錄  │
                                                            fundamentals_extract.py ┘
```

## 套件結構

| 模組 | 職責 |
|------|------|
| `kb/common.py` | 環境穩定性修正、路徑/設定(複用自前身專案) |
| `kb/wiki_extract.py` + `wiki/` | MediaWiki API列舉/抓取(`mw_client.py`)、模板剝離+表格轉換(`wikitext_clean.py`/`wikitable.py`) |
| `kb/game_extract.py` + `clausewitz/` + `renderers/` | Clausewitz腳本解析(`tokenizer.py`/`parser.py`)、本地化(`localisation.py`)、宏展開(`macro_expand.py`)、trigger/effect規則翻譯(`translate_rules.py`)、實體渲染器 |
| `kb/fundamentals_extract.py` + `fundamentals_src/` | 手寫「EU4遊戲常識框架」源md(簡單title/category frontmatter) → docs/*.md + manifest(`source=fundamentals`，`doc_id`前綴`f-`) |
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
python -m kb.build_all --kb eu4 --stages fundamentals_extract                             # 只重跑fundamentals_src/*.md(快，秒級)
python -m kb.build_all --kb eu4 --stages chunk,embed,lance,organize,index,tree            # 下游全量重建(embed慢,~40-50分鐘/37000+chunk；改一個字都要全量重跑，見下方已知坑)

# 不經MCP/Claude直接測檢索
.\.venv\Scripts\python.exe test_search.py

# 端到端測agent行為(需先啟後端；印工具軌跡+作答)
.\.venv\Scripts\python.exe ask_client.py "你的問題"

# 健檢
.\.venv\Scripts\python.exe check_kb.py --kb eu4
```

## 部署（GCP Cloud Run，2026-07，已上線驗證）

參考常見的 GitHub Actions → Cloud Run 流程設計，但因為架構差異很大
（見下方"與典型單服務部署的關鍵差異"）沒有直接照抄。完整設計記錄在
（本機計劃文件）（GCP CI/CD部署計劃段落）。**服務已實際部署
並用真實問題驗證通過**（`gcloud run services describe eu4-kb --region asia-northeast1`查URL）。

**訪問控制**：2026-07按用戶要求從"預設私有(需Google IAM身份)"改成**完全公開**
(`--allow-unauthenticated`，`ci.yml`已固化這個設定，之後每次自動部署都會保持公開)——原本要求
每個訪客都要有Google帳號+`gcloud`才能用，對一般用戶不友善。**用量/成本控管目前完全沒做**
(`--max-instances 1`只是限制"最多同時1個實例"這個保底，不是真正的用量限制)，登入機制/rate limit
之類的控管留到之後另外做，目前是有意識地先接受"任何人都能直接用、可能消耗個人Claude訂閱額度"這個
風險換取可用性。

### 部署過程踩的3個真實坑（都在CI/CD實測中發現，不是憑空預想的）

1. **requirements.txt裡`sentence-transformers`拉torch的順序問題**：`pip install -r requirements.txt`
   若先跑，pip解析`sentence-transformers`對torch的依賴時會去預設PyPI源抓**GPU版**torch(現在預設
   附帶一整套`nvidia-cu*`/`cuda-toolkit`依賴，好幾GB)，把CI runner磁碟撐爆(`OSError: No space left
   on device`)。**解法**：Dockerfile裡先單獨裝CPU-only wheel(`--index-url .../whl/cpu`)，
   再裝`requirements.txt`，這樣pip解析時視torch已滿足，不會再抓GPU版。
2. **模型預熱快取的使用者不匹配**：一開始在`useradd`/`USER app`**之前**(root身份)預熱下載
   embedding/reranker模型，快取進了`/root/.cache`；但容器實際serving是`USER app`
   (`/home/app/.cache`，全新空目錄)，導致**預熱完全沒用**，Cloud Run容器啟動時重新下載2.2GB的
   reranker——而Cloud Run的可寫檔案系統是從記憶體配額扣的，下載失敗+直接OOM，`kb_mcp_server.py`
   從未啟動成功，問答後端連不上MCP工具、只能用通用知識瞎答。
3. **modifying 大檔案的chown觸發overlay檔案系統copy-up**：踩坑2的直覺修法是"下載完(root寫入)後
   補一句`chown -R app`"，但overlay檔案系統對已存在於下層的大檔案做**任何**metadata變更(chown/chmod)
   常會把整個檔案copy-up到新層，對2.27GB的reranker做chown等於憑空多佔一份幾GB空間，又把CI runner
   磁碟撐爆。**正解**：`useradd`+對(還是空的)快取目錄`chown`要在下載模型**之前**做，然後
   `USER app`切換後才下載——模型檔案從誕生那一刻就屬於`app`，永遠不需要之後再對大檔案動owner/權限。

這三個坑的教訓：**任何"先用root做某件事、之後再chown給執行期用戶"的Dockerfile寫法都要小心**——
要嘛在下載/生成大檔案前就切換好使用者，要嘛接受"這一層必然要對大檔案做metadata操作"進而預留足夠磁碟。

**容器架構**：單一 Dockerfile，`kb_mcp_server.py` 當內部背景進程(只聽127.0.0.1，不對外)，
`kb_answer_backend.py` 當主進程接管 Cloud Run 注入的 `$PORT`，`entrypoint.sh` 負責編排順序
(起MCP→輪詢等就緒→exec問答後端)。這兩個服務原本就用 `KB_MCP_URL`/`KB_ANSWER_PORT` 環境變數連接，
部署不需要改這兩支程式的代碼。

**知識庫數據持久化**：`kbs/eu4/`(210MB，含LanceDB)**不進Docker鏡像、不進git**——這份數據只能在
本機花40-50分鐘+需要Steam遊戲安裝目錄才能重建，CI機器做不到也不該做。改用 **GCS bucket + Cloud Run
原生Volume Mount**：本地重建完後 `gcloud storage rsync -r kbs/eu4 gs://<bucket>/eu4
--exclude ".*emb/.*"` 同步上去即可，容器內 `EU4_KBS_DIR`(新增的環境變數，見`kb/common.py`)指向
掛載路徑，**更新數據不需要重新部署代碼**。這樣資料在GCP Console的Cloud Storage瀏覽器裡可以直接
查看(manifest.jsonl/INDEX.md/tree.json都是人類可讀文字)。

**與典型單服務部署的關鍵差異**：① 典型單服務部署用Postgres(Cloud SQL)，eu4-kb用本地文件型LanceDB，數據"能不能在
CI裡建"這件事完全不同；② 典型單服務部署單一FastAPI服務，eu4-kb是MCP server+問答後端兩個獨立HTTP服務，合併
進一個容器解決；③ `CLAUDE_CODE_OAUTH_TOKEN`是個人訂閱token非按量計費API key，本服務定位為
"個人/小圈子私有"(`--max-instances 1`、預設不開放匿名訪問)而非典型單服務部署那種可橫向擴展的公開產品；
④ eu4-kb目前零自動化測試，新增了`tests/test_smoke.py`最小冒煙測試(import檢查+合成假數據跑一遍
build_all全流程+kb_mcp_server連通性)作為部署前質量閘門，不追求覆蓋率。

**一次性GCP手動設置**（建Artifact Registry/GCS bucket/Workload Identity Federation/Secret Manager/
IAM角色）見 `docs/gcp_setup.md`，需要你自己的GCP帳號權限執行，我沒辦法代做。GitHub倉庫需要配置的
Variables：`GCP_IMAGE`/`GCP_REGION`/`GCP_WIF_PROVIDER`/`GCP_DEPLOY_SA`/`GCP_RUN_SA`/`GCP_KB_BUCKET`/
`GCP_OAUTH_SECRET_NAME`。

**推送權限**：GitHub push走的是`torisu233`這個帳號(對`torisuorg/eu4-kb`有admin權限)，本機`gh`/`git`
的credential helper已切過去(`gh auth setup-git`)；同機另有另一個帳號登入過但沒有這個repo權限，
如果之後推送被拒絕，先查`gh auth status`確認active account是不是`torisu233`。

### 上線後的性能調優(2026-07，也是實測踩坑+修復，非事先設計)

上線後用戶反饋"網頁打開緩慢、MCP經常沒有服務、MCP工具執行速度很慢"，查生產日誌(`gcloud logging read`)
定位到兩個獨立的真實問題(不是同一個bug)：

1. **CPU瓶頸導致MCP client端逾時斷線**：日誌裡直接看到 `Batches: 100%|...| 1/1 [00:25<00:00,
   25.90s/it]` 後緊跟着 `ERROR Error handling POST request` + `ClientDisconnect`——單次模型推理
   (embedding編碼/reranker重排序)在預設1 vCPU下要25~30秒，長到讓Claude Agent SDK的MCP HTTP client
   等不及先斷線，這次工具調用直接判定失敗。這就是"MCP經常沒有服務"的真正原因(不是服務掛了，是單次
   推理慢到觸發client逾時)。**修法**：`gcloud run deploy`加`--cpu 2`，修復後同一批推理降到
   14~18秒，日誌裡`ClientDisconnect`消失。
2. **`--max-instances 1`卻沒設`--min-instances`，實例會被提前回收**：觀察到一個實例才活了~90秒
   就因為`Starting new instance. Reason: AUTOSCALING`被換掉，下一個請求撞上要重新完整走一遍
   `entrypoint.sh`冷啟動(起kb_mcp_server→輪詢就緒→載入兩個模型進記憶體，耗時~100秒)。Cloud Run
   沒有"閒置多久才縮容"這種可調參數，只能用`--min-instances 1`保底常駐解決，代價是持續計費、不再有
   "沒人用就不花錢"的優勢。**這兩處修復都已經應用**(先用`gcloud run services update`直接改現有
   revision驗證效果，再把改動寫進`ci.yml`讓之後的自動部署保持一致)。

3. **CI/CD本身也加了修正**：`ci.yml`原本`on: push:`沒有路徑過濾，連只改`CLAUDE.md`這種純文檔commit
   都會觸發整套build+deploy，把好端端一個熱實例重新冷啟動——加了`paths-ignore: ['**.md', 'docs/**']`
   避免這種不必要的churn。

**修完之後的殘留現實**：即便CPU/冷啟動都修好了，同一問題本地測跑得比雲端快3~4倍(本地單次推理批處理
4~8秒，雲端同一批次14~18秒)——**Cloud Run的vCPU是共享/受限資源，天生弱於本機開發機的實際算力**，
這是雲部署要接受的成本現實，不是還有沒抓到的bug；再加上這套agentic多輪檢索架構本身單題常需要
7~13次工具調用(本地測試從一開始就是這樣，見上方"內容分層"bug的診斷記錄)，是"雲端算力較弱"+
"多輪推理本身有開銷"兩件事疊加，不是單一原因造成的"慢"。

## 資料現狀（2026-07）

2604篇文檔（1883 wiki + 714 game_file + 7 fundamentals）→ ~37300+個chunk。game_file 是 **PoC範圍**：僅
ideas(25個理念組全量) + government_reforms(688個全量) + country_history(僅明朝Ming 1國)。
missions/events/decisions/province_history **完全未抽取**，是最大的已知覆蓋缺口，按規劃等
`feedback/turns.jsonl` 累積真實查詢日誌後再數據驅動決定優先級。

`fundamentals`是2026-07新增的第三種`source`，內容是**手寫的遊戲常識框架文檔**(非wiki/game_file
自動抽取)，解決"AI有碎片事實但缺整體判斷框架"問題，採用**兩層設計**：
- **Tier 0**：寫死在`kb_answer_backend.py`的SYSTEM提示詞裡的「EU4整體遊戲地圖」段落，不依賴檢索，
  約550字內講完6大板塊(國力引擎/經濟殖民/戰爭擴張戰略層/內政治理/對外關係/戰鬥戰術層，前5個對照
  wiki自帶的`Mechanics`權威分類頁w-d4a20ddcf4劃分，第6個戰術層是後續補充)。
- **Tier 1**：`fundamentals_src/`下7篇檢索文檔，每篇嚴格400-600字、邊緣情況一句話帶過、結尾指回
  1-2個wiki doc_id：`01_growth_engine`(國力引擎，含Institutions)、`02_economy_and_colonization`
  (經濟殖民)、`03_war_and_expansion`(戰爭擴張戰略層，含Aggressive Expansion→Coalition)、
  `04_internal_governance`(內政治理，政體/等級/改革層級三軸精簡版)、`05_diplomacy_and_subjects`
  (外交附庸，含HRE)、`06_content_layering_and_reachability`(AI判斷規則，見下方"已知坑")、
  `07_combat_basics`(戰鬥戰術層：三日火力/衝擊循環、兵種配比、包圍、地形/渡河懲罰、士氣紀律——
  和03的"要不要開戰"戰略層是不同層次)。

這是v2版本，取代了2026-07初版(君主點數/國家等級/術語表3篇獨立文檔)——初版被發現「理解不到位、
缺乏重點」(邊緣情況寫太多，Institutions/Coalition這類真正高頻機制反而漏掉)，診斷後改成對照wiki自帶
`Mechanics`分類骨架 + 外部教程/玩家討論交叉驗證的兩層設計；`07_combat_basics`是v2上線後翻出本輪
早前(對話壓縮前)已完成的兩個背景調研代理完整報告、逐條核對v2覆蓋度後補的第7篇。

## ⚠ 已知坑（踩過的，務必先看）

- **AI把「國家專屬內容」誤答成「通用內容」**：真實bug，問"一個普通的遜尼派君主制國家該怎麼獲得全騎兵
  部隊"時，agent連續24次工具調用仍給不出可信答案，把某特定小國的專屬任務獎勵當成通用答案呈現
  (`feedback/turns.jsonl`有記錄)。根因：EU4內容是**通用默認→地區/宗教/文化/科技組共享池→國家專屬**
  三層覆蓋結構(遊戲文件用`potential={tag=X}`/`generic=yes/no`精確編碼)，專屬內容還要再判斷"是否有
  建國決議(Form Xxx Nation)可達、達成條件是否群體性可滿足"，但知識庫沒把這套邏輯提煉給AI。解法：
  `doc_type=fundamentals`的`06_content_layering_and_reachability.md`(判斷步驟+識別信號表)
  + `kb_answer_backend.py`的SYSTEM提示詞插入濃縮版判斷規則(不依賴檢索觸發，常駐生效)。
  **教訓**：遇到"具體問法答錯"類bug，先判斷是缺一條事實還是缺一套判斷框架——同類誤判模式反覆出現時，
  補框架性文檔/規則比逐case補資料划算。
- **寫「基礎知識/常識」類內容要對照權威分類骨架，不能靠自己直覺挑重點**：fundamentals初版(君主點數/
  國家等級/術語表)被發現「理解不到位、缺乏重點」——邊緣情況(HRE選帝侯等級上限等)寫了很多，但
  Institutions(未接納制度科技成本暴漲)、Aggressive Expansion→Coalition(擴張失控被反制聯合國)這兩個
  真正高頻高殺傷的機制完全沒寫。後來查到wiki自己有一篇`Mechanics`總覽頁(w-d4a20ddcf4)把全部機制分成
  5大類，這種"社群自己沉澱的分類法"比自己憑感覺挑主題可靠得多。**教訓**：寫這類內容前先找該領域有沒有
  現成的權威分類骨架，用它核對覆蓋面，篇幅分配以"真實高頻"為準，不是"我恰好查到了細節"。
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
- **為什麼「只改幾篇fundamentals文檔」也要觸發全量~40分鐘重建**：`run_chunk()`(`kb/build.py:129`)
  每次都把`chunks.jsonl`從整個`manifest.jsonl`重新生成一遍(非增量追加)，生成完unconditional執行
  `shutil.rmtree(emb/); shutil.rmtree(lancedb/)`(`build.py:155-156`)——只要chunk被重新生成過，
  一律清空下游，不做"這次到底哪些內容真的變了"的精細判斷。而`run_embed()`的向量快取是**按位置分段**
  (`seg_00000.npy`=第0-255號chunk、`seg_00001.npy`=第256-511號…)、不是按內容雜湊定址，`emb/`一旦
  被清空，所有段都變成待算，於是3萬7千+個chunk全部要重新跑一次embedding模型(這是真正的耗時來源，
  純計算密集)。`organize`的向量最近鄰(`related_docs`用)本質上是全語料庫兩兩相似度矩陣，就算流水線設計
  成增量，這一步理論上也繞不開全量重算。**結論**：SYSTEM提示詞(在`kb_answer_backend.py`裡，是獨立
  Python字符串，改了只需重啟該進程，和knowledge base完全無關)本身從不需要重建；只有動了
  `fundamentals_src/`等會改變manifest的內容才會連鎖觸發這整套全量重建。這是PoC階段"正確性優先、
  工程量最小"換來的技術債，未來若要支援高頻增量更新知識庫，需要把embedding快取改成內容定址而非
  位置定址，才能只算真正新增/變更的chunk。
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
