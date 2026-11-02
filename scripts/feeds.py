#!/usr/bin/env python3
"""把 raw/ 里的 RSS/Atom 文件解析成逐条素材。

为什么需要：
    fetch.py 的 INDEX.md 每个源只留一条摘录（RSS 是一个请求），所以中文源在
    索引里只有 1 条，出稿时「中文动态」根本凑不出条目。要按篇选材，就得回到
    XML 本身解析。

用法：
    from feeds import feed_items, ai_filter
    items = feed_items(raw_dir, "qbitai.xml")     # [{title,url,text,date}]
"""
import html
import re

TAG = re.compile(r"<[^>]+>")


def _clean(text):
    text = re.sub(r"<!\[CDATA\[(.*?)\]\]>", r"\1", text or "", flags=re.S)
    text = TAG.sub(" ", text)
    return re.sub(r"\s+", " ", html.unescape(text)).strip()


def _pick(block, *names):
    for name in names:
        m = re.search(r"<%s[^>]*>(.*?)</%s>" % (name, name), block, re.S | re.I)
        if m and m.group(1).strip():
            return m.group(1)
    return ""


AI_KW = ("ai", "人工智能", "大模型", "模型", "智能体", "agent", "llm", "gpt", "claude",
         "gemini", "deepseek", "qwen", "通义", "推理", "训练", "多模态", "开源", "算力",
         "芯片", "微调", "rag", "向量", "提示词", "机器人", "具身")


def ai_filter(items, limit=12, require=True):
    if not require:
        return items[:limit]
    kept = [x for x in items
            if any(k in (x["title"] + " " + x["text"][:300]).lower() for k in AI_KW)]
    return kept[:limit]


def feed_items(raw_dir, filename, limit=30):
    """解析一个 RSS/Atom 文件，返回逐条素材（RSS <item> 与 Atom <entry> 都支持）。"""
    path = raw_dir / filename
    if not path.is_file():
        return []
    raw = path.read_text(encoding="utf-8", errors="ignore")
    out = []
    for m in re.finditer(r"<(item|entry)\b.*?</\1>", raw, re.S | re.I):
        blk = m.group(0)
        title = _clean(_pick(blk, "title"))
        if not title:
            continue
        url = _clean(_pick(blk, "link"))
        if not url:
            href = re.search(r'<link[^>]*href="([^"]+)"', blk, re.I)
            url = href.group(1) if href else ""
        body = _clean(_pick(blk, "content:encoded", "description", "summary", "content"))
        date = _clean(_pick(blk, "pubDate", "published", "updated"))[:31]
        out.append({"title": title, "url": url, "text": body[:2400], "date": date})
        if len(out) >= limit:
            break
    return out


def round_robin(groups, per_source):
    """多家源轮流取，避免被最大的一家占满。"""
    out, idx = [], 0
    while len(out) < per_source * len(groups):
        added = False
        for g in groups:
            if idx < len(g):
                out.append(g[idx])
                added = True
                if len(out) >= per_source * len(groups):
                    break
        if not added:
            break
        idx += 1
    return out
