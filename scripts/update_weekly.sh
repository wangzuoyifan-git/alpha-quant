#!/usr/bin/env bash
# =============================================================
# Alpha量化 — 每周数据更新脚本
# 建议在周六运行，更新行业分类、申万指数等低频数据
#
# crontab 配置示例（每周六 10:00 运行）：
#   0 10 * * 6 /home/wz/alpha量化/scripts/update_weekly.sh >> /home/wz/alpha量化/logs/update_weekly.log 2>&1
# =============================================================

set -euo pipefail

PROJ_DIR="/home/wz/alpha量化"
PYTHON="${PROJ_DIR}/venv/bin/python3"
LOG_DIR="${PROJ_DIR}/logs"

mkdir -p "${LOG_DIR}"

echo "========================================"
echo "[$(date '+%Y-%m-%d %H:%M:%S')] 每周更新开始"
echo "========================================"

cd "${PROJ_DIR}"

# 1. 更新 meta 域（行业分类 + 申万指数 + 交易日历）
echo ""
echo "[$(date '+%H:%M:%S')] [1/2] 更新 meta 域..."
"${PYTHON}" -c "
from data.downloader_meta import MetaDownloader
dl = MetaDownloader()
dl.update_db()
"

# 2. 更新 macro 域（月频数据 + 汇率/境外日频兜底）
echo ""
echo "[$(date '+%H:%M:%S')] [2/2] 更新 macro 域..."
"${PYTHON}" -c "
from data.downloader_macro import MacroDownloader
MacroDownloader().update_db()
"

echo ""
echo "[$(date '+%Y-%m-%d %H:%M:%S')] 每周更新完成！"
echo "========================================"
