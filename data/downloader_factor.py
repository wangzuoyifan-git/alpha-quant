"""
factor 域下载器（stub）

台阶二重构后，factor 域专用于计算型因子（动量/价值/质量等衍生值）。
原始资金流数据下载已迁往 downloader_moneyflow.py。

台阶二/后续：在此实现因子计算逻辑（从 market/fundamental 数据衍生）。
"""
import logging

logger = logging.getLogger(__name__)


def _log(msg: str):
    from datetime import datetime
    print(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}", flush=True)
    logger.info(msg)


class FactorDownloader:

    def init_db(self, start: str = "2024-01-02") -> None:
        """初始化 factor 域（台阶二：计算型因子）"""
        _log("Factor 域：暂无计算型因子，跳过（台阶二待实现）")

    def update_db(self) -> None:
        """更新 factor 域（台阶二：计算型因子）"""
        _log("Factor 域：暂无计算型因子，跳过（台阶二待实现）")
