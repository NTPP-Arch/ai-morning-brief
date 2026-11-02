#!/usr/bin/env python3
"""云端出稿：把 sources/<date>/raw 的素材交给模型，产出中文早报（Markdown + 手机 HTML）。

用法：
    python scripts/build_brief.py 2026-11-08
    python scripts/build_brief.py 2026-11-08 --out briefs/_test.md --limit 8

环境变量（GitHub Actions 里用 secrets 注入，不写进仓库）：
    LLM_BASE_URL  例 https://api.deepseek.com
    LLM_API_KEY   模型 key
    LLM_MODEL     例 deepseek-chat

设计依据：reports/2026-11-02-源扩展与降级设计.md v1.2（300–450 字 / 16–22 条、
头条是论文与技术长文、公告置尾）+ AGENTS.md §十一（收录优先级与价值信号）。
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
TEMPLATE_HINT = """你要写的是一份「地铁版」早报：读者在通勤路上用手机读，随时可能到站放下、下一站接着读。

## 整体结构（严格按此顺序，没有对应素材的板块直接省略，不要硬凑）

1. 一级标题：`# {日期} · 前沿 AI 日报（地铁版）`
2. 开场三行（引用块）：
   - 覆盖时间窗（例：过去 24–48 小时）
   - 阅读建议：「先 3 分钟速览，再分块深读；每块独立成篇，到站随时能放下」
   - 全文预估阅读时长
3. `## 🚇 3 分钟速览（今天记这几条）`
   - 5–7 条，编号列表
   - 每条格式：`**一句话事实**：关键数字或细节 → 一句判断/为什么重要`
   - 每条 40–70 字，不许展开
4. 主题板块：`## 📰 板块名`（3–5 个板块，按重要性排序）
   - 每个板块 1–3 条深读，每条用 `### 标题（标题里带关键数字或结论）`
   - 每条固定四段，加粗小标：
     - `**发生了什么**`：2–3 句，谁做了什么
     - `**关键细节**`：4–6 句或 3–5 个短点，数字、名单、条件、时间点
     - `**⚠️ 边界**`：哪些是自测/传闻/未核实、什么条件下不成立、容易被误读的地方
     - `**一句话点评**`：1–2 句，你的判断，明确站边
   - 每条 **320–420 个汉字（不含标点、链接、小标题）**。四个小标都必须写，缺一段视为不合格；「关键细节」段不足 4 句就是没写完，必须继续补机制、数字、对比或限制条件。
5. `## 🧩 其余板块（每条 2 分钟）`
   - 按「产业与资本 / 终端与机器人 / 安全与治理」等分组
   - **短条目 8–12 条**，每条 1–2 句，不展开
6. `## 🔭 本周看点`
   - 只有在素材里确实存在确定日期的事件时才写，用「时间｜事件｜关注点」三列
7. `## 💬 收尾一句话`
   - 跨事件的一条洞察，2–3 句，不要复述前面的内容
8. 末尾一行来源说明：核心事实来自哪些源；哪些条目属于传闻未确认

## 硬规则
- **禁止表格**：唯一例外是第 6 节的「本周看点」（手机上是三列小表，可以保留）。
- 每个板块、每条都必须能在手机上独立读完，不许出现「接上文」。
- 数字、名单、机构名、日期必须来自素材原文；素材没给的写「素材未提供」，不许编。
- 属于传闻、爆料、未官宣的，必须在 ⚠️ 边界段写清「未获官方确认」。
- 不许写「值得关注」「建议收藏」这类推荐语；要写判断就写具体的一句话点评。
- 中文条目用中文标题原文，不要翻译。
- 不要写「需要我帮你整理明天的吗」这类对话式结尾，这是发布页不是聊天。"""


def clean(text):
    return re.sub(r"\s+", " ", text or "").strip()


def parse_index(raw_dir):
    """解析抓取索引，返回 [{source,title,url,text}]。"""
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
        title = name
        body = clean(excerpt.group(1))[:2000] if excerpt else ""
        if not body:
            continue
        out.append({"source": name.split("-")[0], "name": name,
                    "title": title, "url": url.group(1) if url else "", "text": body})
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
        items.append({"source": "arxiv", "title": clean(title.group(1)),
                      "url": (link.group(1) if link else "").strip(),
                      "date": (pub.group(1)[:10] if pub else ""),
                      "text": clean(summ.group(1))[:2400]})
        if len(items) >= limit:
            break
    return items


def gather(raw_dir, limit=None):
    """按 AGENTS §十一 的优先级组织素材：论文 → 一手技术文 → 社区 → 公告。"""
    papers = parse_arxiv(raw_dir)
    idx = parse_index(raw_dir)

    def pick(prefixes, n):
        got = [x for x in idx if any(x["name"].startswith(p) for p in prefixes)]
        return got[:n]

    tech = pick(["lilian", "deepseek", "qwen"], 4)
    CN_KW = ("AI", "大模型", "模型", "智能体", "Agent", "LLM", "GPT", "Claude", "Gemini",
             "推理", "训练", "多模态", "开源", "算力", "芯片", "微调", "RAG", "向量", "提示词")
    cn = [x for x in pick(["infoq", "kr36", "ithome"], 12)
          if any(k.lower() in (x["text"][:400] + x["title"]).lower() for k in CN_KW)][:5]
    hn = pick(["hn-story"], 6)
    official = pick(["anthropic", "openai"], 10)
    other = pick(["simon", "tldr"], 2)

    if limit:
        papers = papers[:limit]
        official = official[: limit // 2]

    sections = []
    if papers:
        sections.append(("论文（arXiv cs.CL）", papers))
    if tech:
        sections.append(("一手技术文", tech))
    if cn:
        sections.append(("中文源（InfoQ / 36氪 / IT之家，仅 AI 相关）", cn))
    if hn:
        sections.append(("社区讨论（Hacker News）", hn))
    if official:
        sections.append(("官方公告", official))
    if other:
        sections.append(("工程博客/聚合", other))
    return sections


def build_prompt(sections, date):
    parts = []
    total = 0
    for heading, items in sections:
        parts.append("\n## %s\n" % heading)
        for i, it in enumerate(items, 1):
            block = "- 标题：%s\n  URL：%s\n  正文：%s\n" % (
                it["title"], it.get("url", ""), it["text"])
            if total + len(block) > 180000:
                break
            parts.append(block)
            total += len(block)
    return ("""今天是 %s。请把下面素材写成一份中文 AI 早报。

要求：
%s

素材：
%s
""" % (date, TEMPLATE_HINT, "".join(parts)))


def call_llm(prompt, base_url, api_key, model, timeout=600):
    body = json.dumps({
        "model": model,
        "messages": [
            {"role": "system", "content": "你是中文 AI 早报主编。你只依据给定素材写作，不编造事实；你的判断只体现在「一句话点评」里。"},
            {"role": "user", "content": prompt},
        ],
        "temperature": 0.4,
        "max_tokens": 16000,
        "stream": False,
    }).encode("utf-8")
    req = urllib.request.Request(
        base_url.rstrip("/") + "/chat/completions", data=body,
        headers={"Authorization": "Bearer " + api_key, "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        data = json.loads(resp.read().decode("utf-8", "replace"))
    return data["choices"][0]["message"]["content"]


def sanitize(md):
    """去掉表格（手机竖屏禁表格）与代码围栏残留。"""
    lines = [ln for ln in md.split("\n") if not ln.strip().startswith("|")]
    return "\n".join(lines).strip()


def main():
    ap = argparse.ArgumentParser(description="云端早报出稿")
    ap.add_argument("date")
    ap.add_argument("--out", default=None, help="输出 md 路径；默认 briefs/<date>.md")
    ap.add_argument("--limit", type=int, default=None, help="限制论文条数（省钱调试用）")
    args = ap.parse_args()

    date = args.date
    raw_dir = ROOT / "sources" / date / "raw"
    if not raw_dir.is_dir():
        print("找不到素材目录 %s，先跑 scripts/fetch.py %s" % (raw_dir, date), file=sys.stderr)
        return 1
    sections = gather(raw_dir, args.limit)
    if not sections:
        print("素材为空，放弃出稿", file=sys.stderr)
        return 1

    base = os.environ.get("LLM_BASE_URL", "https://api.deepseek.com")
    key = os.environ.get("LLM_API_KEY", "")
    model = os.environ.get("LLM_MODEL", "deepseek-chat")
    if not key:
        print("缺少 LLM_API_KEY", file=sys.stderr)
        return 1

    prompt = build_prompt(sections, date)
    print("素材 %d 组、提示词 %d 字符，调用 %s ..." % (len(sections), len(prompt), model), flush=True)
    md = sanitize(call_llm(prompt, base, key, model))

    header = ("> 云端自动出稿 · 素材来自 `sources/%s/raw/`（抓取管线产物）\n"
              "> 生成时间：%s\n\n" % (date, dt.datetime.now().astimezone().isoformat(timespec="minutes")))
    out_md = Path(args.out) if args.out else ROOT / "briefs" / ("%s.md" % date)
    out_md.parent.mkdir(parents=True, exist_ok=True)
    out_md.write_text(header + md + "\n", encoding="utf-8")
    print("已写 %s（%d 汉字）" % (out_md, len(re.findall(r"[\u4e00-\u9fff]", md))), flush=True)

    html = out_md.with_suffix(".html")
    subprocess.run([sys.executable, str(ROOT / "scripts" / "md-to-phone-html.py"),
                    str(out_md), "-o", str(html)], check=False)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
