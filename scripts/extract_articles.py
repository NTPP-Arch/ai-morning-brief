#!/usr/bin/env python3
"""把素材条目抓成「文章正文」，供出稿时当素材用。

为什么需要：
    fetch.py 只存 RSS 摘要（一二百字），模型拿摘要只能写短语。
    2026-11-02 实测：按 INDEX 顺序抓 16 条全被 anthropic 占满，中文源一篇正文都没有，
    成稿只有 3288 字。所以按组配额抓，并且 RSS 源要回到 XML 里取逐条 URL。

用法：
    python scripts/extract_articles.py 2026-11-16
    python scripts/extract_articles.py 2026-11-16 --budget 300   # 总耗时上限（秒）

产出：
    sources/<date>/articles/<hash>-<slug>.md   含标题、URL、正文段落
"""
import argparse
import hashlib
import html
import re
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from urllib.parse import urlsplit

import feeds

ROOT = Path(__file__).resolve().parents[1]
UA = "Mozilla/5.0 (compatible; MorningBriefReader/1.0)"

MIN_PARA = 60          # 正文段落最小长度：过滤导航、按钮、版权行
MAX_CHARS = 4000       # 单篇正文上限
SKIP_HOSTS = ("arxiv.org", "export.arxiv.org", "hacker-news.firebaseio.com",
              "openai.com")  # arXiv 摘要已够；HN 是评论不是正文；openai 正文 403


def clean(text):
    text = re.sub(r"<script.*?</script>", " ", text or "", flags=re.S)
    text = re.sub(r"<style.*?</style>", " ", text, flags=re.S)
    text = re.sub(r"<[^>]+>", " ", text)
    return re.sub(r"\s+", " ", html.unescape(text)).strip()


def paragraphs(raw):
    """优先取 <p>；取不到再退回按句切。"""
    out = []
    for m in re.finditer(r"<p[^>]*>(.*?)</p>", raw, re.S | re.I):
        t = clean(m.group(1))
        if len(t) >= MIN_PARA and t not in out:
            out.append(t)
    if not out:
        for line in clean(raw).split("。"):
            line = line.strip()
            if len(line) >= MIN_PARA:
                out.append(line + "。")
    return out


def fetch(url, timeout=25):
    req = urllib.request.Request(url, headers={"User-Agent": UA,
                                               "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.status, resp.read(MAX_CHARS * 8).decode("utf-8", "replace")


def index_items(date):
    """INDEX.md 里的逐条 sections（Anthropic 逐篇、HN、技术博客）。"""
    path = ROOT / "sources" / date / "raw" / "INDEX.md"
    if not path.is_file():
        return []
    text = path.read_text(encoding="utf-8", errors="ignore")
    items = []
    for block in text.split("\n## ")[1:]:
        name, _, rest = block.partition("\n")
        m = re.search(r"- URL：(\S+)", rest)
        if m:
            items.append({"name": name.strip(), "title": name.strip(), "url": m.group(1).strip()})
    return items


def slug_for(title, url):
    base = re.sub(r"[^a-z0-9\u4e00-\u9fff]+", "-", (title or "").lower())[:40].strip("-")
    digest = hashlib.md5(url.encode("utf-8")).hexdigest()[:8]
    return "%s-%s" % (digest, base or "item")


def collect(date):
    """按组配额挑出要抓正文的条目：中文 10 / 英文媒体 5 / 官方 6 / 技术文 3。"""
    raw_dir = ROOT / "sources" / date / "raw"
    idx = index_items(date)
    plan = []

    cn_files = ["qbitai.xml", "infoq.xml", "kr36.xml", "ithome.xml", "sinatech.xml"]
    cn_groups = [feeds.ai_filter(feeds.feed_items(raw_dir, f), 12) for f in cn_files]
    # 先按新闻价值排序：头条候选必须先拿到全文，否则只能写 RSS 摘要（实测只有 200 字）
    cn_ranked = sorted([x for g in cn_groups for x in g],
                       key=lambda x: -feeds.importance(x["title"]))
    plan += [("中文", x) for x in cn_ranked[:12]]

    en_files = ["techcrunch.xml", "thedecoder.xml"]
    en_groups = [feeds.ai_filter(feeds.feed_items(raw_dir, f), 6) for f in en_files]
    en_ranked = sorted([x for g in en_groups for x in g],
                       key=lambda x: -feeds.importance(x["title"]))
    plan += [("英文媒体", x) for x in en_ranked[:6]]

    official = [x for x in idx if x["name"].startswith("anthropic")][:6]
    plan += [("官方", x) for x in official]

    tech = [x for x in idx if x["name"].startswith(("lilian", "deepseek", "qwen"))][:3]
    plan += [("技术文", x) for x in tech]
    return plan


def main():
    ap = argparse.ArgumentParser(description="抓文章正文")
    ap.add_argument("date")
    ap.add_argument("--budget", type=int, default=300, help="总耗时上限秒数（默认 300）")
    args = ap.parse_args()

    plan = collect(args.date)
    if not plan:
        print("没有可用素材：先跑 scripts/fetch.py %s" % args.date, file=sys.stderr)
        return 1

    outdir = ROOT / "sources" / args.date / "articles"
    outdir.mkdir(parents=True, exist_ok=True)
    deadline = time.time() + args.budget

    done = skipped = failed = 0
    per_group = {}
    for label, it in plan:
        url = it.get("url") or ""
        if not url:
            skipped += 1
            continue
        host = urlsplit(url).netloc.lower()
        if any(h in host for h in SKIP_HOSTS):
            skipped += 1
            continue
        dest = outdir / (slug_for(it.get("title", ""), url) + ".md")
        if dest.is_file() and dest.stat().st_size > 400:
            skipped += 1
            per_group[label] = per_group.get(label, 0) + 1
            continue
        if time.time() > deadline:
            print("[跳过] %s %s：超出时间预算" % (label, it.get("title", "")[:24]))
            skipped += 1
            continue
        try:
            status, raw = fetch(url)
        except (urllib.error.URLError, OSError, ValueError) as exc:
            print("[失败] %s %s: %s" % (label, it.get("title", "")[:24], type(exc).__name__), flush=True)
            failed += 1
            continue
        paras = paragraphs(raw)
        if not paras:
            print("[空] %s %s 无可识别正文" % (label, it.get("title", "")[:24]), flush=True)
            failed += 1
            continue
        body = "\n\n".join(paras)[:MAX_CHARS]
        dest.write_text("# %s\n\n> 原文：%s\n> 抓取：%s\n\n%s\n" %
                        (it.get("title", ""), url, time.strftime("%Y-%m-%d %H:%M"), body),
                        encoding="utf-8")
        print("[成功] %-6s %-24s %d 段 / %d 字" % (label, it.get("title", "")[:24], len(paras), len(body)), flush=True)
        done += 1
        per_group[label] = per_group.get(label, 0) + 1

    print("完成：新建 %d 条，跳过 %d，失败 %d；分组 %s" %
          (done, skipped, failed, per_group), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
