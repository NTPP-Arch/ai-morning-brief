# 每天早上的 AI 早报（云端自动跑）

电脑不用开。每天北京时间 06:00，GitHub Actions 自动抓素材、生成中文早报、发布成网页。
手机上打开这个地址看：

**https://ntpp-arch.github.io/ai-morning-brief/**

## 它是怎么跑的

```
06:00（北京时间）
  └─ scripts/fetch.py       抓 arXiv / Anthropic / OpenAI / HN / TLDR / Lilian 等
  └─ scripts/build_brief.py 把素材交给模型，按模板写成 16–20 条、每条 320–420 字
  └─ GitHub Pages           发布成手机友好的单页
```

全部在 GitHub 的服务器上完成，与你的电脑是否开机无关。

## 改什么会变什么

| 想改 | 改哪里 |
|---|---|
| 用哪个模型写稿 | 仓库 Settings → Variables：`LLM_BASE_URL`、`LLM_MODEL` |
| 换 key | 仓库 Settings → Secrets：`LLM_API_KEY` |
| 抓哪些源 | `scripts/fetch.py` 顶部的 `SOURCES` |
| 写法与条数 | `scripts/build_brief.py` 里的 `TEMPLATE_HINT` |
| 每天几点跑 | `.github/workflows/daily.yml` 的 `cron`（UTC，减 8 小时＝北京时间） |

## 已知限制

- **arXiv 只抓 cs.CL**，cs.LG / cs.AI 未接入。
- **中文源没有**：机器之心、量子位未接；DeepSeek 文档站是前端渲染，抓不到正文。
- **没有"发现"能力**：只抓白名单源，不会主动找白名单外的文章。
- **openai.com 正文 403**，只能写 RSS 摘要。
- **Substack 系超时**，Import AI 等抓不到。
- GitHub 的 cron 在整点高峰期可能延迟几十分钟；06:00 触发不等于 06:00 一定跑完，建议 06:30 之后再打开。

## 素材与密钥

- 抓取产物写在 `sources/`，**不入库**（`.gitignore` 已排除），每天重抓。
- key 只存在仓库 Secrets 里，代码里不出现。
