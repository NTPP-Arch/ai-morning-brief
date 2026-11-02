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


# ---------------------------------------------------------------- 重要性排序
# 出稿与正文抽取共用：决定「哪条值得给全文、哪条进头版」
WEIGHTS = (("发布", 4), ("官宣", 4), ("开源", 5), ("推出", 3), ("上线", 3),
           ("融资", 4), ("估值", 3), ("亿美元", 3), ("亿元", 2), ("财报", 3), ("营收", 3),
           ("份额", 3), ("榜单", 3), ("调用量", 3), ("占比", 2),
           ("大模型", 2), ("模型", 1), ("智能体", 2), ("Agent", 3), ("agent", 3),
           ("AI", 1), ("LLM", 2), ("GPT", 2), ("Claude", 2), ("Gemini", 2),
           ("Qwen", 2), ("千问", 2), ("DeepSeek", 2), ("OpenAI", 3), ("Anthropic", 3),
           ("谷歌", 2), ("苹果", 3), ("美团", 3), ("阿里", 2), ("华为", 2),
           ("英伟达", 3), ("字节", 2), ("腾讯", 2), ("百度", 2),
           ("芯片", 2), ("算力", 2), ("机器人", 2), ("眼镜", 2), ("手机", 2),
           ("政策", 2), ("监管", 2), ("标准", 2), ("漏洞", 2), ("安全", 1),
           ("超过", 1), ("首次", 2), ("灰测", 3), ("曝光", 2), ("爆料", 2), ("泄露", 2))

NOISE = ("邀请函", "分会场", "报名", "招聘", "直播预告", "活动预告", "征稿",
         "问卷", "嘉宾揭晓", "倒计时", "抽奖")
COLLECT = ("8点1氪", "早报", "晚报", "周报", "盘点", "汇总", "快讯", "日报", "一周")
FORECAST = ("预测", "预计", "展望", "调研", "报告称", "研究显示")


def importance(title):
    """新闻价值打分：发布/开源/融资/榜单高，活动预告与合集低，预测类降权。"""
    if any(k in title for k in NOISE):
        return -1
    score = sum(w for k, w in WEIGHTS if k in title)
    if any(k in title for k in COLLECT):
        score -= 8
    if any(k in title for k in FORECAST):
        score -= 6
    return score
