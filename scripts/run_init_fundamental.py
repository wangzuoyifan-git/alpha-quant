#!/usr/bin/env python3
"""
独立运行 fundamental 域初始化，绕过 main.py 的复杂 import
"""
import sys
import logging
import logging.handlers
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

# 简单日志配置
log_dir = Path(__file__).parent.parent / "logs"
log_dir.mkdir(exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[
        logging.FileHandler(log_dir / "init_fundamental_detail.log", encoding="utf-8"),
        logging.StreamHandler(sys.stdout),
    ],
)

from data.downloader_fundamental import FundamentalDownloader

if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--start-year", type=int, default=2024)
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()

    dl = FundamentalDownloader()
    dl.init_db(start_year=args.start_year, force=args.force)
