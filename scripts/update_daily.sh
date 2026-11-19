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
#   - 开始前先做 Tushare 连通性预检，失败直接终止，避免接口抖动导致空跑假成功
#   - 若已有实例在跑（如手动更新），默认最多等待 3 小时再执行，避免定时任务被跳过；
#     等待超时退出码 75（可用环境变量 UPDATE_LOCK_WAIT 调整秒数，0=不等待）
#   - 单步失败不中断后续步骤，末尾汇总；有失败或被跳过的步骤时退出码为 1
#   - 结束前核对 market/stock、moneyflow/stock 是否达到最近交易日，不达标计入失败
#     （退出码 1）；market/hk 因港A节假日不同仅告警，不阻断
#   - 目标交易日按本次启动时刻快照：16:00 前启动只更新到上一交易日；即使任务跑到
#     收盘后也不改目标，当日数据由下一次运行补。K线/资金流共用该快照。
#   - 港股步骤仅在盘中 09:30–16:15 跳过（数据未落定）；收盘后/开盘前（含跨夜到凌晨）正常执行
#   - 首次/长期未更新时，K 线补数据可能耗时较长（历史缺口越大越久）
#   - events 域（E2 企业事件：减持/增持/质押/分红/回购）全市场按日期批量，约 1-2 分钟
#   - moneyflow 域（个股资金流/两融）按交易日批量，约 1 分钟；其余按日/按代码
#   - 港股估值（PE/PB/PCF）逐只抓取，港股通约 30 分钟
#   - 港股资金流/分红事件逐只抓取，各约 15-25 分钟（资金流按最小间隔限速，避免东财限流封禁）
# =============================================================

set -uo pipefail

PROJ_DIR="/home/wz/alpha-quant"
PYTHON="${PROJ_DIR}/venv/bin/python3"
LOG_DIR="${PROJ_DIR}/logs"
LOG_FILE="${LOG_DIR}/update_daily.log"

# 记录本次运行日期/时刻（跨夜运行也以启动时点为准，供收尾校验使用）
RUN_DATE=$(date '+%Y-%m-%d')
RUN_HM=$(date '+%H%M')
export RUN_DATE RUN_HM

mkdir -p "${LOG_DIR}"
cd "${PROJ_DIR}"

# 输出同时写入终端与日志
exec > >(tee -a "${LOG_FILE}") 2>&1

# 防止重复运行：若已有实例（例如手动更新）在跑，默认等待其结束再执行，
# 避免 17:30 定时任务因手动长任务占用锁被整天跳过。
# 等待上限默认 3 小时；设为 0 则退回"立即退出"行为。
LOCK_WAIT=${UPDATE_LOCK_WAIT:-10800}
exec 9>"${LOG_DIR}/update_daily.lock"
if ! flock -n 9; then
  if [ "${LOCK_WAIT}" -le 0 ]; then
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] 已有 update_daily 实例在运行，本次退出"
    exit 75
  fi
  echo "[$(date '+%Y-%m-%d %H:%M:%S')] 已有 update_daily 实例在运行，最多等待 ${LOCK_WAIT}s..."
  waited=0
  until flock -n 9; do
    if [ "${waited}" -ge "${LOCK_WAIT}" ]; then
      echo "[$(date '+%Y-%m-%d %H:%M:%S')] 等待锁超时（${LOCK_WAIT}s），本次退出"
      exit 75
    fi
    step=30
    remaining=$((LOCK_WAIT - waited))
    [ "${remaining}" -lt "${step}" ] && step=${remaining}
    sleep "${step}"
    waited=$((waited + step))
  done
  echo "[$(date '+%Y-%m-%d %H:%M:%S')] 已获取锁，继续执行（RUN_DATE=${RUN_DATE}）"
fi

FAILED=()
SKIPPED=()

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
# 注意：这里是独立 python -c 进程，不会经过 main.py，需显式加载 .env，
# 否则 TUSHARE_TOKEN 缺失会每天跳过申万三级分类。
update_meta() {
  "${PYTHON}" -c "
from dotenv import load_dotenv
load_dotenv('.env')
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

# 港股步骤：仅"当日盘中/收盘前"（09:30–16:15）跳过，避免写入盘中残缺 bar。
# 16:15 后（数据落定）以及次日 09:30 前（如整点到凌晨才轮到港股）市场已收盘，
# 上一交易日的数据是最终值，可安全更新——此前用"早于 16:15 即跳过"会把跨夜
# 到凌晨的运行误判为盘中，导致港股整天数据被漏更。
run_hk_step() {
  local name="$1"; shift
  local hm; hm=$(date '+%H%M')
  if [ "${hm}" -ge 0930 ] && [ "${hm}" -lt 1615 ]; then
    echo ""
    echo "[$(date '+%H:%M:%S')] [跳过] ${name}：港股盘中（09:30–16:15 数据未落定），本次跳过"
    SKIPPED+=("${name}")
  else
    run_step "$name" "$@"
  fi
}

echo "========================================"
echo "[$(date '+%Y-%m-%d %H:%M:%S')] 每日更新开始"
echo "========================================"

# 0. Tushare 连通性预检：tushare 在 HTTP 非 2xx 时会静默返回空表，
#    若接口不可用，后续所有"基础列表"都会为空并伪装成 0 更新。预检失败直接终止。
echo ""
echo "[$(date '+%H:%M:%S')] [0/10] Tushare 连通性预检..."
if ! "${PYTHON}" - <<'PY'
from dotenv import load_dotenv
load_dotenv(".env")
import os
import time
import tushare as ts

token = os.getenv("TUSHARE_TOKEN", "")
if not token:
    raise SystemExit("TUSHARE_TOKEN 未配置")

pro = ts.pro_api(token, timeout=20)
last = "未知"
for attempt in range(4):
    try:
        df = pro.stock_basic(exchange="", list_status="L", fields="ts_code")
        rows = 0 if df is None else len(df)
        if rows > 1000:
            print(f"Tushare 正常，股票列表 {rows} 只")
            break
        last = f"返回 {rows} 行"
    except Exception as e:
        last = str(e)
    if attempt < 3:
        wait = 10 * (attempt + 1)
        print(f"Tushare 预检第 {attempt + 1} 次失败：{last}，{wait}s 后重试")
        time.sleep(wait)
else:
    raise SystemExit(f"Tushare 预检失败：{last}")
PY
then
  echo "[$(date '+%Y-%m-%d %H:%M:%S')] [致命] Tushare 不可用，终止本次更新（避免空跑假成功）"
  exit 1
fi
echo "[$(date '+%H:%M:%S')] [0/10] Tushare 连通性预检 完成"

# 目标交易日快照（按本次启动时刻，而非各步骤执行时刻）：
#   - 16:00 前启动：只更新到上一交易日
#   - 16:00 后启动：更新到最近交易日（含当日）
# 即使等待锁跨过收盘、或任务跨夜运行，本次目标都不变；当日数据由下一次运行补。
TARGET_FILE="${LOG_DIR}/target_trade_date.tmp"
export TARGET_FILE
if ! "${PYTHON}" - <<'PY'
import os
import time

import pandas as pd
from dotenv import load_dotenv
load_dotenv(".env")

run_date = os.environ["RUN_DATE"]
run_hm = os.environ["RUN_HM"]
include_today = run_hm >= "1600"


def weekday_fallback(upto: str, include: bool) -> str:
    d = pd.Timestamp(upto)
    if not include:
        d -= pd.Timedelta(days=1)
    while d.weekday() >= 5:
        d -= pd.Timedelta(days=1)
    return d.strftime("%Y-%m-%d")


target = ""
try:
    import tushare as ts
    pro = ts.pro_api(os.getenv("TUSHARE_TOKEN", ""), timeout=20)
    start = (pd.Timestamp(run_date) - pd.Timedelta(days=20)).strftime("%Y%m%d")
    end = pd.Timestamp(run_date).strftime("%Y%m%d")
    for attempt in range(3):
        try:
            df = pro.trade_cal(exchange="SSE", start_date=start, end_date=end,
                               is_open="1", fields="cal_date")
            if df is not None and not df.empty:
                s = pd.to_datetime(df["cal_date"])
                if not include_today:
                    s = s[s < pd.Timestamp(run_date)]
                if len(s):
                    target = s.max().strftime("%Y-%m-%d")
                break
        except Exception as e:
            print(f"trade_cal 第 {attempt + 1} 次失败：{e}")
        if attempt < 2:
            time.sleep(2 * (attempt + 1))
except Exception as e:
    print(f"trade_cal 不可用，改用本地日历：{e}")

if not target:
    try:
        cal = pd.read_parquet("data/meta/calendar.parquet")
        cal.index = pd.to_datetime(cal.index)
        trading = cal.index[cal["is_trading"].astype(int) == 1]
        trading = trading[trading <= pd.Timestamp(run_date)]
        if not include_today:
            trading = trading[trading < pd.Timestamp(run_date)]
        if len(trading):
            target = trading.max().strftime("%Y-%m-%d")
    except Exception as e:
        print(f"本地日历不可用，改用工作日兜底：{e}")

if not target:
    target = weekday_fallback(run_date, include_today)

with open(os.environ["TARGET_FILE"], "w", encoding="utf-8") as f:
    f.write(target)
print(f"本次目标交易日（启动快照）: {target}")
PY
then
  echo "[$(date '+%Y-%m-%d %H:%M:%S')] [致命] 目标交易日计算失败，终止本次更新"
  exit 1
fi
TARGET_TRADE_DATE=$(<"${TARGET_FILE}")
if [ -z "${TARGET_TRADE_DATE}" ]; then
  echo "[$(date '+%Y-%m-%d %H:%M:%S')] [致命] 目标交易日为空，终止本次更新"
  exit 1
fi
export TARGET_TRADE_DATE

# 1. A 股 K 线（stock + index + etf，Tushare 主源）
# --end 用启动时快照：等待锁跨收盘/跨夜都不改本次目标，当日留给下一次运行
run_step "[1/10] 更新 A 股 K 线数据" \
  "${PYTHON}" main.py update --types stock index etf --end "${TARGET_TRADE_DATE}"

# 2. meta 域（行业分类 + 申万指数 + 交易日历）
run_step "[2/10] 更新 meta 域" update_meta

# 3. moneyflow 域（个股/龙虎榜汇总/龙虎榜席位明细/大宗交易/两融/行业/大盘/北向）
#    注：席位明细 top_inst 与大宗交易 block_trade 已包含在 update-moneyflow 默认类型中
#    北向个股持股 hk_hold 为季度披露、按代码逐只拉取，不在此每日脚本，见季度脚本
run_step "[3/10] 更新 moneyflow 域" \
  "${PYTHON}" main.py update-moneyflow

# 4. events 域（E1 日历 + E2 减持/增持/质押/分红/回购，供风控过滤与产业资本画像；全市场按日期批量约 1-2 分钟）
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

# 收尾校验：核对核心域最新日期是否达到最近交易日（以运行启动日为准）。
# 这是防止"接口抖动 -> 0 更新 -> 假成功"的最后一道防线。
echo ""
echo "[$(date '+%H:%M:%S')] 收尾校验：核心域最新交易日..."
if "${PYTHON}" - <<'PY'
import os

import data.store as store
import data.store_moneyflow as store_mf

expected = os.environ.get("TARGET_TRADE_DATE", "")
if not expected:
    print("TARGET_TRADE_DATE 为空，跳过校验")
    raise SystemExit(0)


def coverage(codes, getter):
    """返回 (达到期望日期的只数, 有数据的只数)。"""
    dates = [getter(c) for c in codes]
    dates = [d for d in dates if d]
    return sum(1 for d in dates if d >= expected), len(dates)


cn_codes = store.list_codes("stock", market="cn")[:50]
hk_codes = store.list_codes("stock", market="hk")[:20]
mf_codes = store_mf.list_codes("stock")[:20]

# A 股必需项：按覆盖率判断（容忍少量停牌/退市/新股），避免个别标的掩盖整体漏更。
# 覆盖率阈值 90%，也避免"只修了少数股票"被误判为整体已完成。
required = [
    ("market/stock", cn_codes,
     lambda c: store.last_date(c, "stock", market="cn")),
    ("moneyflow/stock", mf_codes,
     lambda c: store_mf.last_date("stock", code=c)),
]
# 港股为提示项：港股与 A 股节假日不完全一致，避免港休日误判整体失败
warn_only = [
    ("market/hk", hk_codes,
     lambda c: store.last_date(c, "stock", market="hk")),
]

bad = []
for name, codes, getter in required:
    ok_n, total = coverage(codes, getter)
    ok = total > 0 and ok_n / total >= 0.9
    print(f"{'[OK]' if ok else '[FAIL]'} {name}: {ok_n}/{total} 只达到 {expected}")
    if not ok:
        bad.append(name)

for name, codes, getter in warn_only:
    ok_n, total = coverage(codes, getter)
    ok = total > 0 and ok_n / total >= 0.9
    print(f"{'[OK]' if ok else '[WARN]'} {name}: {ok_n}/{total} 只达到 {expected}")

if bad:
    print(f"收尾校验失败：{', '.join(bad)} 未达到最近交易日 {expected}")
    raise SystemExit(1)
print("收尾校验通过")
PY
then
  echo "[$(date '+%H:%M:%S')] 收尾校验 完成"
else
  echo "[$(date '+%H:%M:%S')] [警告] 收尾校验失败，核心域未更新到最近交易日"
  FAILED+=("收尾校验")
fi

echo ""
echo "========================================"
if [ "${#FAILED[@]}" -gt 0 ] || [ "${#SKIPPED[@]}" -gt 0 ]; then
  MSG="[$(date '+%Y-%m-%d %H:%M:%S')] 每日更新结束，但未全部成功"
  [ "${#FAILED[@]}" -gt 0 ] && MSG+="；失败步骤: ${FAILED[*]}"
  [ "${#SKIPPED[@]}" -gt 0 ] && MSG+="；跳过步骤: ${SKIPPED[*]}"
  echo "${MSG}"
  echo "========================================"
  exit 1
fi
echo "[$(date '+%Y-%m-%d %H:%M:%S')] 每日更新全部完成！"
echo "========================================"
