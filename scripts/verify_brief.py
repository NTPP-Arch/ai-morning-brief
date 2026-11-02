#!/usr/bin/env python3
"""回查闸门：把成稿里的每个数字与专名，拿去素材里对一遍。

用法：
    python scripts/verify_brief.py briefs/2026-11-08.md
    python scripts/verify_brief.py briefs/2026-11-08.md --date 2026-11-08 --json
    python scripts/verify_brief.py briefs/x.md --max-missing 2   # 超过即退出码 1

为什么要有这个脚本：
    大模型天生会把碎片补成通顺的整体，而简报要求「宁可残缺也不能补」。
    抓取（只取证）+ 写作（只据证据写）这两段不够——必须有一道反向校验：
    成稿里出现的每个数字、每个模型名，都要能在素材原文里找到。
    找不到的，要么是转述时算错了，要么是编的，两种都必须人工看一眼。

判定口径（故意简单、可复现）：
    - 数字：阿拉伯数字串（含小数、百分比、万/亿/万亿后缀），去掉千分位逗号后比对。
    - 专名：连续 3 段以上的拉丁字母/数字组合（如 GPT-6.2、LongCat-2.5、Opus 5.5）。
    - 只要素材全文中出现过同一串，就算「查到」；不判断语义是否一致（这层留给人工）。
    - 不算缺失的白名单：章节序号、日期本身、字数/时长这类自述数字。

退出码：0 = 全部查到；1 = 有缺失且超过阈值；2 = 参数或文件问题。
"""
import argparse
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

# 成稿里允许「素材中查不到」的自我描述类数字
SELF_NUMBERS = {"3", "2", "1", "5", "6", "24", "48", "60", "300", "420", "7500"}
NUM_RE = re.compile(r"\d[\d,]*(?:\.\d+)?%?")
LATIN_RE = re.compile(r"[A-Za-z][A-Za-z0-9.\-]{2,}")
# 只把「含数字的型号」或「全大写缩写」当专名；普通英文词不算（避免 wall-clock 这类误报）
PROPER_RE = re.compile(r"[A-Za-z][A-Za-z0-9.\-]*\d[A-Za-z0-9.\-]*|[A-Z]{3,}")


def norm(text):
    return re.sub(r"[\s,，]", "", text or "").lower()


def load_material(date):
    """把当天素材拼成一个大字符串（含 INDEX 摘录与原始抓取件）。"""
    raw = ROOT / "sources" / date / "raw"
    if not raw.is_dir():
        return ""
    parts = []
    for path in sorted(raw.rglob("*")):
        if not path.is_file():
            continue
        if path.name.startswith("."):
            continue
        if path.suffix.lower() in (".xml", ".json", ".md", ".html", ".txt"):
            try:
                parts.append(path.read_text(encoding="utf-8", errors="ignore"))
            except OSError:
                continue
    return norm("\n".join(parts))


def iter_items(md):
    """按 ### 切分深读条目；没有 ### 时退回整篇。"""
    body = md.split("<!-- 以下为验收附录")[0]
    chunks = re.split(r"\n### ", body)
    if len(chunks) <= 1:
        return [("（全篇）", body)]
    out = []
    for chunk in chunks[1:]:
        title, _, rest = chunk.partition("\n")
        out.append((title.strip(), rest))
    return out


def check(md, material, max_missing):
    items = iter_items(md)
    report = []
    total_missing = 0
    for title, text in items:
        nums = {n.strip(",.") for n in NUM_RE.findall(text)}
        nums = {n for n in nums if re.sub(r"[^\d.]", "", n) not in SELF_NUMBERS}
        names = {n for n in PROPER_RE.findall(text) if len(n) >= 3 and not n.isdigit()}
        miss_num = sorted(n for n in nums if norm(n) not in material)
        miss_name = sorted(n for n in names if norm(n) not in material)
        missing = len(miss_num) + len(miss_name)
        total_missing += missing
        report.append({"title": title, "missing_numbers": miss_num,
                       "missing_names": miss_name, "missing_total": missing})
    ok = total_missing <= max_missing
    return {"ok": ok, "items": report, "missing_total": total_missing}


def main():
    ap = argparse.ArgumentParser(description="成稿数字/专名回查")
    ap.add_argument("brief", help="成稿 Markdown 路径")
    ap.add_argument("--date", default=None, help="素材日期目录；默认从文件名推断")
    ap.add_argument("--max-missing", type=int, default=None,
                    help="允许的缺失总数；默认按条数 20%% 自动算，至少 1")
    ap.add_argument("--json", action="store_true", help="输出 JSON")
    args = ap.parse_args()

    brief = Path(args.brief)
    if not brief.is_file():
        print("找不到成稿：%s" % brief, file=sys.stderr)
        return 2
    date = args.date or brief.stem
    material = load_material(date)
    if not material:
        print("找不到素材目录 sources/%s/raw/ —— 无法回查" % date, file=sys.stderr)
        return 2

    md = brief.read_text(encoding="utf-8", errors="ignore")
    _items = iter_items(md)
    limit = args.max_missing if args.max_missing is not None else max(1, round(len(_items) * 0.2))
    result = check(md, material, limit)

    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print("回查：%s（素材 sources/%s/raw）" % (brief, date))
        for item in result["items"]:
            if not item["missing_total"]:
                continue
            print("\n✗ %s" % item["title"][:60])
            if item["missing_numbers"]:
                print("    数字查无：%s" % "、".join(item["missing_numbers"][:8]))
            if item["missing_names"]:
                print("    专名查无：%s" % "、".join(item["missing_names"][:8]))
        checked = len(result["items"])
        print("\n合计：%d 条，缺失 %d 处，阈值 %d → %s"
              % (checked, result["missing_total"], limit,
                 "通过" if result["ok"] else "不通过（需人工核对上列项）"))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
