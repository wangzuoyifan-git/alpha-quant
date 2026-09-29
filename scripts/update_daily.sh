#!/usr/bin/env bash
# =============================================================
# Alpha量化 — 每日数据更新脚本
# 建议通过 crontab 在收盘后（17:30 左右）运行
# （A股 EOD 数据 16:00 后落定；港股 16:00 收盘，17:30 可拿到当日最终值）
#
# crontab 配置示例（每个交易日 17:30 运行）：
#   30 17 * * 1-5 /home/wz/alpha-quant/scripts/update_daily.sh
# 脚本内部会 tee 到 logs/update_daily.log，crontab 无需再重定向
#
# 手动运行：
#   bash /home/wz/alpha-quant/scripts/update_daily.sh
#
# 说明：
#   - 单步失败不中断后续步骤，末尾汇总；有失败时退出码为 1
#   - 首次/长期未更新时，K 线补数据可能耗时较长（历史缺口越大越久）
#   - events 域（E2 企业事件：减持/增持/质押/分红/回购）为全市场逐只拉取，约 1.5 小时
#   - 港股估值（PE/PB/PCF）逐只抓取，港股通约 30 分钟
#   - 港股资金流/分红事件逐只抓取，各约 10-15 分钟
# =============================================================

set -uo pipefail

PROJ_DIR="/home/wz/alpha-quant"
PYTHON="${PROJ_DIR}/venv/bin/python3"
LOG_DIR="${PROJ_DIR}/logs"
LOG_FILE="${LOG_DIR}/update_daily.log"

mkdir -p "${LOG_DIR}"
cd "${PROJ_DIR}"

# 输出同时写入终端与日志
exec > >(tee -a "${LOG_FILE}") 2>&1

# 防止重复运行（上一轮未结束时新的 cron/手动执行直接退出）
exec 9>"${LOG_DIR}/update_daily.lock"
if ! flock -n 9; then
  echo "[$(date '+%Y-%m-%d %H:%M:%S')] 已有 update_daily 实例在运行，本次退出"
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

# meta：行业分类每周一全量刷新，其余每日增量
update_meta() {
  "${PYTHON}" -c "
from data.downloader_meta import MetaDownloader
import datetime
today = datetime.datetime.now().weekday()
dl = MetaDownloader()
if today == 0:  # 周一
    dl.init_db()
else:
    dl.update_db()
"
}

# 港股步骤：16:15 前跳过（避免写入盘中残缺 bar）
run_hk_step() {
  local name="$1"; shift
  if [ "$(date '+%H%M')" -ge 1615 ]; then
    run_step "$name" "$@"
  else
    echo ""
    echo "[$(date '+%H:%M:%S')] [跳过] ${name}：当前 $(date '+%H:%M') 早于 16:15（港股未收盘）"
  fi
}

echo "========================================"
echo "[$(date '+%Y-%m-%d %H:%M:%S')] 每日更新开始"
echo "========================================"

# 1. A 股 K 线（stock + index + etf，Tushare 主源）
run_step "[1/10] 更新 A 股 K 线数据" \
  "${PYTHON}" main.py update --types stock index etf

# 2. meta 域（行业分类 + 申万指数 + 交易日历）
run_step "[2/10] 更新 meta 域" update_meta

# 3. moneyflow 域（个股/龙虎榜汇总/龙虎榜席位明细/大宗交易/两融/行业/大盘/北向）
#    注：席位明细 top_inst 与大宗交易 block_trade 已包含在 update-moneyflow 默认类型中
#    北向个股持股 hk_hold 为季度披露、按代码逐只拉取，不在此每日脚本，见季度脚本
run_step "[3/10] 更新 moneyflow 域" \
  "${PYTHON}" main.py update-moneyflow

# 4. events 域（E1 日历 + E2 减持/增持/质押/分红/回购，供风控过滤与产业资本画像；全市场约 1.5 小时）
run_step "[4/10] 更新 events 域" \
  "${PYTHON}" main.py update-events

# 5. macro 域（国内月频 + 汇率/境外日频）
run_step "[5/10] 更新 macro 域" \
  "${PYTHON}" main.py update-macro

# 6. 港股 K 线（默认仅港股通标的）
run_hk_step "[6/10] 更新港股 K 线（港股通）" \
  "${PYTHON}" main.py update-hk

# 7. 港股 PE/PB/PCF 估值（百度股市通，合并进 K 线；约 30 分钟）
run_hk_step "[7/10] 更新港股估值（PE/PB/PCF）" \
  "${PYTHON}" main.py update-hk-valuation

# 8. 港股资金流（东财，滚动约 100 个交易日；约 10-15 分钟）
run_hk_step "[8/10] 更新港股资金流" \
  "${PYTHON}" main.py update-hk-moneyflow

# 9. 港股分红事件（events E2；约 10-15 分钟）
run_hk_step "[9/10] 更新港股分红事件" \
  "${PYTHON}" main.py update-hk-events

# 10. factor 域（台阶二前为空，空跑占位）
run_step "[10/10] 更新 factor 域" "${PYTHON}" -c "
from data.downloader_factor import FactorDownloader
FactorDownloader().update_db()
"

# 11. 扫描信号（默认关闭，需要时取消注释）
#run_step "[11/11] 扫描信号" "${PYTHON}" main.py scan --types stock

echo ""
echo "========================================"
if [ "${#FAILED[@]}" -gt 0 ]; then
  echo "[$(date '+%Y-%m-%d %H:%M:%S')] 每日更新完成，但有失败步骤: ${FAILED[*]}"
  echo "========================================"
  exit 1
fi
echo "[$(date '+%Y-%m-%d %H:%M:%S')] 每日更新全部完成！"
echo "========================================"
