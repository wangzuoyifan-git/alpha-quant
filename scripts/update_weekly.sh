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
#   - events 域已在每日脚本中更新；港股财务/A股 PCF 数据量大，放周更
#   - 席位字典（游资/量化）会衰减，每周重建一次：先导入硬标签 xlsx（若有更新），
#     再基于最新 top_inst 行为聚类重估
#   - fund 域（ETF列表/映射）周更一次（ETF 份额/净值按需用 update-fund --codes 拉取）
# =============================================================

set -uo pipefail

PROJ_DIR="/home/wz/alpha-quant"
PYTHON="${PROJ_DIR}/venv/bin/python3"
LOG_DIR="${PROJ_DIR}/logs"
LOG_FILE="${LOG_DIR}/update_weekly.log"

mkdir -p "${LOG_DIR}"
cd "${PROJ_DIR}"

exec > >(tee -a "${LOG_FILE}") 2>&1

# 防止重复运行
exec 9>"${LOG_DIR}/update_weekly.lock"
if ! flock -n 9; then
  echo "[$(date '+%Y-%m-%d %H:%M:%S')] 已有 update_weekly 实例在运行，本次退出"
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
echo "[$(date '+%Y-%m-%d %H:%M:%S')] 每周更新开始"
echo "========================================"

# 1. meta 域（行业分类 + 申万指数 + 交易日历）
run_step "[1/7] 更新 meta 域" "${PYTHON}" -c "
from data.downloader_meta import MetaDownloader
MetaDownloader().update_db()
"

# 2. macro 域（月频数据 + 汇率/境外日频兜底）
run_step "[2/7] 更新 macro 域" "${PYTHON}" -c "
from data.downloader_macro import MacroDownloader
MacroDownloader().update_db()
"

# 3. 港股财务（三大报表 + 成长/杜邦指标，逐只拉取；约 1 小时）
run_step "[3/7] 更新港股财务" "${PYTHON}" main.py update-hk-financials

# 4. A 股 PCF 补充（百度市现率，全市场约 1-1.5 小时）
run_step "[4/7] 补充 A 股 PCF" "${PYTHON}" main.py update-cn-pcf

# 5. 席位字典硬标签导入（外部 xlsx；文件缺失则跳过，不阻断）
run_step "[5/7] 导入席位字典硬标签（xlsx）" "${PYTHON}" main.py seat-dict import

# 6. 席位字典重建（硬标签 + 行为聚类软标签；游资/量化识别，约 15-40s）
run_step "[6/7] 重建席位字典（游资/量化）" "${PYTHON}" main.py seat-dict build --window 6 --min-appearances 10

# 7. fund 域（ETF 列表 + ETF→跟踪指数映射；供板块/ETF 画像）
run_step "[7/7] 更新 fund 域（ETF列表+映射）" "${PYTHON}" main.py update-fund

echo ""
echo "========================================"
if [ "${#FAILED[@]}" -gt 0 ]; then
  echo "[$(date '+%Y-%m-%d %H:%M:%S')] 每周更新完成，但有失败步骤: ${FAILED[*]}"
  echo "========================================"
  exit 1
fi
echo "[$(date '+%Y-%m-%d %H:%M:%S')] 每周更新全部完成！"
echo "========================================"
