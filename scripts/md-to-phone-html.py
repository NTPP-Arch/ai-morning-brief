#!/usr/bin/env python3
"""把早报 Markdown 转成手机竖屏友好的单文件 HTML。

用法:
    python scripts/md-to-phone-html.py briefs/2026-11-02.md
    python scripts/md-to-phone-html.py briefs/2026-11-02.md -o briefs/2026-11-02.html

设计约束（见 templates/早报模板.md）：
- 竖屏优先，最大宽度 720px，17px 字号，行高 1.78
- 不用表格（模板已禁）；如遇表格自动转成横向滚动容器，避免挤压
- 一级标题做页头，二级标题加左侧色条，三级标题加粗
- 代码块深色卡片，行内代码浅灰
- 末尾注释块（<!-- 以下为样张验收附录 -->）自动隐藏，不进入阅读视图
"""
import argparse
import html
import re
import sys
from pathlib import Path

CSS = """
:root{--ink:#1a1d24;--sub:#5b6472;--line:#e6e9ef;--bg:#fbfbfd;--accent:#2563eb}
*{box-sizing:border-box;-webkit-text-size-adjust:100%}
body{margin:0;background:var(--bg);color:var(--ink);
 font:17px/1.78 -apple-system,BlinkMacSystemFont,"PingFang SC","Hiragino Sans GB","Microsoft YaHei",sans-serif;
 padding:0 0 56px}
.wrap{max-width:720px;margin:0 auto;padding:0 18px}
header{padding:26px 0 16px;border-bottom:1px solid var(--line);margin-bottom:6px}
h1{font-size:23px;line-height:1.4;margin:0 0 8px;letter-spacing:-.2px}
h2{font-size:19px;margin:32px 0 12px;padding-left:11px;border-left:4px solid var(--accent);line-height:1.45}
h3{font-size:16.5px;margin:24px 0 8px}
p{margin:11px 0}
blockquote{margin:14px 0;padding:10px 14px;background:#fff;border-left:3px solid #cbd5e1;
 border-radius:0 10px 10px 0;color:var(--sub);font-size:15.5px}
blockquote p{margin:4px 0}
ul,ol{margin:10px 0;padding-left:22px}
li{margin:7px 0}
strong{font-weight:600}
a{color:var(--accent);text-decoration:none;border-bottom:1px solid rgba(37,99,235,.25)}
code{background:#f1f3f7;border-radius:5px;padding:1.5px 5px;font-size:13.5px;
 font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace}
pre{background:#0f172a;color:#e6edf7;border-radius:12px;padding:14px;overflow-x:auto;
 font-size:13px;line-height:1.7;margin:14px 0;
 font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace}
pre code{background:none;color:inherit;padding:0;font-size:13px}
hr{border:0;border-top:1px solid var(--line);margin:28px 0}
.scroll{overflow-x:auto;-webkit-overflow-scrolling:touch;margin:14px -18px;padding:0 18px}
table{border-collapse:collapse;width:100%;min-width:520px;font-size:14px;background:#fff;border-radius:12px;overflow:hidden}
th,td{border:1px solid var(--line);padding:9px 10px;text-align:left;vertical-align:top;line-height:1.65}
th{background:#f4f6f9;font-weight:600;white-space:nowrap}
.foot{font-size:13px;color:var(--sub);margin-top:14px}
@media (max-width:400px){body{font-size:16.5px}h1{font-size:21px}h2{font-size:18px}}
"""

INLINE = [
    (re.compile(r"`([^`]+)`"), r"<code>\1</code>"),
    (re.compile(r"\*\*([^*]+)\*\*"), r"<strong>\1</strong>"),
    (re.compile(r"\[([^\]]+)\]\(([^)\s]+)\)"), r'<a href="\2">\1</a>'),
]


def inline(text: str) -> str:
    out = html.escape(text, quote=False)
    out = INLINE[0][0].sub(INLINE[0][1], out)
    out = INLINE[1][0].sub(INLINE[1][1], out)
    out = INLINE[2][0].sub(INLINE[2][1], out)
    return out


def convert(md: str) -> str:
    # 去掉末尾 HTML 注释附录（验收用，不上屏）
    md = re.split(r"<!--\s*以下为", md)[0]

    lines = md.split("\n")
    body, i = [], 0
    title = "AI 早报"

    while i < len(lines):
        line = lines[i]
        stripped = line.strip()

        if stripped.startswith("```"):
            j = i + 1
            buf = []
            while j < len(lines) and not lines[j].strip().startswith("```"):
                buf.append(lines[j])
                j += 1
            body.append("<pre><code>" + html.escape("\n".join(buf)) + "</code></pre>")
            i = j + 1
            continue

        if stripped.startswith("|") and stripped.endswith("|"):
            j = i
            rows = []
            while j < len(lines) and lines[j].strip().startswith("|"):
                rows.append([c.strip() for c in lines[j].strip().strip("|").split("|")])
                j += 1
            table = ["<div class='scroll'><table>"]
            for n, row in enumerate(rows):
                if n == 1 and all(set(c) <= set("-: ") for c in row):
                    continue
                tag = "th" if n == 0 else "td"
                table.append("<tr>" + "".join(f"<{tag}>{inline(c)}</{tag}>" for c in row) + "</tr>")
            table.append("</table></div>")
            body.append("".join(table))
            i = j
            continue

        if stripped.startswith("### "):
            body.append(f"<h3>{inline(stripped[4:])}</h3>")
        elif stripped.startswith("## "):
            body.append(f"<h2>{inline(stripped[3:])}</h2>")
        elif stripped.startswith("# "):
            title = stripped[2:].strip()
            body.append(f"<h1>{inline(title)}</h1>")
        elif stripped.startswith("> "):
            j = i
            buf = []
            while j < len(lines) and lines[j].strip().startswith("> "):
                buf.append(lines[j].strip()[2:])
                j += 1
            body.append("<blockquote><p>" + inline("<br>".join(buf)) + "</p></blockquote>")
            i = j
            continue
        elif stripped == "---":
            body.append("<hr>")
        elif re.match(r"^[-*] ", stripped):
            j = i
            items = []
            while j < len(lines) and re.match(r"^[-*] ", lines[j].strip()):
                items.append(lines[j].strip()[2:])
                j += 1
            body.append("<ul>" + "".join(f"<li>{inline(x)}</li>" for x in items) + "</ul>")
            i = j
            continue
        elif re.match(r"^\d+\. ", stripped):
            j = i
            items = []
            while j < len(lines) and re.match(r"^\d+\. ", lines[j].strip()):
                items.append(re.sub(r"^\d+\. ", "", lines[j].strip()))
                j += 1
            body.append("<ol>" + "".join(f"<li>{inline(x)}</li>" for x in items) + "</ol>")
            i = j
            continue
        elif stripped:
            body.append(f"<p>{inline(stripped)}</p>")

        i += 1

    return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<meta name="theme-color" content="#0f172a">
<title>{html.escape(title)}</title>
<style>{CSS}</style>
</head>
<body>
<div class="wrap">
{chr(10).join(body)}
<div class="foot">手机版 · 由 scripts/md-to-phone-html.py 生成</div>
</div>
</body>
</html>
"""


def main() -> int:
    ap = argparse.ArgumentParser(description="早报 Markdown → 手机竖屏 HTML")
    ap.add_argument("src", help="输入 Markdown 路径")
    ap.add_argument("-o", "--out", help="输出 HTML 路径（默认同名 .html 放在同目录）")
    args = ap.parse_args()

    src = Path(args.src)
    if not src.is_file():
        print(f"找不到文件: {src}", file=sys.stderr)
        return 1
    out = Path(args.out) if args.out else src.with_suffix(".html")
    out.write_text(convert(src.read_text(encoding="utf-8")), encoding="utf-8")
    print(f"已生成 {out} （{out.stat().st_size} 字节）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
