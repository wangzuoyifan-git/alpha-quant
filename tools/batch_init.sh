#!/bin/bash
# 分批运行 init，每批 500 只，自动断点续传
# 用法: bash tools/batch_init.sh

cd /home/wz/alpha量化

BATCH=500
TOTAL=5207  # 大约值，实际以断点续传为准

echo "开始分批下载，每批约 $BATCH 只..."

while true; do
    # 查当前已有文件数
    current=$(find data/market/cn/stock -name "*.parquet" 2>/dev/null | wc -l)
    echo "[$(date '+%H:%M:%S')] 当前已有: $current 只"

    if [ "$current" -ge "$TOTAL" ]; then
        echo "stock 下载完成！"
        break
    fi

    # 跑一次 init（断点续传，每次跑到进程被杀或完成）
    venv/bin/python3 -u main.py init --types stock >> logs/init.log 2>&1
    exit_code=$?
    echo "[$(date '+%H:%M:%S')] 进程退出，exit_code=$exit_code，等待5秒后继续..."
    sleep 5
done

echo "开始下载 index 和 etf..."
venv/bin/python3 -u main.py init --types index etf >> logs/init.log 2>&1
echo "全部完成！"
