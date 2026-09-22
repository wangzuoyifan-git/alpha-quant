#!/usr/bin/env bash
# =============================================================
# Alpha量化 — 每周数据更新脚本
# 建议在周六运行，更新行业分类、申万指数等低频数据
#
# crontab 配置示例（每周六 10:00 运行）：
#   0 10 * * 6 /home/wz/alpha-quant/scripts/update_weekly.sh
# 脚本内部会 tee 到 logs/update_weekly.log，crontab 无需再重定向
#
# 说明：
#   - 单步失败不中断后续步骤，末尾汇总；有失败时退出码为 1
#   - events 域已在每日脚本中更新（全市场约 1 小时），此处不再重复
# =============================================================

set -uo pipefail

PROJ_DIR="/home/wz/alpha-quant"
PYTHON="${PROJ_DIR}/venv/bin/python3"
LOG_DIR="${PROJ_DIR}/logs"
LOG_FILE="${LOG_DIR}/update_weekly.log"

mkdir -p "${LOG_DIR}"
cd "${PROJ_DIR}"

exec > >(tee -a "${LOG_FILE}") 2>&1

FAILED=()

run_step() {
  local name="$1"; shift
  echo ""
  echo "[$(date '+%H:%M:%S')] ${name}..."
  if "$@"; then
    echo "[$(date '+%H:%M:%S')] ${name} 完成"
  else
    local rc=$?
    echo "[$(date '+%H:%M:%S')] [警告] ${name} 失败（exit=${rc}），继续后续步骤"
    FAILED+=("${name}")
  fi
}

echo "========================================"
echo "[$(date '+%Y-%m-%d %H:%M:%S')] 每周更新开始"
echo "========================================"

# 1. meta 域（行业分类 + 申万指数 + 交易日历）
run_step "[1/2] 更新 meta 域" "${PYTHON}" -c "
from data.downloader_meta import MetaDownloader
MetaDownloader().update_db()
"

# 2. macro 域（月频数据 + 汇率/境外日频兜底）
run_step "[2/2] 更新 macro 域" "${PYTHON}" -c "
from data.downloader_macro import MacroDownloader
MacroDownloader().update_db()
"

echo ""
echo "========================================"
if [ "${#FAILED[@]}" -gt 0 ]; then
  echo "[$(date '+%Y-%m-%d %H:%M:%S')] 每周更新完成，但有失败步骤: ${FAILED[*]}"
  echo "========================================"
  exit 1
fi
echo "[$(date '+%Y-%m-%d %H:%M:%S')] 每周更新全部完成！"
echo "========================================"
