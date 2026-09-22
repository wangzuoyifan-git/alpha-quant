#!/usr/bin/env bash
# =============================================================
# Alpha量化 — 每日数据更新脚本
# 建议通过 crontab 在收盘后（17:30 左右）运行
# （A股 EOD 数据 16:00 后落定；港股 16:00 收盘，17:30 可拿到当日最终值）
#
# crontab 配置示例（每个交易日 17:30 运行）：
#   30 17 * * 1-5 /home/wz/alpha-quant/scripts/update_daily.sh >> /home/wz/alpha-quant/logs/update_daily.log 2>&1
#
# 手动运行：
#   bash /home/wz/alpha-quant/scripts/update_daily.sh
# =============================================================

set -euo pipefail

PROJ_DIR="/home/wz/alpha-quant"
PYTHON="${PROJ_DIR}/venv/bin/python3"
LOG_DIR="${PROJ_DIR}/logs"
LOG_FILE="${LOG_DIR}/update_daily.log"

mkdir -p "${LOG_DIR}"

echo "========================================"
echo "[$(date '+%Y-%m-%d %H:%M:%S')] 每日更新开始"
echo "========================================"

cd "${PROJ_DIR}"

# 1. 更新 K 线数据（stock + index + etf）
echo ""
echo "[$(date '+%H:%M:%S')] [1/6] 更新 K 线数据..."
"${PYTHON}" main.py update --types stock index etf

# 2. 更新 meta 域（行业分类每周一更新，其他每日）
echo ""
echo "[$(date '+%H:%M:%S')] [2/6] 更新 meta 域..."
"${PYTHON}" -c "
from data.downloader_meta import MetaDownloader
import datetime
# 只在周一更新行业分类（避免频繁请求）
today = datetime.datetime.now().weekday()
dl = MetaDownloader()
if today == 0:  # 周一
    dl.init_db()
else:
    dl.update_db()
"

# 3. 更新 factor 域（每日：北向资金 + 行业资金流向）
echo ""
echo "[$(date '+%H:%M:%S')] [3/6] 更新 factor 域..."
"${PYTHON}" -c "
from data.downloader_factor import FactorDownloader
dl = FactorDownloader()
dl.update_db()
"

# 4. 更新 tushare 域（融资融券等每日数据）
echo ""
echo "[$(date '+%H:%M:%S')] [4/6] 更新 tushare 域..."
"${PYTHON}" main.py update-tushare

# 5. 更新 macro 域（国内月频 + 汇率/境外日频）
# 注：17:30 前运行时港股/境外当日 bar 可能未收，次日刷新会自动覆盖修正
echo ""
echo "[$(date '+%H:%M:%S')] [5/6] 更新 macro 域..."
"${PYTHON}" main.py update-macro

# 6. 更新港股 K 线（默认仅港股通标的；港股 16:00 收盘，16:15 前跳过避免写入盘中残缺 bar）
echo ""
if [ "$(date '+%H%M')" -ge 1615 ]; then
  echo "[$(date '+%H:%M:%S')] [6/6] 更新港股 K 线（港股通）..."
  "${PYTHON}" main.py update-hk
else
  echo "[$(date '+%H:%M:%S')] [6/6] 跳过港股更新：当前 $(date '+%H:%M') 早于 16:15（港股未收盘）"
  echo "        如需更新请稍后单独执行: ${PYTHON} main.py update-hk"
fi

# 7. 扫描信号
#echo ""
#echo "[$(date '+%H:%M:%S')] [7/7] 扫描信号..."
#"${PYTHON}" main.py scan --types stock

echo ""
echo "[$(date '+%Y-%m-%d %H:%M:%S')] 每日更新完成！"
echo "========================================"
