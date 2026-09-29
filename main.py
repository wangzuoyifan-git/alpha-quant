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


def _resolve_stock_codes(queries: list[str]) -> list[str] | None:
    """
    将股票查询（代码或名称）解析为6位代码列表。
    - 6位纯数字 → 直接当代码
    - 其他 → ipo_dates 中精确/模糊匹配名称
    - 多候选 → 打印候选让用户明确，返回 None
    """
    import data.store as store
    ipo = store.load_ipo_dates()
    stock_ipo = {k: v for k, v in ipo.items() if v.get("type") == "stock"}

    result = []
    for q in queries:
        if q.isdigit() and len(q) == 6:
            result.append(q)
            continue
        exact = [k.split(":")[1] for k, v in stock_ipo.items() if v.get("name") == q]
        if exact:
            result.extend(exact)
            continue
        fuzzy = [(k.split(":")[1], v.get("name", ""))
                 for k, v in stock_ipo.items() if q in v.get("name", "")]
        if not fuzzy:
            print(f"[错误] 找不到股票 '{q}'，请检查名称或代码")
            return None
        if len(fuzzy) == 1:
            result.append(fuzzy[0][0])
            continue
        print(f"[提示] '{q}' 匹配到 {len(fuzzy)} 只股票，请用更精确的名称或代码：")
        for code, name in fuzzy[:20]:
            print(f"  {code}  {name}")
        if len(fuzzy) > 20:
            print(f"  ...（共 {len(fuzzy)} 个）")
        return None
    return result


def _resolve_sw_industries(queries: list[str], level: str = "L1") -> list[str] | None:
    """
    将申万行业查询（代码或名称）解析为标准行业代码列表。
    - 纯数字（6位）或带后缀（如801080.SI）→ 直接当代码
    - 其他 → industry_member 中精确/模糊匹配行业名称
    - 多候选 → 打印让用户明确，返回 None
    """
    from data.store_meta import load as load_meta
    member = load_meta("industry_member")
    if member is None or member.empty:
        print("[错误] 申万行业分类数据不存在，请先运行 init-meta")
        return None

    name_col = {"L1": "l1_name", "L2": "l2_name", "L3": "l3_name"}.get(level, "l1_name")
    code_col = {"L1": "l1_code", "L2": "l2_code", "L3": "l3_code"}.get(level, "l1_code")
    # 去重行业列表
    ind_df = member[[code_col, name_col]].drop_duplicates().reset_index(drop=True)

    result = []
    for q in queries:
        # 已是代码形式（纯数字或带.SI）
        q_norm = q.split(".")[0]
        if q_norm.isdigit():
            full = q if "." in q else f"{q}.SI"
            result.append(full)
            continue
        # 精确名称匹配
        exact = ind_df[ind_df[name_col] == q]
        if not exact.empty:
            result.extend(exact[code_col].tolist())
            continue
        # 模糊匹配
        fuzzy = ind_df[ind_df[name_col].str.contains(q, na=False)]
        if fuzzy.empty:
            print(f"[错误] 找不到申万行业 '{q}'")
            sample = ind_df[name_col].head(8).tolist()
            print(f"  示例：{sample}")
            return None
        if len(fuzzy) == 1:
            result.extend(fuzzy[code_col].tolist())
            continue
        print(f"[提示] '{q}' 匹配到 {len(fuzzy)} 个申万{level}行业，请用更精确的名称：")
        for _, row in fuzzy.iterrows():
            print(f"  {row[code_col]}  {row[name_col]}")
        return None
    return result


def cmd_scan(date: str | None = None, asset_types: list | None = None,
             strategy: str | None = None, codes: list | None = None,
             industries: list | None = None, industry_level: str = "L1"):
    """执行扫描并推送信号。支持单策略/指定股票快速验证 L3。"""
    from rich.console import Console
    from rich.table import Table
    from rich.panel import Panel

    console = Console()
    asset_types = asset_types or ["stock"]
    label = "+".join(asset_types)

    # 名称/代码解析（--codes 和 --industries 均支持名称输入）
    if codes:
        codes = _resolve_stock_codes(codes)
        if codes is None:
            return  # 有歧义，已打印候选，让用户重新输入
    elif industries:
        resolved = _resolve_sw_industries(industries, level=industry_level)
        if resolved is None:
            return
        from data.store_meta import get_member_stocks
        codes = get_member_stocks(resolved, level=industry_level)
        industries = resolved  # 用于 pool_label 显示标准化后的名称
        if not codes:
            console.print(f"[yellow]行业 {resolved} 无成员股数据，请先运行 init-meta[/yellow]")
            return

    # 策略选择
    strat_map = {strategy: BUILTIN_STRATEGIES[strategy]} if strategy else BUILTIN_STRATEGIES
    strat_label = strategy or f"全部({len(strat_map)}个)"
    pool_label = (f"{len(codes)}只（行业 {industries}）" if industries and codes
                  else f"{len(codes)}只" if codes else "全市场")
    console.print(Panel(
        f"[bold cyan]Alpha量化 - L3 策略扫描[/bold cyan]\n"
        f"策略: [yellow]{strat_label}[/yellow]  "
        f"资产: [yellow]{label}[/yellow]  "
        f"日期: [yellow]{date or '今日'}[/yellow]  "
        f"股票池: [yellow]{pool_label}[/yellow]",
        expand=False
    ))

    engine = ScanEngine(fetcher, SCAN)
    engine.load_strategies(strat_map)

    # 指定股票池时跳过全市场扫描（直接加载指定代码）
    if codes:
        import data.store as store
        from strategies.base import Signal as Sig
        ipo_dates = store.load_ipo_dates()
        from datetime import datetime, timedelta
        max_bars = max(s.requires_bars() for s in engine.strategies.values())
        end = date or datetime.now().strftime("%Y-%m-%d")
        start = (datetime.strptime(end, "%Y-%m-%d") -
                 timedelta(days=int(max_bars * 1.5))).strftime("%Y-%m-%d")
        signals = []
        for code in codes:
            df = store.load(code, asset_type="stock", market="cn", start=start, end=end)
            if df is None or df.empty:
                console.print(f"[dim]  {code}: 无本地数据[/dim]")
                continue
            df = df.reset_index()
            name = ipo_dates.get(f"cn:{code}", {}).get("name", code)
            for strat in engine.strategies.values():
                allowed = getattr(strat, "applicable_asset_types", [])
                if allowed and "stock" not in allowed:
                    continue
                sigs = strat.generate_signals(df, code, name)
                signals.extend(sigs)
        signals.sort(key=lambda s: s.score, reverse=True)
    else:
        signals = engine.scan(date=date, asset_types=asset_types)

    if not signals:
        console.print("[yellow]无触发信号[/yellow]")
        return

    # 为 BUY/WATCH 信号附加操作建议（委托价 + 建议股数）
    from strategies.position_manager import calc_position_size, calc_r_ratio
    from data.portfolio import load_portfolio
    _pf = load_portfolio()
    _capital = _pf.get("total_capital") or RISK_CONTROL.get("default_capital", 2_000_000)
    _risk = RISK_CONTROL.get("risk_per_trade", 0.01)
    _max_pos = RISK_CONTROL.get("max_single_position", 0.10)
    _min_r = RISK_CONTROL.get("min_r_ratio", 1.5)

    # 有 df 的情况下（--codes 模式）预先计算 exit_levels
    _exit_cache: dict = {}  # code -> ExitLevels
    if codes and "df_cache" in dir():
        pass  # df_cache 不存在，下面用 store 单独读
    if codes:
        import data.store as _store2
        _ipo2 = _store2.load_ipo_dates()
        for _sig in signals:
            if _sig.signal not in ("BUY", "WATCH") or _sig.code in _exit_cache:
                continue
            try:
                from datetime import datetime as _dt2, timedelta as _td2
                _max_b = max(s.requires_bars() for s in engine.strategies.values())
                _end2 = date or _dt2.now().strftime("%Y-%m-%d")
                _start2 = (_dt2.strptime(_end2, "%Y-%m-%d") - _td2(days=int(_max_b * 1.5))).strftime("%Y-%m-%d")
                _df2 = _store2.load(_sig.code, asset_type="stock", market="cn", start=_start2, end=_end2)
                if _df2 is not None and not _df2.empty:
                    # 找认领策略（产出该信号的策略）
                    _strat_inst = next(iter(engine.strategies.values()))
                    for _k, _st in engine.strategies.items():
                        _allowed = getattr(_st, "applicable_asset_types", [])
                        if _allowed and "stock" not in _allowed:
                            continue
                        _test_sigs = _st.generate_signals(_df2.reset_index(), _sig.code, _sig.name)
                        if any(ts.signal == _sig.signal for ts in _test_sigs):
                            _strat_inst = _st
                            break
                    _el = _strat_inst.compute_exit_levels(_df2.reset_index(), entry_price=float(_df2.iloc[-1]["close"]))
                    _exit_cache[_sig.code] = _el
            except Exception:
                pass

    # 按信号类型分组展示
    buy_sigs  = [s for s in signals if s.signal == "BUY"]
    sell_sigs = [s for s in signals if s.signal == "SELL"]
    watch_sigs = [s for s in signals if s.signal == "WATCH"]

    def _render_table(sigs, title, color):
        if not sigs:
            return
        show_ops = sigs[0].signal in ("BUY", "WATCH")
        t = Table(title=title, show_lines=True)
        t.add_column("代码", style="cyan", width=8)
        t.add_column("名称", width=12)
        t.add_column("价格", justify="right", width=8)
        t.add_column("评分", justify="right", width=6)
        t.add_column("持仓类型", width=8)
        t.add_column("触发原因")
        if show_ops:
            t.add_column("委托参考价", justify="right", width=10)
            t.add_column("建议股数", justify="right", width=9)
            t.add_column("止损价", justify="right", width=8)
        t.add_column("事件警告", style="yellow")
        for s in sigs:
            warnings = ", ".join(s.extra.get("event_warnings", []))
            row = [
                s.code, s.name,
                f"¥{s.price:.2f}",
                f"[{color}]{s.score:.0f}[/{color}]",
                s.position_type,
                s.reason,
            ]
            if show_ops:
                from strategies.position_manager import calc_entry_bid
                el = _exit_cache.get(s.code)
                _exit_logic = el.exit_logic if el else "atr"
                _pct = float(s.extra.get("pct_chg", 0.0))
                bid, bid_note = calc_entry_bid(s.price, _exit_logic, s.score,
                                               _pct, s.position_type)
                if el and el.stop_loss > 0:
                    stop = el.stop_loss
                    shares = calc_position_size(s.price, stop, _capital, _risk, _max_pos)
                    r = calc_r_ratio(s.price, stop, el.take_profit)
                    r_tag = f" R={r:.1f}" if r > 0 else ""
                    shares_str = f"{shares}股{r_tag}"
                    stop_str = f"¥{stop:.2f}"
                elif el and el.stop_loss == 0:
                    shares_str = "配置仓"
                    stop_str = "基本面"
                else:
                    est_stop = round(s.price * (1 - RISK_CONTROL.get("stop_loss_ratio", 0.07)), 2)
                    shares = calc_position_size(s.price, est_stop, _capital, _risk, _max_pos)
                    shares_str = f"~{shares}股(估)"
                    stop_str = f"¥{est_stop:.2f}(估)"
                row += [f"¥{bid:.2f}\n[dim]{bid_note}[/dim]", shares_str, stop_str]
            row.append(warnings or "-")
            t.add_row(*row)
        console.print(t)

    _render_table(buy_sigs,   f"BUY 信号（{len(buy_sigs)} 个）",   "red")
    _render_table(sell_sigs,  f"SELL 信号（{len(sell_sigs)} 个）",  "green")
    _render_table(watch_sigs, f"WATCH 信号（{len(watch_sigs)} 个）（配置仓候选）", "magenta")

    console.print(f"\n[bold]合计: {len(signals)} 个信号（BUY {len(buy_sigs)} / SELL {len(sell_sigs)} / WATCH {len(watch_sigs)}）[/bold]")

    # 非指定股票模式才推送/持久化
    if not codes:
        # A2：为信号附加 P0 辅助分析（技术面/资金流/基本面），写入 extra 随信号持久化
        from agent_tools.analysis import enrich_signal_objects
        enrich_signal_objects(signals, date=date)
        notifier.send_signals(signals)
        from signals.manager import save_signals
        save_signals(signals, date=date)
        orders = trader.generate_orders(signals)
        if orders:
            console.print(f"[bold]已生成 {len(orders)} 份指令单，运行 `python main.py orders` 查看[/bold]")


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
                 asset_types: list | None = None,
                 industry_level: str = "L1"):
    """运行分层决策 workflow（L1宏观 → L2行业 → L3个股），分层可视化输出。

    :param date: 决策日期（默认今日）
    :param industries: 手动指定申万行业代码列表（如 801080 801770），
                       跳过 L2 自动产出的全部行业，只扫描指定行业（快速验证）。
    :param asset_types: 资产类型（默认 stock）
    :param industry_level: 申万行业层级 L1/L2/L3（默认 L1）
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
        console.print(f"[yellow]行业范围限定（override，{industry_level}）:[/yellow] {industries}")
    elif industry_level != "L1":
        console.print(f"[yellow]行业层级:[/yellow] {industry_level}（个股资金流聚合评分）")
    console.print("[dim]运行中（L3 扫描可能需要一些时间）...[/dim]\n")

    result = run_daily_workflow(
        date=date,
        asset_types=asset_types or ["stock"],
        industry_override=industries,
        industry_level=industry_level,
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


def _build_aux_summary(extra: dict) -> str:
    """从持久化的 extra.analysis 构建 A2 辅助分析精简摘要（Web 看板用）。"""
    aux = (extra or {}).get("analysis")
    if not aux:
        return ""
    parts = []
    tech = aux.get("technical", {})
    if tech.get("available"):
        if tech.get("pct_chg") is not None:
            parts.append(f"涨跌{tech['pct_chg']:+.1f}%")
        if tech.get("volume_ratio") is not None:
            parts.append(f"量比{tech['volume_ratio']:.2f}")
        if tech.get("turnover") is not None:
            parts.append(f"换手{tech['turnover']:.1f}%")
    mf = aux.get("moneyflow", {})
    if mf.get("available") and mf.get("main_net_inflow_5d") is not None:
        parts.append(f"主力5日{mf['main_net_inflow_5d']/10000:+.2f}亿")
    fund = aux.get("fundamental", {})
    if fund.get("available"):
        if fund.get("roe_ttm") is not None:
            parts.append(f"ROE{fund['roe_ttm']:.1f}%")
        if fund.get("net_profit_yoy") is not None:
            parts.append(f"净利同比{fund['net_profit_yoy']:+.1f}%")
        if fund.get("pe_ttm") is not None:
            parts.append(f"PE{fund['pe_ttm']:.1f}")
        if fund.get("pb_mrq") is not None:
            parts.append(f"PB{fund['pb_mrq']:.2f}")
    return " | ".join(parts)


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
          <tr><th>#</th><th>代码</th><th>名称</th><th>信号</th><th>价格</th><th>评分</th><th>原因</th><th>辅助分析(P0)</th><th>时间</th></tr>
          {% for s in signals %}
          <tr data-sig="{{ s.signal }}">
            <td class="muted">{{ loop.index }}</td>
            <td>{{ s.code }}</td>
            <td>{{ s.name }}</td>
            <td><span class="tag tag-{{ s.signal|lower }}">{{ s.signal }}</span></td>
            <td>¥{{ "%.3f"|format(s.price) }}</td>
            <td class="score">{{ "%.1f"|format(s.score) }}</td>
            <td>{{ s.reason }}</td>
            <td class="muted" style="font-size:12px">{{ s.aux_summary or "数据不可用" }}</td>
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

            # A2：从 extra.analysis 构建辅助分析精简摘要
            for s in raw:
                s["aux_summary"] = _build_aux_summary(s.get("extra", {}))

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


def _build_kline_downloader(workers: int = 1):
    """构建 K 线下载器。

    主链路优先 Tushare（已付费、稳定）；未配置 token 时降级 baostock。
    两个下载器接口对齐（init_db / update_db），上层无需感知差异。
    """
    token = DATA_SOURCE.get("tushare_token", "")
    if token:
        import tushare as ts
        from data.downloader_tushare import TushareKlineDownloader
        # timeout=20：单接口硬超时（防止网络异常时单只死堵）
        pro = ts.pro_api(token, timeout=20)
        logger.info("K线主下载源: Tushare（pro_bar hfq）")
        return TushareKlineDownloader(pro, workers=workers)
    logger.warning("未配置 TUSHARE_TOKEN，K线下载降级为 baostock")
    return KlineDownloader(fetcher, workers=workers)


def cmd_init(start: str = "2024-01-02", end: str | None = None, force: bool = False,
             workers: int = 1, types: list | None = None):
    """首次全量下载全市场 K 线数据（支持断点续传）"""
    dl = _build_kline_downloader(workers=workers)
    dl.init_db(start=start, end=end, force=force, types=types)


def cmd_update(end: str | None = None, workers: int = 1, types: list | None = None):
    """增量更新：补全每只证券到最新交易日"""
    dl = _build_kline_downloader(workers=workers)
    dl.update_db(end=end, types=types)


def cmd_init_meta(start: str = "2024-01-02"):
    """初始化 meta 域（行业分类 + 申万指数 + 交易日历）"""
    from data.downloader_meta import MetaDownloader
    dl = MetaDownloader()
    dl.init_db(start=start)


def cmd_init_sw_classify():
    """单独下载申万三级行业分类（Tushare SW2021，代码匹配）"""
    from data.downloader_meta import MetaDownloader
    dl = MetaDownloader()
    dl.download_sw_classify()


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


def cmd_update_macro():
    """增量更新 macro 域（含汇率与境外指标）"""
    from data.downloader_macro import MacroDownloader
    MacroDownloader().update_db()


def cmd_update_events():
    """增量更新 events 域（E1 日历 + E2 企业事件）"""
    from data.downloader_events import update_events
    update_events()


def cmd_init_hk(start: str = "2020-01-02", end: str | None = None,
                force: bool = False, connect_only: bool = True):
    """首次全量下载港股 K 线（默认仅港股通标的）"""
    from data.downloader_hk import HKDownloader
    dl = HKDownloader(connect_only=connect_only)
    dl.init_db(start=start, end=end, force=force)


def cmd_update_hk(end: str | None = None, connect_only: bool = True):
    """增量更新港股 K 线到最新交易日"""
    from data.downloader_hk import HKDownloader
    dl = HKDownloader(connect_only=connect_only)
    dl.update_db(end=end)


def cmd_update_hk_valuation(connect_only: bool = True, period: str = "近三年"):
    """更新港股 PE/PB 估值（百度股市通）并合并进 K 线"""
    from data.downloader_hk import HKDownloader
    HKDownloader(connect_only=connect_only).update_valuation(period=period)


def cmd_update_hk_moneyflow(connect_only: bool = True):
    """更新港股个股资金流（东财，滚动约 100 个交易日）"""
    from data.downloader_hk import HKDownloader
    HKDownloader(connect_only=connect_only).update_moneyflow()


def cmd_update_hk_financials(connect_only: bool = True):
    """更新港股财务（东财三大报表 + 成长/杜邦指标）"""
    from data.downloader_hk import HKDownloader
    HKDownloader(connect_only=connect_only).update_financials()


def cmd_update_hk_events(connect_only: bool = True):
    """更新港股分红派息事件（写入 events E2）"""
    from data.downloader_hk import HKDownloader
    HKDownloader(connect_only=connect_only).update_dividend_events()


def cmd_update_cn_pcf(codes: list | None = None):
    """补充 A 股 pcf_ttm（百度市现率）"""
    from data.downloader_valuation import update_cn_pcf
    update_cn_pcf(codes=codes)


def cmd_init_moneyflow(start: str = "2024-01-01",
                       stock_start: str = "2010-01-01",
                       types: list | None = None) -> None:
    """初始化 moneyflow 域（资金流与情绪）"""
    from data.downloader_moneyflow import MoneyflowDownloader
    dl = MoneyflowDownloader()
    types = types or ["stock", "top_list", "top_inst", "block_trade",
                      "margin", "industry", "market", "northbound"]
    if "stock" in types:
        dl.init_stock(start=stock_start)
    if "top_list" in types:
        dl.init_top_list(start=start)
    if "top_inst" in types:
        dl.init_top_inst(start=start)
    if "block_trade" in types:
        dl.init_block_trade(start=start)
    if "margin" in types:
        dl.init_margin(start=start)
    if "industry" in types:
        dl.init_industry(start=start)
    if "market" in types:
        dl.init_market(start=start)
    if "northbound" in types:
        dl.init_northbound()


def cmd_update_moneyflow(types: list | None = None) -> None:
    """增量更新 moneyflow 域"""
    from data.downloader_moneyflow import MoneyflowDownloader
    dl = MoneyflowDownloader()
    types = types or ["stock", "top_list", "top_inst", "block_trade",
                      "margin", "industry", "market", "northbound"]
    if "stock" in types:
        dl._update_stock()
    if "top_list" in types:
        dl._update_top_list()
    if "top_inst" in types:
        dl._update_top_inst()
    if "block_trade" in types:
        dl._update_block_trade()
    if "margin" in types:
        dl._update_margin()
    if "industry" in types:
        dl._update_industry()
    if "market" in types:
        dl._update_market()
    if "northbound" in types:
        dl._update_northbound()


def cmd_update_hk_hold(codes: list | None = None, from_portfolio: bool = False,
                       from_lhb: int | None = None):
    """下载北向个股持股（Tushare hk_hold，按代码，普通积分账户为季度披露）。

    标的来源可组合：--codes 显式指定 / --from-portfolio 当前持仓 /
    --from-lhb N 最近 N 个龙虎榜上榜交易日涉及的股票。
    """
    from data.downloader_moneyflow import MoneyflowDownloader

    targets = [str(c) for c in (codes or []) if str(c).strip()]

    if from_portfolio:
        try:
            from data.portfolio import load_portfolio
            for h in load_portfolio().get("holdings", []):
                c = h.get("code")
                if c:
                    targets.append(str(c))
        except Exception as e:
            print(f"[警告] 读取持仓文件失败: {e}")

    if from_lhb:
        try:
            import data.store_moneyflow as store_mf
            dates = sorted(store_mf.list_dates("top_inst"))
            if not dates:
                print("[警告] 无 top_inst 数据，跳过 --from-lhb（请先 update-moneyflow --types top_inst）")
            else:
                from agents.smart_money_agent import load_seat_episodes
                eps = load_seat_episodes(start=dates[-int(from_lhb)])
                targets.extend(eps["code"].astype(str).tolist())
        except Exception as e:
            print(f"[警告] 读取近期龙虎榜失败: {e}")

    targets = sorted({str(c).split(".")[0] for c in targets if str(c).strip()})
    if not targets:
        print("请用 --codes 指定代码，或用 --from-portfolio / --from-lhb N 收集标的")
        return
    print(f"北向个股持股（hk_hold）更新：{len(targets)} 只")
    MoneyflowDownloader().update_hk_hold(targets)


def cmd_smart_money(target: str = None, target_type: str = "auto", mode: str = "full",
                    window_months: int = 12, horizons: str = "5,10,20",
                    fetch: bool = False, as_json: bool = False,
                    as_markdown: bool = False, rank: bool = False,
                    top_n: int = 20, recent_lhb: bool = False,
                    days: int = 1, history_months: int = 0):
    """资金主体行为画像：识别谁在买卖 + 跨期行为模式（描述性，不构成投资建议）"""
    from rich.console import Console
    from rich.table import Table
    from rich.panel import Panel
    from agents.smart_money_agent import (
        build_profile, report_to_markdown, scan_recent_lhb, recent_lhb_to_markdown,
        parse_horizons)

    console = Console()

    # ── 盘前龙虎榜标的扫描（近期标的 + 席位历史画像）──
    if recent_lhb:
        result = scan_recent_lhb(days=days, history_months=history_months,
                                 top_n=top_n, horizons=parse_horizons(horizons))
        if as_json:
            import json
            console.print_json(json.dumps(result, ensure_ascii=False, default=str))
            return
        if as_markdown:
            console.print(recent_lhb_to_markdown(result))
            return
        if not result.get("available"):
            console.print(f"[yellow]{'; '.join(result.get('notes', [])) or '无数据'}[/yellow]")
            return
        console.print(Panel(
            f"[bold cyan]盘前龙虎榜标的与席位画像[/bold cyan]\n"
            f"披露日: [yellow]{', '.join(result['dates'])}[/yellow]  "
            f"标的: [yellow]{result['target_count']}[/yellow]  "
            f"涉及席位: [yellow]{result['seat_count']}[/yellow]  "
            f"席位历史窗口: [yellow]{result['window']['start']}~{result['window']['end']}[/yellow]",
            expand=False))
        ov = Table(title="标的概览", border_style="green")
        for c in ("日期", "代码", "名称", "涨跌%", "龙虎榜净额(亿)", "买卖(亿)", "席位数"):
            ov.add_column(c)
        for t in result["targets"]:
            pct = f"{t['pct_change']:+.2f}" if t.get("pct_change") is not None else "-"
            ov.add_row(t["date"], t["code"], t["name"], pct,
                       f"{t['net']/1e8:+.2f}", f"{t['buy']/1e8:+.2f}/{t['sell']/1e8:+.2f}",
                       str(t["seat_total"]))
        console.print(ov)
        for t in result["targets"]:
            st = Table(
                title=f"{t['date']} {t['code']} {t['name']}｜席位画像（{t['seat_total']} 席，展示前{len(t['seats'])}）",
                border_style="magenta")
            for c in ("席位", "身份", "本次净额(亿)", "历史上榜(买/卖)",
                      "历史累计(亿)", "5/10/20日胜率", "平均持有(日)", "本股历史其他上榜"):
                st.add_column(c)
            for s in t["seats"]:
                h = s.get("history") or {}
                ac = h.get("appearance_count") or {}
                wr = h.get("win_rate") or {}
                wr_s = "/".join(
                    (f"{wr.get(f'd{x}'):.0%}" if wr.get(f"d{x}") is not None else "NA")
                    for x in result["horizons"])
                hold = f"{h.get('avg_hold_days')}" if h.get("avg_hold_days") else "样本不足"
                sh = s.get("stock_history") or []
                sh_s = "；".join(f"{e['date']} {e['net']/1e8:+.2f}亿" for e in sh[-2:]) or "-"
                st.add_row(s["agency"][:20], f"{s['label']}({s['label_basis']})",
                           f"{s['this_net']/1e8:+.2f}",
                           f"{ac.get('buy', 0)}/{ac.get('sell', 0)}",
                           f"{(h.get('cum_net_value') or 0)/1e8:+.2f}", wr_s, hold, sh_s)
            console.print(st)
            if t.get("reasons"):
                console.print(f"[dim]上榜原因: {'；'.join(t['reasons'])}[/dim]")
        console.print("[dim]席位标签来自规则匹配/推断，不等于官方认定[/dim]")
        console.print(f"[dim]{result.get('disclaimer', '')}[/dim]")
        return

    # ── 近期活跃资金主体榜 ──
    if rank:
        from agents.smart_money_agent import rank_active_seats
        from agents.smart_money_agent import parse_horizons
        cards = rank_active_seats(window_months=window_months, top_n=top_n,
                                  horizons=parse_horizons(horizons))
        if not cards:
            console.print("[yellow]窗口内无龙虎榜席位数据（top_inst）[/yellow]")
            return
        t = Table(title=f"近期活跃资金主体榜（近 {window_months} 个月，活动名册非买入清单）",
                  border_style="magenta")
        for c in ("席位", "身份", "上榜(买/卖)", "累计净买卖(亿)", "5/10/20日胜率",
                  "平均持有(日)", "偏好板块"):
            t.add_column(c)
        for s in cards:
            wr = s["win_rate"]
            wr_s = "/".join((f"{wr.get(f'd{h}'):.0%}" if wr.get(f"d{h}") is not None else "NA")
                            for h in parse_horizons(horizons))
            hold = f"{s['avg_hold_days']}" if s.get("avg_hold_days") else "-"
            fav = ",".join(x["sector"] for x in s["fav_sectors"][:3]) or "-"
            ac = s["appearance_count"]
            t.add_row(s["agency"][:24], f"{s['label']}({s['label_basis']})",
                      f"{ac['buy']}/{ac['sell']}", f"{s['cum_net_value']/1e8:+.2f}",
                      wr_s, hold, fav)
        console.print(t)
        console.print("[dim]席位标签来自规则匹配/推断，不等于官方认定[/dim]")
        return

    if not target:
        console.print("[yellow]请提供目标（股票代码/名称 或 席位名），或用 --rank 查看活跃资金主体榜[/yellow]")
        return

    report = build_profile(target, target_type=target_type, mode=mode,
                           window_months=window_months,
                           horizons=horizons, fetch=fetch)

    if as_json:
        import json
        console.print_json(json.dumps(report, ensure_ascii=False, default=str))
        return
    if as_markdown:
        console.print(report_to_markdown(report))
        return

    subj = report["subject"]
    console.print(Panel(
        f"[bold cyan]资金主体行为画像[/bold cyan]\n"
        f"标的/主体: [yellow]{subj['target']}[/yellow]（{subj['type']}）  "
        f"模式: [yellow]{mode}[/yellow]  "
        f"窗口: [yellow]{window_months}个月[/yellow]  "
        f"事后验证: [yellow]{report['horizons']}交易日[/yellow]",
        expand=False))

    # ── 席位画像（按资金主体）──
    seat = report.get("seat_profile")
    if seat:
        t = Table(title=f"席位画像 — {seat['agency']}", show_header=False, border_style="blue")
        t.add_column("项", style="bold cyan")
        t.add_column("值")
        t.add_row("身份", f"{seat['label']}（{seat['label_basis']}，非官方认定）")
        t.add_row("上榜频次", str(seat["appearance_count"]))
        t.add_row("累计净买卖", f"{seat['cum_net_value']/1e8:+.2f}亿")
        wr = seat["win_rate"]
        t.add_row("上榜后胜率", " / ".join(
            f"{h}日 {wr.get(f'd{h}') if wr.get(f'd{h}') is not None else 'NA'}（n={wr.get(f'd{h}_n',0)}）"
            for h in report["horizons"]))
        t.add_row("平均持有周期", f"{seat['avg_hold_days']} 交易日" if seat.get("avg_hold_days") else "样本不足")
        t.add_row("偏好板块", ", ".join(f"{s['sector']}({s['count']})" for s in seat["fav_sectors"]) or "-")
        console.print(t)

    # ── 席位身份与画像（按个股）──
    seats = report.get("seats")
    if seats is not None:
        if not seats.get("available"):
            console.print(f"[yellow]席位：{'; '.join(seats.get('notes', [])) or '无数据'}[/yellow]")
        else:
            t = Table(title=f"席位身份与画像（{len(seats['seats'])} 个席位，按|本股净额|降序，展示前20）",
                      border_style="magenta")
            for c in ("席位", "身份", "本股净买卖(亿)", "累计净买卖(亿)",
                      "5/10/20日胜率", "平均持有(日)", "偏好板块"):
                t.add_column(c)
            for s in seats["seats"][:20]:
                wr = s["win_rate"]
                wr_s = "/".join(
                    (f"{wr.get(f'd{h}'):.0%}" if wr.get(f"d{h}") is not None else "NA")
                    for h in report["horizons"])
                hold = f"{s['avg_hold_days']}" if s.get("avg_hold_days") else "样本不足"
                fav = ",".join(x["sector"] for x in s["fav_sectors"][:3]) or "-"
                t.add_row(s["agency"][:22], f"{s['label']}({s['label_basis']})",
                          f"{s['this_stock']['net']/1e8:+.2f}",
                          f"{s['cum_net_value']/1e8:+.2f}", wr_s, hold, fav)
            console.print(t)

    # ── 北向跨期行为 ──
    nb = report.get("northbound")
    if nb is not None:
        if not nb.get("available"):
            console.print(f"[yellow]北向：{'; '.join(nb.get('notes', [])) or '无数据'}[/yellow]")
        else:
            t = Table(title="北向跨期行为", show_header=False, border_style="cyan")
            t.add_column("项", style="bold cyan")
            t.add_column("值")
            t.add_row("披露点数", f"{nb['points']}（{nb.get('disclosure')}）")
            t.add_row("最长同向 streak", f"{nb['streak']['direction']} {nb['streak']['length']} 个披露点")
            hr = nb["holding_ratio"]
            t.add_row("持股比例", f"{hr.get('start')}% → {hr.get('end')}%（Δ{hr.get('change')}）")
            t.add_row("窗口价格变动", f"{nb.get('price_return')}")
            t.add_row("与价格背离", nb.get("divergence") or "无")
            console.print(t)

    # ── 资金合力 / 分歧 ──
    cons = report.get("consensus")
    if cons is not None:
        t = Table(title=f"资金合力 / 分歧 — {cons['verdict']}", border_style="green")
        t.add_column("资金路", style="cyan")
        t.add_column("方向")
        t.add_column("依据")
        color = {"buy": "red", "sell": "green", "no_data": "dim", "flat": "yellow"}
        for name, v in cons["routes"].items():
            t.add_row(name, f"[{color.get(v['direction'],'')}]{v['direction']}[/]",
                      v["note"])
        console.print(t)

    # ── 数据附录 ──
    appendix = report.get("data_appendix") or []
    if appendix:
        t = Table(title="数据附录", border_style="dim")
        for c in ("数据模块", "来源接口", "查询窗口", "行数", "备注"):
            t.add_column(c)
        for a in appendix:
            t.add_row(a.get("module", ""), a.get("source", ""), a.get("window", ""),
                      str(a.get("rows", "")), str(a.get("notes", "")))
        console.print(t)

    for note in report.get("notes", []):
        console.print(f"[dim]· {note}[/dim]")
    console.print(f"[dim]{report['disclaimer']}[/dim]")


def cmd_seat_dict(action: str = "list", window: int = 6, min_appearances: int = 10,
                  k: int = 3, t1_days: int = 2, label: str = None, top: int = 30,
                  xlsx_path: str = None):
    """席位字典（游资/量化识别）：build 构建 / list 查询"""
    from rich.console import Console
    from rich.table import Table
    from rich.panel import Panel
    from agents.seat_profiler import (
        build_seat_dictionary, dictionary_to_rows, import_seat_labels_xlsx)

    console = Console()

    if action == "import":
        res = import_seat_labels_xlsx(xlsx_path=xlsx_path)
        labels = res.get("labels", [])
        from collections import Counter
        cnt = Counter(x["label"] for x in labels)
        console.print(f"[green]已导入硬标签 {len(labels)} 条[/green] → data/smart_money/seat_labels.json")
        console.print("  " + "，".join(f"{k}:{v}" for k, v in cnt.most_common()))
        console.print("[dim]下一步: python main.py seat-dict build[/dim]")
        return

    if action == "build":
        console.print(f"[dim]构建席位字典（窗口 {window} 月，最小上榜 {min_appearances}，"
                      f"k={k}，T+1阈值 {t1_days} 交易日）...[/dim]")
        res = build_seat_dictionary(window_months=window, min_appearances=min_appearances,
                                    k=k, t1_days=t1_days)
        seats = res.get("seats", [])
        console.print(Panel(
            f"[bold cyan]席位字典构建完成[/bold cyan]\n"
            f"窗口: [yellow]{res['window']['start']}~{res['window']['end']}[/yellow]  "
            f"入库席位: [yellow]{len(seats)}[/yellow]  "
            f"硬标签: [yellow]{res.get('hard_label_count', 0)}[/yellow]\n"
            f"输出: seat_dictionary.json / .csv / _report.md",
            expand=False))
        by = {}
        for s in seats:
            by.setdefault(s["label"], []).append(s)
        t = Table(title="各标签数量与代表席位", border_style="magenta")
        for c in ("标签", "数量", "代表席位(置信度)", "簇画像均值"):
            t.add_column(c)
        for lb, group in sorted(by.items(), key=lambda kv: -len(kv[1])):
            group = sorted(group, key=lambda s: -s["confidence"])[:3]
            reps = "；".join(f"{s['agency'][:16]}({s['confidence']})" for s in group)
            t.add_row(lb, str(len(by[lb])), reps, "")
        console.print(t)
        return

    rows = dictionary_to_rows()
    if not rows:
        console.print("[yellow]无席位字典，请先运行: python main.py seat-dict build[/yellow]")
        return
    if label:
        rows = [s for s in rows if label in s["label"]]
    rows = sorted(rows, key=lambda s: (-s["confidence"], -s["freq"]))[:top]
    t = Table(title=f"席位字典（{label or '全部'}，展示 {len(rows)} 条）", border_style="magenta")
    for c in ("席位", "标签", "依据", "置信度", "freq", "dispersion",
              "同席率", "T+1兑现", "次日跟投胜率(1/3/5/10)"):
        t.add_column(c)
    for s in rows:
        wr = "/".join(str(s.get(f"win_d{h}")) for h in (1, 3, 5, 10))
        t.add_row(s["agency"][:22], s["label"], s["label_basis"], str(s["confidence"]),
                  str(s["freq"]), str(s["dispersion"]), str(s["same_seat_rate"]),
                  str(s["t1_exit_rate"]), wr)
    console.print(t)
    console.print("[dim]标签来自数据源硬标签或行为聚类软标签，非官方认定；建议每季度重估[/dim]")


def cmd_capital_profiler(action: str = "profile", level: str = "stock",
                         code: str = None, date: str = None,
                         lookback_days: int = 60, fmt: str = "text"):
    """资金画像（Phase 1：个股级）"""
    from rich.console import Console
    from agents.capital_profiler import profile, format_text

    console = Console()
    if action != "profile":
        console.print("[yellow]目前仅支持 action=profile[/yellow]")
        return
    if not code:
        console.print("[yellow]请提供 --code（6位股票代码）[/yellow]")
        return
    report = profile(level=level, code=code, date=date, lookback_days=lookback_days)
    if fmt == "json":
        import json
        console.print_json(json.dumps(report, ensure_ascii=False, default=str))
    else:
        console.print(format_text(report))


def cmd_update_all():
    """更新所有数据域（A股/港股 K线、meta、fundamental、factor、macro、moneyflow、events、估值）"""
    logger.info("开始全域增量更新...")

    def _run(label: str, fn):
        print(f"\n{label}...")
        try:
            fn()
        except Exception as e:
            print(f"  {label} 跳过: {e}")

    _run("[1/13] 更新 A 股 K 线数据", cmd_update)

    from data.downloader_hk import HKDownloader
    from data.downloader_meta import MetaDownloader
    from data.downloader_fundamental import FundamentalDownloader
    from data.downloader_factor import FactorDownloader
    from data.downloader_macro import MacroDownloader

    _run("[2/13] 更新港股 K 线数据",
         lambda: HKDownloader(connect_only=True).update_db())
    _run("[3/13] 更新 meta 域", lambda: MetaDownloader().update_db())
    _run("[4/13] 更新 fundamental 域", lambda: FundamentalDownloader().update_db())
    _run("[5/13] 更新 factor 域", lambda: FactorDownloader().update_db())
    _run("[6/13] 更新 macro 域", lambda: MacroDownloader().update_db())
    _run("[7/13] 更新 moneyflow 域", cmd_update_moneyflow)
    _run("[8/13] 更新 events 域", cmd_update_events)
    _run("[9/13] 更新港股估值（PE/PB/PCF）", cmd_update_hk_valuation)
    _run("[10/13] 更新港股资金流", cmd_update_hk_moneyflow)
    _run("[11/13] 更新港股分红事件", cmd_update_hk_events)
    _run("[12/13] 更新港股财务", cmd_update_hk_financials)
    _run("[13/13] 补充 A 股 PCF", cmd_update_cn_pcf)

    logger.info("全域增量更新完成！")


def cmd_inspect(subtype: str, target: str = "",
                n: int = 20, asset_type: str = "stock",
                level: str = "L1", plot: bool = False,
                start: str = None, end: str = None,
                codes: list = None, metric: str = None,
                all_items: bool = False, market: str = "cn"):
    """数据直查工具"""
    from tools.inspect_data import (cmd_kline, cmd_stock_mf, cmd_ind_mf, cmd_macro,
                                    cmd_kline_multi, cmd_stock_mf_multi, cmd_ind_mf_multi,
                                    cmd_fundamental, _resolve_codes, _resolve_industries)
    import data.store_moneyflow as _mf

    # ── 名称→代码解析（kline / mf）──────────────────────────
    def resolve_stock_targets(raw: list[str]) -> list[str] | None:
        resolved = _resolve_codes(raw, asset_type=asset_type)
        return resolved  # None 表示需要用户明确

    # ── 行业名解析（ind）────────────────────────────────────
    def resolve_ind_targets(raw: list[str]) -> list[str] | None:
        dates = _mf.list_dates("industry")
        if not dates:
            print("[错误] 无行业资金流数据")
            return None
        ref = _mf.load("industry", date=sorted(dates)[-1])
        if ref is None:
            return None
        return _resolve_industries(raw, ref)

    if subtype == "kline":
        raw = codes or ([target] if target else [])
        if not raw:
            print("请提供股票代码或名称：inspect kline 贵州茅台  或  --codes 茅台 平安银行")
            return
        targets = resolve_stock_targets(raw)
        if targets is None:
            return
        if len(targets) == 1:
            cmd_kline(targets[0], asset_type=asset_type, n=n,
                      start=start, end=end, plot=plot,
                      market=market)
        else:
            cmd_kline_multi(targets, metric=metric or "close",
                            asset_type=asset_type, n=n, start=start, end=end,
                            market=market)

    elif subtype == "mf":
        raw = codes or ([target] if target else [])
        if not raw:
            print("请提供股票代码或名称：inspect mf 贵州茅台  或  --codes 茅台 平安银行")
            return
        targets = resolve_stock_targets(raw)
        if targets is None:
            return
        if len(targets) == 1:
            cmd_stock_mf(targets[0], n=n, start=start, end=end, plot=plot,
                         market=market)
        else:
            cmd_stock_mf_multi(targets,
                               metric=metric or ("main_net" if market == "hk"
                                                 else "net_mf_amount"),
                               n=n, start=start, end=end, market=market)

    elif subtype == "ind":
        raw = codes or ([target] if target else [])
        if not raw and not all_items:
            print("请提供行业名称：inspect ind 食品饮料  或  --codes 银行 半导体  或  --all")
            return
        if all_items or not raw:
            cmd_ind_mf_multi([], metric=metric or "net_amount",
                             all_industries=True, n=n, start=start, end=end)
        else:
            resolved = resolve_ind_targets(raw)
            if resolved is None:
                return
            if len(resolved) == 1:
                cmd_ind_mf(resolved[0], n=n, level=level,
                           start=start, end=end, plot=plot)
            else:
                cmd_ind_mf_multi(resolved, metric=metric or "net_amount",
                                 all_industries=False, n=n, start=start, end=end)

    elif subtype == "macro":
        cmd_macro(target, n=n, start=start, end=end, plot=plot)
    elif subtype == "fundamental":
        raw = codes or ([target] if target else [])
        if not raw:
            if metric == "list" or not metric:
                cmd_fundamental("", data_type="list")
                return
            print("请提供股票代码或名称")
            return
        if market == "cn":
            resolved = _resolve_codes(raw, asset_type="stock")
            if not resolved:
                return
        else:
            resolved = raw
        for code in resolved:
            cmd_fundamental(code, data_type=metric or "profit",
                            n=n, start=start, end=end, market=market)
    else:
        print("可用子类型: kline / mf / ind / macro")


def _get_adj_factor(code: str, local_last_date: str, hfq_price: float):
    """
    hfq现价换算回不复权口径，返回 (real_price, adj_factor)。
    用本地数据最新日期区间拉不复权价，避免非交易日/盘中失败。
    """
    try:
        from config.settings import DATA_SOURCE as _DS
        import tushare as _ts
        from datetime import datetime as _dt, timedelta as _td
        _pro = _ts.pro_api(_DS["tushare_token"])
        _tsc = code + (".SH" if code.startswith("6") or code.startswith("9") else ".SZ")
        _start = (_dt.strptime(local_last_date, "%Y%m%d") - _td(days=10)).strftime("%Y%m%d")
        _raw = _pro.daily(ts_code=_tsc, start_date=_start, end_date=local_last_date)
        if _raw is not None and not _raw.empty:
            _same = _raw[_raw["trade_date"] == local_last_date]
            if not _same.empty:
                real_price = float(_same.iloc[0]["close"])
            else:
                real_price = float(_raw.sort_values("trade_date").iloc[-1]["close"])
            return real_price, hfq_price / real_price
    except Exception:
        pass
    return hfq_price, 1.0


def cmd_portfolio(date: str | None = None,
                  portfolio_file: str = "data/portfolio.json"):
    """
    持仓评估：对持仓股运行出场框架计算，输出分级操作表 + 委托参考价。
    持仓文件格式：[{"code":"600519","name":"贵州茅台","cost":1500.0,"shares":100}]
    新增字段（可选）：position_type（trading/config）、style、total_capital
    """
    from rich.console import Console
    from rich.table import Table
    from rich.panel import Panel
    from data.portfolio import load_portfolio
    from strategies.position_manager import calc_position_size, calc_r_ratio

    console = Console()

    # ── 读持仓文件 ─────────────────────────────────────────────
    pf = load_portfolio(portfolio_file)
    holdings = pf.get("holdings", pf) if isinstance(pf, dict) else pf
    if not holdings:
        # 兼容旧格式（纯列表）
        from pathlib import Path as _P
        import json as _json
        _p = _P(portfolio_file)
        if not _p.exists():
            console.print(f"[red]持仓文件不存在: {portfolio_file}[/red]")
            console.print('[{"code":"600519","name":"贵州茅台","cost":1500.0,"shares":100}]')
            return
        holdings = _json.loads(_p.read_text(encoding="utf-8"))
        if isinstance(holdings, dict):
            holdings = holdings.get("holdings", [])
    if not holdings:
        console.print("[yellow]持仓文件为空[/yellow]")
        return

    # 去重
    seen = {}
    for h in holdings:
        if h["code"] in seen:
            console.print(f"[yellow]提示: {h['code']} 重复，保留最后一条[/yellow]")
        seen[h["code"]] = h
    holdings = list(seen.values())

    total_capital = pf.get("total_capital") or RISK_CONTROL.get("default_capital", 2_000_000) if isinstance(pf, dict) else RISK_CONTROL.get("default_capital", 2_000_000)
    risk_per_trade = RISK_CONTROL.get("risk_per_trade", 0.01)
    min_r = RISK_CONTROL.get("min_r_ratio", 1.5)

    import data.store as store
    from datetime import datetime, timedelta
    end = date or datetime.now().strftime("%Y-%m-%d")
    max_bars = max(s().requires_bars() for s in BUILTIN_STRATEGIES.values())
    start = (datetime.strptime(end, "%Y-%m-%d") - timedelta(days=int(max_bars * 1.5))).strftime("%Y-%m-%d")
    ipo_dates = store.load_ipo_dates()

    console.print(Panel(
        f"[bold cyan]Alpha量化 - 持仓评估[/bold cyan]\n"
        f"持仓: [yellow]{len(holdings)}只[/yellow]  "
        f"日期: [yellow]{date or '今日'}[/yellow]  "
        f"总资金: [yellow]¥{total_capital:,.0f}[/yellow]",
        expand=False
    ))

    for h in holdings:
        code = h["code"]
        cost = float(h["cost"])
        shares = int(h["shares"])
        pos_type = h.get("position_type", "")  # 可能为空，后续推断
        style = h.get("style", "")

        # ── 自动探测 asset_type（先试 stock，再试 etf）──────────
        declared_asset = h.get("asset_type", "")
        df = None
        detected_asset = declared_asset
        if not declared_asset or declared_asset == "stock":
            df = store.load(code, asset_type="stock", market="cn", start=start, end=end)
        if (df is None or df.empty) and declared_asset != "stock":
            df = store.load(code, asset_type="etf", market="cn", start=start, end=end)
            if df is not None and not df.empty:
                detected_asset = "etf"
        if df is None or df.empty:
            console.print(Panel(f"[red]{code}: 无本地数据[/red]", expand=False))
            continue
        is_etf = (detected_asset == "etf")

        name = ipo_dates.get(f"cn:{code}", {}).get("name", code)
        hfq_price = float(df.iloc[-1]["close"])
        local_last_date = df.index.max().strftime("%Y%m%d")
        real_price, adj_factor = _get_adj_factor(code, local_last_date, hfq_price)
        current_price = real_price
        pnl_pct = (current_price - cost) / cost * 100

        df_r = df.reset_index()

        # ── 出场框架（独立于进场信号）──────────────────────────
        # 三层降级：显式声明 → 策略认领 → ATR兜底
        el = None
        used_strategy = None
        etf_exit_note = ""  # ETF 专属出场说明

        # 层1：position_type + style 显式声明
        if not pos_type and style in ("value", "quality", "growth"):
            pos_type = "config"
        elif not pos_type and style in ("momentum", "reversal", "breakout"):
            pos_type = "trading"

        # ── ETF 专属出场路径（按 style 分流）─────────────
        if is_etf:
            pos_type = pos_type or "config"
            if style == "theme_etf":
                # 细分主题 ETF：etf_agent 三维综合评分（技术动量+估值代理+政策）
                from strategies.builtin.etf_momentum import ETFMomentumStrategy
                el = ETFMomentumStrategy().compute_exit_levels(df_r, entry_price=hfq_price)
                used_strategy = "ETF三维评分"
                try:
                    from agents.etf_agent import evaluate_etf
                    ev = evaluate_etf(code, df=df, date=end)
                except Exception:
                    ev = None
                if ev and ev.get("available"):
                    ts = ev["total_score"]
                    parts = []
                    if ev["technical"] is not None:
                        parts.append(f"技{ev['technical']:.0f}")
                    if ev["valuation"] is not None:
                        parts.append(f"估{ev['valuation']:.0f}")
                    if ev["policy"] is not None:
                        parts.append(f"政{ev['policy']:.0f}")
                    dims_str = "/".join(parts)
                    ts_disp = f"{ts:.0f}" if ts is not None else "?"
                    if ts is not None and ts >= 65:
                        color = "green"
                    elif ts is not None and ts >= 45:
                        color = "yellow"
                    else:
                        color = "red"
                    etf_exit_note = (f"[{color}]主题评分{ts_disp}({dims_str})："
                                     f"{ev['advice']}[/{color}]")
                else:
                    etf_exit_note = "[dim]主题评分数据不可用，按 ATR 技术止损[/dim]"

            elif style == "industry_allocation":
                # L2 行业评分：读行业三维评分判断是否应减配
                industry_code = h.get("industry", "")
                ind_level = h.get("industry_level", "L2")  # 默认L2粒度
                try:
                    from agents.industry_agent import get_industry_score
                    score_info = get_industry_score(industry_code, level=ind_level) if industry_code else None
                except Exception:
                    score_info = None

                from strategies.builtin.etf_momentum import ETFMomentumStrategy
                el = ETFMomentumStrategy().compute_exit_levels(df_r, entry_price=hfq_price)
                used_strategy = f"{ind_level}行业评分"
                if score_info:
                    pct = score_info["percentile"]
                    score_val = score_info["score"]
                    if pct <= 0.25:
                        etf_exit_note = (f"[red]行业评分偏低({score_val:.0f}分，分位{pct*100:.0f}%)，"
                                         f"建议减配[/red]")
                    elif pct <= 0.50:
                        etf_exit_note = f"[yellow]行业评分中等({score_val:.0f}分，分位{pct*100:.0f}%)[/yellow]"
                    else:
                        etf_exit_note = f"[green]行业评分良好({score_val:.0f}分，分位{pct*100:.0f}%)[/green]"
                else:
                    etf_exit_note = f"[dim]行业评分数据不可用（industry 字段: {industry_code or '未填'}）[/dim]"

            elif style == "macro_allocation":
                # L1 宏观 regime：crisis/risk_off 建议减仓
                try:
                    from agents.strategy_agent import evaluate_market_condition
                    env = evaluate_market_condition()
                    regime = env.get("regime") or env.get("risk_mode")
                except Exception:
                    regime = None
                from strategies.builtin.etf_momentum import ETFMomentumStrategy
                el = ETFMomentumStrategy().compute_exit_levels(df_r, entry_price=hfq_price)
                used_strategy = "L1宏观Regime"
                if regime in ("crisis",):
                    etf_exit_note = f"[red]宏观 regime={regime}，建议大幅减仓宽基ETF[/red]"
                elif regime in ("risk_off",):
                    etf_exit_note = f"[yellow]宏观 regime={regime}，建议适当降低宽基配置[/yellow]"
                elif regime:
                    etf_exit_note = f"[green]宏观 regime={regime}，宽基ETF可持有[/green]"
                else:
                    etf_exit_note = "[dim]宏观 regime 数据不可用，按 ATR 技术止损[/dim]"

            else:
                # style=momentum 或未声明：走 etf_momentum 策略出场逻辑
                from strategies.builtin.etf_momentum import ETFMomentumStrategy
                el = ETFMomentumStrategy().compute_exit_levels(df_r, entry_price=hfq_price)
                used_strategy = "etf_momentum"
                etf_exit_note = "[dim]动量轮动：相对排名跌出 Top N 时轮出[/dim]"

        else:
            # ── 股票出场路径（原有逻辑）──────────────────────────
            # 层2：运行所有策略，找认领策略（同时收集进场信号）
            buy_sigs, sell_sigs, watch_sigs = [], [], []
            strategy_exit_candidates = []
            for key, cls in BUILTIN_STRATEGIES.items():
                strat = cls()
                if getattr(strat, "applicable_asset_types", []) == ["etf"]:
                    continue
                sigs = strat.generate_signals(df_r, code, name)
                for s in sigs:
                    if s.signal == "BUY":
                        buy_sigs.append((key, s))
                        if el is None:
                            strategy_exit_candidates.append((key, strat, "trading"))
                    elif s.signal == "SELL":
                        sell_sigs.append((key, s))
                    elif s.signal == "WATCH":
                        watch_sigs.append((key, s))
                        if el is None:
                            strategy_exit_candidates.append((key, strat, "config"))

            if strategy_exit_candidates:
                best_key, best_strat, inferred_type = strategy_exit_candidates[0]
                if not pos_type:
                    pos_type = inferred_type
                el = best_strat.compute_exit_levels(df_r, entry_price=hfq_price)
                used_strategy = best_key

        # 层3：ATR兜底（ETF/股票均适用）
        if el is None:
            from strategies.builtin.ma_cross import MACrossStrategy
            el = MACrossStrategy().compute_exit_levels(df_r, entry_price=hfq_price)
            used_strategy = "ATR兜底"
            if not pos_type:
                pos_type = "trading"

        # ETF 无基本面信号，置空
        if is_etf:
            buy_sigs, sell_sigs, watch_sigs = [], [], []

        # 将 hfq 出场价换算回不复权口径（与 cost 口径对齐）
        def _to_real(p):
            return round(p / adj_factor, 2) if p and p > 0 and adj_factor > 0 else 0.0

        stop_real = _to_real(el.stop_loss)
        tp_real = _to_real(el.take_profit)
        levels_real = [
            {**lv.__dict__, "price": _to_real(lv.price)}
            for lv in el.scale_levels
        ] if el.scale_levels else []

        # ── 进场信号参考（附加信息，不决定出场逻辑）──────────────
        sig_summary = []
        if buy_sigs:
            sig_summary.append(f"[green]BUY×{len(buy_sigs)}[/green] {buy_sigs[0][1].reason[:30]}")
        if sell_sigs:
            sig_summary.append(f"[red]SELL×{len(sell_sigs)}[/red] {sell_sigs[0][1].reason[:30]}")
        if watch_sigs:
            sig_summary.append(f"[blue]WATCH×{len(watch_sigs)}[/blue]")

        # ── 主要建议（基于出场框架，而非进场信号）──────────────
        if stop_real > 0 and current_price <= stop_real:
            action = "[red]硬止损触发[/red]"
        elif stop_real > 0 and current_price <= cost * 0.95:
            action = "[red]考虑减仓[/red]"
        elif tp_real > 0 and current_price >= tp_real:
            action = "[yellow]止盈目标已到[/yellow]"
        elif sell_sigs:
            action = "[red]策略SELL信号[/red]"
        elif buy_sigs:
            action = "[green]持有/可加仓[/green]"
        else:
            action = "[white]观望[/white]"

        # ── R倍数和仓位建议 ──────────────────────────────────
        r = calc_r_ratio(cost, stop_real, tp_real) if stop_real > 0 else 0.0
        rec_shares = calc_position_size(cost, stop_real, total_capital,
                                        risk_per_trade, RISK_CONTROL.get("max_single_position", 0.10)) if stop_real > 0 else 0
        pnl_color = "green" if pnl_pct >= 0 else "red"
        from strategies.position_manager import calc_entry_bid
        _pct = float(df_r.iloc[-1].get("pct_chg", 0.0)) if "pct_chg" in df_r.columns else 0.0
        bid, bid_note = calc_entry_bid(current_price, el.exit_logic, 70.0, _pct, pos_type)
        ask = round(current_price * 0.995, 2)

        # ── 输出卡片 ────────────────────────────────────────
        asset_tag = "[dim]ETF[/dim]  " if is_etf else ""
        header = (
            f"[bold]{code}  {name}[/bold]  {asset_tag}\n"
            f"  现价: ¥{current_price:.2f}   成本: ¥{cost:.2f}   "
            f"盈亏: [{pnl_color}]{pnl_pct:+.1f}%[/{pnl_color}]  ({shares}股)\n"
            f"  建议: {action}  |  出场逻辑: [cyan]{el.exit_logic}[/cyan]（{used_strategy}）\n"
            + (f"  {etf_exit_note}\n" if etf_exit_note else "")
            + (f"  进场信号参考: {'  '.join(sig_summary)}\n" if sig_summary else "")
            + (f"  R={r:.2f}" + ("[yellow]⚠[/yellow]" if 0 < r < min_r else "") if r > 0 else "  配置仓（无价格止损）")
            + (f"  | 按当前成本计算建议{rec_shares}股/批\n" if rec_shares > 0 else "\n")
        )

        border = "red" if "止损" in action or "减仓" in action else \
                 "yellow" if "止盈" in action else \
                 "green" if "加仓" in action or "持有" in action else "white"
        console.print(Panel(header.rstrip(), border_style=border, expand=False))

        if el.exit_logic == "fundamental":
            console.print(f"  [dim]基本面出场: {el.fundamental_exit}[/dim]")
        elif levels_real:
            # 过滤掉价格为0的档位（配置仓兜底时stop_loss=0会产生¥0.00的档位）
            valid_levels = [lv for lv in levels_real if lv.get("price", 0) > 0]
            if valid_levels:
                tbl = Table(show_header=True, header_style="bold dim", box=None, padding=(0, 1))
                tbl.add_column("价位", style="bold")
                tbl.add_column("类型")
                tbl.add_column("操作")
                tbl.add_column("仓位")
                tbl.add_column("依据")
                for lv in valid_levels:
                    is_stop = "stop" in lv.get("level_type", "")
                    color = "red" if is_stop else "green"
                    tbl.add_row(
                        f"[{color}]¥{lv['price']:.2f}[/{color}]",
                        lv.get("level_type", ""),
                        lv.get("action", ""),
                        f"{lv.get('size_pct', 0)*100:.0f}%",
                        lv.get("reason", ""),
                    )
                console.print(tbl)

        console.print(f"  [dim]委托参考 → 买入¥{bid:.2f}（{bid_note}）  卖出¥{ask:.2f}（昨收-0.5%）[/dim]\n")


def cmd_exit_levels(code: str, entry: float | None = None, strategy: str | None = None):
    """
    查看指定股票的出场价位框架（分级操作表）。
    对每个适用策略运行 compute_exit_levels，展示止损/止盈档位和仓位建议。
    """
    from rich.console import Console
    from rich.table import Table
    from rich.panel import Panel
    import data.store as store
    from strategies.position_manager import calc_position_size, calc_r_ratio
    from config.settings import RISK_CONTROL
    from data.portfolio import load_portfolio

    console = Console()

    from datetime import datetime as _dt, timedelta as _td
    _end = _dt.now().strftime("%Y-%m-%d")
    _max_bars = max(s().requires_bars() for s in BUILTIN_STRATEGIES.values())
    _start = (_dt.now() - _td(days=int(_max_bars * 1.5))).strftime("%Y-%m-%d")
    df = store.load(code, asset_type="stock", market="cn", start=_start, end=_end)
    if df is None or df.empty:
        console.print(f"[red]无本地数据: {code}[/red]")
        return
    df = df.reset_index()

    ipo_dates = store.load_ipo_dates()
    name = ipo_dates.get(f"cn:{code}", {}).get("name", code)
    hfq_price = float(df.iloc[-1]["close"])
    local_last_date = df["date"].iloc[-1]
    if hasattr(local_last_date, "strftime"):
        local_last_date = local_last_date.strftime("%Y%m%d")
    else:
        local_last_date = str(local_last_date).replace("-", "")[:8]

    real_price, adj_factor = _get_adj_factor(code, local_last_date, hfq_price)
    # adj_factor = hfq / real（如 10473 / 1240 ≈ 8.45）
    # entry_display 是用户输入的不复权价，策略计算需要 hfq 口径
    entry_hfq = entry * adj_factor if entry else hfq_price
    entry_display = entry if entry else real_price  # 展示用不复权

    # 读 total_capital
    pf = load_portfolio()
    capital = pf.get("total_capital") or RISK_CONTROL.get("default_capital", 2_000_000)
    risk_per_trade = RISK_CONTROL.get("risk_per_trade", 0.01)
    min_r = RISK_CONTROL.get("min_r_ratio", 1.5)

    console.print(Panel(
        f"[bold cyan]{code}  {name}[/bold cyan]  "
        f"现价¥{real_price:.2f}  进场价¥{entry_display:.2f}  总资金¥{capital:,.0f}",
        expand=False
    ))

    strategies_to_run = {}
    for k, cls in BUILTIN_STRATEGIES.items():
        if strategy and k != strategy:
            continue
        inst = cls()
        if getattr(inst, "applicable_asset_types", []) == ["etf"]:
            continue
        strategies_to_run[k] = inst

    if not strategies_to_run:
        console.print(f"[red]策略不存在: {strategy}[/red]")
        return

    for key, inst in strategies_to_run.items():
        # 策略计算用 hfq 口径，展示换算回不复权：real = hfq / adj_factor
        el = inst.compute_exit_levels(df, entry_price=entry_hfq)
        def _to_real(p): return round(p / adj_factor, 2) if p and p > 0 and adj_factor > 0 else 0.0
        stop_r  = _to_real(el.stop_loss)
        tp_r    = _to_real(el.take_profit)

        r = calc_r_ratio(entry_display, stop_r, tp_r)
        r_color = "green" if r >= min_r else "yellow" if r > 0 else "dim"
        r_str = f"[{r_color}]R={r:.2f}[/{r_color}]" if r > 0 else "[dim]R=N/A（配置仓）[/dim]"

        if stop_r > 0:
            shares = calc_position_size(
                entry_display, stop_r, capital,
                risk_per_trade, RISK_CONTROL.get("max_single_position", 0.10)
            )
            max_loss = (entry_display - stop_r) * shares
            size_str = f"建议{shares}股（最大亏损¥{max_loss:,.0f}，占{max_loss/capital*100:.1f}%）"
        else:
            size_str = "无价格止损（配置仓，按固定比例建仓）"

        header = (f"[bold]{key}[/bold]（{inst.name}）  "
                  f"出场逻辑: [cyan]{el.exit_logic}[/cyan]  {r_str}\n  {size_str}")

        if el.exit_logic == "fundamental":
            body = f"  PE分位出场: >{el.pe_exit_pct*100:.0f}%开始减仓\n  基本面出场: {el.fundamental_exit or '基本面恶化'}"
            console.print(Panel(header + "\n" + body, expand=False))
        else:
            tbl = Table(show_header=True, header_style="bold", box=None, padding=(0, 1))
            tbl.add_column("价位", style="bold")
            tbl.add_column("类型")
            tbl.add_column("操作")
            tbl.add_column("仓位")
            tbl.add_column("依据")
            for lv in el.scale_levels:
                price_r = _to_real(lv.price)
                if price_r <= 0:
                    continue
                color = "red" if "stop" in lv.level_type else "green"
                tbl.add_row(
                    f"[{color}]¥{price_r:.2f}[/{color}]",
                    lv.level_type, lv.action,
                    f"{lv.size_pct*100:.0f}%",
                    lv.reason,
                )
            console.print(Panel(header, expand=False))
            console.print(tbl)
            console.print()


def cmd_position_size(entry: float, stop: float, capital: float | None = None):
    """仓位计算器：给定进场价和止损价，输出基于风险的建议股数和R倍数。"""
    from rich.console import Console
    from rich.panel import Panel
    from strategies.position_manager import calc_position_size, calc_r_ratio
    from config.settings import RISK_CONTROL
    from data.portfolio import load_portfolio

    console = Console()

    if stop >= entry:
        console.print("[red]错误: 止损价必须低于进场价[/red]")
        return

    pf = load_portfolio()
    cap = capital or pf.get("total_capital") or RISK_CONTROL.get("default_capital", 2_000_000)
    risk_per_trade = RISK_CONTROL.get("risk_per_trade", 0.01)
    max_pos = RISK_CONTROL.get("max_single_position", 0.10)
    min_r = RISK_CONTROL.get("min_r_ratio", 1.5)

    shares = calc_position_size(entry, stop, cap, risk_per_trade, max_pos)
    risk_dist = entry - stop
    max_loss = risk_dist * shares
    position_pct = entry * shares / cap * 100 if shares > 0 else 0

    # 默认 R=2 止盈目标
    default_tp = round(entry + risk_dist * 2, 2)
    r = calc_r_ratio(entry, stop, default_tp)
    r_color = "green" if r >= min_r else "yellow"

    text = (
        f"进场价: ¥{entry:.2f}   止损价: ¥{stop:.2f}   风险距: ¥{risk_dist:.2f}/股\n"
        f"总资金: ¥{cap:,.0f}   单笔风险: {risk_per_trade*100:.1f}%（¥{cap*risk_per_trade:,.0f}）\n"
        f"\n"
        f"建议股数:  [bold]{shares}股[/bold]   仓位: {position_pct:.1f}%   "
        f"最大亏损: ¥{max_loss:,.0f}\n"
        f"R=2止盈目标: ¥{default_tp:.2f}   [{r_color}]R={r:.2f}[/{r_color}]"
        + (f"   [yellow]⚠ R<{min_r}，信号质量偏低[/yellow]" if r < min_r else "")
    )
    console.print(Panel(text, title="[bold]仓位计算（基于风险）[/bold]", expand=False))


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


def cmd_data_status():
    """各域数据新鲜度 + 后台更新进程状态"""
    import subprocess
    from rich.console import Console
    from rich.table import Table
    from rich.panel import Panel
    from datetime import datetime

    console = Console()

    # ── 后台更新进程检测 ──────────────────────────────────────
    bg_tasks = {}  # cmd_keyword -> last_log_line
    try:
        result = subprocess.run(["ps", "aux"], capture_output=True, text=True)
        for line in result.stdout.splitlines():
            if "main.py" not in line or "grep" in line:
                continue
            today = datetime.now().strftime("%Y%m%d")
            # 精确匹配命令关键词（避免 update-moneyflow 被 update 误匹配）
            cmd_map = [
                ("update-moneyflow", f"logs/update_mf_{today}.log"),
                ("update-all",       f"logs/update_all_{today}.log"),
                ("init-moneyflow",   f"logs/init_tushare_moneyflow.log"),
                ("update",           f"logs/update_kline_{today}.log"),
                ("init",             f"logs/init_{today}.log"),
            ]
            for kw, log_file in cmd_map:
                # 用空格边界匹配，避免 update 匹配到 update-moneyflow
                if f" {kw}" in line or line.endswith(kw):
                    last_line = "(运行中)"
                    p = Path(log_file)
                    if p.exists() and p.stat().st_size > 0:
                        try:
                            lines = p.read_text(errors="ignore").strip().splitlines()
                            if lines:
                                last_line = lines[-1][-120:]
                        except Exception:
                            pass
                    bg_tasks[kw] = last_line
                    break
    except Exception:
        pass

    if bg_tasks:
        for cmd, status in bg_tasks.items():
            console.print(Panel(
                f"[bold yellow]⟳ 后台任务: {cmd}[/bold yellow]\n{status}",
                expand=False
            ))

    # ── 各域最新日期 ──────────────────────────────────────────
    import data.store as store
    import data.store_meta as store_meta
    import data.store_fundamental as store_fund
    import data.store_macro as store_macro
    import data.store_moneyflow as store_mf

    today_str = datetime.now().strftime("%Y-%m-%d")

    def _age(date_str):
        """计算距今天数，返回 (days, color)"""
        if not date_str:
            return None, "red"
        try:
            d = datetime.strptime(str(date_str)[:10], "%Y-%m-%d")
            days = (datetime.now() - d).days
            color = "green" if days <= 3 else "yellow" if days <= 14 else "red"
            return days, color
        except Exception:
            return None, "red"

    rows = []

    # market
    sample_codes = store.list_codes("stock", market="cn")[:3]
    market_date = None
    for c in sample_codes:
        d = store.last_date(c, asset_type="stock", market="cn")
        if d and (market_date is None or d > market_date):
            market_date = d
    rows.append(("market/stock", market_date))

    # market/hk
    hk_codes = store.list_codes("stock", market="hk")[:3]
    hk_date = None
    for c in hk_codes:
        d = store.last_date(c, asset_type="stock", market="hk")
        if d and (hk_date is None or d > hk_date):
            hk_date = d
    rows.append(("market/hk", hk_date))

    mf_codes = store_mf.list_codes("stock")
    mf_last = store_mf.last_date("stock", code=mf_codes[0]) if mf_codes else None
    rows.append(("moneyflow/stock", mf_last))

    ind_dates = store_mf.list_dates("industry") if hasattr(store_mf, "list_dates") else store_mf.list_codes("industry")
    ind_last = sorted(ind_dates)[-1] if ind_dates else None
    rows.append(("moneyflow/industry", ind_last))

    for _dt in ("top_inst", "block_trade"):
        _dates = store_mf.list_dates(_dt)
        rows.append((f"moneyflow/{_dt}", sorted(_dates)[-1] if _dates else None))

    for dtype in ("bond_yield", "pmi", "m2"):
        d = store_macro.last_date(dtype)
        if d:
            rows.append((f"macro/{dtype}", d))
            break  # 宏观取一个代表即可

    fund_codes = store_fund.list_codes("profit")
    if fund_codes:
        fd = store_fund.last_date(fund_codes[0], "profit")
        rows.append(("fundamental/profit", fd))

    cal = store_meta.last_date("calendar")
    rows.append(("meta/calendar", cal))

    table = Table(title="数据域新鲜度", show_lines=False)
    table.add_column("域", style="cyan", width=24)
    table.add_column("最新日期", width=12)
    table.add_column("距今", justify="right", width=8)
    table.add_column("状态", width=6)

    for domain, date_val in rows:
        date_str = str(date_val)[:10] if date_val else "无数据"
        days, color = _age(date_str if date_val else None)
        age_str = f"{days}天" if days is not None else "-"
        status = "✓" if (days is not None and days <= 3) else "⚠" if (days is not None and days <= 14) else "✗"
        table.add_row(domain, date_str,
                      f"[{color}]{age_str}[/{color}]",
                      f"[{color}]{status}[/{color}]")

    console.print(table)


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
  python main.py init-factor                   # 初始化 factor 域（计算型因子，台阶二）
  python main.py init-macro                    # 初始化宏观经济数据
  python main.py update-macro                  # 增量更新宏观数据（含汇率/境外指标）
  python main.py update-events                 # 增量更新事件域（E1 日历 + E2 企业事件）
  python main.py init-moneyflow                        # 初始化 moneyflow 域（stock 2010起，其余 2024起）
  python main.py init-moneyflow --types stock          # 只下载个股资金流
  python main.py init-moneyflow --stock-start 2015-01-01  # 个股资金流从2015年开始
  python main.py update-moneyflow                      # 增量更新 moneyflow 域
  python main.py init-hk                      # 首次下载港股K线（默认仅港股通标的）
  python main.py init-hk --full               # 下载全部港股
  python main.py update-hk                    # 增量更新港股到今日
  python main.py update-hk-valuation          # 更新港股 PE/PB（港股通，合并进 K 线）
  python main.py update-hk-moneyflow          # 更新港股个股资金流（东财）
  python main.py update-hk-financials         # 更新港股财务（三大报表+指标）
  python main.py update-hk-events             # 更新港股分红事件（events E2）
  python main.py update-cn-pcf                # 补充 A 股 pcf_ttm（百度市现率）
  python main.py inspect kline 00700 --market hk   # 直查港股K线
  python main.py update-all                            # 更新所有域（A股/港股/估值/events，耗时较长）
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
    # data-status
    sub.add_parser("data-status", help="各域数据新鲜度 + 后台更新进程状态")
    # portfolio
    p_pf = sub.add_parser("portfolio", help="持仓评估：加仓/止损建议 + 止盈止损点 + 委托参考价")
    p_pf.add_argument("--date", help="评估日期 YYYY-MM-DD（默认今日）")
    p_pf.add_argument("--file", default="data/portfolio.json",
                      help="持仓文件路径（默认 data/portfolio.json）")

    # scan
    p_scan = sub.add_parser("scan", help="全市场扫描（支持单策略/指定股票快速验证L3）")
    p_scan.add_argument("--date", help="指定日期 YYYY-MM-DD（默认今日）")
    p_scan.add_argument("--types", nargs="*", default=["stock"],
                        choices=["stock", "etf"],
                        help="扫描资产类型（默认 stock；可传 etf 或 stock etf）")
    p_scan.add_argument("--strategy", choices=list(BUILTIN_STRATEGIES.keys()),
                        help="只运行指定策略（不传则全部策略）")
    p_scan.add_argument("--codes", nargs="*",
                        help="只扫描指定股票代码（如 000001 600519），不传则全市场")
    p_scan.add_argument("--industries", nargs="*",
                        help="按申万行业代码过滤股票池（如 801080 801780），与--codes互斥")
    p_scan.add_argument("--industry-level", default="L1", choices=["L1", "L2", "L3"],
                        help="行业层级（默认L1）")

    # workflow（分层决策：L1→L2→L3）
    p_wf = sub.add_parser("workflow", help="运行分层决策 workflow（L1宏观→L2行业→L3个股，可视化）")
    p_wf.add_argument("--date", help="决策日期 YYYY-MM-DD（默认今日）")
    p_wf.add_argument("--industries", nargs="*",
                      help="手动指定申万行业代码（如 801080 801770），限定扫描范围加速验证")
    p_wf.add_argument("--industry-level", default="L1", choices=["L1", "L2", "L3"],
                      help="申万行业层级（默认 L1；L2/L3 用个股资金流聚合评分）")
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

    # init-sw-classify（申万三级分类，Tushare）
    sub.add_parser("init-sw-classify", help="单独下载申万三级行业分类（Tushare SW2021）")

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

    # update-macro
    sub.add_parser("update-macro", help="增量更新 macro 域（含汇率与境外指标）")

    # update-events
    sub.add_parser("update-events", help="增量更新 events 域（E1 日历 + E2 企业事件）")

    # init-moneyflow
    p_init_mf = sub.add_parser("init-moneyflow", help="初始化 moneyflow 域（资金流与情绪）")
    p_init_mf.add_argument("--start", default="2024-01-01", help="行业/大盘/龙虎榜/融资融券起始日期（默认2024-01-01）")
    p_init_mf.add_argument("--stock-start", default="2010-01-01",
                           help="个股资金流起始日期（默认2010-01-01）")
    p_init_mf.add_argument("--types", nargs="*",
                           default=["stock", "top_list", "top_inst", "block_trade",
                                    "margin", "industry", "market", "northbound"],
                           choices=["stock", "top_list", "top_inst", "block_trade",
                                    "margin", "industry", "market", "northbound"],
                           help="下载类型（默认全部）")

    # update-moneyflow
    p_upd_mf = sub.add_parser("update-moneyflow", help="增量更新 moneyflow 域")
    p_upd_mf.add_argument("--types", nargs="*",
                          default=["stock", "top_list", "top_inst", "block_trade",
                                   "margin", "industry", "market", "northbound"],
                          choices=["stock", "top_list", "top_inst", "block_trade",
                                   "margin", "industry", "market", "northbound"],
                          help="更新类型（默认全部）")

    # update-hk-hold（北向个股持股，按代码）
    p_upd_hkh = sub.add_parser("update-hk-hold",
                               help="按需下载北向个股持股（Tushare hk_hold，季度披露）")
    p_upd_hkh.add_argument("--codes", nargs="*", default=None,
                           help="股票代码（空格分隔），如 --codes 000001 600519")
    p_upd_hkh.add_argument("--from-portfolio", action="store_true",
                           help="标的取自 data/portfolio.json 当前持仓")
    p_upd_hkh.add_argument("--from-lhb", type=int, default=None, metavar="N",
                           help="标的取自最近 N 个龙虎榜上榜交易日涉及的股票")

    # smart-money（资金主体行为画像）
    p_sm = sub.add_parser("smart-money", help="资金主体行为画像（谁在买卖 + 跨期行为模式）")
    p_sm.add_argument("target", nargs="?", default=None,
                      help="股票代码/名称，或席位名称（如 机构专用）；--rank 时可省略")
    p_sm.add_argument("--type", dest="target_type", default="auto",
                      choices=["auto", "stock", "seat"], help="主体类型（默认自动判断）")
    p_sm.add_argument("--mode", default="full",
                      choices=["full", "seat", "northbound", "consensus"],
                      help="画像范围（默认完整三支柱；seat=仅席位身份与画像）")
    p_sm.add_argument("--window", type=int, default=12, help="回溯月数（默认12）")
    p_sm.add_argument("--horizons", default="5,10,20",
                      help="事后验证窗口（交易日，逗号分隔，默认5,10,20）")
    p_sm.add_argument("--fetch", action="store_true",
                      help="北向持股缺失时按需联网下载（Tushare hk_hold）")
    p_sm.add_argument("--rank", action="store_true",
                      help="列出近期活跃资金主体榜（活动名册，非买入清单）")
    p_sm.add_argument("--recent-lhb", action="store_true",
                      help="盘前扫描最近龙虎榜标的 + 席位历史画像（不用 target）")
    p_sm.add_argument("--days", type=int, default=1,
                      help="--recent-lhb 取最近 N 个龙虎榜披露日（默认1）")
    p_sm.add_argument("--history-months", type=int, default=0,
                      help="--recent-lhb 席位历史画像回溯月数（0=全部本地，默认0）")
    p_sm.add_argument("--top", type=int, default=20,
                      help="--rank 榜单条数 / --recent-lhb 每股席位数（默认20）")
    p_sm.add_argument("--json", dest="as_json", action="store_true", help="输出原始 JSON")
    p_sm.add_argument("--markdown", dest="as_markdown", action="store_true", help="输出 Markdown")

    # seat-dict（游资/量化席位字典）
    p_sd = sub.add_parser("seat-dict", help="席位字典（游资/量化识别）：build 构建 / list 查询")
    p_sd.add_argument("action", nargs="?", default="list",
                      choices=["build", "list", "import"],
                      help="build=构建字典，list=查询，import=从 xlsx 导入硬标签")
    p_sd.add_argument("--window", type=int, default=6, help="build 回溯月数（默认6）")
    p_sd.add_argument("--min-appearances", type=int, default=10,
                      help="build 参与聚类的最小上榜次数（默认10）")
    p_sd.add_argument("--k", type=int, default=3, help="build 聚类簇数（默认3）")
    p_sd.add_argument("--t1-days", type=int, default=2,
                      help="build T+1 兑现的交易日间隔阈值（默认2）")
    p_sd.add_argument("--label", default=None, help="list 过滤标签（如 知名游资营业部）")
    p_sd.add_argument("--top", type=int, default=30, help="list 展示条数（默认30）")
    p_sd.add_argument("--xlsx", dest="xlsx_path", default=None,
                      help="import 外部字典 xlsx 路径（默认 reference/A股龙虎榜席位资金画像字典.xlsx）")

    # capital-profiler（资金画像 Phase 1：个股级）
    p_cp = sub.add_parser("capital-profiler", help="资金画像（Phase1 个股级：主导资金/反馈/持续性）")
    p_cp.add_argument("action", nargs="?", default="profile", choices=["profile"],
                      help="profile=画像（默认）")
    p_cp.add_argument("--level", default="stock",
                      choices=["stock", "sector_etf", "sector_macro"],
                      help="对象层级（Phase1 仅 stock）")
    p_cp.add_argument("--code", default=None, help="股票代码（如 688256）")
    p_cp.add_argument("--date", default=None, help="分析日期 YYYY-MM-DD（默认最新）")
    p_cp.add_argument("--lookback", type=int, default=60, help="回看交易日（默认60）")
    p_cp.add_argument("--format", dest="fmt", default="text", choices=["text", "json"],
                      help="输出格式（默认 text）")

    # init-hk（港股）
    p_init_hk = sub.add_parser("init-hk", help="首次全量下载港股 K 线（默认仅港股通标的）")
    p_init_hk.add_argument("--start", default="2020-01-02", help="起始日期（默认2020-01-02）")
    p_init_hk.add_argument("--end", default=None, help="结束日期（默认今日）")
    p_init_hk.add_argument("--force", action="store_true", help="强制重新下载已有数据")
    p_init_hk.add_argument("--full", dest="connect_only", action="store_false",
                           help="下载全部港股（默认仅港股通标的）")

    # update-hk（港股）
    p_upd_hk = sub.add_parser("update-hk", help="增量更新港股 K 线到最新交易日")
    p_upd_hk.add_argument("--end", default=None, help="目标日期（默认今日）")
    p_upd_hk.add_argument("--full", dest="connect_only", action="store_false",
                          help="更新全部港股（默认仅港股通标的）")

    # update-hk-valuation（港股 PE/PB）
    p_upd_hkv = sub.add_parser("update-hk-valuation",
                               help="更新港股 PE/PB 估值（百度股市通，合并进 K 线）")
    p_upd_hkv.add_argument("--full", dest="connect_only", action="store_false",
                           help="全部港股（默认仅港股通标的）")
    p_upd_hkv.add_argument("--period", default="近三年",
                           choices=["近一年", "近三年"],
                           help="估值历史区间（默认近三年）")

    # update-hk-moneyflow（港股资金流）
    p_upd_hkm = sub.add_parser("update-hk-moneyflow",
                               help="更新港股个股资金流（东财，约 100 个交易日滚动）")
    p_upd_hkm.add_argument("--full", dest="connect_only", action="store_false",
                           help="全部港股（默认仅港股通标的）")

    # update-hk-financials（港股财务）
    p_upd_hkf = sub.add_parser("update-hk-financials",
                               help="更新港股财务（三大报表 + 成长/杜邦指标）")
    p_upd_hkf.add_argument("--full", dest="connect_only", action="store_false",
                           help="全部港股（默认仅港股通标的）")

    # update-hk-events（港股分红事件）
    p_upd_hke = sub.add_parser("update-hk-events",
                               help="更新港股分红派息事件（events E2）")
    p_upd_hke.add_argument("--full", dest="connect_only", action="store_false",
                           help="全部港股（默认仅港股通标的）")

    # update-cn-pcf（A股 PCF）
    p_upd_pcf = sub.add_parser("update-cn-pcf",
                               help="补充 A 股 pcf_ttm（百度市现率）")
    p_upd_pcf.add_argument("--codes", nargs="*", default=None,
                           help="指定股票代码（默认全市场，约 1 小时）")

    # update-all
    sub.add_parser("update-all", help="更新所有数据域（A股/港股 K线、meta、fundamental、factor、macro、moneyflow、events、估值）")

    # inspect
    p_ins = sub.add_parser("inspect", help="数据直查工具（K线/个股资金流/行业资金流/宏观）")
    p_ins.add_argument("subtype", choices=["kline", "mf", "ind", "macro", "fundamental"],
                       help="查询类型: kline=K线 mf=个股资金流 ind=行业资金流 macro=宏观 fundamental=基本面")
    p_ins.add_argument("target", nargs="?", default="",
                       help="查询目标: 股票代码(kline/mf) / 行业名(ind) / 宏观指标名或list(macro)")
    p_ins.add_argument("--n", type=int, default=20, help="显示最近N条（默认20）")
    p_ins.add_argument("--type", dest="asset_type", default="stock",
                       choices=["stock", "index", "etf"], help="资产类型（kline用，默认stock）")
    p_ins.add_argument("--market", default="cn", choices=["cn", "hk", "us"],
                       help="市场（kline/mf/fundamental 用，默认 cn）")
    p_ins.add_argument("--codes", nargs="*", default=None,
                       help="多个代码/行业名（空格分隔），与 target 二选一")
    p_ins.add_argument("--metric", default=None,
                       help="多只对比时的指标（kline默认close，mf默认net_mf_amount，ind默认net_amount）")
    p_ins.add_argument("--all", dest="all_items", action="store_true",
                       help="ind --all：输出全部行业资金流排名汇总")
    p_ins.add_argument("--level", default="L1", choices=["L1", "L2", "L3"],
                       help="行业层级（ind用，默认L1）")
    p_ins.add_argument("--start", default=None, help="起始日期 YYYY-MM-DD")
    p_ins.add_argument("--end", default=None, help="结束日期 YYYY-MM-DD")
    p_ins.add_argument("--plot", action="store_true", help="显示 matplotlib 图表")

    # web
    sub.add_parser("web", help="启动Web看板")

    # schedule
    sub.add_parser("schedule", help="启动定时任务")

    # exit-levels
    p_el = sub.add_parser("exit-levels", help="查看股票出场价位框架（分级操作表）")
    p_el.add_argument("code", help="股票代码，如 600519")
    p_el.add_argument("--entry", type=float, default=None, help="进场价（不填则用当前收盘价）")
    p_el.add_argument("--strategy", default=None, help="只看指定策略，如 ma_cross")

    # position-size
    p_ps = sub.add_parser("position-size", help="基于风险的仓位计算（固定风险法）")
    p_ps.add_argument("--entry", type=float, required=True, help="进场价")
    p_ps.add_argument("--stop", type=float, required=True, help="止损价")
    p_ps.add_argument("--capital", type=float, default=None, help="总资金（不填则用 portfolio.json 或默认值）")

    args = parser.parse_args()

    if args.command == "init":
        cmd_init(start=args.start, end=args.end,
                 force=args.force, workers=args.workers,
                 types=args.types)
    elif args.command == "update":
        cmd_update(end=args.end, workers=args.workers, types=args.types)
    elif args.command == "dbstats":
        cmd_dbstats()
    elif args.command == "data-status":
        cmd_data_status()
    elif args.command == "portfolio":
        cmd_portfolio(date=args.date, portfolio_file=args.file)
    elif args.command == "scan":
        cmd_scan(date=args.date, asset_types=args.types,
                 strategy=args.strategy, codes=args.codes,
                 industries=args.industries,
                 industry_level=args.industry_level)
    elif args.command == "workflow":
        cmd_workflow(date=args.date, industries=args.industries,
                     asset_types=args.types, industry_level=args.industry_level)
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
    elif args.command == "init-sw-classify":
        cmd_init_sw_classify()
    elif args.command == "init-fundamental":
        cmd_init_fundamental(start_year=args.start_year, force=args.force)
    elif args.command == "init-factor":
        cmd_init_factor(start=args.start)
    elif args.command == "init-macro":
        cmd_init_macro(start=args.start)
    elif args.command == "update-macro":
        cmd_update_macro()
    elif args.command == "update-events":
        cmd_update_events()
    elif args.command == "init-moneyflow":
        cmd_init_moneyflow(
            start=args.start,
            stock_start=args.stock_start,
            types=args.types,
        )
    elif args.command == "update-moneyflow":
        cmd_update_moneyflow(types=args.types)
    elif args.command == "update-hk-hold":
        cmd_update_hk_hold(codes=args.codes,
                           from_portfolio=args.from_portfolio,
                           from_lhb=args.from_lhb)
    elif args.command == "smart-money":
        cmd_smart_money(target=args.target, target_type=args.target_type,
                        mode=args.mode, window_months=args.window,
                        horizons=args.horizons, fetch=args.fetch,
                        as_json=args.as_json, as_markdown=args.as_markdown,
                        rank=args.rank, top_n=args.top,
                        recent_lhb=args.recent_lhb, days=args.days,
                        history_months=args.history_months)
    elif args.command == "seat-dict":
        cmd_seat_dict(action=args.action, window=args.window,
                      min_appearances=args.min_appearances, k=args.k,
                      t1_days=args.t1_days, label=args.label, top=args.top,
                      xlsx_path=args.xlsx_path)
    elif args.command == "capital-profiler":
        cmd_capital_profiler(action=args.action, level=args.level, code=args.code,
                             date=args.date, lookback_days=args.lookback, fmt=args.fmt)
    elif args.command == "init-hk":
        cmd_init_hk(start=args.start, end=args.end,
                    force=args.force, connect_only=args.connect_only)
    elif args.command == "update-hk":
        cmd_update_hk(end=args.end, connect_only=args.connect_only)
    elif args.command == "update-hk-valuation":
        cmd_update_hk_valuation(connect_only=args.connect_only, period=args.period)
    elif args.command == "update-hk-moneyflow":
        cmd_update_hk_moneyflow(connect_only=args.connect_only)
    elif args.command == "update-hk-financials":
        cmd_update_hk_financials(connect_only=args.connect_only)
    elif args.command == "update-hk-events":
        cmd_update_hk_events(connect_only=args.connect_only)
    elif args.command == "update-cn-pcf":
        cmd_update_cn_pcf(codes=args.codes)
    elif args.command == "update-all":
        cmd_update_all()
    elif args.command == "inspect":
        cmd_inspect(subtype=args.subtype, target=args.target,
                    n=args.n, asset_type=args.asset_type,
                    level=args.level, plot=args.plot,
                    start=args.start, end=args.end,
                    codes=args.codes, metric=args.metric,
                    all_items=args.all_items, market=args.market)
    elif args.command == "exit-levels":
        cmd_exit_levels(code=args.code, entry=args.entry, strategy=args.strategy)
    elif args.command == "position-size":
        cmd_position_size(entry=args.entry, stop=args.stop, capital=args.capital)
    else:
        parser.print_help()
