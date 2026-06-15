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
from config.settings import BACKTEST, DATA_SOURCE, LOG, NOTIFIER, RISK_CONTROL, SCAN, SCHEDULE, WEB
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
trader = TradeAssistant(BACKTEST, RISK_CONTROL)


def cmd_scan(date: str | None = None, asset_types: list | None = None):
    """执行全市场扫描并推送信号"""
    from rich.console import Console
    from rich.table import Table

    console = Console()
    asset_types = asset_types or ["stock"]
    label = "+".join(asset_types)
    console.print(f"[bold cyan]Alpha量化 - 全市场扫描启动 ({label})[/bold cyan]")

    engine = ScanEngine(fetcher, SCAN)
    engine.load_strategies(BUILTIN_STRATEGIES)

    signals = engine.scan(date=date, asset_types=asset_types)

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

    # B3：回测结果持久化
    result_path = backtester.save_result(result)
    console.print(f"回测结果已保存: {result_path}")

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


def cmd_workflow(date: str | None = None,
                 industries: list | None = None,
                 asset_types: list | None = None):
    """运行分层决策 workflow（L1宏观 → L2行业 → L3个股），分层可视化输出。

    :param date: 决策日期（默认今日）
    :param industries: 手动指定申万行业代码列表（如 801080 801770），
                       跳过 L2 自动产出的全部行业，只扫描指定行业（快速验证）。
    :param asset_types: 资产类型（默认 stock）
    """
    from rich.console import Console
    from rich.table import Table
    from rich.panel import Panel
    from agents.orchestrator import run_daily_workflow

    console = Console()
    console.print(Panel.fit(
        "[bold cyan]Alpha量化 — 分层决策 Workflow[/bold cyan]\n"
        "L1 宏观场景 → L2 行业轮动 → L3 个股选择 → 风控 → 指令单",
        border_style="cyan"))

    if industries:
        console.print(f"[yellow]行业范围限定（override）:[/yellow] {industries}")
    console.print("[dim]运行中（L3 扫描可能需要一些时间）...[/dim]\n")

    result = run_daily_workflow(
        date=date,
        asset_types=asset_types or ["stock"],
        industry_override=industries,
    )

    if not result["success"]:
        console.print(f"[red]Workflow 失败: {result.get('error')}[/red]")
        return

    # ── L1 宏观层 ──
    conf_color = "green" if result["confidence"] >= 0.7 else "yellow"
    l1 = Table(title="L1 宏观场景层", show_header=False, border_style="blue")
    l1.add_column("项", style="bold cyan")
    l1.add_column("值")
    l1.add_row("regime（市场状态）", result["regime"])
    l1.add_row("risk_mode（风险模式）", result["risk_mode"])
    l1.add_row("confidence（置信度）", f"[{conf_color}]{result['confidence']:.2f}[/{conf_color}]")
    ctx = result.get("decision_context", {})
    l1.add_row("max_position（总仓位上限）", str(ctx.get("max_position", "-")))
    l1.add_row("allocation（配置/交易仓）", str(ctx.get("allocation", "-")))
    l1.add_row("l2_weight（景气/估值/资金流）", str(ctx.get("l2_weight_config", "-")))
    llm = result.get("llm_review", {})
    l1.add_row("LLM 研判", "可用" if llm.get("llm_available") else "[dim]OFF/未启用[/dim]")
    console.print(l1)

    # ── L2 行业层 ──
    allowed = result.get("allowed_industries", [])
    detail = result.get("industry_detail", {})
    l2 = Table(title=f"L2 行业轮动层（选出 {len(allowed)} 个行业）", border_style="magenta")
    l2.add_column("申万代码", style="cyan")
    l2.add_column("行业名称")
    l2.add_column("评分", justify="right")
    l2.add_column("time_horizon")
    for code in allowed:
        d = detail.get(code, {})
        l2.add_row(str(code), d.get("industry_name", "-"),
                   f"{d.get('score', 0):.1f}", d.get("time_horizon", "-"))
    if allowed:
        console.print(l2)
    else:
        console.print("[yellow]L2 行业池为空（数据缺失或 fallback），L3 退化为全市场[/yellow]")

    # ── L3 策略与信号 ──
    console.print(f"\n[bold]L3 选用策略:[/bold] {result.get('strategy_keys', [])}")

    signals = result.get("filtered_signals", [])
    if signals:
        l3 = Table(title=f"L3 交易仓信号（风控通过 {len(signals)} 个）", border_style="green")
        l3.add_column("代码", style="cyan")
        l3.add_column("信号", style="bold")
        l3.add_column("评分", justify="right")
        l3.add_column("原因")
        for s in signals[:20]:
            l3.add_row(str(s.get("code")), str(s.get("signal")),
                       f"{s.get('score', 0):.0f}", str(s.get("reason", ""))[:40])
        console.print(l3)
    else:
        console.print("[yellow]无交易仓信号（BUY 通过风控）[/yellow]")

    # ── L3-B 配置仓候选（WATCH）──
    watch = result.get("watch_pool", [])
    if watch:
        wt = Table(title=f"L3-B 配置仓候选 WATCH（{len(watch)} 个，待人工确认）",
                   border_style="yellow")
        wt.add_column("代码", style="cyan")
        wt.add_column("评分", justify="right")
        wt.add_column("原因")
        for s in watch[:20]:
            wt.add_row(str(s.get("code")), f"{s.get('score', 0):.0f}",
                       str(s.get("reason", ""))[:40])
        console.print(wt)

    # ── 汇总 ──
    console.print(Panel.fit(
        f"扫描信号 {result.get('scan_results', {}).get('count', 0)} | "
        f"风控通过 {result['signals']} | "
        f"配置仓候选 {len(watch)} | "
        f"指令单 {result['orders']}\n"
        f"[dim]决策日志: {result.get('decision_log_path')}[/dim]",
        title="执行汇总", border_style="cyan"))

    # ── 执行链路日志 ──
    console.print("\n[bold]分层执行链路:[/bold]")
    for line in result["log"]:
        console.print(f"  [dim]{line}[/dim]")


def cmd_web():
    """启动 Web 看板"""
    try:
        from flask import Flask, jsonify, render_template_string, request
        app = Flask(__name__)

        _BASE_STYLE = """
        <style>
          *{box-sizing:border-box}
          body{font-family:monospace;background:#111;color:#eee;padding:20px;margin:0}
          h1{color:#0f0;margin-bottom:4px}
          nav{margin:12px 0 20px}
          nav a{color:#0af;text-decoration:none;margin-right:20px;font-size:14px}
          nav a.active{color:#fff;border-bottom:2px solid #0f0;padding-bottom:2px}
          table{border-collapse:collapse;width:100%}
          th{background:#222;padding:8px 10px;text-align:left;color:#aaa;font-weight:normal}
          td{padding:7px 10px;border-bottom:1px solid #222;vertical-align:top}
          tr:hover td{background:#1a1a1a}
          .buy{color:#f55} .sell{color:#5f5} .watch{color:#fa0}
          .score{font-weight:bold}
          .tag{display:inline-block;padding:1px 6px;border-radius:3px;font-size:12px}
          .tag-buy{background:#500;color:#f88}
          .tag-sell{background:#050;color:#8f8}
          .tag-watch{background:#540;color:#fc8}
          .muted{color:#666;font-size:12px}
          .page-info{color:#666;font-size:13px;margin:10px 0}
          .filter-bar{margin-bottom:14px;display:flex;gap:10px;align-items:center;flex-wrap:wrap}
          .filter-bar select,.filter-bar input{background:#222;color:#eee;border:1px solid #444;
            padding:4px 8px;border-radius:4px;font-family:monospace}
          .empty{color:#555;padding:30px;text-align:center}
        </style>
        """

        _NAV = """
        <nav>
          <a href="/" {active_orders}>指令单</a>
          <a href="/signals" {active_signals}>扫描信号</a>
        </nav>
        """

        HTML_ORDERS = _BASE_STYLE + """
        <!DOCTYPE html><html lang="zh-CN">
        <head><meta charset="UTF-8"><meta http-equiv="refresh" content="{{ refresh }}">
        <title>Alpha量化 - 指令单</title>
        """ + _BASE_STYLE + """
        </head>
        <body>
        <h1>Alpha量化 实时看板</h1>
        <p class="muted">更新时间: {{ now }}</p>
        """ + _NAV.replace("{active_orders}", 'class="active"').replace("{active_signals}", "") + """
        <h2>待确认指令单</h2>
        {% if orders %}
        <table>
          <tr><th>代码</th><th>名称</th><th>方向</th><th>价格</th><th>数量</th><th>金额</th><th>原因</th></tr>
          {% for o in orders %}
          <tr>
            <td>{{ o.code }}</td><td>{{ o.name }}</td>
            <td><span class="tag tag-{{ o.direction|lower }}">{{ o.direction }}</span></td>
            <td>¥{{ "%.3f"|format(o.price) }}</td>
            <td>{{ o.quantity }}</td>
            <td>¥{{ "{:,.0f}".format(o.amount) }}</td>
            <td>{{ o.reason }}</td>
          </tr>
          {% endfor %}
        </table>
        {% else %}
        <div class="empty">暂无待确认指令单</div>
        {% endif %}
        </body></html>
        """

        HTML_SIGNALS = """
        <!DOCTYPE html><html lang="zh-CN">
        <head><meta charset="UTF-8">
        <title>Alpha量化 - 扫描信号</title>
        """ + _BASE_STYLE + """
        </head>
        <body>
        <h1>Alpha量化 实时看板</h1>
        <p class="muted">更新时间: {{ now }}</p>
        """ + _NAV.replace("{active_orders}", "").replace("{active_signals}", 'class="active"') + """
        <h2>扫描信号 &nbsp;<span class="muted">{{ date }}</span></h2>
        <div class="filter-bar">
          <label>日期:
            <select onchange="location.href='/signals?date='+this.value">
              {% for d in dates %}
              <option value="{{ d }}" {% if d == date %}selected{% endif %}>{{ d }}</option>
              {% endfor %}
            </select>
          </label>
          <label>类型:
            <select id="sigFilter" onchange="filterTable()">
              <option value="">全部</option>
              <option value="BUY">BUY</option>
              <option value="SELL">SELL</option>
              <option value="WATCH">WATCH</option>
            </select>
          </label>
          <label>搜索: <input id="searchBox" oninput="filterTable()" placeholder="代码/名称"></label>
        </div>
        {% if signals %}
        <p class="page-info">共 {{ signals|length }} 条信号（按评分降序）</p>
        <table id="sigTable">
          <tr><th>#</th><th>代码</th><th>名称</th><th>信号</th><th>价格</th><th>评分</th><th>原因</th><th>时间</th></tr>
          {% for s in signals %}
          <tr data-sig="{{ s.signal }}">
            <td class="muted">{{ loop.index }}</td>
            <td>{{ s.code }}</td>
            <td>{{ s.name }}</td>
            <td><span class="tag tag-{{ s.signal|lower }}">{{ s.signal }}</span></td>
            <td>¥{{ "%.3f"|format(s.price) }}</td>
            <td class="score">{{ "%.1f"|format(s.score) }}</td>
            <td>{{ s.reason }}</td>
            <td class="muted">{{ s.generated_at[:16] if s.generated_at else "" }}</td>
          </tr>
          {% endfor %}
        </table>
        {% else %}
        <div class="empty">{{ date }} 暂无信号，请先运行 scan</div>
        {% endif %}
        <script>
        function filterTable(){
          var sig=document.getElementById('sigFilter').value.toUpperCase();
          var kw=document.getElementById('searchBox').value.toLowerCase();
          var rows=document.querySelectorAll('#sigTable tr[data-sig]');
          rows.forEach(function(r){
            var match=(sig===''||r.dataset.sig===sig);
            var text=r.innerText.toLowerCase();
            match=match&&(kw===''||text.indexOf(kw)>=0);
            r.style.display=match?'':'none';
          });
        }
        </script>
        </body></html>
        """

        @app.route("/")
        def index():
            from datetime import datetime as dt
            orders = trader.list_pending_orders()
            return render_template_string(
                HTML_ORDERS,
                orders=orders,
                now=dt.now().strftime("%Y-%m-%d %H:%M:%S"),
                refresh=WEB["auto_refresh"],
            )

        @app.route("/signals")
        def signals_page():
            from datetime import datetime as dt
            from signals.manager import load_signals
            import os, re

            # 列出所有已有信号文件的日期
            sig_dir = Path("data/processed")
            dates = sorted([
                re.sub(r"signals_(.+)\.json", r"\1", f.name)
                for f in sig_dir.glob("signals_*.json")
            ], reverse=True)

            date = request.args.get("date", dates[0] if dates else dt.now().strftime("%Y-%m-%d"))
            raw = load_signals(date)
            # 按评分降序
            raw.sort(key=lambda x: x.get("score", 0), reverse=True)

            return render_template_string(
                HTML_SIGNALS,
                signals=raw,
                date=date,
                dates=dates,
                now=dt.now().strftime("%Y-%m-%d %H:%M:%S"),
            )

        @app.route("/api/orders")
        def api_orders():
            return jsonify(trader.list_pending_orders())

        @app.route("/api/signals")
        def api_signals():
            from datetime import datetime as dt
            from signals.manager import load_signals
            date = request.args.get("date", dt.now().strftime("%Y-%m-%d"))
            data = load_signals(date)
            data.sort(key=lambda x: x.get("score", 0), reverse=True)
            return jsonify(data)

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


def cmd_init_meta(start: str = "2024-01-02"):
    """初始化 meta 域（行业分类 + 申万指数 + 交易日历）"""
    from data.downloader_meta import MetaDownloader
    dl = MetaDownloader()
    dl.init_db(start=start)


def cmd_init_fundamental(start_year: int = 2024, force: bool = False):
    """初始化 fundamental 域（季频财务数据）"""
    from data.downloader_fundamental import FundamentalDownloader
    dl = FundamentalDownloader()
    dl.init_db(start_year=start_year, force=force)


def cmd_init_factor(start: str = "2024-01-02"):
    """初始化 factor 域（资金流向 + 北向资金）"""
    from data.downloader_factor import FactorDownloader
    dl = FactorDownloader()
    dl.init_db(start=start)


def cmd_init_macro(start: str = "2024-01-01"):
    """初始化 macro 域（宏观经济数据）"""
    from data.downloader_macro import MacroDownloader
    dl = MacroDownloader()
    dl.init_db(start=start)


def cmd_init_tushare(start: str = "2024-01-01",
                     moneyflow_start: str = "2010-01-01",
                     types: list | None = None) -> None:
    """初始化 tushare 域"""
    from data.downloader_tushare import TushareDownloader
    dl = TushareDownloader()
    types = types or ["moneyflow", "daily", "top_list", "margin", "moneyflow_ind", "moneyflow_mkt"]
    if "moneyflow" in types:
        dl.init_moneyflow(start=moneyflow_start)
    if "daily" in types:
        dl.init_daily(start=start)
    if "top_list" in types:
        dl.init_top_list(start=start)
    if "margin" in types:
        dl.init_margin(start=start)
    if "moneyflow_ind" in types:
        dl.init_moneyflow_ind(start=start)
    if "moneyflow_mkt" in types:
        dl.init_moneyflow_mkt(start=start)


def cmd_update_tushare(types: list | None = None) -> None:
    """增量更新 tushare 域"""
    from data.downloader_tushare import TushareDownloader
    dl = TushareDownloader()
    types = types or ["moneyflow", "daily", "top_list", "margin", "moneyflow_ind", "moneyflow_mkt"]
    if "moneyflow" in types:
        dl._update_moneyflow()
    if "daily" in types:
        dl._update_daily()
    if "top_list" in types:
        dl._update_top_list()
    if "margin" in types:
        dl._update_margin()
    if "moneyflow_ind" in types:
        dl._update_moneyflow_ind()
    if "moneyflow_mkt" in types:
        dl._update_moneyflow_mkt()


def cmd_update_all():
    """更新所有数据域（market + meta + fundamental + factor + macro + tushare）"""
    logger.info("开始全域增量更新...")

    print("\n[1/6] 更新 K 线数据...")
    cmd_update()

    print("\n[2/6] 更新 meta 域...")
    from data.downloader_meta import MetaDownloader
    MetaDownloader().update_db()

    print("\n[3/6] 更新 fundamental 域...")
    from data.downloader_fundamental import FundamentalDownloader
    FundamentalDownloader().update_db()

    print("\n[4/6] 更新 factor 域...")
    from data.downloader_factor import FactorDownloader
    FactorDownloader().update_db()

    print("\n[5/6] 更新 macro 域...")
    from data.downloader_macro import MacroDownloader
    MacroDownloader().update_db()

    print("\n[6/6] 更新 tushare 域...")
    try:
        cmd_update_tushare()
    except RuntimeError as e:
        print(f"  tushare 跳过: {e}")

    logger.info("全域增量更新完成！")


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
  python main.py init-meta                     # 初始化行业分类+申万指数+交易日历
  python main.py init-fundamental              # 初始化季频财务数据（2024起）
  python main.py init-fundamental --start-year 2022  # 从2022年开始
  python main.py init-factor                   # 初始化资金流向+北向资金
  python main.py init-macro                    # 初始化宏观经济数据
  python main.py init-tushare                          # 初始化 tushare 域（moneyflow 2010起，其余 2024起）
  python main.py init-tushare --types moneyflow        # 只下载资金流向
  python main.py init-tushare --moneyflow-start 2015-01-01  # 资金流向从2015年开始
  python main.py update-tushare                        # 增量更新 tushare 域
  python main.py update-all                            # 更新所有域（含 tushare）
  python main.py scan                          # 今日全市场扫描（A股）
  python main.py scan --date 2024-11-01        # 指定日期扫描
  python main.py scan --types etf              # 扫描 ETF（动量轮动）
  python main.py scan --types stock etf        # 同时扫描 A股 + ETF
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
    p_scan.add_argument("--types", nargs="*",
                        default=["stock"],
                        choices=["stock", "etf"],
                        help="扫描资产类型（默认 stock；可传 etf 或 stock etf）")

    # workflow（分层决策：L1→L2→L3）
    p_wf = sub.add_parser("workflow", help="运行分层决策 workflow（L1宏观→L2行业→L3个股，可视化）")
    p_wf.add_argument("--date", help="决策日期 YYYY-MM-DD（默认今日）")
    p_wf.add_argument("--industries", nargs="*",
                      help="手动指定申万行业代码（如 801080 801770），限定扫描范围加速验证")
    p_wf.add_argument("--types", nargs="*",
                      default=["stock"],
                      choices=["stock", "etf"],
                      help="资产类型（默认 stock）")

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

    # init-meta
    p_init_meta = sub.add_parser("init-meta", help="初始化 meta 域（行业分类+申万指数+交易日历）")
    p_init_meta.add_argument("--start", default="2024-01-02", help="起始日期（默认2024-01-02）")

    # init-fundamental
    p_init_fund = sub.add_parser("init-fundamental", help="初始化 fundamental 域（季频财务数据）")
    p_init_fund.add_argument("--start-year", type=int, default=2024, help="起始年份（默认2024）")
    p_init_fund.add_argument("--force", action="store_true", help="强制重新下载已有数据")

    # init-factor
    p_init_factor = sub.add_parser("init-factor", help="初始化 factor 域（资金流向+北向资金）")
    p_init_factor.add_argument("--start", default="2024-01-02", help="起始日期（默认2024-01-02）")

    # init-macro
    p_init_macro = sub.add_parser("init-macro", help="初始化 macro 域（宏观经济数据）")
    p_init_macro.add_argument("--start", default="2024-01-01", help="起始日期（默认2024-01-01）")

    # init-tushare
    p_init_ts = sub.add_parser("init-tushare", help="初始化 tushare 域（moneyflow/daily/top_list/margin）")
    p_init_ts.add_argument("--start", default="2024-01-01", help="日线/龙虎榜/融资融券起始日期（默认2024-01-01）")
    p_init_ts.add_argument("--moneyflow-start", default="2010-01-01",
                           help="资金流向起始日期（默认2010-01-01）")
    p_init_ts.add_argument("--types", nargs="*",
                           default=["moneyflow", "daily", "top_list", "margin", "moneyflow_ind", "moneyflow_mkt"],
                           choices=["moneyflow", "daily", "top_list", "margin", "moneyflow_ind", "moneyflow_mkt"],
                           help="下载类型（默认全部）")

    # update-tushare
    p_upd_ts = sub.add_parser("update-tushare", help="增量更新 tushare 域")
    p_upd_ts.add_argument("--types", nargs="*",
                          default=["moneyflow", "daily", "top_list", "margin", "moneyflow_ind", "moneyflow_mkt"],
                          choices=["moneyflow", "daily", "top_list", "margin", "moneyflow_ind", "moneyflow_mkt"],
                          help="更新类型（默认全部）")

    # update-all
    sub.add_parser("update-all", help="更新所有数据域（market+meta+fundamental+factor+macro）")

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
        cmd_scan(date=args.date, asset_types=args.types)
    elif args.command == "workflow":
        cmd_workflow(date=args.date, industries=args.industries,
                     asset_types=args.types)
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
    elif args.command == "init-meta":
        cmd_init_meta(start=args.start)
    elif args.command == "init-fundamental":
        cmd_init_fundamental(start_year=args.start_year, force=args.force)
    elif args.command == "init-factor":
        cmd_init_factor(start=args.start)
    elif args.command == "init-macro":
        cmd_init_macro(start=args.start)
    elif args.command == "init-tushare":
        cmd_init_tushare(
            start=args.start,
            moneyflow_start=args.moneyflow_start,
            types=args.types,
        )
    elif args.command == "update-tushare":
        cmd_update_tushare(types=args.types)
    elif args.command == "update-all":
        cmd_update_all()
    else:
        parser.print_help()
