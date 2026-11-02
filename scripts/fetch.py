#!/usr/bin/env python3
# 用法：python scripts/fetch.py [YYYY-MM-DD]
# 不带参数则抓取本机当天；示例：python scripts/fetch.py 2026-11-03
# 仅用 Python 3.9+ 标准库；--help 查看可选的失败重试/单源诊断参数。
"""Fetch raw morning-brief sources; cache requests and record verifiable outcomes."""

import argparse
import datetime as dt
import html
from html.parser import HTMLParser
import http.client
import json
from pathlib import Path
import re
import socket
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from urllib.parse import urljoin, urlsplit
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]
UA = "Mozilla/5.0 (compatible; MorningBriefFetcher/1.0; contact: local editorial team)"
SOURCES = {
    "anthropic": ("https://www.anthropic.com/news", "anthropic.html"),
    "openai": ("https://openai.com/news/rss.xml", "openai.xml"),
    # arXiv 改用 official API：RSS 端点当日经常 0 条且不带摘要
    "arxiv": ("http://export.arxiv.org/api/query?search_query=cat:cs.CL&sortBy=submittedDate&max_results=50", "arxiv.xml"),
    "qwen": ("https://qwenlm.github.io/blog/index.xml", "qwen.xml"),
    "lilian": ("https://lilianweng.github.io/index.xml", "lilian.xml"),
    "deepseek": ("https://api-docs.deepseek.com/news", "deepseek.html"),
    # 中文源（2026-11-02 实测）：机器之心/量子位 RSS 是空壳或 000，改用这两家带 AI 条目的综合技术源
    "infoq": ("https://www.infoq.cn/feed", "infoq.xml"),
    "kr36": ("https://www.36kr.com/feed", "kr36.xml"),
    "ithome": ("https://www.ithome.com/rss/", "ithome.xml"),
    "hn": ("https://hacker-news.firebaseio.com/v0/topstories.json", "hn-topstories.json"),
    "tldr": ("https://tldr.tech/ai", "tldr.html"),
    "substack": ("https://importai.substack.com/feed", "substack.xml"),
    "simon": ("https://simonwillison.net/", "simon.html"),
}
# 源优先级（设计文档 §2.2）：失败后的重试次数；存档源仍抓取
SOURCE_PRIORITY = {
    "anthropic": "P0", "openai": "P0", "arxiv": "P0",
    "qwen": "archive", "lilian": "P0", "deepseek": "P0",
    "hn": "P1", "tldr": "P1", "simon": "P1", "substack": "P1",
    "infoq": "P1", "kr36": "P1", "ithome": "P1",
}
RETRIES_BY_PRIORITY = {"P0": 3, "P1": 1, "archive": 1}

KEYWORDS = re.compile(r"\b(?:AI|LLM|GPT|Claude|model|training|dataset)\b", re.I)
ARTICLE_PATH = re.compile(r"^/news/[a-z0-9][a-z0-9-]*$", re.I)
# 发布页数量异常预警阈值；只报警、不截断，确保规格 §三要求逐条取。
ARTICLE_LINK_WARNING_THRESHOLD = 40
# /news/<slug> 也会匹配栏目页；显式排除，避免栏目标题变化后误当发布页。
HUBS = {"/news/announcements", "/news/features", "/news/product", "/news/partnerships", "/news/research", "/news/policy", "/news/safeguards", "/news/security"}
MAX_BYTES = 8 * 1024 * 1024

class FetchError(Exception):
    def __init__(self, reason, status=None, retryable=True):
        super().__init__(reason)
        self.reason, self.status, self.retryable = reason, status, retryable


def now():
    return dt.datetime.now().astimezone().isoformat(timespec="seconds")


def fetch_once(url):
    """Return (HTTP code, final URL, response bytes); connect 10s, read 15s."""
    seen = set()
    for _ in range(6):
        parts = urlsplit(url)
        if parts.scheme not in ("http", "https") or not parts.hostname:
            raise FetchError("InvalidURL: only public HTTP(S) URLs are supported", retryable=False)
        if url in seen:
            raise FetchError("RedirectLoop", retryable=False)
        seen.add(url)
        conn_type = http.client.HTTPSConnection if parts.scheme == "https" else http.client.HTTPConnection
        port = parts.port or (443 if parts.scheme == "https" else 80)
        conn = conn_type(parts.hostname, port, timeout=10)
        phase = "connect"
        try:
            conn.connect()
            if conn.sock:
                conn.sock.settimeout(15)
            phase = "read"
            target = parts.path or "/"
            if parts.query:
                target += "?" + parts.query
            conn.request("GET", target, headers={"User-Agent": UA, "Accept": "text/html,application/json,application/rss+xml,application/xml;q=0.9,*/*;q=0.8", "Accept-Encoding": "identity"})
            response = conn.getresponse()
            status = response.status
            if status in (301, 302, 303, 307, 308):
                location = response.getheader("Location")
                if not location:
                    raise FetchError("RedirectWithoutLocation", status, retryable=False)
                url = urljoin(url, location)
                continue
            if not (200 <= status < 300):
                raise FetchError("HTTP %s" % status, status, retryable=(status >= 500 or status == 429))
            body = bytearray()
            while True:
                part = response.read(65536)
                if not part:
                    break
                body.extend(part)
                if len(body) > MAX_BYTES:
                    raise FetchError("ResponseTooLarge: >8 MiB", status, retryable=False)
            if not body:
                raise FetchError("EmptyResponse: HTTP %s without body" % status, status, retryable=False)
            return status, url, bytes(body)
        except socket.gaierror as exc:
            raise FetchError("DNSFailure: %s" % exc.__class__.__name__) from exc
        except (socket.timeout, TimeoutError) as exc:
            raise FetchError("ConnectionTimeout" if phase == "connect" else "ReadTimeout") from exc
        except (ConnectionRefusedError, ConnectionResetError, BrokenPipeError) as exc:
            raise FetchError("%s: %s" % (exc.__class__.__name__, str(exc)[:120])) from exc
        except (OSError, http.client.HTTPException) as exc:
            raise FetchError("%s: %s" % (exc.__class__.__name__, str(exc)[:120])) from exc
        finally:
            conn.close()
    raise FetchError("TooManyRedirects: >5", retryable=False)


def request(url, max_attempts=2):
    """Retry with exponential delays (1s/2s/4s for three P0 retries)."""
    for attempt in range(1, max_attempts + 1):
        try:
            status, final_url, body = fetch_once(url)
            return {"status": status, "final_url": final_url, "body": body, "attempts": attempt}
        except FetchError as exc:
            if attempt == max_attempts or not exc.retryable:
                return {"status": exc.status, "error": exc.reason, "attempts": attempt}
            time.sleep(2 ** (attempt - 1))


class TextParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.skip = 0
        self.tag = None
        self.fragments = []
        self.paragraphs = []
        self.anchor = None
        self.links = []

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style", "noscript", "svg"):
            self.skip += 1
        if self.skip:
            return
        if tag in ("p", "h1", "h2", "title"):
            self.tag, self.fragments = tag, []
        if tag == "a":
            self.anchor = (dict(attrs).get("href", ""), [])

    def handle_data(self, data):
        if self.skip:
            return
        if self.tag:
            self.fragments.append(data)
        if self.anchor is not None:
            self.anchor[1].append(data)

    def handle_endtag(self, tag):
        if tag in ("script", "style", "noscript", "svg"):
            self.skip = max(0, self.skip - 1)
            return
        if self.skip:
            return
        if tag == self.tag:
            text = " ".join(" ".join(self.fragments).split())
            if text:
                self.paragraphs.append((tag, text))
            self.tag, self.fragments = None, []
        if tag == "a" and self.anchor is not None:
            href, words = self.anchor
            text = " ".join(" ".join(words).split())
            self.links.append((href, text))
            self.anchor = None


def excerpt(raw, filename):
    """Extract *only for INDEX* after original raw bytes were written unchanged."""
    if filename.endswith(".xml"):
        try:
            root = ET.fromstring(raw)
        except ET.ParseError as exc:
            raise FetchError("InvalidXML: %s" % exc, status=200, retryable=False) from exc
        items = [node for node in root.iter() if node.tag.split("}")[-1] in ("item", "entry")]
        if not items:
            return "无条目（原始 XML 已保留）", True
        lines = []
        for item in items[:3]:
            elements = list(item)
            def field(k):
                return next((" ".join((e.text or "").split()) for e in elements if e.tag.split("}")[-1] == k), "")
            values = [field(k) for k in ("title", "pubDate", "description", "summary")]
            text = " | ".join(x for x in values if x)
            lines.append(html.unescape(text)[:500])
        return " / ".join(lines), False
    if filename.endswith(".json"):
        try:
            obj = json.loads(raw)
        except (ValueError, UnicodeError) as exc:
            raise FetchError("InvalidJSON: %s" % exc, status=200, retryable=False) from exc
        if isinstance(obj, list):
            return "原始 JSON ID 列表：%s 条（无正文）" % len(obj), not obj
        if isinstance(obj, dict):
            story = obj.get("story", obj)
            if isinstance(story, dict):
                text = story.get("text", "")
                values = [str(story.get("title", "")), str(story.get("url", "")), html.unescape(str(text))]
                comments = obj.get("comments", [])
                if comments:
                    values.append("顶层评论：" + html.unescape(str(comments[0].get("text", ""))))
                value = " | ".join(x.strip()[:350] for x in values if x)
                return re.sub(r"<[^>]*>", "", value)[:650] or "JSON 记录无可引正文", False
        return "JSON 记录无可引正文", False
    parser = TextParser()
    parser.feed(raw.decode("utf-8", errors="replace"))
    paragraphs = [t for tag, t in parser.paragraphs if tag == "p" and len(t) >= 55]
    if not paragraphs:
        paragraphs = [t for _, t in parser.paragraphs]
    return " / ".join(paragraphs[:3])[:700] or "HTML 中无可识别段落（见原文件）", False


def news_links(raw):
    parser = TextParser()
    parser.feed(raw.decode("utf-8", errors="replace"))
    links = []
    seen = set()
    for href, text in parser.links:
        path = urlsplit(urljoin(SOURCES["anthropic"][0], href)).path.rstrip("/")
        if ARTICLE_PATH.match(path) and path not in HUBS and len(text) >= 12 and path not in seen:
            links.append(path)
            seen.add(path)
    if len(links) > ARTICLE_LINK_WARNING_THRESHOLD:
        print("[Anthropic] 发布页链接 %s 条，超过预警阈值 %s；仍逐条抓取，请核查列表页" %
              (len(links), ARTICLE_LINK_WARNING_THRESHOLD), flush=True)
    return links


class Run:
    def __init__(self, date, selected, overrides, retry_failed):
        self.date = date
        self.selected = selected
        self.overrides = overrides
        self.retry_failed = retry_failed
        self.base = ROOT / "sources" / date
        self.raw_dir = self.base / "raw"
        self.raw_dir.mkdir(parents=True, exist_ok=True)
        self.manifest_path = self.raw_dir / ".fetch-manifest.json"
        self.lock = threading.RLock()
        if self.manifest_path.exists():
            self.entries = json.loads(self.manifest_path.read_text(encoding="utf-8")).get("entries", {})
        else:
            self.entries = {}

    def save_manifest(self):
        with self.lock:
            content = json.dumps({"date": self.date, "entries": self.entries}, ensure_ascii=False, indent=2) + "\n"
            temp = self.manifest_path.with_suffix(".tmp")
            temp.write_text(content, encoding="utf-8")
            temp.replace(self.manifest_path)

    def record(self, name, data):
        with self.lock:
            self.entries[name] = data
            self.save_manifest()

    def get(self, name, url, filename):
        dest = self.raw_dir / filename
        with self.lock:
            old = self.entries.get(name)
            if old and old["url"] != url:
                raise ValueError("%s 的 URL 已改变；请勿在同一日期混用不同源 URL" % name)
            if old and old["state"] in ("ok", "empty") and dest.is_file() and dest.stat().st_size:
                print("[缓存] %s HTTP %s (%s)" % (name, old["http_status"], filename), flush=True)
                return dest.read_bytes()
            if old and old["state"] == "failed" and not self.retry_failed:
                print("[已记录失败] %s %s；用 --retry-failed 重试" % (name, old["error"]), flush=True)
                return None
            if dest.exists() and dest.stat().st_size and (not old or old["state"] == "failed"):
                # Never claim an unknown HTTP code or overwrite a pre-existing raw file.
                self.record(name, {"url": url, "at": now(), "state": "failed", "http_status": None,
                                   "attempts": 0, "error": "UntrackedRawFile: 原文件存在但缺成功的 HTTP 元数据，请人工核对"})
                print("[元数据缺失] %s %s" % (name, filename), flush=True)
                return None
        # 按源等级决定重试次数（设计文档 §2.2：P0 三次、P1 一次）
        attempts = 1 + RETRIES_BY_PRIORITY.get(SOURCE_PRIORITY.get(name, "P1"), 1)
        result = request(url, max_attempts=attempts)
        if "error" in result:
            self.record(name, {"url": url, "at": now(), "state": "failed", "http_status": result["status"],
                               "attempts": result["attempts"], "error": result["error"]})
            print("[失败] %s %s (%s 次请求)" % (name, result["error"], result["attempts"]), flush=True)
            return None
        data = result["body"]
        try:
            with dest.open("xb") as output:
                output.write(data)
        except FileExistsError:
            # Other processes must not silently overwrite the first writer's raw bytes.
            self.record(name, {"url": url, "at": now(), "state": "failed", "http_status": result["status"],
                               "attempts": result["attempts"], "error": "ConcurrentWrite: 原文件已存在，未覆盖"})
            print("[冲突] %s 原始文件已有其他写入" % name, flush=True)
            return None
        try:
            quote, empty = excerpt(data, filename)
            state, problem = ("empty" if empty else "ok"), None
        except FetchError as exc:
            quote, empty, state, problem = "", False, "failed", exc.reason
        meta = {"url": url, "final_url": result["final_url"], "at": now(), "state": state,
                "http_status": result["status"], "attempts": result["attempts"],
                "size": len(data), "path": "raw/" + filename, "excerpt": quote}
        if problem:
            meta["error"] = problem
        self.record(name, meta)
        print("[%s] %s HTTP %s %s B %s" % ("空内容" if empty else "成功" if not problem else "解析失败", name,
                                               result["status"], len(data), filename), flush=True)
        return data if not problem else None

    def combined_story(self, rank, story):
        story_id = story["id"]
        name, filename = "hn-story-%s" % story_id, "hn-story-%s.json" % story_id
        dest = self.raw_dir / filename
        if name in self.entries and dest.exists() and dest.stat().st_size:
            print("[缓存] %s" % name, flush=True)
            return
        comments = []
        kids = story.get("kids", [])[:7]
        if rank < 3 and kids:
            def comment(kid):
                data = self.get("hn-comment-%s" % kid,
                                "https://hacker-news.firebaseio.com/v0/item/%s.json" % kid,
                                "hn-comment-%s.json" % kid)
                if data:
                    try:
                        item = json.loads(data)
                        if isinstance(item, dict) and item.get("parent") == story_id:
                            return item
                    except (ValueError, UnicodeError):
                        pass
                return None
            with ThreadPoolExecutor(max_workers=7) as pool:
                comments = [c for c in pool.map(comment, kids) if c is not None]
        payload = json.dumps({"story": story, "comments": comments}, ensure_ascii=False, indent=2).encode("utf-8") + b"\n"
        try:
            with dest.open("xb") as output:
                output.write(payload)
        except FileExistsError:
            print("[缓存] %s (并行写入)" % name, flush=True)
            return
        orig = self.entries.get("hn-item-%s" % story_id, {})
        quote, _ = excerpt(payload, filename)
        self.record(name, {"url": orig.get("url", "https://hacker-news.firebaseio.com/v0/item/%s.json" % story_id),
                           "at": now(), "state": "ok", "http_status": orig.get("http_status"),
                           "attempts": orig.get("attempts", 1), "size": len(payload), "path": "raw/" + filename,
                           "excerpt": quote, "derived": "story + %s top-level comments (first 7 IDs only for top 3)" % len(comments)})
        print("[成功] %s + %s 条顶层评论" % (name, len(comments)), flush=True)

    def hn(self, url):
        data = self.get("hn-top", url, "hn-topstories.json")
        if not data:
            return
        try:
            ids = json.loads(data)[:30]
            ids = [i for i in ids if isinstance(i, int)]
        except (TypeError, ValueError):
            print("[失败] hn-top 非有效 ID 列表", flush=True)
            return
        def item(story_id):
            payload = self.get("hn-item-%s" % story_id,
                               "https://hacker-news.firebaseio.com/v0/item/%s.json" % story_id,
                               "hn-item-%s.json" % story_id)
            if not payload:
                return None
            try:
                return json.loads(payload)
            except (ValueError, UnicodeError):
                return None
        with ThreadPoolExecutor(max_workers=8) as pool:
            results = list(pool.map(item, ids))
        selected = []
        for rank, story in enumerate(results):
            if not isinstance(story, dict):
                continue
            if KEYWORDS.search(str(story.get("title", "")) + " " + str(story.get("url", ""))) and (story.get("score", 0) >= 100 or rank < 30):
                selected.append(story)
        for rank, story in enumerate(selected):
            self.combined_story(rank, story)
        print("[HN] 前 %s 名，AI 关键词匹配 %s 条；前三条各最多 7 条顶层评论" % (len(ids), len(selected)), flush=True)

    @staticmethod
    def write_if_changed(path, content):
        if not path.exists() or path.read_text(encoding="utf-8") != content:
            path.write_text(content, encoding="utf-8")

    def reports(self):
        entries = self.entries
        rows = ["# 抓取索引（%s）" % self.date, "", "原始响应按源保存在 raw/；摘录只供定位，引用前请核对原文件。HTTP 码与抓取时间来自真实请求，缓存项沿用首次记录。", "", "HN 热榜前 30 条和顶层评论的中间响应单独存于 `raw/hn-item-*.json`、`raw/hn-comment-*.json`；逐项 HTTP 元数据见 `raw/.fetch-manifest.json`。下列仅展示可用于编辑的源与匹配的 HN 帖子。", ""]
        for name, meta in sorted(entries.items()):
            # 存档源的原始文件和 manifest 仍保留，但不进入编辑候选索引。
            if name == "qwen" or urlsplit(meta["url"]).scheme not in ("http", "https") or meta["state"] not in ("ok", "empty") or name.startswith(("hn-item-", "hn-comment-")):
                continue
            rows += ["## %s%s" % (name, " · 当日无条目（HTTP 成功，非抓取失败）" if meta["state"] == "empty" else ""),
                     "- URL：%s" % meta["url"], "- 抓取时间：%s" % meta["at"],
                     "- HTTP：%s；文件大小：%s B；原始文件：%s" % (meta["http_status"], meta["size"], meta["path"])]
            if meta.get("final_url") and meta["final_url"] != meta["url"]:
                rows.append("- 最终 URL（重定向）：%s" % meta["final_url"])
            if meta.get("derived"):
                rows.append("- 组合记录：%s（组成请求原始 JSON 另存 hn-item-*/hn-comment-*）" % meta["derived"])
            rows += ["- 原文摘录：", ("> " + meta.get("excerpt", "无可识别段落").replace("\n", " ")).rstrip(), ""]
        if not any(x["state"] in ("ok", "empty") for x in entries.values()):
            rows += ["本轮没有成功素材，详见 ../fetch-status.md。", ""]
        self.write_if_changed(self.raw_dir / "INDEX.md", "\n".join(rows).rstrip() + "\n")
        primary = [entries[k] for k in ("anthropic", "openai", "arxiv", "lilian", "deepseek",
                                        "infoq", "kr36", "ithome",
                                        "hn-top", "tldr", "substack", "simon") if k in entries]
        ok = sum(m["state"] in ("ok", "empty") for m in primary)
        failures = [(k, v) for k, v in sorted(entries.items()) if v["state"] == "failed"]
        lines = ["# 抓取状态（%s）" % self.date, "", "主源成功：%s/%s；失败项：%s；无条目：%s。缓存重跑不会重复请求成功源或叠加失败行。" %
                 (ok, len(primary), len(failures), sum(v["state"] == "empty" for v in entries.values())), "", "## 当日无内容（非失败）", ""]
        empties = [(k, v) for k, v in sorted(entries.items()) if v["state"] == "empty"]
        lines += ["- %s：%s；HTTP %s；条目 0（原始文件 %s）" % (k, v["url"], v["http_status"], v["path"]) for k, v in empties] if empties else ["- 无"]
        lines += ["", "## 真失败（含错误类型或实际 HTTP 状态）", ""]
        lines += ["- %s：%s；HTTP %s；原因：%s；请求 %s 次／重试 %s 次；记录时间：%s" %
                  (k, v["url"], v.get("http_status") if v.get("http_status") is not None else "无响应（见错误类型）",
                   v["error"], v["attempts"], max(0, v["attempts"] - 1), v["at"]) for k, v in failures] if failures else ["- 无"]
        lines += [""]
        self.write_if_changed(self.base / "fetch-status.md", "\n".join(lines))


def main(argv=None):
    parser = argparse.ArgumentParser(description="抓取早报原始素材（Python 3.9+，纯标准库）")
    parser.add_argument("date", nargs="?", help="目标目录日期 YYYY-MM-DD；默认本机今日")
    parser.add_argument("--only", choices=list(SOURCES), help="仅抓指定源（供诊断，不影响其他已有记录）")
    parser.add_argument("--source-url", action="append", default=[], metavar="NAME=URL", help="临时覆盖源 URL（用于错误分支自测；同日期不可混用不同 URL）")
    parser.add_argument("--retry-failed", action="store_true", help="只重新尝试既有失败项；绝不覆盖成功原始文件")
    args = parser.parse_args(argv)
    date = args.date or dt.datetime.now().astimezone().date().isoformat()
    try:
        if dt.date.fromisoformat(date).isoformat() != date:
            raise ValueError("日期格式应为 YYYY-MM-DD")
    except ValueError:
        parser.error("日期格式应为有效 YYYY-MM-DD")
    overrides = {}
    for assignment in args.source_url:
        if "=" not in assignment or assignment.split("=", 1)[0] not in SOURCES:
            parser.error("--source-url 格式：NAME=URL；NAME=%s" % ",".join(SOURCES))
        name, url = assignment.split("=", 1)
        overrides[name] = url
    selected = [args.only] if args.only else list(SOURCES)
    run = Run(date, selected, overrides, args.retry_failed)
    for name, url in overrides.items():
        key = "hn-top" if name == "hn" else name
        old = run.entries.get(key)
        if old and old["url"] != url:
            parser.error("%s 已存档 URL %s；不可更换 URL 覆盖同日期缓存" % (name, old["url"]))
    started = time.monotonic()
    print("[开始] %s；目录 %s；源 %s" % (now(), run.base, ",".join(selected)), flush=True)
    def primary(name):
        url, filename = SOURCES[name]
        url = overrides.get(name, url)
        if name == "hn":
            run.hn(url)
        else:
            raw = run.get(name, url, filename)
            if name == "anthropic" and raw:
                links = news_links(raw)
                if not links:
                    print("[Anthropic] 列表已抓，但未识别到发布页链接；人工检查原始 HTML", flush=True)
                for link in links:
                    slug = link.rsplit("/", 1)[-1]
                    run.get("anthropic-" + slug, "https://www.anthropic.com" + link,
                            "anthropic-" + slug + ".html")
    try:
        # Top-level sources run concurrently; HN children have their own bounded pool.
        with ThreadPoolExecutor(max_workers=4) as pool:
            futures = {pool.submit(primary, key): key for key in selected}
            for future in as_completed(futures):
                try:
                    future.result()
                except Exception as exc:
                    # Programming/unexpected IO error is visible, not silently discarded.
                    key = futures[future]
                    url = overrides.get(key, SOURCES[key][0])
                    run.record(key, {"url": url, "at": now(), "state": "failed", "http_status": None,
                                     "attempts": 1, "error": "%s: %s" % (exc.__class__.__name__, str(exc)[:200])})
                    print("[异常] %s %s: %s" % (key, exc.__class__.__name__, exc), flush=True)
    finally:
        run.reports()
    elapsed = time.monotonic() - started
    primary_keys = ["hn-top" if k == "hn" else k for k in selected if k != "qwen"]  # 存档源不计主源
    succeeded = sum(run.entries.get(k, {}).get("state") in ("ok", "empty") for k in primary_keys)
    failed = sum(v["state"] == "failed" for v in run.entries.values())
    print("[完成] 主源成功 %s/%s；失败项 %s；耗时 %.2f 秒；%s" %
          (succeeded, len(primary_keys), failed, elapsed, run.base), flush=True)
    return 0  # Partial network failure is recorded in fetch-status.md, not an uncaught crash.


if __name__ == "__main__":
    sys.exit(main())
