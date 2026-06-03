"""
agents/data_agent.py — Data Agent
每日收盘后检查数据新鲜度，自动触发增量更新
"""
import logging

logger = logging.getLogger(__name__)


def check_and_update() -> dict:
    """
    数据 Agent 单次运行：检查 → 更新 → 验证。

    返回 {"all_fresh": bool, "updated": list, "issues": list}
    """
    from agent_tools import check_data_freshness, update_domain, get_domain_stats

    status = check_data_freshness()
    stale = status.get("stale", [])
    updated = []
    issues = []

    for domain in stale:
        result = update_domain(domain)
        if result["success"]:
            updated.append(domain)
            logger.info(f"DataAgent: {domain} 更新成功")
        else:
            issues.append({"domain": domain, "error": result.get("error", "unknown")})
            logger.warning(f"DataAgent: {domain} 更新失败 — {result.get('error')}")

    stats = get_domain_stats()

    return {
        "all_fresh": len(stale) == 0 or len(stale) == len(updated),
        "domains_checked": len(status.get("domains", {})),
        "stale_before": len(stale),
        "updated": updated,
        "issues": issues,
        "stats_summary": {k: f"{v.get('count', 0)} items" if isinstance(v, dict) else str(v)
                          for k, v in stats.items()},
    }
