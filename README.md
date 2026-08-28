# 🤖 AI Daily News — 全球 AI 每日新闻双语日报

Collect AI news from 16 sources worldwide every day and generate a bilingual
(中文 / English) Markdown report, powered by an LLM API (default: DeepSeek).

每天自动抓取全球 16 个 AI 新闻源，用 LLM 生成中英双语摘要，输出 Markdown 日报。

---

## 📖 目录 / Contents

1. [工作原理 / How it works](#1-工作原理--how-it-works)
2. [环境要求 / Prerequisites](#2-环境要求--prerequisites)
3. [快速开始 / Quick start](#3-快速开始--quick-start)
4. [手动运行 / Run manually](#4-手动运行--run-manually)
5. [设置每日定时任务 / Schedule a daily task](#5-设置每日定时任务--schedule-a-daily-task)
6. [Git 自动提交与推送 / Auto-commit & push to git](#6-git-自动提交与推送--auto-commit--push-to-git)
7. [配置说明 / Configuration](#7-配置说明--configuration)
8. [输出说明 / Output](#8-输出说明--output)
9. [常见问题 / FAQ](#9-常见问题--faq)

---

## 1. 工作原理 / How it works

```
每日定时任务 (Task Scheduler)
        │
        ▼
python main.py
        │
        ├─ 1. 抓取 16 个 RSS 源（OpenAI / Google DeepMind / VentureBeat /
        │     量子位 / 雷锋网 / InfoQ 中文 … 全球中英文源）  [多线程]
        ├─ 2. 过滤：24 小时内 + AI 关键词 + 去重
        ├─ 3. LLM 逐条生成双语摘要（英文 45 词内 + 中文 80 字内）[多线程]
        │     + 自动分类（产品/研究/融资/政策/行业）+ 重要度评分 1-5
        ├─ 4. 生成 reports/ai-news-YYYY-MM-DD.md（中英双语 Markdown）
        │     + 同目录 .json 原始数据 + logs/ 日志
        └─ 5. Git：自动 commit 报告并 push 到远程仓库（可选，默认开启）
```

The pipeline is fault-tolerant: any single feed or LLM call that fails is
skipped and logged — the report still gets generated from the rest.

---

## 2. 环境要求 / Prerequisites

- **Python 3.10+** (tested on 3.12) — 下载 https://www.python.org (勾选 *Add to PATH*)
- **pip**（随 Python 自带）
- **LLM API Key** — 推荐 [DeepSeek 开放平台](https://platform.deepseek.com)（便宜、中文质量好）。
  也兼容任何 OpenAI 格式的 API：OpenAI / Moonshot(Kimi) / 通义千问 / 智谱 等。
- Windows 10/11（用于定时任务）

---

## 3. 快速开始 / Quick start

```powershell
# 1) 进入项目目录
cd ai-news-daily

# 2) 安装依赖（推荐用虚拟环境，避免污染全局 Python）
python -m venv .venv
.\.venv\Scripts\activate
pip install -r requirements.txt

# 3) 配置 API Key（二选一）
#    方式 A：设为用户环境变量（推荐，定时任务也能读到）
setx DEEPSEEK_API_KEY "sk-你的key"
#    方式 B：直接写进 config.json 的 llm.api_key 字段
#    说明：setx 之后要新开一个终端窗口才生效

# 4) 先离线测试（不用 API，验证抓取+报告流程）
python main.py --offline

# 5) 正式运行（LLM 双语摘要）
python main.py
```

---

## 4. 手动运行 / Run manually

```powershell
python main.py                 # 正常：抓取 + LLM 双语摘要 + 报告
python main.py --offline       # 离线：不调 LLM，用原文标题/简介（无翻译）
python main.py --dry-run       # 只抓取并打印统计，不生成报告
python main.py --hours 48      # 只看最近 48 小时
python main.py --max 30        # 最多收录 30 条
python main.py --config my.json  # 使用自定义配置
python main.py -v              # 调试日志
```

报告输出：`reports/ai-news-YYYY-MM-DD.md`（双击即可在 VS Code / Typora 预览）。

---

## 5. 设置每日定时任务 / Schedule a daily task

```powershell
# 注册：每天 08:00 自动运行（用 pythonw.exe，不弹黑窗口）
powershell -ExecutionPolicy Bypass -File register_task.ps1

# 自定义时间：每天 09:30
powershell -ExecutionPolicy Bypass -File register_task.ps1 -Time "09:30"

# 立即测试运行一次
Start-ScheduledTask -TaskName "AI Daily News"

# 查看上次运行结果
Get-ScheduledTaskInfo -TaskName "AI Daily News"

# 取消定时任务
powershell -ExecutionPolicy Bypass -File register_task.ps1 -Unregister
```

> ⚠️ **重要**：定时任务运行在系统环境下，只能读到 **用户级** 环境变量。
> 用 `setx DEEPSEEK_API_KEY "sk-xxx"` 设置后，需要 **注销/重启** 或新开终端，
> 任务才能真正读到。如果没设置 Key，任务会自动退化为离线模式（仍会生成报告）。

> 💡 如果你用虚拟环境安装依赖，请把 `register_task.ps1` 里第 39 行附近的
> `python` 改为 `.venv\Scripts\pythonw.exe` 的完整路径，例如：
> `$exe = "D:\path\to\ai-news-daily\.venv\Scripts\pythonw.exe"`
> （脚本默认使用全局 `python`。）

---

## 6. Git 自动提交与推送 / Auto-commit & push to git

生成报告后，任务会自动把报告文件 commit 并 push 到远程 git 仓库
（默认开启，可用 `--no-git` 关闭）。

### 一次性初始化（只需做一次）

```powershell
cd ai-news-daily
git init                                   # 初始化仓库（本目录已初始化）
git remote add origin <你的远程仓库地址>     # 例如 https://github.com/you/ai-news-daily.git
git branch -M main                         # 可选：改主分支名为 main
git push -u origin main                    # 首次推送，会缓存登录凭据
```

> 💡 首次 `git push` 会弹出登录窗口（Git Credential Manager），
> 登录一次后凭据会被缓存，之后定时任务即可静默推送，无需再次登录。

### 配置项（config.json → `git`）

| 字段 | 默认值 | 说明 |
|---|---|---|
| `enabled` | `true` | 是否自动 commit + push |
| `remote` | `origin` | 推送到哪个远程名 |
| `commit_prefix` | `ai-news: daily report ` | 提交信息前缀 |
| `auto_add_remote_url` | `""` | 可选：若远程不存在，自动 `git remote add` 这个地址 |

### 失败处理

Git 步骤是**尽力而为**的，任何失败（无仓库、无远程、无凭据、网络问题）
只会记录 warning 日志，**不会**让任务失败——本地报告仍然生成。
查看日志：`logs/ai-news-YYYY-MM-DD.log`。

---

## 7. 配置说明 / Configuration

编辑 `config.json`：

### LLM 设置 `llm`

| 字段 | 默认值 | 说明 |
|---|---|---|
| `base_url` | `https://api.deepseek.com` | OpenAI 兼容接口地址。OpenAI 用 `https://api.openai.com/v1` |
| `model` | `deepseek-chat` | 模型名。OpenAI 可换 `gpt-4o-mini` |
| `api_key_env` | `DEEPSEEK_API_KEY` | 从哪个环境变量读 Key |
| `api_key` | `""` | 也可直接把 Key 写这里（二选一，env 优先） |
| `temperature` | `0.3` | 越低越稳定 |
| `concurrency` | `4` | 同时翻译的并发数 |

### 新闻设置 `news`

- `hours_back` (24)：只看最近多少小时内的新闻
- `max_articles` (20)：每天最多收录条数
- `min_importance` (2)：低于此重要度的条目不进报告

### 新闻源 `feeds`

每条：`name`（显示名）、`url`（RSS）、`lang`（`en`/`zh`）、
`keywords`（`true` = 综合科技源，只保留含 AI 关键词的文章）。

内置 16 个源（全部验证可用）：

| 源 | 语言 | 类型 |
|---|---|---|
| OpenAI / Google AI / Google DeepMind | EN | 官方博客 |
| MIT Technology Review / VentureBeat / The Verge / TechCrunch / The Guardian / MarkTechPost | EN | 科技媒体 |
| Ars Technica / BBC Tech / CNBC Tech | EN | 综合科技（关键词过滤） |
| 量子位 QbitAI / 雷锋网 Leiphone / InfoQ 中文 / 爱范儿 ifanr | 中文 | 中文科技媒体 |

想加源：直接在 `feeds` 数组追加一行即可（任意 RSS/Atom 都支持）。

---

## 8. 输出说明 / Output

`reports/ai-news-2026-08-28.md` 结构：

```
# 🤖 全球 AI 每日新闻 | AI Daily News (Worldwide)
> 日期 / 收录数 / 来源数 / 模式

## 🏆 今日头条 Top Stories        （重要度 ≥ 4 的精华，最多 5 条）
### 1. OpenAI launches GPT-5 | OpenAI 发布 GPT-5
> ⭐⭐⭐⭐⭐ · 来源 · 日期 · 🚀 产品发布 Product & Launches
- English: …
- 中文: …
- 🔗 链接

## 📂 分类浏览 By Category
### 🚀 产品发布 · Product & Launches
### 🔬 研究与突破 · Research & Breakthroughs
### 💰 融资与商业 · Funding & Business
### 🏛️ 政策与监管 · Policy & Regulation
### 📈 行业动态 · Industry News
```

同一目录还会生成 `ai-news-YYYY-MM-DD.json`（结构化数据，便于二次处理），
运行日志在 `logs/`。

---

## 9. 常见问题 / FAQ

**Q: 没有 API Key 会怎样？**
A: 自动降级为离线模式：报告用原文标题+简介，中文源给中文、英文源给英文，
   不做翻译。设置 Key 后重新运行即可。

**Q: 某个新闻源抓取失败？**
A: 正常，单个源失败只记日志、不影响其他源。可到 `logs/ai-news-YYYY-MM-DD.log`
   查看具体原因（网络、反爬、改版等），把失效的源从 `config.json` 删掉即可。

**Q: 定时任务运行了但没生成报告？**
A: 打开「任务计划程序」→ 找到 AI Daily News → 「上次运行结果」看错误码；
   也可以手动跑一次 `python main.py` 看报错。最常见原因是 Python 不在 PATH 或
   Key 没读到（环境变量需重启会话）。

**Q: 想换 DeepSeek 之外的模型？**
A: 改 `config.json` 的 `llm.base_url` + `llm.model` 即可，任何 OpenAI 兼容
   接口都行（OpenAI / Moonshot / Qwen / GLM…）。

**Q: 报告是英文还是中文为主？**
A: 双语并列：`英文标题 | 中文标题`，摘要分 `English:` / `中文:` 两行。
   完全对称，方便对照阅读。

**Q: 为什么日志里 Git 提交了但推送失败？**
A: 常见原因：① 还没 `git remote add origin <地址>`（先做一次性的首次推送）；
   ② 远程需要登录而定时任务无法弹窗——请先手动 `git push -u origin <分支>`
   登录一次缓存凭据；③ 网络/远程仓库问题。失败只记日志，不影响报告生成。

**Q: 不想自动推送到 git？**
A: 运行加 `--no-git`，或把 `config.json` 的 `git.enabled` 改为 `false`。

---

*Generated daily by ai-news-daily · 每天自动生成 · Happy reading! 🚀*
