# -*- coding: utf-8 -*-
"""最小冒煙測試：不追求覆蓋率，只防止「壞掉的代碼被部署上線」。
不需要真實 kbs/eu4/ 資料(210MB)也不需要 Steam 遊戲安裝目錄——用臨時目錄 + 幾篇合成假文檔
跑一遍完整的 build_all 下游流水線(chunk/embed/lance/organize/index/tree)，驗證流水線代碼
本身沒壞；再起一次 kb_mcp_server.py 子行程驗證它真的能監聽、回應。"""
import os, sys, json, subprocess, time, urllib.request, urllib.error

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)


def test_core_modules_import():
    """各核心模組 import 不報錯(不需要遊戲檔案/wiki網路，只驗證代碼本身沒有語法/依賴壞掉)。"""
    import kb.common  # noqa: F401  必須最先 import(環境變數修正)
    import kb.build, kb.build_all, kb.searcher, kb.multi  # noqa: F401
    import kb.wiki_extract, kb.game_extract, kb.fundamentals_extract  # noqa: F401
    import clausewitz.tokenizer, clausewitz.parser, clausewitz.localisation  # noqa: F401
    import clausewitz.macro_expand, clausewitz.translate_rules, clausewitz.serialize  # noqa: F401
    import renderers.base, renderers.idea_group, renderers.government_reform  # noqa: F401
    import renderers.country_history  # noqa: F401
    import wiki.mw_client, wiki.wikitext_clean, wiki.wikitable  # noqa: F401


def _write_fixture_kb(kbs_dir, name="smoketest"):
    """造一個2篇文檔的假知識庫(manifest.jsonl + docs/*.md)，供下游流水線測試用。"""
    base = os.path.join(kbs_dir, name)
    docs_dir = os.path.join(base, "docs")
    os.makedirs(docs_dir, exist_ok=True)
    docs = [
        {"doc_id": "t-0001", "title": "Smoke Test Doc One", "system": name, "doc_type": "test",
         "lang": "en", "version": "", "source_hash": "aaa111", "status": "active",
         "md_path": "docs/t-0001.md", "source": "test", "source_path": "test/t-0001"},
        {"doc_id": "t-0002", "title": "Smoke Test Doc Two", "system": name, "doc_type": "test",
         "lang": "en", "version": "", "source_hash": "bbb222", "status": "active",
         "md_path": "docs/t-0002.md", "source": "test", "source_path": "test/t-0002"},
    ]
    bodies = [
        "# Smoke Test Doc One\n\nThis is a short paragraph used only to verify the chunk/embed/"
        "lance pipeline runs end to end without needing the real game data.\n\n## Section A\n\n"
        "Second paragraph with a bit more text so chunking has something to split on.",
        "# Smoke Test Doc Two\n\nAnother tiny document, unrelated content, just for pipeline smoke "
        "testing purposes only.",
    ]
    for m, body in zip(docs, bodies):
        fm = "\n".join(f"{k}: {json.dumps(v)}" for k, v in m.items())
        open(os.path.join(base, m["md_path"]), "w", encoding="utf-8").write(f"---\n{fm}\n---\n\n{body}\n")
    with open(os.path.join(base, "manifest.jsonl"), "w", encoding="utf-8") as f:
        for m in docs:
            f.write(json.dumps(m, ensure_ascii=False) + "\n")
    return name


def test_build_pipeline_end_to_end(tmp_path):
    """用假資料跑完整 chunk->embed->lance->organize->index->tree，驗證流水線本身沒壞。
    kb.common 的 KBS_DIR 在 import 時就讀了環境變數一次，故用子行程跑(每個子行程重新 import)，
    而不是在本行程內 monkeypatch 環境變數(那樣對已 import 的 kb.common 不生效)。"""
    kbs_dir = str(tmp_path / "kbs")
    os.makedirs(kbs_dir, exist_ok=True)
    name = _write_fixture_kb(kbs_dir)
    env = dict(os.environ, EU4_KBS_DIR=kbs_dir)
    r = subprocess.run(
        [sys.executable, "-m", "kb.build_all", "--kb", name,
         "--stages", "chunk,embed,lance,organize,index,tree"],
        cwd=ROOT, env=env, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=600,
    )
    assert r.returncode == 0, f"build_all 失敗:\nSTDOUT:\n{r.stdout}\nSTDERR:\n{r.stderr}"
    assert os.path.exists(os.path.join(kbs_dir, name, "lancedb"))
    assert os.path.exists(os.path.join(kbs_dir, name, "tree.json"))


def test_mcp_server_starts_and_responds(tmp_path):
    """起一次 kb_mcp_server.py --http 子行程，確認它真的能監聽並回應(用剛才造的假庫)。"""
    kbs_dir = str(tmp_path / "kbs2")
    os.makedirs(kbs_dir, exist_ok=True)
    name = _write_fixture_kb(kbs_dir)
    env = dict(os.environ, EU4_KBS_DIR=kbs_dir)
    r = subprocess.run(
        [sys.executable, "-m", "kb.build_all", "--kb", name,
         "--stages", "chunk,embed,lance,organize,index,tree"],
        cwd=ROOT, env=env, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=600,
    )
    assert r.returncode == 0, f"前置建庫失敗:\n{r.stdout}\n{r.stderr}"

    port = 18766
    # 不接 PIPE：kb_mcp_server.py 日誌量不小，沒人讀取 pipe 的話緩衝區會塞滿把子行程卡住
    # (在只檢查連通性、不需要看輸出的冒煙測試裡，直接丟給 DEVNULL 最單純)。
    proc = subprocess.Popen(
        [sys.executable, "kb_mcp_server.py", "--kb", name, "--http",
         "--host", "127.0.0.1", "--port", str(port)],
        cwd=ROOT, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    try:
        ok = False
        for _ in range(45):
            try:
                urllib.request.urlopen(f"http://127.0.0.1:{port}/mcp", timeout=2)
                ok = True; break
            except urllib.error.HTTPError:
                ok = True; break  # 有 HTTP 回應(即使是 4xx)＝伺服器已在聽
            except Exception:
                time.sleep(1)
        assert ok, "kb_mcp_server 30秒內未回應 /mcp"
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
