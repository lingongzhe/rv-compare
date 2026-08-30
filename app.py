# -*- coding: utf-8 -*-
"""二手房车聚合比价 Web 应用（本地运行）

用法: python app.py  然后浏览器打开 http://127.0.0.1:5000
"""
import json
from flask import Flask, render_template, request, abort

import database

app = Flask(__name__)
database.init_db()

# 数据来源的中文名，用于列表/详情标注
SOURCE_NAMES = {"rv28": "房车猫", "21rv": "21世纪房车", "cn2rv": "舒旅二手房车网"}
SOURCE_SITES = {"rv28": "房车猫二手房专区", "21rv": "21世纪房车二手车板块",
                "cn2rv": "舒旅二手房车网（cn2rv.com）"}
# 各来源徽章配色（用于列表/比价页标注）
SOURCE_COLORS = {"rv28": "#3a6ea5", "21rv": "#e67e22", "cn2rv": "#2e9e5b"}


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
    return render_template("detail.html", v=row, similar=similar, active="index")


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


@app.route("/stats")
def stats():
    s = database.stats_overview()
    return render_template("stats.html", s=s, active="stats")


if __name__ == "__main__":
    print("二手房车聚合比价应用已启动: http://127.0.0.1:5000")
    app.run(host="127.0.0.1", port=5000, debug=False)
