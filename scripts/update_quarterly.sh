#!/usr/bin/env bash
# =============================================================
# Alpha量化 — 每季度财务数据更新脚本（A股 fundamental 域）
# 建议在季报披露期后运行（4月/8月/10月/次年1月）
#
# crontab 配置示例（每月1日 09:00 检查，季报期自动更新）：
#   0 9 1 1,4,8,10 * /home/wz/alpha-quant/scripts/update_quarterly.sh
# 脚本内部会 tee 到 logs/update_quarterly.log，crontab 无需再重定向
#
# 说明：
#   - 单步失败不中断后续步骤，末尾汇总；有失败时退出码为 1
#   - 港股财务在每周脚本（update-hk-financials）中更新
# =============================================================

set -uo pipefail

PROJ_DIR="/home/wz/alpha-quant"
PYTHON="${PROJ_DIR}/venv/bin/python3"
LOG_DIR="${PROJ_DIR}/logs"
LOG_FILE="${LOG_DIR}/update_quarterly.log"

mkdir -p "${LOG_DIR}"
cd "${PROJ_DIR}"

exec > >(tee -a "${LOG_FILE}") 2>&1

# 防止重复运行
exec 9>"${LOG_DIR}/update_quarterly.lock"
if ! flock -n 9; then
  echo "[$(date '+%Y-%m-%d %H:%M:%S')] 已有 update_quarterly 实例在运行，本次退出"
  exit 1
fi

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
echo "[$(date '+%Y-%m-%d %H:%M:%S')] 季度财务数据更新开始"
echo "========================================"

# A 股季频财务（profit/balance/cashflow/growth/dupont）
run_step "[1/1] 更新 A 股 fundamental 域" "${PYTHON}" -c "
from data.downloader_fundamental import FundamentalDownloader
FundamentalDownloader().update_db()
"

echo ""
echo "========================================"
if [ "${#FAILED[@]}" -gt 0 ]; then
  echo "[$(date '+%Y-%m-%d %H:%M:%S')] 季度更新完成，但有失败步骤: ${FAILED[*]}"
  echo "========================================"
  exit 1
fi
echo "[$(date '+%Y-%m-%d %H:%M:%S')] 季度财务数据更新全部完成！"
echo "========================================"
