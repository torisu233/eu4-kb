# -*- coding: utf-8 -*-
"""建庫一鍵管線：把 wiki_extract/game_extract -> chunk -> embed -> lance -> organize -> index -> tree 串成可執行入口。

用法：
  # 全量：wiki全站 + game_file(可用 --entity-types/--country-filter 限定PoC範圍)
  python -m kb.build_all --kb eu4 --stages wiki_extract,game_extract,chunk,embed,lance,organize,index,tree \
      --game-dir "C:\\Program Files (x86)\\Steam\\steamapps\\common\\Europa Universalis IV" \
      --entity-types ideas,government_reforms,country_history --country-filter MNG
  # 只重跑 wiki 抓取(cache/wiki_raw 有快取則不重打API，只重跑清洗)
  python -m kb.build_all --kb eu4 --stages wiki_extract
  # 只重跑 game_file 抽取(渲染器/翻譯規則改了)
  python -m kb.build_all --kb eu4 --stages game_extract --entity-types ideas
  # 只重建下游(chunk 邏輯改了、來源不變)
  python -m kb.build_all --kb eu4 --stages chunk,embed,lance,organize,index,tree
  # 只重生索引(改了 organize/index 邏輯)
  python -m kb.build_all --kb eu4 --stages organize,index,tree

階段(--stages，逗號分隔；預設 all)：
  wiki_extract          MediaWiki API 抓取全站頁面 → 清洗 → docs/*.md + manifest.jsonl(source=wiki)
  game_extract          解析遊戲檔案(Clausewitz腳本) → 渲染 → docs/*.md + manifest.jsonl(source=game_file)
  fundamentals_extract  手寫「EU4基礎知識」源文件(fundamentals_src/*.md) → docs/*.md + manifest.jsonl(source=fundamentals)
  chunk         .md → chunks.jsonl（⚠ 會清空舊 emb/ 與 lancedb/ 待重建）
  embed         chunks → emb/seg_*.npy（可續跑；中斷重跑只補缺段）
  lance         emb → LanceDB 向量表
  organize      分類統計(source x doc_type) + 向量最近鄰 → doc_map.json
  index         → INDEX.md（人看目錄）
  tree          → tree.json（list_tree 導航 + path_prefix 子樹）

註：重的相依(torch/sentence-transformers/lancedb)在各階段內才載入；wiki_extract/game_extract 各自的模組
    只在對應 stage 被選取時才 import，避免互相拖累依賴(例如只跑 game_extract 不需要裝 requests)。
"""
import argparse, sys, time
from . import common
from . import build as _build

ALL_STAGES = ["wiki_extract", "game_extract", "fundamentals_extract", "chunk", "embed", "lance", "organize", "index", "tree"]

def main():
    ap = argparse.ArgumentParser(description="EU4 知識庫建置管線（一鍵串接）",
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--kb", required=True, help="知識庫名稱（對應 kbs/<name>/）")
    ap.add_argument("--stages", default="all", help="要跑的階段(逗號分隔)；預設 all。可選: " + ",".join(ALL_STAGES))
    ap.add_argument("--embed-model", default=None, help="嵌入模型（預設沿用 config，否則內建 multilingual-MiniLM）")
    # wiki_extract 專屬
    ap.add_argument("--wiki-src", default="https://eu4.paradoxwikis.com", help="wiki base URL（一般不需要改）")
    ap.add_argument("--skip-wiki-cache", action="store_true", help="忽略 cache/wiki_raw 強制重新打API")
    ap.add_argument("--wiki-limit", type=int, default=None, help="限定只處理前 N 頁(驗證清洗規則用，不給則全站)")
    # game_extract 專屬
    ap.add_argument("--game-dir", default=None, help="EU4 遊戲安裝目錄（game_extract 階段必需）")
    ap.add_argument("--entity-types", default=None, help="限定處理的實體類別(逗號分隔，如 ideas,government_reforms,country_history)；不給則跑全部已實作類別")
    ap.add_argument("--country-filter", default=None, help="限定 country_history 等按國家切分的實體只處理指定國家tag(逗號分隔)")
    # fundamentals_extract 專屬
    ap.add_argument("--fundamentals-src-dir", default=None, help="fundamentals源md目錄(預設 fundamentals_src/)")
    a = ap.parse_args()

    name = a.kb.strip()
    stages = ALL_STAGES if a.stages.strip().lower() == "all" else [s.strip() for s in a.stages.split(",") if s.strip()]
    bad = [s for s in stages if s not in ALL_STAGES]
    if bad:
        print(f"[X] 未知階段 {bad}；可選 {ALL_STAGES}", file=sys.stderr); sys.exit(2)

    cfg = common.load_config(name)
    embed_model = a.embed_model or cfg.get("embed_model") or common.DEFAULT_EMBED_MODEL

    if "game_extract" in stages and not (a.game_dir or cfg.get("game_dir")):
        print("[X] game_extract 階段需要 --game-dir（或既有 config.json 的 game_dir）", file=sys.stderr); sys.exit(2)

    t0 = time.time()
    print(f"[build_all] 庫={name} 階段={stages} 模型={embed_model}", flush=True)

    if "wiki_extract" in stages:
        print("\n=== wiki_extract ===", flush=True)
        from . import wiki_extract as _wiki_extract
        _wiki_extract.run(name, base_url=a.wiki_src, skip_cache=a.skip_wiki_cache, limit=a.wiki_limit)
        common.save_config(name, {**cfg, "name": name, "system": name, "embed_model": embed_model,
                                  "wiki_src": a.wiki_src})
        cfg = common.load_config(name)
    if "game_extract" in stages:
        print("\n=== game_extract ===", flush=True)
        from . import game_extract as _game_extract
        game_dir = a.game_dir or cfg.get("game_dir")
        entity_types = [s.strip() for s in a.entity_types.split(",")] if a.entity_types else None
        country_filter = [s.strip() for s in a.country_filter.split(",")] if a.country_filter else None
        _game_extract.run(name, game_dir=game_dir, entity_types=entity_types, country_filter=country_filter)
        common.save_config(name, {**cfg, "name": name, "system": name, "embed_model": embed_model,
                                  "game_dir": game_dir})
        cfg = common.load_config(name)
    if "fundamentals_extract" in stages:
        print("\n=== fundamentals_extract ===", flush=True)
        from . import fundamentals_extract as _fundamentals_extract
        _fundamentals_extract.run(name, src_dir=a.fundamentals_src_dir)
    if "chunk" in stages:
        print("\n=== chunk ===", flush=True); _build.run_chunk(name)
    if "embed" in stages:
        print("\n=== embed ===", flush=True); _build.run_embed(name, embed_model)
    if "lance" in stages:
        print("\n=== lance ===", flush=True); _build.build_lance(name)
    if "organize" in stages:
        print("\n=== organize ===", flush=True); _build.run_organize(name)
    if "index" in stages:
        print("\n=== index ===", flush=True); _build.generate_index(name)
    if "tree" in stages:
        print("\n=== tree ===", flush=True); _build.generate_tree(name)

    print(f"\n[build_all] 完成，用時 {int(time.time()-t0)}s", flush=True)

if __name__ == "__main__":
    main()
