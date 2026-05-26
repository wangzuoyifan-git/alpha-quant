"""
init 流程自动化验证脚本
模拟完整 init 流程（小样本），发现问题直接报告
"""
import sys
import traceback
import shutil
from pathlib import Path
from datetime import datetime

sys.path.insert(0, str(Path(__file__).parent.parent))

import data.store as store
from data.fetcher import DataFetcher
from data.downloader import KlineDownloader
from config.settings import DATA_SOURCE

# ---- 测试样本 ----
TEST_STOCKS = ["600519", "000001", "300750", "600036", "601318",
               "000858", "002415", "600276", "000333", "600900"]
TEST_INDEX  = ["000001", "000300", "399001", "000016", "000905"]
TEST_ETF    = ["510300", "510500", "159915", "510050", "159919"]

START = "2024-01-02"
END   = datetime.now().strftime("%Y-%m-%d")

PASS = "✓"
FAIL = "✗"
results = []


def check(name, fn):
    try:
        fn()
        results.append((PASS, name))
        print(f"  {PASS} {name}")
        return True
    except Exception as e:
        results.append((FAIL, name, traceback.format_exc()))
        print(f"  {FAIL} {name}")
        print(f"     {e}")
        return False


def run():
    print(f"\n{'='*60}")
    print(f"  Alpha量化 init 流程验证  {END}")
    print(f"{'='*60}\n")

    # ---- 1. 清理测试数据 ----
    print("[1] 清理测试目录...")
    test_dir = store.BASE_DIR / "_test"
    if test_dir.exists():
        shutil.rmtree(test_dir)

    # 临时把 BASE_DIR 指向测试目录，不污染真实数据
    original_base = store.BASE_DIR
    store.BASE_DIR = test_dir
    store.IPO_FILE = test_dir / "ipo_dates.json"
    for market, atypes in store.MARKET_ASSET_TYPES.items():
        for atype in atypes:
            (test_dir / market / atype).mkdir(parents=True, exist_ok=True)
    store.DIRS = {atype: test_dir / "cn" / atype
                  for atype in store.MARKET_ASSET_TYPES["cn"]}
    print(f"  测试目录: {test_dir}\n")

    fetcher = DataFetcher(DATA_SOURCE)
    dl = KlineDownloader(fetcher)

    # ---- 2. 获取证券列表 ----
    print("[2] 获取证券列表...")
    security_list = {}
    def _get_list():
        nonlocal security_list
        security_list = dl._get_security_list(["stock", "index", "etf"])
        assert security_list["stock"], "stock 列表为空"
        assert security_list["index"], "index 列表为空"
        assert security_list["etf"],   "etf 列表为空"
        print(f"     stock={len(security_list['stock'])} "
              f"index={len(security_list['index'])} "
              f"etf={len(security_list['etf'])}")
    check("获取全市场证券列表", _get_list)

    # ---- 3. 下载各类型小样本 ----
    import baostock as bs

    for atype, sample_codes in [
        ("stock", TEST_STOCKS),
        ("index", TEST_INDEX),
        ("etf",   TEST_ETF),
    ]:
        print(f"\n[3] 下载 {atype} 样本（{len(sample_codes)} 只）...")

        # 从真实列表里取交集，确保代码有效
        valid = {c for c, _ in security_list.get(atype, [])}
        samples = [(c, c) for c in sample_codes if c in valid]
        if not samples:
            print(f"  ⚠ 样本代码不在证券列表中，跳过")
            continue

        def _download(atype=atype, samples=samples):
            bs.login()
            try:
                ok = skip = fail = 0
                for code, name in samples:
                    status = dl._download_one(bs, code, atype, START, END)
                    if status == "ok":    ok += 1
                    elif status == "skip": skip += 1
                    else:                  fail += 1
                    print(f"     {code}: {status}")
                print(f"     合计: ok={ok} skip={skip} fail={fail}")
                assert ok > 0, f"全部失败，ok=0"
                assert fail == 0, f"有 {fail} 只失败"
            finally:
                bs.logout()
        check(f"下载 {atype} 样本", _download)

        # ---- 4. 验证写入数据 ----
        def _verify(atype=atype, samples=samples):
            for code, _ in samples:
                df = store.load(code, asset_type=atype, market="cn",
                                start=START, end=END)
                assert df is not None and not df.empty, \
                    f"{code} 读取为空"
                assert len(df) > 10, \
                    f"{code} 数据行数过少: {len(df)}"
                expected_cols = store._cols("cn", atype)
                missing = [c for c in expected_cols if c not in df.columns]
                assert not missing, \
                    f"{code} 缺少列: {missing}"
                # 检查数值列无全 NaN
                num_cols = [c for c in expected_cols
                            if c not in store.STR_COLS]
                for col in ["open", "high", "low", "close", "volume"]:
                    if col in df.columns:
                        assert df[col].notna().any(), \
                            f"{code}.{col} 全为 NaN"
        check(f"验证 {atype} 数据完整性", _verify)

    # ---- 5. 验证 last_date / exists ----
    print("\n[4] 验证 store 接口...")
    def _store_api():
        for code in TEST_STOCKS[:3]:
            if store.exists(code, "stock", market="cn"):
                ld = store.last_date(code, "stock", market="cn")
                assert ld is not None, f"{code} last_date 返回 None"
                assert ld <= END, f"{code} last_date {ld} > end {END}"
        codes = store.list_codes("stock", market="cn")
        assert len(codes) > 0, "list_codes 返回空"
    check("store API（exists/last_date/list_codes）", _store_api)

    # ---- 6. 验证 IPO 日期保存 ----
    print("\n[5] 验证 IPO 日期...")
    def _ipo():
        dl._save_ipo_dates()
        ipo = store.load_ipo_dates()
        assert len(ipo) > 0, "ipo_dates 为空"
        # 检查 key 格式
        sample_key = next(iter(ipo))
        assert ":" in sample_key, \
            f"ipo key 格式错误，应为 'market:code'，实际: {sample_key}"
        # 检查几只已知股票
        for code in ["600519", "000001"]:
            key = f"cn:{code}"
            assert key in ipo, f"{key} 不在 ipo_dates 中"
            assert ipo[key].get("ipo_date"), f"{key} ipo_date 为空"
    check("IPO 日期保存与格式", _ipo)

    # ---- 7. 验证 stats ----
    print("\n[6] 验证 stats...")
    def _stats():
        s = store.stats()
        assert "cn" in s, "stats 缺少 cn"
        assert "total" in s, "stats 缺少 total"
        assert s["total"]["count"] > 0, "total count = 0"
        cn_stock = s.get("cn", {}).get("stock", {})
        assert cn_stock.get("count", 0) > 0, "cn/stock count = 0"
    check("stats() 结构正确", _stats)

    # ---- 8. 验证日期合法性（start > end 应跳过）----
    print("\n[7] 验证日期边界...")
    def _date_guard():
        bs.login()
        try:
            status = dl._download_one(bs, "600519", "stock",
                                      start="2099-01-01", end=END)
            assert status == "skip", \
                f"start > end 应返回 skip，实际: {status}"
        finally:
            bs.logout()
    check("start > end 自动跳过", _date_guard)

    # ---- 清理 ----
    shutil.rmtree(test_dir, ignore_errors=True)
    store.BASE_DIR = original_base
    store.IPO_FILE = original_base / "ipo_dates.json"
    store.DIRS = {atype: original_base / "cn" / atype
                  for atype in store.MARKET_ASSET_TYPES["cn"]}

    # ---- 汇总 ----
    print(f"\n{'='*60}")
    passed = sum(1 for r in results if r[0] == PASS)
    failed = sum(1 for r in results if r[0] == FAIL)
    print(f"  结果: {passed} 通过  {failed} 失败")
    if failed:
        print(f"\n失败详情:")
        for r in results:
            if r[0] == FAIL:
                print(f"\n  ✗ {r[1]}")
                print(r[2])
    print(f"{'='*60}\n")
    return failed == 0


if __name__ == "__main__":
    ok = run()
    sys.exit(0 if ok else 1)
