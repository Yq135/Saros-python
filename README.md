# Saros

**「沉淀即永恒」** —— 一个「联网搜索 + 检索增强生成（RAG）」的个性化本地智能知识库。

把外部知识（搜索 / 网页 / 视频）转化为私有资产，让「获取的新知识」与「过去的旧总结」碰撞、复用。本地 Web 应用，浏览器访问 localhost，单用户、无登录、全中文界面。

## 功能模块

| 模块 | 能力 |
|---|---|
| **联网问答** | DuckDuckGo 等免费搜索源 + 沉淀知识混合检索（语义 + 标签 + 关键词加权）→ LLM 流式回答带引用标注；同会话多轮追问；自动推荐标签；搜索全挂时降级为仅沉淀回答 |
| **网页出题** | 传 URL → 抽取正文（trafilatura，Jina Reader 兜底）→ 生成 3-5 道「读后掌握」问题 + 参考答案 |
| **B站视频** | 传链接 → 下载字幕（官方 CC → B 站 AI 字幕 → 音频兜底走 ASR 转写）→ 带 `[MM:SS]` 时间戳大纲 + 出题；详情页内嵌官方播放器，点击时间点跳转；后台异步任务、断点续跑 |
| **知识沉淀** | 手打录入（**禁止粘贴**，强制逐字输入加深记忆）+ 标签自动补全 + 掌握度星级；写入 pgvector 向量库 |
| **知识查询** | 已沉淀知识的分页列表（关键词 / 标签 / 掌握度筛选）+ 编辑删除；**语义查询**（小RAG）：自然语言输入 → 返回语义匹配的笔记 + 相似度分数（纯检索，不调用 LLM） |
| **设置** | LLM / ASR / B 站 cookie 配置，保存后**即时生效**（写 .env + 进程内热刷新，无需重启）；cookie 在线校验登录态 |

## 技术栈

- **后端**：Python 3.10 + FastAPI + uvicorn + psycopg
- **存储**：PostgreSQL + pgvector（512 维向量，元数据 + 向量同库）
- **嵌入**：sentence-transformers + `BAAI/bge-small-zh-v1.5`（本地 CPU 推理）
- **LLM**：OpenAI 兼容协议（DeepSeek 等国产模型，base_url / api_key / model 均可配）
- **ASR**：自建 mlx-qwen3-asr（OpenAI 兼容接口，另一台机器部署；仅视频无字幕时启用）
- **搜索**：ddgs（DuckDuckGo 免费源）主力，Bing/百度抓取兜底
- **视频**：yt-dlp + ffmpeg
- **前端**：Vue 3 + Vite + Element Plus + vue-router（marked + DOMPurify 渲染 Markdown）

## 目录结构

```
├── dev.sh              # 一键启动（后端 8000 + 前端 5173，Ctrl+C 同停）
├── db_init.sql         # 数据库初始化脚本（建表 + pgvector 扩展，不用 alembic）
├── docs/               # 需求 / 路线图 / 设计文档
├── backend/
│   ├── app/
│   │   ├── main.py     # FastAPI 装配入口
│   │   ├── config.py   # pydantic-settings 配置（读 backend/.env）
│   │   ├── routers/    # 各模块 API（qa / webpages / bilibili / knowledge / settings）
│   │   ├── services/   # 问答、视频任务队列、下载、ASR、设置等服务
│   │   ├── vector_store.py / embeddings.py   # pgvector 封装 / 本地嵌入
│   │   └── llm.py / search.py / prompts.py   # LLM 客户端 / 搜索源 / 提示词
│   ├── tests/          # pytest（见下文「测试」）
│   └── data/           # 媒体文件、cookie、本地模型缓存（gitignore）
└── frontend/
    └── src/views/      # QaView / WebpageView / VideoView / KnowledgeView / KnowledgeQueryView / SettingsView
```

## 快速开始

### 环境要求

- **Python 3.10**（conda 环境 `saros`，`~/anaconda3/envs/saros`）
- **Node.js**（前端 Vite 6）
- **PostgreSQL + pgvector**（pgvector 扩展已启用，嵌入维度 512；连接参数走 .env，网络需可达）
- 可选：另一台机器部署的 **mlx-qwen3-asr** 服务（无字幕视频的音频转写）

### 步骤

1. **初始化数据库**（本地 Linux 的 PG）：

   ```bash
   psql -U <user> -d saros_db -f db_init.sql
   ```

2. **配置 .env**：

   ```bash
   cp backend/.env.example backend/.env
   # 编辑 backend/.env：PG 连接、LLM_API_KEY、ASR 地址、B 站 cookie 路径等
   ```

3. **安装依赖**：

   ```bash
   ~/anaconda3/envs/saros/bin/python -m pip install -r backend/requirements.txt
   cd frontend && npm install
   ```

4. **一键启动**：

   ```bash
   ./dev.sh
   ```

   浏览器访问 **http://localhost:5173**；后端 API 文档在 http://127.0.0.1:8000/docs

> 首次启动会加载嵌入模型（本地 CPU），若网络不通可先从 [ModelScope](https://www.modelscope.cn/AI-ModelScope/bge-small-zh-v1.5) 下载到 `backend/data/models/bge-small-zh-v1.5`，并把 `EMBEDDING_MODEL` 指向该目录。

## 配置项（backend/.env）

| 键 | 说明 |
|---|---|
| `PG_HOST / PG_PORT / PG_USER / PG_PASSWORD / PG_DB` | PostgreSQL + pgvector 连接 |
| `LLM_BASE_URL / LLM_API_KEY / LLM_MODEL` | 主 LLM（OpenAI 兼容协议） |
| `ASR_BASE_URL / ASR_API_KEY / ASR_MODEL` | 自建 ASR（音频模式转写） |
| `EMBEDDING_MODEL` | 嵌入模型（HF 模型名或本地目录，相对路径基于 backend/） |
| `HF_ENDPOINT` | Hugging Face 镜像（默认 hf-mirror.com） |
| `SEARCH_PROVIDERS` | 搜索源逗号分隔（ddgs / bing / baidu） |
| `COOKIE_PATH` | B 站 cookie 文件路径（相对路径基于 backend/） |
| `MAX_VIDEO_HEIGHT` | 视频清晰度上限（720p 封顶） |
| `SKIP_SUBTITLE` | 跳过字幕下载开关（True = 全部视频直接走音频 ASR 模式） |

## 测试

```bash
cd backend && ~/anaconda3/envs/saros/bin/python -m pytest tests/ -v
```

- 集成测试走**真实 PG + 真实嵌入模型**（.env 需可达），搜索/LLM/网络等外部依赖用 monkeypatch 假替，测试数据自建自清
- 真实链路冒烟（真实搜索 + 真实 LLM）：`SAROS_LIVE=1 ~/anaconda3/envs/saros/bin/python -m pytest tests/test_qa.py -v`

## 常见问题

- **`conda activate saros` 后 `python` 仍指向别的环境**：非交互 shell 下 profile 会干扰 PATH，本项目一律用 `~/anaconda3/envs/saros/bin/python` 绝对路径（`dev.sh` 已自动处理），安装依赖同理用 `-m pip`。
- **B 站下载 403**：cookie 缺失或失效。在设置页粘贴新的 B 站 cookie（Netscape 格式）并点击校验，保存后即时生效。
- **网页抽取/搜索偶发失败**：系统代理间歇性抽风（SSL EOF）会时好时坏，稍等重试即可。
- **修改了 .env 想立即生效**：在「设置」页编辑保存即可（进程内热刷新，无需重启）；直接手改 .env 文件则需重启后端。

## 文档

- [docs/REQUIREMENTS.md](docs/REQUIREMENTS.md) —— 需求文档（含历次决议）
- [docs/ROADMAP.md](docs/ROADMAP.md) —— 三阶段演进路线
- [docs/PLAN.md](docs/PLAN.md) —— 实施计划
- [docs/数据库设计说明.md](docs/数据库设计说明.md) —— 数据模型说明

## License

[MIT](LICENSE) © 2026 KAiRON
