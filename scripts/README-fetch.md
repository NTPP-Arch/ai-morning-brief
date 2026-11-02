# 早报原始素材抓取（fetch.py）

仅抓取并落盘原始响应及人工可读的索引/状态；**不会调用任何 AI、不会代写 clean.md 或早报**。要求 Python 3.9+，仅使用标准库，**无需 pip 安装依赖，也无需 requirements.txt**。

## 运行

在仓库根目录执行：

```powershell
python scripts/fetch.py --help
python scripts/fetch.py 2026-11-03
python scripts/fetch.py                 # 本机当地日期
python scripts/fetch.py 2026-11-03 --retry-failed
```

`--retry-failed` 只用于重新尝试失败请求；默认重复运行命中本地成功缓存，且既有失败也不重复请求、不会重复附加行。成功原始文件永不覆盖。测试错误分支可在**独立测试日期/临时目录**使用 `--only arxiv --source-url arxiv=http://127.0.0.1:1/bad`；同一天已存档的同名源禁止更换 URL。

每次输出到 `sources/<日期>/raw/`（原始 HTML/XML/JSON、`INDEX.md`、`.fetch-manifest.json`）及 `sources/<日期>/fetch-status.md`。`.fetch-manifest.json` 记录所有实际 HTTP 请求的 URL/时间/状态/字节数（或错误类型与重试次数），用于幂等与索引重建；不要删除。`INDEX.md` 展示编辑可用的源及原文摘录；HN 中间请求也逐一保存，原始响应不会被合并、HTML 解析只发生在保存之后且仅用于索引。

源：Anthropic 新闻列表及识别出的全部发布页、OpenAI 新闻 RSS、arXiv cs.CL 官方 API（最多返回 50 篇及摘要）、DeepSeek News、Lil'Log、HN Firebase API（前 30 个故事，标题/URL AI 关键词，选中的前三帖各最多 7 条顶层评论）、TLDR AI、Import AI Substack RSS、Simon Willison 首页。Qwen RSS 是**存档源**：继续抓取原始响应并保存在 raw/ 和 manifest，但不进入编辑候选 INDEX、不计入主源成功分母，也不参与筛选。Twitter/X 与 OpenAI 正文不抓（认证限制与已知 403）；不会抓 HN 网页或 Algolia。本脚本不做搜索补源，也不消耗搜索 API 额度。各源失败要看状态清单，不能当成当日无新闻。

单请求连接限时 10 秒、读取限时 15 秒；P0 最多请求 4 次（失败重试 3 次），P1/存档最多请求 2 次（失败重试 1 次），重试等待依次为 1 秒、2 秒、4 秒；单源失败不阻止其他源。arXiv API 返回 HTTP 200 且 entry 为 0 → 状态「无条目」，不会列为网络失败。**日期参数只是保存目录，不会凭空生成该日期的资讯**；在 2026-11-02 抓 `2026-11-09` 是预演快照，不能据此断言 2026-11-09 全天无新闻。抓取需遵守各站 robots/频率限制，不绕付费墙；调度频率建议每天一次。

## Windows 任务计划程序（每日 06:00）

1. 在 Windows 搜索“任务计划程序”→“创建基本任务”，命名“早报素材抓取”。
2. 触发器选“每天”，开始时间选 **06:00**（以该 Windows 主机时区计）。
3. 操作选“启动程序”：程序/脚本输入 `python`（若 `python` 不在计划任务 PATH，请填 `where python` 查得的 `python.exe` 绝对路径）；“添加参数”输入 `scripts/fetch.py`；“起始于”输入 `D:\早报新闻`（不要加引号）。
4. 勾选“无论用户是否登录都要运行”（需要相应 Windows 凭据），保存后右键该任务“运行”测试；检查 `sources/<今天>/raw/INDEX.md`、`fetch-status.md` 和任务历史/退出码。若需要持久控制台日志，可将操作改为 `cmd.exe`，参数 `/c "python scripts\fetch.py >> notes\fetch-console.log 2>&1"`，起始于同上；不建议无凭据存环境密钥。

本脚本不包含内建调度。首次成功抓取的文件与时间在同日重跑时保持不变；如需新快照，请用新的日期目录并确认没有其他进程同时抓取该日期。不会把网络失败写成“当日无内容”。
