"""
Alpha量化 - 主入口
支持命令行直接运行各功能模块

用法:
  python main.py scan              # 全市场扫描，推送信号
  python main.py backtest          # 运行策略回测
  python main.py orders            # 查看待确认指令单
  python main.py web               # 启动 Web 看板
  python main.py schedule          # 启动自动定时任务
"""
import logging
from pathlib import Path

# 加载 .env 环境变量
try:
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).parent / ".env")
except ImportError:
    pass

# ---- 日志配置 ----
import logging.handlers

from backtest.engine import Backtester
from config.settings import BACKTEST, DATA_SOURCE, LOG, NOTIFIER, SCAN, SCHEDULE, WEB
from data.downloader import KlineDownloader
from data.fetcher import DataFetcher
from engine.scanner import ScanEngine
from notifier.push import Notifier
from strategies.builtin import BUILTIN_STRATEGIES
from trader.assistant import TradeAssistant

log_dir = Path(LOG["dir"])
log_dir.mkdir(exist_ok=True)
handler = logging.handlers.RotatingFileHandler(
    log_dir / "alpha.log",
    maxBytes=LOG["max_bytes"],
    backupCount=LOG["backup_count"],
    encoding="utf-8",
)
logging.basicConfig(
    level=getattr(logging, LOG["level"]),
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[handler, logging.StreamHandler()],
)
logger = logging.getLogger("alpha")


# ============================================================
# 全局组件初始化
# ============================================================
fetcher = DataFetcher(DATA_SOURCE)
notifier = Notifier(NOTIFIER)
trader = TradeAssistant(BACKTEST)


def cmd_scan(date: str | None = None):
    """执行全市场扫描并推送信号"""
    from rich.console import Console
    from rich.table import Table

    console = Console()
    console.print("[bold cyan]Alpha量化 - 全市场扫描启动[/bold cyan]")

    engine = ScanEngine(fetcher, SCAN)
    engine.load_strategies(BUILTIN_STRATEGIES)

    signals = engine.scan(date=date)

    if not signals:
        console.print("[yellow]今日无触发信号[/yellow]")
        return

    # 终端展示
    table = Table(title=f"选股信号 (共 {len(signals)} 个)")
    table.add_column("代码", style="cyan")
    table.add_column("名称")
    table.add_column("信号", style="bold")
    table.add_column("价格", justify="right")
    table.add_column("评分", justify="right")
    table.add_column("原因")
    for s in signals:
        color = "red" if s.signal == "BUY" else "green"
        table.add_row(
            s.code, s.name,
            f"[{color}]{s.signal}[/{color}]",
            f"¥{s.price:.2f}",
            f"{s.score:.0f}",
            s.reason,
        )
    console.print(table)

    # 推送通知
    notifier.send_signals(signals)

    # 持久化信号
    from signals.manager import save_signals
    save_signals(signals, date=date)

    # 生成指令单
    orders = trader.generate_orders(signals)
    if orders:
        console.print(f"\n[bold]已生成 {len(orders)} 份指令单，运行 `python main.py orders` 查看[/bold]")


def cmd_backtest(strategy_key: str = "macd_rsi",
                 start: str = "2022-01-01",
                 end: str = "2024-12-31",
                 codes: list | None = None):
    """运行回测"""
    from rich.console import Console
    console = Console()

    if strategy_key not in BUILTIN_STRATEGIES:
        console.print(f"[red]未知策略: {strategy_key}[/red]")
        console.print(f"可用策略: {list(BUILTIN_STRATEGIES.keys())}")
        return

    strategy_cls = BUILTIN_STRATEGIES[strategy_key]
    strategy = strategy_cls()

    # codes 为空 -> 传空列表给 backtester，由它自动使用本地全市场数据
    codes = codes or []

    backtester = Backtester(fetcher, BACKTEST)
    result = backtester.run(strategy, codes, start, end)
    console.print(result.summary())

    # 保存权益曲线
    if not result.equity_curve.empty:
        out = Path("data/processed") / f"equity_{strategy_key}.csv"
        result.equity_curve.to_csv(out, header=True)
        console.print(f"权益曲线已保存: {out}")


def cmd_orders():
    """查看并管理待确认指令单"""
    from rich.console import Console
    from rich.table import Table
    console = Console()

    orders = trader.list_pending_orders()
    if not orders:
        console.print("[green]暂无待确认指令单[/green]")
        return

    table = Table(title=f"待确认指令单 ({len(orders)} 条)")
    table.add_column("订单ID", style="dim")
    table.add_column("代码")
    table.add_column("名称")
    table.add_column("方向", style="bold")
    table.add_column("价格", justify="right")
    table.add_column("数量", justify="right")
    table.add_column("金额", justify="right")
    for o in orders:
        color = "red" if o["direction"] == "BUY" else "green"
        table.add_row(
            o["order_id"], o["code"], o["name"],
            f"[{color}]{o['direction']}[/{color}]",
            f"¥{o['price']:.2f}",
            str(o["quantity"]),
            f"¥{o['amount']:,.0f}",
        )
    console.print(table)
    console.print("\n[dim]确认: python main.py confirm <order_id>[/dim]")
    console.print("[dim]取消: python main.py cancel <order_id>[/dim]")


def cmd_web():
    """启动 Web 看板"""
    try:
        from flask import Flask, jsonify, render_template_string
        app = Flask(__name__)

        HTML = """
        <!DOCTYPE html><html lang="zh-CN">
        <head><meta charset="UTF-8"><meta http-equiv="refresh" content="{{ refresh }}">
        <title>Alpha量化看板</title>
        <style>
          body{font-family:monospace;background:#111;color:#eee;padding:20px}
          h1{color:#0f0} table{border-collapse:collapse;width:100%}
          th{background:#333;padding:8px} td{padding:6px;border-bottom:1px solid #333}
          .buy{color:#f55} .sell{color:#5f5}
        </style></head>
        <body><h1>Alpha量化 实时看板</h1>
        <p>数据更新时间: {{ now }}</p>
        <h2>待确认指令单</h2>
        <table><tr><th>代码</th><th>名称</th><th>方向</th><th>价格</th><th>数量</th><th>金额</th><th>原因</th></tr>
        {% for o in orders %}
        <tr><td>{{ o.code }}</td><td>{{ o.name }}</td>
        <td class="{{ o.direction|lower }}">{{ o.direction }}</td>
        <td>¥{{ "%.2f"|format(o.price) }}</td>
        <td>{{ o.quantity }}</td>
        <td>¥{{ "{:,.0f}".format(o.amount) }}</td>
        <td>{{ o.reason }}</td></tr>
        {% endfor %}
        </table></body></html>
        """

        @app.route("/")
        def index():
            from datetime import datetime as dt
            orders = trader.list_pending_orders()
            return render_template_string(
                HTML,
                orders=orders,
                now=dt.now().strftime("%Y-%m-%d %H:%M:%S"),
                refresh=WEB["auto_refresh"],
            )

        @app.route("/api/orders")
        def api_orders():
            return jsonify(trader.list_pending_orders())

        logger.info(f"Web 看板启动: http://{WEB['host']}:{WEB['port']}")
        app.run(host=WEB["host"], port=WEB["port"], debug=WEB["debug"])
    except ImportError:
        print("请先安装 Flask: pip install flask")


def cmd_init(start: str = "2024-01-02", end: str | None = None, force: bool = False,
             workers: int = 1, types: list | None = None):
    """首次全量下载全市场 K 线数据（支持断点续传）"""
    dl = KlineDownloader(fetcher, workers=workers)
    dl.init_db(start=start, end=end, force=force, types=types)


def cmd_update(end: str | None = None, workers: int = 1, types: list | None = None):
    """增量更新：补全每只证券到最新交易日"""
    dl = KlineDownloader(fetcher, workers=workers)
    dl.update_db(end=end, types=types)


def cmd_dbstats():
    """查看本地 K 线数据库统计"""
    import data.store as store
    s = store.stats()
    total = s.get("total", {})
    print("\n本地 K 线数据库")
    for market, atypes in s.items():
        if market == "total":
            continue
        for atype, info in atypes.items():
            print(f"  {market}/{atype}: {info['count']}只  {info['rows']:>10,}行  {info['size_mb']:.1f}MB")
    print(f"  合计: {total.get('count', 0)}只  {total.get('rows', 0):>10,}行  {total.get('size_mb', 0):.1f}MB")


def cmd_schedule():
    """启动定时任务调度"""
    try:
        import pytz
        from apscheduler.schedulers.blocking import BlockingScheduler
        from apscheduler.triggers.cron import CronTrigger

        tz = pytz.timezone(SCHEDULE["timezone"])
        scheduler = BlockingScheduler(timezone=tz)

        # 收盘后先更新数据，再扫描
        h, m = SCHEDULE["scan_time"].split(":")

        def _update_then_scan():
            cmd_update()
            cmd_scan()

        scheduler.add_job(_update_then_scan, CronTrigger(
            hour=int(h), minute=int(m),
            day_of_week="mon-fri", timezone=tz
        ), id="daily_update_scan")

        print("定时任务已启动:")
        print(f"  收盘更新+扫描: 周一至周五 {SCHEDULE['scan_time']}")
        print("按 Ctrl+C 停止")
        scheduler.start()
    except ImportError as e:
        print(f"缺少依赖: {e}\n请运行: pip install apscheduler pytz")


# ============================================================
# CLI 入口
# ============================================================
if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(
        description="Alpha量化 - A股日线级别量化分析系统",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  python main.py init                          # 首次全量下载K线（2024-01-02至今）
  python main.py init --start 2023-01-01       # 指定起始日期
  python main.py init --force                  # 强制重新下载（覆盖已有数据）
  python main.py update                        # 每日增量更新到今天
  python main.py dbstats                       # 查看本地数据库统计
  python main.py scan                          # 今日全市场扫描
  python main.py scan --date 2024-11-01        # 指定日期扫描
  python main.py backtest --strategy macd_rsi  # MACD+RSI回测
  python main.py backtest --strategy ma_cross --start 2023-01-01 --end 2024-01-01
  python main.py orders                        # 查看指令单
  python main.py confirm <order_id>            # 确认指令单
  python main.py cancel <order_id>             # 取消指令单
  python main.py web                           # 启动Web看板
  python main.py schedule                      # 启动定时任务（自动更新+扫描）
        """,
    )
    sub = parser.add_subparsers(dest="command")

    # init
    p_init = sub.add_parser("init", help="首次全量下载全市场K线数据")
    p_init.add_argument("--start", default="2024-01-02", help="起始日期（默认2024-01-02）")
    p_init.add_argument("--end", default=None, help="结束日期（默认今日）")
    p_init.add_argument("--force", action="store_true", help="强制重新下载已有数据")
    p_init.add_argument("--workers", type=int, default=1, help="并发线程数（默认1）")
    p_init.add_argument("--types", nargs="*",
                        default=["stock", "index", "etf"],
                        choices=["stock", "index", "etf"],
                        help="下载类型（默认全部）")

    # update
    p_update = sub.add_parser("update", help="增量更新K线数据到今日")
    p_update.add_argument("--end", default=None, help="目标日期（默认今日）")
    p_update.add_argument("--workers", type=int, default=1, help="并发线程数（默认1）")
    p_update.add_argument("--types", nargs="*",
                          default=["stock", "index", "etf"],
                          choices=["stock", "index", "etf"],
                          help="更新类型（默认全部）")

    # dbstats
    sub.add_parser("dbstats", help="查看本地K线数据库统计")

    # scan
    p_scan = sub.add_parser("scan", help="全市场扫描")
    p_scan.add_argument("--date", help="指定日期 YYYY-MM-DD（默认今日）")

    # backtest
    p_bt = sub.add_parser("backtest", help="策略回测")
    p_bt.add_argument("--strategy", default="macd_rsi",
                      choices=list(BUILTIN_STRATEGIES.keys()),
                      help="策略名称")
    p_bt.add_argument("--start", default="2024-01-02", help="开始日期（默认2024-01-02）")
    p_bt.add_argument("--end", default="2026-05-25", help="结束日期（默认2026-05-25）")
    p_bt.add_argument("--codes", nargs="*", help="股票代码列表（空格分隔）")

    # orders
    sub.add_parser("orders", help="查看待确认指令单")

    # confirm / cancel
    p_confirm = sub.add_parser("confirm", help="确认指令单")
    p_confirm.add_argument("order_id")

    p_cancel = sub.add_parser("cancel", help="取消指令单")
    p_cancel.add_argument("order_id")

    # web
    sub.add_parser("web", help="启动Web看板")

    # schedule
    sub.add_parser("schedule", help="启动定时任务")

    args = parser.parse_args()

    if args.command == "init":
        cmd_init(start=args.start, end=args.end,
                 force=args.force, workers=args.workers,
                 types=args.types)
    elif args.command == "update":
        cmd_update(end=args.end, workers=args.workers, types=args.types)
    elif args.command == "dbstats":
        cmd_dbstats()
    elif args.command == "scan":
        cmd_scan(date=args.date)
    elif args.command == "backtest":
        cmd_backtest(
            strategy_key=args.strategy,
            start=args.start,
            end=args.end,
            codes=args.codes,
        )
    elif args.command == "orders":
        cmd_orders()
    elif args.command == "confirm":
        trader.confirm_order(args.order_id)
    elif args.command == "cancel":
        trader.cancel_order(args.order_id)
    elif args.command == "web":
        cmd_web()
    elif args.command == "schedule":
        cmd_schedule()
    else:
        parser.print_help()
