"""
全局配置文件
修改此文件以适配你的环境和偏好
"""
import os
from pathlib import Path

# 项目根目录
ROOT_DIR = Path(__file__).parent.parent

# ============================================================
# 数据源配置
# ============================================================
DATA_SOURCE = {
    "primary": "akshare",        # 主数据源: akshare / baostock / tushare
    "fallback": ["baostock"],    # 备用数据源列表（按顺序尝试）
    "tushare_token": os.getenv("TUSHARE_TOKEN", ""),   # Tushare Pro Token
    "cache_days": 1,             # 本地缓存有效天数
    "cache_dir": ROOT_DIR / "data" / "cache",
}

# ============================================================
# 扫描与选股配置
# ============================================================
SCAN = {
    "market": "A股",             # 扫描范围
    "exclude_st": True,          # 排除ST股票
    "exclude_new": True,         # 排除上市不足60天新股
    "min_volume": 1_000_000,     # 最低成交量（手）
    "min_price": 2.0,            # 最低股价（元）
    "max_price": 500.0,          # 最高股价（元）
    "top_n": 20,                 # 每次推送最多选股数量
}

# ============================================================
# 回测配置
# ============================================================
BACKTEST = {
    "initial_capital": 1_000_000,   # 初始资金（元）
    "commission_rate": 0.0003,       # 手续费率（买+卖）
    "slippage": 0.001,               # 滑点
    "position_size": 0.1,            # 单只股票仓位上限（占总资金比例）
    "benchmark": "000300.SH",        # 基准指数（沪深300）
}

# ============================================================
# 通知推送配置
# ============================================================
_wecom_webhook = os.getenv("WECOM_WEBHOOK", "")
_email_user    = os.getenv("EMAIL_USER", "")
_email_pass    = os.getenv("EMAIL_PASS", "")
_email_to      = os.getenv("EMAIL_TO", "")

# 只启用真正配置了的渠道（占位符视为未配置）
_channels = []
if _wecom_webhook and "your_key" not in _wecom_webhook:
    _channels.append("wecom")
if _email_user and _email_pass and "example.com" not in _email_to and _email_to:
    _channels.append("email")
_tg_token = os.getenv("TELEGRAM_TOKEN", "")
if _tg_token and "your_bot" not in _tg_token:
    _channels.append("telegram")

NOTIFIER = {
    "channels": _channels,

    # 企业微信机器人 Webhook
    "wecom_webhook": _wecom_webhook,

    # 邮件配置
    "email": {
        "smtp_host": os.getenv("SMTP_HOST", "smtp.qq.com"),
        "smtp_port": int(os.getenv("SMTP_PORT", "465")),
        "use_ssl": True,
        "username": _email_user,
        "password": _email_pass,
        "recipients": [r for r in _email_to.split(",") if r],
    },

    # Telegram Bot（可选）
    "telegram": {
        "bot_token": os.getenv("TELEGRAM_TOKEN", ""),
        "chat_id": os.getenv("TELEGRAM_CHAT_ID", ""),
    },
}

# ============================================================
# Web 看板配置
# ============================================================
WEB = {
    "host": "0.0.0.0",
    "port": 8888,
    "debug": False,
    "auto_refresh": 60,   # 页面自动刷新间隔（秒）
}

# ============================================================
# 日志配置
# ============================================================
LOG = {
    "level": "INFO",
    "dir": ROOT_DIR / "logs",
    "max_bytes": 10 * 1024 * 1024,   # 10MB
    "backup_count": 5,
}

# ============================================================
# 风控约束配置（RC1~RC8，硬性规则不得绕过）
# ============================================================
RISK_CONTROL = {
    # RC1: 单笔仓位上限（占总资金比例）
    "max_single_position": 0.10,
    # RC2: 同时持有标的上限
    "max_positions": 10,
    # RC3: 同一申万一级行业持仓上限（占总仓位比例）
    "max_industry_concentration": 0.30,
    # RC4: 单笔止损线（从买入价下跌触发比例）
    "stop_loss_ratio": 0.07,
    # RC5: 总账户最大回撤上限（触发后暂停新开仓）
    "max_total_drawdown": 0.15,
    # RC6: 标的质量过滤（由扫描引擎完成，此处为预留）
    "quality_filter": {
        "exclude_st": True,
        "exclude_beijing": True,
        "exclude_new_ipo": True,
    },
    # RC7: 减持窗口期过滤（D8b 事件，当前预留）
    "reduce_window_block": True,
    # RC8: 突发事件熔断
    "circuit_breaker": {
        "vix_threshold": 30,
        "index_drop_threshold": 0.03,
        "pause_new_orders": True,
    },
    # 止盈比例
    "take_profit_ratio": 0.20,
    # 风控启用开关（Phase 1 半自动，人工最终决策）
    "enforce": True,
}

# ============================================================
# 调度配置（每日自动运行）
# ============================================================
SCHEDULE = {
    "scan_time": "15:05",        # 收盘后扫描时间
    "pre_market_time": "09:15",  # 盘前预警时间
    "timezone": "Asia/Shanghai",
}
