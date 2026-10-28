"""
全局配置文件
修改此文件以适配你的环境和偏好
"""
import os
from pathlib import Path

# 加载 .env 环境变量
try:
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).parent.parent / ".env")
except ImportError:
    pass

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
    # 日线网络兜底单源硬超时（秒）：防止单只慢请求拖垮并发扫描池。
    # get_daily 优先级：tushare → akshare → baostock。
    "fetch_timeout": int(os.getenv("FETCH_TIMEOUT", "15")),
}

# ============================================================
# 扫描与选股配置
# ============================================================
SCAN = {
    "market": "A股",             # 扫描范围
    "exclude_st": True,          # 排除ST股票
    "exclude_new": True,         # 排除上市不足60天新股
    "min_volume": 1_000_000,     # 最低成交量（手）
    "min_amount": 30_000_000,    # 最低成交额（元，S4 流动性过滤；0 表示不过滤）
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
    "max_positions": 10,             # 同时持仓上限（对齐 RC2）
    "benchmark": "000300.SH",        # 基准指数（沪深300）
}

# ============================================================
# 回测成交规则（P0-1）
# ============================================================
EXECUTION = {
    # next_open：t 日收盘信号 → t+1 开盘成交（默认，生产回测口径）
    # same_close：t 日收盘成交（仅用于复现历史结果，含未来函数）
    "mode": "next_open",
    "commission_rate": 0.0003,   # 佣金（买卖双向）
    "slippage": 0.001,           # 滑点
    "stamp_duty": 0.0005,        # 印花税（仅卖出）
    "min_lot": 100,              # 最小交易单位（股）
    "t_plus_one": True,          # A股 T+1
    "limit_check": True,         # 涨跌停/一字板可成交性检查
    "limit_buffer": 0.98,        # 开盘价达到涨跌停价的此比例视为不可成交
}

# ============================================================
# 缠论配置（chan/）
# ============================================================
CHAN = {
    "bi_min_bars": 4,            # 成笔最少合并K线间隔
    "divergence_ratio": 0.8,     # MACD 面积缩小到前次此比例以下视为背驰
    "divergence_min_bars": 3,    # 参与背驰比较的笔最少K线数
    "levels": ["daily", "weekly"],
    "min_weekly_bars": 20,       # 周线少于该数量时不做高级别确认
    "min_bars": 300,             # 日线结构预热所需K线
    "max_signal_age_bars": 1,    # 买卖点确认后允许的信号有效期（日）
    "weekly_confirm": True,      # 三买是否要求周线不同向下
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
    # 止盈比例（兜底，策略给了 ExitLevels 时以策略为准）
    "take_profit_ratio": 0.20,
    # 风控启用开关（Phase 1 半自动，人工最终决策）
    "enforce": True,

    # ── 持仓管理参数（position_manager 使用）──────────────────
    # 基于风险的仓位管理
    "risk_per_trade": 0.01,          # 单笔最大亏损占总资金比例（1%）
    "default_capital": 2_000_000,    # portfolio.json 未填 total_capital 时的默认值
    # R 倍数警告阈值
    "min_r_ratio": 1.5,              # R < 此值时展示警告
    # 分批出场比例
    "scale_out": {
        "take_1_pct": 0.33,          # 第一止盈减仓比例
        "take_2_pct": 0.50,          # 第二止盈减仓比例
        "soft_stop_pct": 0.50,       # 近支撑止损减仓比例
    },
    # ATR 止损/止盈倍数（策略 params 可覆盖）
    "atr_stop_multiplier": 2.5,      # 止损距 = ATR × 此值
}

# ============================================================
# 调度配置（每日自动运行）
# ============================================================
SCHEDULE = {
    "scan_time": "15:05",        # 收盘后扫描时间
    "pre_market_time": "09:15",  # 盘前预警时间
    "timezone": "Asia/Shanghai",
}

# ============================================================
# LLM 配置（阶段四，autonomy_level 总闸门 + 洋葱架构）
# ============================================================
LLM = {
    # 执行权总闸门（LLM-R8）：OFF / ADVISORY / ASSIST / COPILOT / AUTO
    # 默认 OFF：LLM 不参与任何决策，workflow 行为与纯规则版完全一致
    "autonomy_level": os.getenv("LLM_AUTONOMY_LEVEL", "OFF"),

    # LLM 研判最大 confidence 调整幅度（±，LLM-R5）
    "confidence_adjust_max": 0.2,

    "primary": {
        "provider": "deepseek",
        "api_style": "openai",           # openai 兼容协议
        "model": "deepseek-chat",
        "api_key_env": "DEEPSEEK_API_KEY",
        "base_url": "https://api.deepseek.com/v1",
    },
    "fallback": {
        "provider": "yunwu",
        "api_style": "openai",           # 聚合平台走 openai 分支（LLM-R4）
        "model": "claude-3-5-sonnet-20241022",
        "api_key_env": "YUNWU_API_KEY",
        "base_url_env": "YUNWU_BASE_URL",
    },
    "temperature": 0.1,
    "timeout": 30,
    "max_retries": 2,
    "candidate_top_n": 5,  # LLM-R6: 每日最多点评 Top N 候选标的
}
