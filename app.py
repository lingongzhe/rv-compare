# -*- coding: utf-8 -*-
"""二手房车聚合比价 Web 应用（本地运行）

用法: python app.py  然后浏览器打开 http://127.0.0.1:5000

页面:
    /               车源列表（筛选/排序/分页/收藏/多选对比）
    /vehicle/<tid>  车源详情（配置清单/价格历史/同款比价入口）
    /compare        同款比价（按底盘型号聚合）
    /side           多车横向对比（?ids=a,b,c，最多4台）
    /drops          最近降价车源
    /stats          行情总览
API:
    /api/vehicles   车源 JSON（支持与列表页相同的筛选参数）
    /api/vehicle/<tid>
    /api/stats
    /api/drops
导出:
    /export.csv     按当前筛选条件导出 CSV
"""
import csv
import io
import json

from flask import (Flask, Response, abort, jsonify, render_template, request)

import database

app = Flask(__name__)
database.init_db()

# 数据来源的中文名，用于列表/详情标注
SOURCE_NAMES = {"rv28": "房车猫", "21rv": "21世纪房车", "cn2rv": "舒旅二手房车网"}
SOURCE_SITES = {"rv28": "房车猫二手房专区", "21rv": "21世纪房车二手车板块",
                "cn2rv": "舒旅二手房车网（cn2rv.com）"}
# 各来源徽章配色（用于列表/比价页标注）
SOURCE_COLORS = {"rv28": "#3a6ea5", "21rv": "#e67e22", "cn2rv": "#2e9e5b"}

CSV_FIELDS = [
    "tid", "source", "title", "price", "new_price", "mileage_km",
    "reg_date", "reg_year", "emission", "transfer_count", "usage_type",
    "location", "chassis_brand", "chassis_model", "brand", "rv_type",
    "tags", "url",
]


@app.context_processor
def inject_helpers():
    def query_without_page():
        args = {k: v for k, v in request.args.items() if k != "page" and v != ""}
        if not args:
            return ""
        return "&".join(f"{k}={v}" for k, v in args.items()) + "&"
    def source_name(code):
        return SOURCE_NAMES.get(code, code or "未知来源")
    def source_color(code):
        return SOURCE_COLORS.get(code, "#8a93a3")
    return {
        "query_without_page": query_without_page,
        "source_name": source_name,
        "source_color": source_color,
    }


def get_filters():
    return {
        "kw": request.args.get("kw", "").strip(),
        "rv_type": request.args.get("rv_type", ""),
        "chassis_brand": request.args.get("chassis_brand", ""),
        "reg_year": request.args.get("reg_year", ""),
        "pmin": request.args.get("pmin", ""),
        "pmax": request.args.get("pmax", ""),
        "sort": request.args.get("sort", ""),
        "source": request.args.get("source", ""),
    }


@app.template_filter("fmt_price")
def fmt_price(v):
    return f"{v:.1f}" if v else "面议"


@app.template_filter("fmt_mileage")
def fmt_mileage(v):
    if v is None:
        return "-"
    return f"{v/10000:.1f}万" if v >= 10000 else f"{v}"


@app.template_filter("specs_from_json")
def specs_from_json(s):
    if not s:
        return {}
    try:
        return json.loads(s)
    except Exception:
        return {}


@app.route("/")
def index():
    f = get_filters()
    page = max(1, request.args.get("page", 1, type=int))
    per_page = 24
    rows, total = database.query_vehicles(f, page, per_page)
    types, cbs, years = database.filter_options()
    srcs = database.sources()
    pages = max(1, (total + per_page - 1) // per_page)
    return render_template(
        "index.html", rows=rows, f=f, total=total, page=page, pages=pages,
        types=types, cbs=cbs, years=years, srcs=srcs, active="index",
    )


@app.route("/vehicle/<tid>")
def vehicle(tid):
    row = database.get_vehicle(tid)
    if row is None:
        abort(404)
    # 同款（同底盘型号）其他在售车源，供快速比价
    similar = []
    if row["chassis_model"]:
        similar = [r for r in database.compare_vehicles(row["chassis_model"])
                   if r["tid"] != row["tid"]][:8]
    history = database.get_price_history(tid)
    return render_template("detail.html", v=row, similar=similar,
                           history=history, active="index")


@app.route("/compare")
def compare():
    groups = database.compare_groups()
    model = request.args.get("model", "")
    brand = request.args.get("brand", "")
    year = request.args.get("year", "")
    rows = []
    brands = []
    if model:
        rows = database.compare_vehicles(model, brand, year)
        brands = sorted({r["brand"] for r in rows if r["brand"]})
    prices = [r["price"] for r in rows if r["price"]]
    stat = None
    if prices:
        stat = {
            "n": len(prices),
            "min": min(prices), "max": max(prices),
            "avg": round(sum(prices) / len(prices), 1),
        }
    years = sorted({r["reg_year"] for r in rows if r["reg_year"]}, reverse=True)
    return render_template(
        "compare.html", groups=groups, model=model, brand=brand, year=year,
        rows=rows, brands=brands, stat=stat, years=years, active="compare",
    )


@app.route("/side")
def side_by_side():
    """多车横向对比：?ids=tid1,tid2,...（最多4台，列表页多选产生）"""
    ids = [t.strip() for t in request.args.get("ids", "").split(",")
           if t.strip()][:4]
    rows = database.get_vehicles_by_ids(ids)
    # 按用户选择的顺序排列
    by_id = {r["tid"]: r for r in rows}
    rows = [by_id[t] for t in ids if t in by_id]
    return render_template("side.html", rows=rows, active="index")


@app.route("/drops")
def drops():
    rows = database.recent_price_drops(100)
    return render_template("drops.html", rows=rows, active="drops")


@app.route("/stats")
def stats():
    s = database.stats_overview()
    return render_template("stats.html", s=s, active="stats")


# ---------------- CSV 导出 ----------------

@app.route("/export.csv")
def export_csv():
    f = get_filters()
    rows, _total = database.query_vehicles(f, page=1, per_page=100000)
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(CSV_FIELDS)
    for r in rows:
        writer.writerow([r[c] if r[c] is not None else "" for c in CSV_FIELDS])
    data = "\ufeff" + buf.getvalue()  # BOM，Excel 直接打开不乱码
    return Response(data, mimetype="text/csv",
                    headers={"Content-Disposition":
                             "attachment; filename=rv_vehicles.csv"})


# ---------------- JSON API ----------------

def _row_to_api(r, full=False):
    d = {k: r[k] for k in r.keys() if k != "specs_json"}
    if full and r["specs_json"]:
        try:
            d["specs"] = json.loads(r["specs_json"])
        except Exception:
            d["specs"] = {}
    return d


@app.route("/api/vehicles")
def api_vehicles():
    f = get_filters()
    page = max(1, request.args.get("page", 1, type=int))
    per_page = min(100, max(1, request.args.get("per_page", 24, type=int)))
    rows, total = database.query_vehicles(f, page, per_page)
    return jsonify({
        "total": total, "page": page, "per_page": per_page,
        "items": [_row_to_api(r) for r in rows],
    })


@app.route("/api/vehicle/<tid>")
def api_vehicle(tid):
    r = database.get_vehicle(tid)
    if r is None:
        abort(404)
    d = _row_to_api(r, full=True)
    d["price_history"] = [
        {"price": h["price"], "captured_at": h["captured_at"]}
        for h in database.get_price_history(tid)]
    return jsonify(d)


@app.route("/api/stats")
def api_stats():
    s = database.stats_overview()
    return jsonify({k: [dict(r) for r in v] if isinstance(v, list) else v
                    for k, v in s.items()})


@app.route("/api/drops")
def api_drops():
    return jsonify({"items": [dict(r) for r in database.recent_price_drops(100)]})


if __name__ == "__main__":
    print("二手房车聚合比价应用已启动: http://127.0.0.1:5000")
    app.run(host="127.0.0.1", port=5000, debug=False)
