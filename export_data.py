# -*- coding: utf-8 -*-
"""把 rv.db 导出为静态站点数据 docs/data.js（供 GitHub Pages 手机/电脑访问）
只导出展示所需字段，省略超大原始配置，控制体积便于移动端加载。

除了车源数组，还导出 window.RV_SOURCES（各数据源新鲜度），
让页面能提示"某数据源已停更"，避免用户误以为是本站抓取坏了。
"""
import datetime
import json
import os
import re

import database

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "docs", "data.js")

# 只导出前端实际用到的字段；new_price/mileage_text 前端未使用，一并剔除减负
FIELDS = [
    "tid", "source", "url", "title", "price", "mileage_km",
    "reg_year", "reg_date", "emission", "transfer_count",
    "usage_type", "location", "chassis_brand", "chassis_model", "brand",
    "rv_type", "tags", "image_url", "description", "posted_at",
]

# 描述截断长度（前端仅在详情页展示，短一点足够识别车况；可用环境变量 RV_DESC_LEN 覆盖）
DESC_LEN = int(os.environ.get("RV_DESC_LEN", "150"))

# 超过这个天数没有新挂牌，就在页面上提示"该源已停更"
STALE_DAYS = int(os.environ.get("RV_STALE_DAYS", "14"))


def main():
    with database.get_conn() as conn:
        rows = conn.execute("SELECT * FROM vehicles").fetchall()
        first_price = {r["tid"]: r["fp"] for r in conn.execute(
            "SELECT tid, MIN(price) AS fp FROM price_history WHERE price IS NOT NULL "
            "GROUP BY tid")}
    out = []
    for r in rows:
        rec = {f: r[f] for f in FIELDS}
        rec["first_price"] = first_price.get(r["tid"])
        # 描述太长会造成包体膨胀，按 DESC_LEN 截断（完整配置可在前端详情页查看）
        if rec.get("description") and len(rec["description"]) > DESC_LEN:
            rec["description"] = rec["description"][:DESC_LEN] + "…"
        out.append(rec)
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    now = datetime.datetime.now()
    updated = now.strftime("%Y-%m-%d %H:%M")

    # 数据源新鲜度：条数 / 最后入库 / 源站最新挂牌 / 停更天数 / 是否告警
    sources = database.source_freshness()
    for k, v in sources.items():
        v["stale"] = bool(v.get("stale_days") is not None and v["stale_days"] > STALE_DAYS)
    by_src = {r["source"]: r["n"] for r in database.sources()}
    for k, n in by_src.items():
        sources.setdefault(k, {})["n"] = n
    fresh = [v["last_post"] for v in sources.values() if v.get("last_post")]
    overall_latest = max(fresh) if fresh else ""

    with open(OUT, "w", encoding="utf-8") as f:
        f.write("window.RV_UPDATED = %r;\n" % updated)
        f.write("window.RV_SOURCES = " + json.dumps(sources, ensure_ascii=False) + ";\n")
        f.write("window.RV_DATA = " + json.dumps(out, ensure_ascii=False) + ";\n")

    # 缓存破除：把 index.html 里 data.js 的加载网址加上版本号，
    # 否则浏览器/CDN 会一直复用旧文件，页面显示不到本次新数据
    ver = now.strftime("%Y%m%d%H%M")
    idx_html = os.path.join(HERE, "docs", "index.html")
    if os.path.exists(idx_html):
        html = open(idx_html, encoding="utf-8").read()
        new_html, n = re.subn(r'<script\s+src="data\.js(\?v=\d+)?"',
                              f'<script src="data.js?v={ver}"', html)
        if n:
            with open(idx_html, "w", encoding="utf-8") as f:
                f.write(new_html)
    size = os.path.getsize(OUT) / 1024
    print(f"导出 {len(out)} 台 -> docs/data.js  ({size:.0f} KB)  更新于 {updated}")
    print("分源:", by_src)
    print("数据源最新挂牌:", overall_latest)
    for k, v in sorted(sources.items(), key=lambda kv: -(kv[1].get("n") or 0)):
        flag = "⚠停更" if v.get("stale") else "正常"
        if v.get("basis") == "source":
            basis = "源站最新挂牌 " + (v.get("last_post") or "?")
        elif v.get("basis") == "local":
            basis = "源站无发布时间，本站最近收录 " + (v.get("last_new") or "?")
        else:
            basis = "无时间依据"
        print(f"  {k:<6} {v.get('n', 0):>5} 台 | {basis:<36} "
              f"| 停更 {v.get('stale_days')} 天 | {flag}")


if __name__ == "__main__":
    main()
