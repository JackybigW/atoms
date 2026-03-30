#!/bin/bash
# deploy.sh - 从本地同时推送到 GitHub + 服务器
# 用法: ./deploy.sh "feat: 你的提交信息"

set -e

SERVER="root@129.211.217.58"
SERVER_PATH="/home/ubuntu/atoms"
REMOTE_APP_PATH="$SERVER_PATH/app"

MSG="${1:-"chore: update"}"

echo "==> [1/3] 提交并推送到 GitHub..."
git add -A
git commit -m "$MSG" || echo "(nothing to commit)"
git push origin main
echo "    GitHub OK"

echo "==> [2/3] 同步代码到服务器..."
rsync -az --delete \
  --exclude='.git' \
  --exclude='node_modules' \
  --exclude='__pycache__' \
  --exclude='*.pyc' \
  --exclude='.env' \
  --exclude='dist' \
  --exclude='logs' \
  --exclude='*.db' \
  /Users/jackywang/Documents/atoms/ \
  $SERVER:$SERVER_PATH/
echo "    rsync OK"

echo "==> [3/3] 服务器重建前端 + 重启后端..."
ssh $SERVER "
  cd $REMOTE_APP_PATH/frontend && pnpm run build 2>&1 | tail -3 &&
  systemctl restart atoms-backend &&
  sleep 2 && systemctl is-active atoms-backend
"
echo ""
echo "部署完成！"
echo "  服务器: http://129.211.217.58:8080"
echo "  GitHub: https://github.com/JackybigW/atoms"
