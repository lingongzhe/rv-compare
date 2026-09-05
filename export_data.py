# -*- coding: utf-8 -*-
"""把 rv.db 导出为静态站点数据 docs/data.js（供 GitHub Pages 手机/电脑访问）
只导出展示所需字段，省略超大原始配置，控制体积便于移动端加载。
"""
import datetime
import json
import os

import database

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "docs", "data.js")

# 只导出前端实际用到的字段；new_price/mileage_text 前端未使用，一并剔除减负
FIELDS = [
    "tid", "source", "url", "title", "price", "mileage_km",
    "reg_year", "reg_date", "emission", "transfer_count",
    "usage_type", "location", "chassis_brand", "chassis_model", "brand",
    "rv_type", "tags", "image_url", "description",
]

# 描述截断长度（前端仅在详情页展示，短一点足够识别车况；可用环境变量 RV_DESC_LEN 覆盖）
DESC_LEN = int(os.environ.get("RV_DESC_LEN", "150"))


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
    updated = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
    with open(OUT, "w", encoding="utf-8") as f:
        f.write("window.RV_UPDATED = %r;\n" % updated)
        f.write("window.RV_DATA = " + json.dumps(out, ensure_ascii=False) + ";\n")
    size = os.path.getsize(OUT) / 1024
    by_src = {}
    for r in out:
        by_src[r["source"]] = by_src.get(r["source"], 0) + 1
    print(f"导出 {len(out)} 台 -> docs/data.js  ({size:.0f} KB)  更新于 {updated}")
    print("分源:", by_src)


if __name__ == "__main__":
    main()