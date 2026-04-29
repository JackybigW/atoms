#!/bin/bash
# =============================================================================
# deploy.sh — Vibe Coding Studio 一键部署脚本
# 用法: ./deploy.sh "feat: 你的提交信息"
#
# 做什么：
#   1. 把本地改动 commit 并 push 到 GitHub
#   2. 用 rsync 从本地直接推文件到服务器，再把服务器 git 指到 GitHub 同一 commit
#   3. 应用服务器 Clash 出站策略
#   4. 在服务器上重建前端静态文件、重启后端服务
#
# 背景/踩坑记录：
#   - 服务器是 OpenCloudOS (RHEL)，用 dnf 不是 apt
#   - 服务器不能稳定访问 GitHub，所以文件同步仍用 rsync；git 只 fetch 已推送的 commit 元数据
#   - .env 文件含密钥，不进 git / 不被 rsync 覆盖（服务器有独立的 .env）
#   - nginx 要用 $http_host 而不是 $host，才能保留端口号给 Auth0 回调 URL
#   - Auth0 token 端点是 /oauth/token（不是 /token），logout 是 /v2/logout
#   - PostgreSQL 连接串里的 @ 需要 URL encode 成 %40
#   - systemd EnvironmentFile 里含空格的值必须加引号（如 OIDC_SCOPE="openid email profile"）
#   - vite build 不支持 --silent 参数，用 2>&1 | tail -3 代替
#   - 服务器 git 必须 reset 到 GitHub main 的同一 commit，避免三端 hash 漂移
# =============================================================================

set -e  # 任意命令失败立即退出

# ---------- 配置 ----------
SERVER="root@129.211.217.58"
SERVER_PATH="/home/ubuntu/atoms"
FRONTEND_PATH="$SERVER_PATH/app/frontend"
LOCAL_PATH="/Users/jackywang/Documents/atoms"
GITHUB_REPO="https://github.com/JackybigW/vibe-coding-studio"

# 如果没传参数，默认提交信息是 "chore: update"
COMMIT_MSG="${1:-"chore: update"}"

# ---------- 工具函数 ----------
log()  { echo ""; echo "==> $1"; }
ok()   { echo "    ✓ $1"; }
fail() { echo "    ✗ $1"; exit 1; }

# =============================================================================
# 前置检查：确保本地环境和服务器可达
# =============================================================================
log "[0/4] 前置检查..."

# 确认在正确的 git 仓库里
if ! git rev-parse --git-dir > /dev/null 2>&1; then
  fail "当前目录不是 git 仓库，请在项目根目录运行"
fi

# 确认服务器 SSH 可达（timeout 5秒）
if ! ssh -o ConnectTimeout=5 -o BatchMode=yes "$SERVER" "exit" 2>/dev/null; then
  fail "SSH 连接服务器失败：$SERVER（检查网络或密钥）"
fi

ok "git 仓库正常"
ok "服务器 SSH 可达"

# =============================================================================
# 第一步：本地 commit → push 到 GitHub
# =============================================================================
log "[1/4] 提交并推送到 GitHub..."

git add -A

# 检查是否有内容要提交
if git diff --cached --quiet; then
  echo "    (没有新改动，跳过 commit)"
else
  git commit -m "$COMMIT_MSG"
  ok "已提交: $COMMIT_MSG"
fi

git push origin main
ok "GitHub 推送成功 → $GITHUB_REPO"
echo "    最新 commit: $(git log --oneline -1)"
LOCAL_COMMIT="$(git rev-parse HEAD)"

# =============================================================================
# 第二步：rsync 同步文件到服务器，并对齐服务器 git commit
#
# 为什么用 rsync 而不是 git pull：
#   服务器访问 GitHub 速度不稳定，rsync 走 SSH 走本地网络，快且可靠
#
# 排除项说明：
#   .git        — 服务器有自己独立的 git 历史，不覆盖
#   node_modules — 服务器有自己安装的依赖，rsync 传这个太慢
#   __pycache__  — Python 编译缓存，各平台不通用
#   .env        — 服务器有独立的 .env（含 PostgreSQL URL 和密钥），不能覆盖
#   dist        — 前端构建产物，部署时会在服务器上重新构建
#   logs        — 服务器运行日志，不同步
#   *.db        — 本地 SQLite 数据库，服务器用 PostgreSQL
# =============================================================================
log "[2/4] rsync 同步文件到服务器..."

rsync -az --delete \
  --exclude='.git' \
  --exclude='node_modules' \
  --exclude='__pycache__' \
  --exclude='*.pyc' \
  --exclude='.env' \
  --exclude='dist' \
  --exclude='logs' \
  --exclude='*.db' \
  "$LOCAL_PATH/" \
  "$SERVER:$SERVER_PATH/"

ok "文件同步完成"

ssh "$SERVER" "
  git config --global user.email 'jackywang@vibe-coding.studio' 2>/dev/null
  git config --global user.name 'Jacky Wang' 2>/dev/null
  git config --global --add safe.directory $SERVER_PATH 2>/dev/null
  cd $SERVER_PATH
  http_proxy=http://127.0.0.1:7890 https_proxy=http://127.0.0.1:7890 git fetch origin main
  git reset --hard '$LOCAL_COMMIT'
"
ok "服务器 git 已对齐到 GitHub commit: $LOCAL_COMMIT"

# =============================================================================
# 第三步：应用服务器 Clash 出站策略
# =============================================================================
log "[3/4] 应用服务器 Clash 出站策略..."

ssh "$SERVER" "cd $SERVER_PATH && bash scripts/configure-server-clash-policy.sh"
ok "Clash 出站策略已应用"

# =============================================================================
# 第四步：服务器重建前端 + 重启后端
#
# 前端：pnpm run build 把 React/TS 编译成静态文件到 dist/，由 nginx 直接提供
# 后端：systemctl restart 重启 FastAPI uvicorn 进程，让新代码生效
# =============================================================================
log "[4/4] 服务器重建前端 + 重启后端..."

ssh "$SERVER" bash << 'REMOTE'
  set -e

  # 重建前端（vite build）
  # 注意：vite 不支持 --silent，用 tail -3 只显示最后几行
  echo "  → 构建前端..."
  cd /home/ubuntu/atoms/app/frontend
  pnpm run build 2>&1 | tail -4

  # 重启后端 systemd 服务
  echo "  → 重启后端..."
  systemctl restart atoms-backend
  sleep 2

  # 检查后端是否成功启动
  STATUS=$(systemctl is-active atoms-backend)
  if [ "$STATUS" = "active" ]; then
    echo "  → 后端状态: active ✓"
  else
    echo "  → 后端状态: $STATUS ✗"
    systemctl status atoms-backend --no-pager | tail -10
    exit 1
  fi

  # 快速 health check；后端 active 后端口可能还需要一两秒才接受连接。
  HEALTH=""
  for attempt in 1 2 3 4 5 6 7 8 9 10; do
    if HEALTH=$(curl -fsS http://localhost:8001/health 2>/dev/null); then
      break
    fi
    sleep 1
  done
  if [ -z "$HEALTH" ]; then
    echo "  → health check failed ✗"
    journalctl -u atoms-backend --no-pager -n 20
    exit 1
  fi
  echo "  → health check: $HEALTH"
REMOTE

# =============================================================================
# 完成
# =============================================================================
echo ""
echo "============================================="
echo "  部署完成！"
echo "  commit : $(git log --oneline -1)"
echo "  服务器 : http://129.211.217.58:8080"
echo "  GitHub : $GITHUB_REPO"
echo "============================================="
