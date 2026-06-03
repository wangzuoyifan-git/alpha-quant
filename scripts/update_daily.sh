#!/usr/bin/env bash
# =============================================================
# Alpha量化 — 每日数据更新脚本
# 建议通过 crontab 在收盘后（15:30~16:00）运行
#
# crontab 配置示例（每个交易日 15:35 运行）：
#   35 15 * * 1-5 /home/wz/alpha量化/scripts/update_daily.sh >> /home/wz/alpha量化/logs/update_daily.log 2>&1
#
# 手动运行：
#   bash /home/wz/alpha量化/scripts/update_daily.sh
# =============================================================

set -euo pipefail

PROJ_DIR="/home/wz/alpha量化"
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
echo "[$(date '+%H:%M:%S')] [1/4] 更新 K 线数据..."
"${PYTHON}" main.py update --types stock index etf

# 2. 更新 meta 域（行业分类每周一更新，其他每日）
echo ""
echo "[$(date '+%H:%M:%S')] [2/4] 更新 meta 域..."
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
echo "[$(date '+%H:%M:%S')] [3/5] 更新 factor 域..."
"${PYTHON}" -c "
from data.downloader_factor import FactorDownloader
dl = FactorDownloader()
dl.update_db()
"

# 4. 更新 tushare 域（融资融券等每日数据）
echo ""
echo "[$(date '+%H:%M:%S')] [4/5] 更新 tushare 域..."
"${PYTHON}" main.py update-tushare

# 5. 扫描信号
echo ""
echo "[$(date '+%H:%M:%S')] [5/5] 扫描信号..."
"${PYTHON}" main.py scan --types stock

echo ""
echo "[$(date '+%Y-%m-%d %H:%M:%S')] 每日更新完成！"
echo "========================================"
