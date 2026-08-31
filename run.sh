#!/usr/bin/env bash
# BoundlessKG 一键启动脚本（WSL Ubuntu）
# 用法：
#   ./run.sh                  启动 API 服务（默认）
#   ./run.sh serve            同上
#   ./run.sh generate "主题"  生成知识图谱
#   ./run.sh validate         校验图谱质量
#   ./run.sh report           输出质量评分
#   ./run.sh cards            查看提示词卡片
#   ./run.sh install          重新安装依赖
#   ./run.sh shell            仅激活 venv 进入交互式 shell

set -e

PROJECT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$PROJECT_DIR"

VENV_DIR="$PROJECT_DIR/.venv"
PYTHON_BIN="$VENV_DIR/bin/python"
PIP_BIN="$VENV_DIR/bin/pip"

# ---------- 颜色输出 ----------
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m'

log()  { echo -e "${GREEN}[run.sh]${NC} $*"; }
warn() { echo -e "${YELLOW}[run.sh]${NC} $*"; }
err()  { echo -e "${RED}[run.sh]${NC} $*" >&2; }

# ---------- 检查 python3 ----------
if ! command -v python3 >/dev/null 2>&1; then
    err "找不到 python3，请先安装：sudo apt install python3 python3-venv python3-pip"
    exit 1
fi

# ---------- 特殊命令：仅进入 venv shell ----------
if [ "${1:-}" = "shell" ]; then
    if [ ! -d "$VENV_DIR" ]; then
        err "虚拟环境不存在，请先执行：./run.sh install"
        exit 1
    fi
    # shellcheck disable=SC1091
    source "$VENV_DIR/bin/activate"
    log "已激活 venv。输入 deactivate 退出。"
    exec "${SHELL:-bash}"
fi

# ---------- 安装/重建 venv ----------
ensure_venv() {
    if [ ! -x "$PYTHON_BIN" ]; then
        warn "虚拟环境不存在，开始创建 .venv ..."
        if ! python3 -m venv "$VENV_DIR" 2>/dev/null; then
            err "创建 venv 失败，尝试安装系统依赖："
            err "  sudo apt install python3-venv python3-full python3-pip"
            exit 1
        fi
        log "venv 创建成功"
    fi
}

# ---------- 检查并安装依赖 ----------
ensure_deps() {
    # 用 import 探测关键依赖，避免每次都跑 pip
    if "$PYTHON_BIN" -c "import pydantic, fastapi" 2>/dev/null; then
        return 0
    fi
    warn "依赖未安装，开始安装（首次约 1-3 分钟）..."
    "$PIP_BIN" install --upgrade pip >/dev/null
    "$PIP_BIN" install -e "$PROJECT_DIR[dev,deepagents]"
    log "依赖安装完成"
}

# ---------- 安装命令 ----------
if [ "${1:-}" = "install" ]; then
    ensure_venv
    # shellcheck disable=SC1091
    source "$VENV_DIR/bin/activate"
    "$PIP_BIN" install --upgrade pip
    "$PIP_BIN" install -e "$PROJECT_DIR[dev,deepagents]"
    log "全部依赖安装完毕"
    exit 0
fi

# ---------- 主流程 ----------
ensure_venv
ensure_deps

# 激活 venv 后转发所有参数给 src
# shellcheck disable=SC1091
source "$VENV_DIR/bin/activate"

CMD="${1:-serve}"
if [ "$CMD" != "serve" ] && [ "$CMD" != "generate" ] && [ "$CMD" != "validate" ] \
   && [ "$CMD" != "report" ] && [ "$CMD" != "cards" ]; then
    err "未知子命令：$CMD"
    err "支持：serve / generate / validate / report / cards / install / shell"
    exit 1
fi

log "启动：python -m src $*"
exec python -m src "$@"