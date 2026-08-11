#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""验证产品完善计划相关 API 与页面（对运行中的服务做冒烟检查）。"""

import json
import sys
import urllib.error
import urllib.parse
import urllib.request

BASE = "http://127.0.0.1:8000"


def get(path: str, timeout: int = 15):
    url = BASE + path
    req = urllib.request.Request(url)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        body = resp.read().decode("utf-8")
        return resp.status, json.loads(body) if body else {}


def check(name: str, ok: bool, detail: str = ""):
    status = "PASS" if ok else "FAIL"
    line = f"[{status}] {name}"
    if detail:
        line += f" — {detail}"
    print(line)
    return ok


def main():
    all_ok = True
    today = "2026-06-01"

    try:
        code, health = get("/api/system/health")
        all_ok &= check("健康检查", code == 200 and health.get("status") == "healthy", str(health))
    except Exception as exc:
        all_ok &= check("健康检查", False, str(exc))
        print("服务未启动，请先运行: python3 scripts/start.py")
        return 1

    # P0 仪表盘聚合
    try:
        code, summary = get("/api/dashboard/summary")
        dash = summary.get("dashboard") or {}
        occ = dash.get("table_occupancy") or {}
        dq = summary.get("data_quality") or {}
        all_ok &= check(
            "P0 仪表盘 dashboard 字段",
            summary.get("success") and "dish_category_count" in dash and "urgent_order_count" in dash,
            f"categories={dash.get('dish_category_count')} urgent={dash.get('urgent_order_count')}",
        )
        all_ok &= check(
            "P0 数据质量字段",
            "api_failures" in dq and "biz_date" in dq,
            f"api_failures={dq.get('api_failures')}",
        )
        all_ok &= check(
            "P0 餐桌占用结构",
            "occupied" in occ and "total" in occ,
            f"occupied={occ.get('occupied')}",
        )
        orders = summary.get("orders") or {}
        all_ok &= check("P0 今日订单统计", "total_orders" in orders and "total_revenue" in orders)
    except Exception as exc:
        all_ok &= check("P0 仪表盘", False, str(exc))

    # 数据质量 API
    try:
        code, sh = get("/api/system/scraper-health")
        health = sh.get("health") or {}
        all_ok &= check(
            "数据质量 scraper-health",
            sh.get("success") and "api_failures" in health,
            f"biz_date={health.get('biz_date')}",
        )
        code, admin_sh = get("/api/admin/scraper-health")
        all_ok &= check(
            "Admin scraper-health",
            admin_sh.get("success") and "health" in admin_sh,
        )
    except Exception as exc:
        all_ok &= check("数据质量 API", False, str(exc))

    # P0 时区 / 口径：今日 urgent 与 paginated
    try:
        code, urgent = get("/api/orders/priority/urgent")
        all_ok &= check("P0 紧急订单(今日)", code == 200, f"count={len(urgent) if isinstance(urgent, list) else urgent}")
        code, pag = get("/api/orders/paginated?page=1&page_size=5")
        all_ok &= check(
            "P0 分页订单",
            pag.get("success") and "pagination" in pag,
            f"total={pag.get('pagination', {}).get('total_count')}",
        )
        code, st = get("/api/orders/station/shulong/stats")
        all_ok &= check("P0 档口统计(默认今日)", "total_orders" in st, str(st.get("total_orders")))
    except Exception as exc:
        all_ok &= check("P0 订单 API", False, str(exc))

    # P1 经营分析
    try:
        code, trend = get(f"/api/analytics/sales-trend?granularity=day&start_date={today}&end_date={today}")
        all_ok &= check(
            "P1 销售趋势",
            trend.get("success") and isinstance(trend.get("series"), list),
            f"points={len(trend.get('series', []))}",
        )
        code, tables = get("/api/tables/snapshot")
        all_ok &= check(
            "P1 餐桌快照",
            tables.get("success") and "occupied_tables" in tables,
            str(tables.get("occupied_tables")),
        )
        code, ops = get(f"/api/tables/operations?start_date={today}&end_date={today}")
        all_ok &= check("P1 餐桌运营", ops.get("success") and "tables_served" in ops)
        code, refunds = get(f"/api/analytics/refunds?start_date={today}&end_date={today}")
        all_ok &= check(
            "P1 退菜统计",
            refunds.get("success") and "refund_line_count" in refunds,
            str(refunds.get("refund_line_count")),
        )
    except Exception as exc:
        all_ok &= check("P1 经营分析 API", False, str(exc))

    # P1 销售报表 + 导出
    try:
        code, report = get(f"/api/orders/sales-report?start_date={today}&end_date={today}")
        all_ok &= check(
            "P1 销售报表",
            report.get("success") and "dish_sales" in report,
            f"dishes={len(report.get('dish_sales', []))}",
        )
        export_url = (
            f"/api/export/sales-report.csv?"
            f"start_date={urllib.parse.quote(today)}&end_date={urllib.parse.quote(today)}"
        )
        req = urllib.request.Request(BASE + export_url)
        with urllib.request.urlopen(req, timeout=20) as resp:
            csv_head = resp.read(200).decode("utf-8", errors="replace")
        all_ok &= check(
            "P1 CSV 导出",
            "菜品" in csv_head or "类型" in csv_head,
            csv_head[:60].replace("\n", " "),
        )
    except Exception as exc:
        all_ok &= check("P1 报表/导出", False, str(exc))

    # P1 日志实时筛选
    try:
        code, logs = get("/api/logs/recent?limit=5&level=ERROR")
        all_ok &= check(
            "P1 实时日志筛选",
            logs.get("success") and isinstance(logs.get("items"), list),
            f"items={logs.get('count')}",
        )
    except Exception as exc:
        all_ok &= check("P1 日志", False, str(exc))

    # P1 display 已删除
    try:
        req = urllib.request.Request(BASE + "/display")
        urllib.request.urlopen(req, timeout=5)
        all_ok &= check("P1 /display 已下线", False, "仍返回 200")
    except urllib.error.HTTPError as exc:
        all_ok &= check("P1 /display 已下线", exc.code == 404, f"HTTP {exc.code}")
    except Exception as exc:
        all_ok &= check("P1 /display", False, str(exc))

    # 页面 HTML 关键片段
    pages = [
        ("/", ["dTables", "dashRefreshBtn", "orderFilterStation", "salesTrendChart"]),
        ("/sales-report", ["sumRefunds", "exportServerCSV"]),
        ("/prep-plan", ["btnRunAll", "STATION_LABELS"]),
        ("/logs", ["filterToggleBtn", "buildRealtimeParams"]),
        ("/setup", ["留空", "password"]),
    ]
    for path, needles in pages:
        try:
            req = urllib.request.Request(BASE + path)
            with urllib.request.urlopen(req, timeout=15) as resp:
                html = resp.read().decode("utf-8", errors="replace")
            missing = [n for n in needles if n not in html]
            all_ok &= check(f"页面 {path}", not missing, f"缺少: {missing}" if missing else "OK")
        except Exception as exc:
            all_ok &= check(f"页面 {path}", False, str(exc))

    # common.js 导航无 display
    try:
        req = urllib.request.Request(BASE + "/common.js")
        with urllib.request.urlopen(req, timeout=10) as resp:
            js = resp.read().decode("utf-8")
        all_ok &= check("导航无电视大屏", "/display" not in js or "电视大屏" not in js)
    except Exception as exc:
        all_ok &= check("common.js", False, str(exc))

    print("-" * 50)
    if all_ok:
        print("全部检查通过")
        return 0
    print("存在失败项，请查看上方 FAIL")
    return 1


if __name__ == "__main__":
    sys.exit(main())
