#!/usr/bin/env python3
"""云端出稿：把 sources/<date>/raw 的素材交给模型，产出中文早报（Markdown + 手机 HTML）。

用法：
    python scripts/build_brief.py 2026-11-08
    python scripts/build_brief.py 2026-11-08 --limit 6       # 论文条数上限（省钱调试）
    python scripts/build_brief.py 2026-11-08 --dry 3         # 只看第 3 块的素材够不够

环境变量（GitHub Actions 里用 secrets / vars 注入，不写进仓库）：
    LLM_BASE_URL  例 https://api.a-piko.top/v1
    LLM_API_KEY   模型 key
    LLM_MODEL     例 gpt-6.1-sol

设计依据：reports/2026-11-02-源扩展与降级设计.md v1.2 + AGENTS.md §十一（收录优先级与价值信号）
用户口径（2026-11-02）：地铁 50 分钟、要详细、要广、不按「对我有没有用」筛。

为什么要分三次写：
    单次调用写 7000–9000 字，模型会自己缩水到 3000 字（2026-11-02 实测 3288 汉字）。
    拆成「论文与技术 / 中文与社区 / 官方与产业」三次，每次只写一块、字数目标明确，
    再由一次轻量编辑把速览、本周看点、收尾补上。
"""
import argparse
import datetime as dt
import json
import os
import re
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

# ---------------------------------------------------------------- 写作规范

COMMON_RULES = """## 硬规则（违反任意一条就算不合格）
- 数字、名单、机构名、日期只能来自素材原文；素材没给的写「素材未提供」，**一个都不许编**。
- 小数只能照抄素材：素材写「约四成」就写「约四成」，不许换算成 40%。
- 属于传闻、爆料、未官宣的，必须在 ⚠️ 边界里写明「未获官方确认」。
- 不许写「值得关注」「建议收藏」「意义重大」这类空话；要判断就给能被指认的一句判断。
- 中文标题保留原文，不要翻译成英文再翻回来。
- 禁止表格、禁止代码块、禁止「接上文」。
- 不要写「需要我帮你整理明天的吗」这类对话式结尾，这是发布页不是聊天。
- 素材里只有标题没有正文的，标 `仅标题，正文未取到`，不要按标题瞎猜内容。"""

DEEP_RULES = """## 每条深读的格式（四段必须齐全，缺一段视为没写完）
### 标题（标题里带关键数字或结论）
**发生了什么**：2–3 句，谁做了什么。
**关键细节**：4–6 句或 3–5 个短点——机制怎么走、数字带单位和基准、名单写到机构名、条件写清「只有满足 X 才 Y」。
**⚠️ 边界**：哪一步可能错、什么条件下不成立、哪里容易误读。不要用「未官宣」当免责装饰。
**一句话点评**：1–2 句，明确站边的判断，读者要能看出这是推论而不是报道内容。
来源：域名

每条 **320–420 个汉字**（不含标点、链接、小标题）。「关键细节」不足 4 句就是没写完，
继续补机制、补数字、补对比、补限制条件——**不要靠增加条数凑字数**。"""

SHORT_RULES = """## 短条目的格式（1–2 句，不展开）
- **一句话事实**：关键数字或细节 → 一句判断

短条目也不许空话，每条至少带一个具体信息（数字、机构名、日期、金额、比例）。"""


def bucket_prompt(date, name, body, target, deep, short):
    return """今天是 {date}。你是中文 AI 早报主编，这一轮**只写下面这一块**：{name}。

整份早报正文目标 7000–9000 字（读者在地铁上读 40–50 分钟，随时到站放下）。
**这一块负责 {target} 字**。写不到这个量就是没写完——回去把每条写厚（补机制、数字、对比、限制条件），
不是少写几条，也不是灌水复述。

结构：
```
## 📰 板块名（自己起，如「论文与架构」「中文动态」「产业与资本」）
### 深读条目标题
（按四段格式）
- 短条目若干
```

本块计划：{deep} 条深读 + {short} 条短条目。素材撑不起这么多深读时，就少写深读、把短条目写足，
并在本块末尾用一行 `> 素材不足：<哪一类没内容>` 说明。

{rules}

{rules2}

## 素材（只准用这些）
{material}
""".format(date=date, name=name, target=target, deep=deep, short=short,
           rules=DEEP_RULES, rules2=COMMON_RULES, material=body)


EDITOR_PROMPT = """下面是今天（{date}）早报正文的初稿，分 {k} 块，正文约 {n} 字。

请**只输出三块内容**，不要重复也不要改写正文：

1. `## 🚇 3 分钟速览（今天记这几条）`
   6 条，编号列表，每条格式：`**一句话事实**：关键数字或细节 → 一句判断`，每条 40–70 字，不许展开。
   这 6 条要覆盖不同板块，不要都来自同一块。

2. `## 🔭 本周看点`
   只有当正文里确实出现**确定日期**的事件时才写，3–5 行：`- 时间｜事件｜关注点`。
   一条都没有就只写 `- 素材中没有带确定日期的事件`。

3. `## 💬 收尾一句话`
   2–3 句跨事件的洞察，不要复述前面的内容，也不要写「今天信息很多」这种废话。

{rules}

正文初稿：
{body}
"""


# ---------------------------------------------------------------- 素材读取

def clean(text):
    return re.sub(r"\s+", " ", text or "").strip()


def parse_index(raw_dir):
    path = raw_dir / "INDEX.md"
    if not path.is_file():
        return []
    blocks = path.read_text(encoding="utf-8", errors="ignore").split("\n## ")
    out = []
    for block in blocks[1:]:
        name, _, rest = block.partition("\n")
        name = name.strip()
        url = re.search(r"- URL：(\S+)", rest)
        excerpt = re.search(r"原文摘录：\n> (.*)", rest, re.S)
        body = clean(excerpt.group(1))[:2000] if excerpt else ""
        if not body:
            continue
        out.append({"source": name.split("-")[0], "name": name,
                    "title": name, "url": url.group(1) if url else "", "text": body})
    return out


def parse_arxiv(raw_dir, limit=14):
    path = raw_dir / "arxiv.xml"
    if not path.is_file():
        return []
    raw = path.read_text(encoding="utf-8", errors="ignore")
    items = []
    for m in re.finditer(r"<entry>(.*?)</entry>", raw, re.S):
        blk = m.group(1)
        title = re.search(r"<title>(.*?)</title>", blk, re.S)
        summ = re.search(r"<summary>(.*?)</summary>", blk, re.S)
        link = re.search(r"<id>(.*?)</id>", blk)
        pub = re.search(r"<published>(.*?)</published>", blk)
        if not title or not summ:
            continue
        items.append({"source": "arxiv", "name": "", "title": clean(title.group(1)),
                      "url": (link.group(1) if link else "").strip(),
                      "date": (pub.group(1)[:10] if pub else ""),
                      "text": clean(summ.group(1))[:2400]})
        if len(items) >= limit:
            break
    return items


RAW_DIR_HINT = []


def article_text(name, fallback):
    """优先读 extract_articles.py 抓下来的正文；没有就退回索引摘录。"""
    if not RAW_DIR_HINT or not name:
        return fallback
    art = RAW_DIR_HINT[0].parent / "articles"
    if not art.is_dir():
        return fallback
    key = re.sub(r"[^a-z0-9一-鿿]+", "-", name.lower()).strip("-")
    best = None
    for path in art.glob("*.md"):
        stem = path.stem.lower()
        if stem == key or stem.startswith(key[:24]) or key.startswith(stem[:24]):
            if best is None or path.stat().st_size > best.stat().st_size:
                best = path
    if best is None:
        return fallback
    try:
        body = best.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return fallback
    body = "\n".join(l for l in body.splitlines() if l.strip() and not l.startswith(("#", ">")))
    return body[:4000] if len(body) > len(fallback) else fallback


def render(items, budget=90000):
    parts, total = [], 0
    for it in items:
        full = article_text(it.get("name") or "", it["text"])
        block = "- 标题：%s\n  URL：%s\n  正文：%s\n" % (it["title"], it.get("url", ""), full)
        if total + len(block) > budget:
            break
        parts.append(block)
        total += len(block)
    return "".join(parts)


def gather(raw_dir, limit=None):
    """按 AGENTS §十一 组织素材：论文 → 一手技术文 → 社区 → 公告，并分成三块。"""
    papers = parse_arxiv(raw_dir, limit or 14)
    idx = parse_index(raw_dir)

    def pick(prefixes, n):
        return [x for x in idx if any(x["name"].startswith(p) for p in prefixes)][:n]

    tech = pick(["lilian", "deepseek", "qwen", "simon"], 4)
    CN_KW = ("AI", "大模型", "模型", "智能体", "Agent", "LLM", "GPT", "Claude", "Gemini",
             "推理", "训练", "多模态", "开源", "算力", "芯片", "微调", "RAG", "向量", "提示词")
    cn = [x for x in pick(["qbitai", "infoq", "kr36", "ithome", "sinatech"], 30)
          if any(k.lower() in (x["text"][:400] + x["title"]).lower() for k in CN_KW)][:9]
    en_media = [x for x in pick(["techcrunch", "thedecoder"], 14)
                if any(k.lower() in (x["text"][:400] + x["title"]).lower()
                       for k in ("ai", "model", "agent", "llm", "openai", "anthropic", "gpt"))][:5]
    hn = pick(["hn-story"], 5)
    official = pick(["anthropic", "openai"], 10)
    other = pick(["tldr"], 2)

    if limit:
        papers = papers[:limit]

    b1 = papers + tech
    b2 = cn + en_media + hn
    b3 = official + other
    return [("论文与技术长文", b1, "3000", 5, 4),
            ("中文动态、英文科技媒体与社区讨论", b2, "2600", 4, 6),
            ("官方公告与产业", b3, "2200", 4, 5)]


def call_llm(messages, base_url, api_key, model, timeout=900, max_tokens=12000):
    body = json.dumps({"model": model, "messages": messages, "temperature": 0.4,
                       "max_tokens": max_tokens, "stream": False}).encode("utf-8")
    req = urllib.request.Request(
        base_url.rstrip("/") + "/chat/completions", data=body,
        headers={"Authorization": "Bearer " + api_key, "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        data = json.loads(resp.read().decode("utf-8", "replace"))
    if "choices" not in data:
        raise RuntimeError("模型返回异常：%s" % json.dumps(data, ensure_ascii=False)[:400])
    return data["choices"][0]["message"]["content"]


SYSTEM = ("你是中文 AI 早报主编。你只依据给定素材写作，不编造事实；"
          "你的判断只体现在「一句话点评」里。中文用简体。")


def sanitize(md):
    """去掉表格（手机竖屏禁表格）与代码围栏残留。"""
    lines = [ln for ln in (md or "").split("\n") if not ln.strip().startswith("|")]
    out = "\n".join(lines)
    out = re.sub(r"```[a-zA-Z]*\n?", "", out)
    return out.strip()


def cn_chars(text):
    return len(re.findall(r"[\u4e00-\u9fff]", text or ""))


def split_editor(md):
    """把编辑块按二级标题切开，返回 (速览, 本周看点, 收尾)。"""
    chunks = {"速览": "", "看点": "", "收尾": ""}
    cur = None
    for line in md.split("\n"):
        if line.startswith("## "):
            if "速览" in line:
                cur = "速览"
            elif "看点" in line:
                cur = "看点"
            elif "收尾" in line:
                cur = "收尾"
            else:
                cur = None
            continue
        if cur:
            chunks[cur] += line + "\n"
    return chunks["速览"].strip(), chunks["看点"].strip(), chunks["收尾"].strip()


def main():
    ap = argparse.ArgumentParser(description="云端早报出稿")
    ap.add_argument("date")
    ap.add_argument("--out", default=None, help="输出 md 路径；默认 briefs/<date>.md")
    ap.add_argument("--limit", type=int, default=None, help="限制论文条数（调试用）")
    ap.add_argument("--dry", type=int, default=None, help="只看第 N 块（1/2/3）的素材，不出稿")
    args = ap.parse_args()

    date = args.date
    raw_dir = ROOT / "sources" / date / "raw"
    if not raw_dir.is_dir():
        print("找不到素材目录 %s，先跑 scripts/fetch.py %s" % (raw_dir, date), file=sys.stderr)
        return 1
    buckets = gather(raw_dir, args.limit)
    RAW_DIR_HINT.clear()
    RAW_DIR_HINT.append(raw_dir)

    if args.dry:
        name, items, target, deep, short = buckets[args.dry - 1]
        material = render(items)
        print("块 %d %s：%d 条素材，渲染 %d 字符" % (args.dry, name, len(items), len(material)))
        print(material[:1500])
        return 0

    base = os.environ.get("LLM_BASE_URL", "https://api.deepseek.com")
    key = os.environ.get("LLM_API_KEY", "")
    model = os.environ.get("LLM_MODEL", "deepseek-chat")
    if not key:
        print("缺少 LLM_API_KEY", file=sys.stderr)
        return 1

    bodies = []
    for i, (name, items, target, deep, short) in enumerate(buckets, 1):
        if not items:
            bodies.append("## 📰 %s\n\n> 素材不足：今天没有可用素材。" % name)
            continue
        material = render(items)
        print("[%d/%d] %s：%d 条素材、%d 字符，目标 %s 字 ..."
              % (i, len(buckets), name, len(items), len(material), target), flush=True)
        out = sanitize(call_llm([{"role": "system", "content": SYSTEM},
                                 {"role": "user",
                                  "content": bucket_prompt(date, name, material, target, deep, short)}],
                                base, key, model))
        print("      实际 %d 汉字" % cn_chars(out), flush=True)
        bodies.append(out)

    body = "\n\n---\n\n".join(bodies)
    print("正文合计 %d 汉字，生成速览与收尾 ..." % cn_chars(body), flush=True)

    editor = sanitize(call_llm([{"role": "system", "content": SYSTEM},
                                {"role": "user",
                                 "content": EDITOR_PROMPT.format(date=date, k=len(bodies),
                                                                n=cn_chars(body),
                                                                rules=COMMON_RULES, body=body)}],
                               base, key, model, max_tokens=3000))
    brief, look, close = split_editor(editor)

    total = cn_chars(brief) + cn_chars(body) + cn_chars(look) + cn_chars(close)
    minutes = max(1, round(total / 300))
    md = ["# %s · 前沿 AI 日报（地铁版）" % date, ""]
    md.append("> 覆盖时间窗：过去 48 小时的论文、官方公告与行业动态（抓取于 %s）" % date)
    md.append("> 阅读建议：先读 3 分钟速览，再按板块深读；每块独立成篇，到站随时能放下。")
    md.append("> 全文约 %d 分钟（%d 字 ÷ 300 字/分）" % (minutes, total))
    md.append("")
    if brief:
        md += ["## 🚇 3 分钟速览（今天记这几条）", "", brief, ""]
    md += [body, ""]
    if look:
        md += ["## 🔭 本周看点", "", look, ""]
    if close:
        md += ["## 💬 收尾一句话", "", close, ""]
    md.append("来源说明：核心事实来自上文各条标注的来源；论文成效均为作者自报，素材未提供独立复现。")
    md = "\n".join(md)

    gate = ""
    try:
        import verify_brief
        result = verify_brief.check(md, verify_brief.load_material(date),
                                    max(1, round(len(verify_brief.iter_items(md)) * 0.2)))
        miss = result["missing_total"]
        if not result["ok"]:
            rows = []
            for item in result["items"]:
                if not item["missing_total"]:
                    continue
                bits = item["missing_numbers"] + item["missing_names"]
                rows.append("- %s：%s" % (item["title"][:40], "、".join(bits[:6])))
            gate = ("> ⚠️ **本期回查未通过**：%d 处数字/专名在素材中查不到，已列在文末。"
                    "请把标出的项当作「待核实」，不要直接引用。\n" % miss)
            md += "\n\n---\n\n**回查未通过项（素材中查不到）**\n" + "\n".join(rows) + "\n"
        else:
            print("回查通过（%d 条，缺失 %d 处）" % (len(result["items"]), miss), flush=True)
    except Exception as exc:  # 闸门自身故障不能挡住出稿
        print("回查跳过：%s" % exc, flush=True)

    header = ("> 云端自动出稿 · 素材来自 `sources/%s/raw/`（抓取管线产物）\n"
              "> 生成时间：%s\n%s\n" % (date, dt.datetime.now().astimezone().isoformat(timespec="minutes"), gate))
    out_md = Path(args.out) if args.out else ROOT / "briefs" / ("%s.md" % date)
    out_md.parent.mkdir(parents=True, exist_ok=True)
    out_md.write_text(header + md + "\n", encoding="utf-8")
    print("已写 %s（%d 汉字，约 %d 分钟）" % (out_md, total, minutes), flush=True)

    html = out_md.with_suffix(".html")
    subprocess.run([sys.executable, str(ROOT / "scripts" / "md-to-phone-html.py"),
                    str(out_md), "-o", str(html)], check=False)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
