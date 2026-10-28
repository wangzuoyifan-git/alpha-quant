#!/usr/bin/env bash
# =============================================================
# Alpha量化 — 研究回测一键检查（手动运行，不进入每日更新）
#
# 默认：
#   1) 回测/执行/组合相关单测
#   2) quality_trend 快速全市场回测
#   3) 40/40/10/10 多 sleeve 组合回测（monthly）
#   4) 数据质量抽样审计
#
# 可选：
#   --with-walkforward   追加 3 折 walk-forward（约 1 分钟）
#   --with-platform      追加参数平台检查（约 1 分钟）
#
# 用法：
#   bash scripts/research_checks.sh
#   bash scripts/research_checks.sh --with-walkforward --with-platform
# =============================================================
set -euo pipefail

PROJ_DIR="/home/wz/alpha-quant"
PYTHON="${PROJ_DIR}/venv/bin/python3"
START="${START:-2024-07-01}"
END="${END:-2026-10-26}"
WITH_WF=0
WITH_PLATFORM=0

for arg in "$@"; do
  case "$arg" in
    --with-walkforward) WITH_WF=1 ;;
    --with-platform) WITH_PLATFORM=1 ;;
    *) echo "未知参数: $arg"; exit 2 ;;
  esac
done

cd "${PROJ_DIR}"

step() {
  local name="$1"; shift
  echo ""
  echo "===== ${name} ====="
  "$@"
}

step "相关单测" "${PYTHON}" -m pytest \
  tests/test_execution.py tests/test_pit_data.py tests/test_fast_backtest.py \
  tests/test_portfolio.py tests/test_walkforward.py tests/test_backtest.py \
  tests/test_strategies.py tests/test_l3b_strategies.py tests/test_scanner.py -q

step "quality_trend 快速回测" "${PYTHON}" main.py backtest \
  --strategy quality_trend --fast --start "${START}" --end "${END}"

step "多 sleeve 组合回测" "${PYTHON}" main.py portfolio-backtest \
  --start "${START}" --end "${END}" --rebalance monthly

step "数据质量审计" "${PYTHON}" -m tools.data_quality_audit --limit 200

if [ "${WITH_WF}" -eq 1 ]; then
  step "3 折 walk-forward" "${PYTHON}" main.py walkforward \
    --strategy quality_trend --start "${START}" --end "${END}"
fi

if [ "${WITH_PLATFORM}" -eq 1 ]; then
  step "参数平台检查" "${PYTHON}" main.py platform-check \
    --strategy quality_trend --start "${START}" --end "${END}"
fi

echo ""
echo "研究检查完成。实验记录: research/experiments.jsonl"
