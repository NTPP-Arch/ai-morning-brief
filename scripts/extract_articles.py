#!/usr/bin/env python3
"""把索引里的条目抓成「文章正文」，供出稿时当素材用。

为什么需要：
    fetch.py 只存了 RSS/列表页的摘要（每条一两百字），模型拿着摘要只能写出
    300 字的简报。要写出有细节的长文，必须有正文。

用法：
    python scripts/extract_articles.py 2026-11-16            # 抓当天索引里的条目
    python scripts/extract_articles.py 2026-11-16 --max 15   # 限制条数
    python scripts/extract_articles.py 2026-11-16 --only-zh  # 只抓中文源

产出：
    sources/<date>/articles/<slug>.md   每条一个文件，含标题、URL、正文段落
"""
import argparse
import html
import re
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[1]
UA = "Mozilla/5.0 (compatible; MorningBriefReader/1.0)"

# 正文段落的最小长度：过滤导航、按钮、版权行
MIN_PARA = 60
MAX_CHARS = 4000
SKIP_HOSTS = ("arxiv.org", "export.arxiv.org", "hacker-news.firebaseio.com",
              "tavily.com", "openai.com")  # openai 正文 403


def clean(text):
    text = re.sub(r"<script.*?</script>", " ", text or "", flags=re.S)
    text = re.sub(r"<style.*?</style>", " ", text, flags=re.S)
    text = re.sub(r"<[^>]+>", " ", text)
    return re.sub(r"\s+", " ", html.unescape(text)).strip()


def paragraphs(raw):
    """优先取 <p>；取不到再退回按行切。"""
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


def fetch(url, timeout=20):
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.status, resp.read(MAX_CHARS * 8).decode("utf-8", "replace")


def read_index(date):
    """从 INDEX.md 里取出 (name, url) 列表。"""
    path = ROOT / "sources" / date / "raw" / "INDEX.md"
    if not path.is_file():
        return []
    text = path.read_text(encoding="utf-8", errors="ignore")
    items = []
    for block in text.split("\n## ")[1:]:
        name, _, rest = block.partition("\n")
        m = re.search(r"- URL：(\S+)", rest)
        if m:
            items.append((name.strip(), m.group(1).strip()))
    return items


def slug_for(name, url):
    base = name.strip().lower()
    base = re.sub(r"[^a-z0-9\u4e00-\u9fff]+", "-", base)[:60].strip("-")
    if not base:
        base = re.sub(r"[^a-z0-9]+", "-", urlsplit(url).path.lower())[:60].strip("-")
    return base or "item"


def main():
    ap = argparse.ArgumentParser(description="抓文章正文")
    ap.add_argument("date")
    ap.add_argument("--max", type=int, default=18, help="本次最多抓几条（默认 18）")
    ap.add_argument("--only-zh", action="store_true", help="只抓中文源（infoq/kr36/ithome）")
    args = ap.parse_args()

    items = read_index(args.date)
    if not items:
        print("没有可用的 INDEX：sources/%s/raw/INDEX.md" % args.date, file=sys.stderr)
        return 1

    ZH = ("infoq", "kr36", "ithome")
    outdir = ROOT / "sources" / args.date / "articles"
    outdir.mkdir(parents=True, exist_ok=True)

    done = skipped = failed = 0
    for name, url in items:
        if done >= args.max:
            break
        host = urlsplit(url).netloc.lower()
        if any(h in host for h in SKIP_HOSTS):
            skipped += 1
            continue
        if args.only_zh and not any(name.startswith(z) for z in ZH):
            skipped += 1
            continue
        slug = slug_for(name, url)
        dest = outdir / (slug + ".md")
        if dest.is_file() and dest.stat().st_size > 400:
            skipped += 1
            continue
        try:
            status, raw = fetch(url)
        except (urllib.error.URLError, OSError, ValueError) as exc:
            print("[失败] %s %s: %s" % (name, type(exc).__name__, exc), flush=True)
            failed += 1
            continue
        paras = paragraphs(raw)
        if not paras:
            print("[空] %s 无可识别正文" % name, flush=True)
            failed += 1
            continue
        body = "\n\n".join(paras)[:MAX_CHARS]
        dest.write_text("# %s\n\n> 原文：%s\n> 抓取：%s\n\n%s\n" %
                        (name, url, time.strftime("%Y-%m-%d %H:%M"), body), encoding="utf-8")
        print("[成功] %-28s %d 段 / %d 字" % (name[:28], len(paras), len(body)), flush=True)
        done += 1

    print("完成：抓取 %d 条，跳过 %d，失败 %d；目录 %s" % (done, skipped, failed, outdir))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
