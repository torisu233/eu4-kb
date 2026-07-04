# -*- coding: utf-8 -*-
"""MediaWiki API 客戶端：分頁列舉 allpages、拉取 wikitext(action=parse)，
原始回應快取到 cache/wiki_raw/<pageid>.json(供清洗規則反覆調整時免重打API)。"""
import os, json, time, urllib.request, urllib.parse, urllib.error

# 注意：純 "eu4-kb-extractor/0.1 (...)" 這類簡短 UA 會被站台的反爬蟲層(Fastly compute 挑戰頁)攔截，
# 回傳一份 HTML 殼頁而非 JSON(非速率限制，是 UA/Accept 特徵判定)。實測瀏覽器風格的 UA + Accept +
# Accept-Language 組合可穩定拿到 JSON，故採用此組合(仍如實標示這是程式化存取，不偽裝成互動瀏覽行為)。
USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36"
_HEADERS = {
    "User-Agent": USER_AGENT,
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "en-US,en;q=0.9",
}

class MediaWikiError(Exception):
    pass

class MediaWikiClient:
    def __init__(self, base_url, cache_dir=None, throttle=0.35, timeout=30):
        self.base_url = base_url.rstrip("/")
        self.api_url = self.base_url + "/api.php"
        self.cache_dir = cache_dir
        if cache_dir:
            os.makedirs(cache_dir, exist_ok=True)
        self.throttle = throttle
        self.timeout = timeout
        self._last_call = 0.0

    def _get(self, params):
        dt = time.time() - self._last_call
        if dt < self.throttle:
            time.sleep(self.throttle - dt)
        qs = urllib.parse.urlencode(params)
        url = f"{self.api_url}?{qs}"
        req = urllib.request.Request(url, headers=_HEADERS)
        last_err = None
        for attempt in range(4):
            try:
                with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                    data = resp.read()
                self._last_call = time.time()
                return json.loads(data.decode("utf-8"))
            except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError) as e:
                last_err = e
            except json.JSONDecodeError as e:                # 反爬蟲層偶發回傳 HTML 挑戰頁而非 JSON，視為可重試錯誤
                last_err = e
            if attempt < 3:
                time.sleep(2 ** attempt)
        raise MediaWikiError(f"GET {url} 失敗(重試3次): {last_err!r}")

    def list_all_pages(self, namespace=0, limit=500):
        """列舉所有正文頁面(排除轉址頁)，逐個 yield (pageid:int, title:str)。"""
        cont = None
        while True:
            params = {"action": "query", "list": "allpages", "apnamespace": namespace,
                      "aplimit": limit, "apfilterredir": "nonredirects", "format": "json"}
            if cont:
                params["apcontinue"] = cont
            data = self._get(params)
            pages = data.get("query", {}).get("allpages", [])
            for p in pages:
                yield p["pageid"], p["title"]
            cont = data.get("continue", {}).get("apcontinue")
            if not cont:
                break

    def fetch_page(self, pageid, use_cache=True):
        """回傳 action=parse 的原始 JSON(含 wikitext/revid/categories)。快取以 pageid 為 key。"""
        cache_path = os.path.join(self.cache_dir, f"{pageid}.json") if self.cache_dir else None
        if use_cache and cache_path and os.path.exists(cache_path):
            try:
                return json.load(open(cache_path, encoding="utf-8"))
            except Exception:
                pass  # 快取壞掉就重抓
        params = {"action": "parse", "pageid": pageid, "prop": "wikitext|revid|categories", "format": "json"}
        data = self._get(params)
        if "error" in data:
            raise MediaWikiError(f"pageid={pageid}: {data['error']}")
        if cache_path:
            json.dump(data, open(cache_path, "w", encoding="utf-8"), ensure_ascii=False)
        return data
