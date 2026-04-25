# ⚛️ Atoms

> **AI 全栈工程师工作台** — 用自然语言描述你的想法，让 Agent 帮你生成、运行、预览完整的 Web 应用。

**🌐 在线体验：[jackybigwang.site](https://jackybigwang.site)**

```
用户输入一句话  →  Agent 规划 + 编码  →  沙箱自动启动  →  实时预览
```

---

## 概览

Atoms 是一个**自托管的 AI 编程工作台**，核心能力是让大语言模型作为全栈工程师，
端到端地完成从需求理解、代码生成到应用预览的全流程。

不同于只会生成代码片段的 Copilot 类工具，Atoms 真正做到了：

- Agent **自主规划**任务拆解，用户可审核并 Approve
- Agent **逐步执行**，每一步进度实时可见
- 生成的代码**直接在隔离沙箱中运行**，浏览器内即时预览
- 支持**多轮对话迭代**，Agent 记忆上下文，持续修复改进

---

## 核心功能

### 🤖 SWE Agent 引擎
- 基于 **ReAct 循环**的自主编码 Agent，工具集包含 `bash`、`str_replace_editor`、`file_operators`、`todo_write` 等
- **Draft Plan 流程**：Agent 生成方案 → 用户 Approve → Agent 开始执行，防止盲目开工
- **Task Checklist**：Agent 将工作拆分为可追踪任务，逐条打勾，进度全程透明
- **三层 Context 压缩**（micro_compact → auto_compact → manual compact）解决长任务 context 爆炸问题
- 流式输出 + Thinking 指示器，交互体验媲美主流 AI 产品

### 🏗️ 隔离沙箱运行时
- 每个项目独立 **Docker 容器**，代码运行完全隔离
- 沙箱内置 Node.js 20、Python 3、uv，自动识别项目类型（React/Vue/Flask/FastAPI）
- `start-preview` 脚本自动安装依赖、启动前后端服务、健康检查就绪后触发预览
- 应用 URL 通过 **Nginx 反向代理**暴露，浏览器内嵌 iframe 实时预览
- 每次 Agent 完成任务后**自动刷新预览**（cache-bust via `?_v` 参数）

### 💬 实时流式通信
- 前后端通过 **WebSocket** 全双工通信
- 后端事件驱动：`assistant.stream_chunk` / `draft_plan.*` / `task.*` / `preview_ready`
- 前端 typewriter 动效渲染，丝滑无抖动
- Stop 控制：随时中断 Agent，状态安全回收

### 🔐 认证系统
- 支持邮箱注册 / 登录
- **Google 原生 OIDC 登录**（直连 `accounts.google.com`，无第三方中间层），JWT 鉴权
- 完整的邮箱验证、忘记密码、重置密码流程

### 🗂️ 项目工作台
- 多项目管理，每个项目独立工作区
- Monaco Editor 在线代码编辑
- 文件树浏览 & 文件内容实时同步

---

## 技术架构

```
┌─────────────────────────────────────────────────────┐
│                     浏览器客户端                       │
│  React 18 · TypeScript · Vite · Tailwind · Radix UI  │
│  Monaco Editor · WebSocket Client                    │
└────────────────────┬────────────────────────────────┘
                     │ HTTP / WebSocket
┌────────────────────▼────────────────────────────────┐
│                  FastAPI 后端                          │
│  Python 3.11 · SQLAlchemy async · Pydantic v2         │
│  JWT Auth · OIDC · Alembic · Resend Email            │
│                                                       │
│  ┌─────────────────────────────────────────────┐     │
│  │            SWE Agent Runtime                │     │
│  │  ReAct Loop · ToolCallAgent · LLM Client    │     │
│  │  micro_compact / auto_compact               │     │
│  │  Draft Plan · Task Store · Approval Gate    │     │
│  └─────────────────────────────────────────────┘     │
│                                                       │
│  ┌─────────────────────────────────────────────┐     │
│  │           Sandbox Runtime                   │     │
│  │  Docker SDK · start-preview · Health Check  │     │
│  │  Preview Gateway (WebSocket Proxy)          │     │
│  └─────────────────────────────────────────────┘     │
└────────────────────┬────────────────────────────────┘
                     │ Docker API
┌────────────────────▼────────────────────────────────┐
│              Docker 沙箱容器 (per project)             │
│  Node 20 · Python 3 · uv · pnpm                      │
│  start-preview → 前端 dev server + 后端 server        │
└─────────────────────────────────────────────────────┘
```

### 技术选型

| 层级 | 技术 |
|------|------|
| **前端** | React 18 · TypeScript · Vite · Tailwind CSS · Radix UI · Monaco Editor |
| **后端** | Python 3.11 · FastAPI · SQLAlchemy (async) · Pydantic v2 · Alembic |
| **数据库** | SQLite（本地） / PostgreSQL（生产） |
| **AI** | OpenAI-compatible API（默认 MiniMax） · tiktoken · tenacity |
| **沙箱** | Docker · Node 20 · Python 3 · uv · pnpm |
| **认证** | JWT · OIDC（Auth0 / Google） · Resend |
| **部署** | Nginx · systemd · Linux |

---

## 快速开始

### 前置要求

- Python 3.11+
- Node.js 18+（pnpm）
- Docker
- OpenAI-compatible API Key（支持 MiniMax、OpenAI 等）

### 本地启动

**1. 克隆项目**

```bash
git clone https://github.com/JackybigW/atoms.git
cd atoms
```

**2. 配置环境变量**

```bash
cp app/backend/.env.example app/backend/.env
# 编辑 app/backend/.env，填入 AI API Key、JWT Secret 等
```

关键配置项：

```env
# AI 模型
APP_AI_BASE_URL=https://api.minimax.chat/v1
APP_AI_KEY=your-api-key
APP_AI_DEFAULT_MODEL=MiniMax-M2.7-highspeed

# 数据库
DATABASE_URL=sqlite:///./atoms.db

# JWT
JWT_SECRET_KEY=your-random-secret-64-chars

# OIDC 社交登录（可选）
OIDC_ISSUER_URL=https://your-auth0-domain.us.auth0.com
OIDC_CLIENT_ID=your-client-id
OIDC_CLIENT_SECRET=your-client-secret
```

**3. 启动后端**

```bash
cd app/backend
pip install -r requirements.txt
uvicorn main:app --host 0.0.0.0 --port 8000 --reload
```

**4. 启动前端**

```bash
cd app/frontend
pnpm install
pnpm dev
```

**5. 构建沙箱镜像**

```bash
cd docker/atoms-sandbox
docker build -t atoms-sandbox:latest .
```

访问 `http://localhost:3000` 开始使用。

---

## Agent 工作流

```
用户发送需求
     │
     ▼
Agent 生成 Draft Plan（流式输出）
     │
     ▼
用户审核 → Approve ✓
     │
     ▼
Agent 调用 todo_write 拆分任务
     │
     ▼
循环执行每个任务：
  bash / str_replace_editor / file_operators
     │
     ▼
沙箱启动 → 健康检查通过
     │
     ▼
preview_ready 事件 → 浏览器刷新预览
     │
     ▼
Agent 输出完成摘要
```

每一步状态均通过 WebSocket 实时推送到前端，用户全程可见。

---

## 项目结构

```
atoms/
├── app/
│   ├── backend/
│   │   ├── core/               # 配置、数据库、认证核心
│   │   ├── models/             # SQLAlchemy ORM 模型
│   │   ├── routers/            # FastAPI 路由
│   │   ├── services/           # 业务逻辑
│   │   ├── openmanus_runtime/  # SWE Agent 引擎
│   │   │   ├── agent/          # ReAct / ToolCall Agent
│   │   │   ├── tool/           # 工具集（bash、编辑器、任务等）
│   │   │   ├── llm.py          # LLM 客户端（流式 + token 管理）
│   │   │   └── schema.py       # 消息结构定义
│   │   └── tests/              # pytest 测试套件
│   └── frontend/
│       ├── src/
│       │   ├── components/     # UI 组件（ChatPanel、WorkspacePanel 等）
│       │   ├── pages/          # 页面（ProjectWorkspace、Login 等）
│       │   ├── contexts/       # React Context（Auth、Workspace）
│       │   ├── hooks/          # 自定义 Hooks（useDraftPlan 等）
│       │   └── lib/            # API 客户端、工具函数
│       └── package.json
└── docker/
    └── atoms-sandbox/
        ├── Dockerfile          # 沙箱基础镜像
        ├── start-preview       # 自动启动脚本
        └── start-dev           # 开发模式启动脚本
```

---

## 测试

```bash
# 后端测试
cd app/backend
pytest tests/ -v

# 前端测试
cd app/frontend
pnpm test
```

---

## 线上部署

**生产环境**：[https://jackybigwang.site](https://jackybigwang.site)

- 服务器：腾讯云 Linux，Python 3.11 + Node 20
- 流量入口：Cloudflare Tunnel（无需暴露公网 IP）→ Nginx → FastAPI / 前端静态文件
- 进程管理：systemd（`atoms-backend.service`）
- 数据库：PostgreSQL

## 本地部署

### 生产环境（Linux + systemd + Nginx）

**1. 构建前端**

```bash
cd app/frontend && pnpm build
```

**2. 配置 systemd 服务**

```ini
# /etc/systemd/system/atoms-backend.service
[Unit]
Description=Atoms Backend (FastAPI)
After=network.target

[Service]
User=ubuntu
WorkingDirectory=/home/ubuntu/atoms/app/backend
ExecStart=/path/to/python -m uvicorn main:app --host 0.0.0.0 --port 8001
Restart=always

[Install]
WantedBy=multi-user.target
```

**3. Nginx 反向代理**

```nginx
server {
    listen 80;
    server_name your-domain.com;

    location /api/ {
        proxy_pass http://127.0.0.1:8001;
        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection "upgrade";
    }

    location / {
        root /home/ubuntu/atoms/app/frontend/dist;
        try_files $uri /index.html;
    }
}
```

---

## 开发亮点

- **Agent Context 压缩**：三层策略（micro → auto → manual）保证长任务不因 context 溢出失败
- **沙箱智能识别**：`start-preview` 自动检测 React/Vue/Flask/FastAPI 项目并正确启动，兼容 npm/pnpm/uv/pip
- **Draft Plan 审批门**：`asyncio.Event` 实现异步等待，Agent 在用户未 Approve 前真正阻塞
- **流式渲染**：typewriter 动效 + shimmer Thinking 指示器，交互体验接近 ChatGPT
- **WebSocket 状态机**：前端 `sessionGenerationRef` 防止跨会话消息串台，stop 信号安全传播

---

## License

MIT
