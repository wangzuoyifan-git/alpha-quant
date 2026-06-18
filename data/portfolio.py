"""portfolio.json 读写辅助"""
import json
from pathlib import Path

PORTFOLIO_PATH = Path(__file__).parent / "portfolio.json"


def load_portfolio(path: str = None) -> dict:
    """
    读持仓文件，返回统一格式 dict：
      {"total_capital": float, "holdings": [...]}
    兼容旧格式（纯列表）和新格式（含 total_capital 的 dict）。
    """
    p = Path(path) if path else PORTFOLIO_PATH
    if not p.exists():
        return {"total_capital": 0.0, "holdings": []}
    raw = json.loads(p.read_text(encoding="utf-8"))
    if isinstance(raw, list):
        return {"total_capital": 0.0, "holdings": raw}
    # dict 格式但 total_capital 可能为 0 或缺失
    if not raw.get("total_capital"):
        raw["total_capital"] = 0.0
    return raw
