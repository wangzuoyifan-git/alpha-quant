#!/usr/bin/env bash
# =============================================================
# Alpha量化 — 每季度财务数据更新脚本
# 建议在季报披露期后运行（4月/8月/10月/次年1月）
#
# crontab 配置示例（每月1日 09:00 检查，季报期自动更新）：
#   0 9 1 1,4,8,10 * /home/wz/alpha量化/scripts/update_quarterly.sh >> /home/wz/alpha量化/logs/update_quarterly.log 2>&1
# =============================================================

set -euo pipefail

PROJ_DIR="/home/wz/alpha量化"
PYTHON="${PROJ_DIR}/venv/bin/python3"
LOG_DIR="${PROJ_DIR}/logs"

mkdir -p "${LOG_DIR}"

echo "========================================"
echo "[$(date '+%Y-%m-%d %H:%M:%S')] 季度财务数据更新开始"
echo "========================================"

cd "${PROJ_DIR}"

echo ""
echo "[$(date '+%H:%M:%S')] 更新 fundamental 域（季频财务数据）..."
"${PYTHON}" -c "
from data.downloader_fundamental import FundamentalDownloader
FundamentalDownloader().update_db()
"

echo ""
echo "[$(date '+%Y-%m-%d %H:%M:%S')] 季度财务数据更新完成！"
echo "========================================"
