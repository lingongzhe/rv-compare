# -*- coding: utf-8 -*-
"""SQLite 数据库层：车源存储与查询"""
import os
import sqlite3

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BASE_DIR, "data", "rv.db")

SCHEMA = """
CREATE TABLE IF NOT EXISTS vehicles (
    tid            TEXT PRIMARY KEY,     -- 论坛帖子ID，去重主键
    source         TEXT DEFAULT 'rv28',  -- 数据来源站点
    url            TEXT,
    title          TEXT,
    price          REAL,                 -- 挂牌价（万元）
    new_price      REAL,                 -- 新车含税价（万元，可能缺失）
    mileage_km     INTEGER,              -- 行驶里程（公里）
    mileage_text   TEXT,
    reg_date       TEXT,                 -- 上牌时间（原始文本）
    reg_year       INTEGER,              -- 上牌年份（同年限对比用）
    emission       TEXT,                 -- 排放标准
    transfer_count TEXT,                 -- 过户次数
    usage_type     TEXT,                 -- 使用性质
    location       TEXT,                 -- 车辆所在地
    chassis_brand  TEXT,                 -- 底盘品牌（福特/依维柯/大通…）
    chassis_model  TEXT,                 -- 底盘型号（F150/欧盛/V90…）
    brand          TEXT,                 -- 上装品牌（旅美速腾/宇通…）
    rv_type        TEXT,                 -- 类型（自行B型/自行C型/拖挂/露营车…）
    tags           TEXT,                 -- 标签（官方直营/个人车源…）
    image_url      TEXT,
    specs_json     TEXT,                 -- 正文提取的完整配置（JSON）
    description    TEXT,                 -- 正文描述
    fetched_at     TEXT
);
CREATE INDEX IF NOT EXISTS idx_vehicles_brand ON vehicles(brand);
CREATE INDEX IF NOT EXISTS idx_vehicles_chassis ON vehicles(chassis_model);
CREATE INDEX IF NOT EXISTS idx_vehicles_year ON vehicles(reg_year);
"""


def get_conn():
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    with get_conn() as conn:
        conn.executescript(SCHEMA)


def vehicle_exists(tid):
    with get_conn() as conn:
        row = conn.execute("SELECT 1 FROM vehicles WHERE tid=?", (tid,)).fetchone()
        return row is not None


def detail_exists(tid):
    """是否已抓取过详情页（以specs_json是否填充为准）"""
    with get_conn() as conn:
        row = conn.execute(
            "SELECT specs_json FROM vehicles WHERE tid=?", (tid,)).fetchone()
        return row is not None and bool(row["specs_json"])


COLUMNS = [
    "tid", "source", "url", "title", "price", "new_price", "mileage_km",
    "mileage_text", "reg_date", "reg_year", "emission", "transfer_count",
    "usage_type", "location", "chassis_brand", "chassis_model", "brand",
    "rv_type", "tags", "image_url", "specs_json", "description", "fetched_at",
]


def upsert_vehicle(rec):
    vals = [rec.get(c) for c in COLUMNS]
    sql = (
        "INSERT INTO vehicles ({cols}) VALUES ({ph}) "
        "ON CONFLICT(tid) DO UPDATE SET {upd}"
    ).format(
        cols=",".join(COLUMNS),
        ph=",".join(["?"] * len(COLUMNS)),
        upd=",".join(f"{c}=excluded.{c}" for c in COLUMNS if c != "tid"),
    )
    with get_conn() as conn:
        conn.execute(sql, vals)


def count_vehicles():
    with get_conn() as conn:
        return conn.execute("SELECT COUNT(*) c FROM vehicles").fetchone()["c"]


# ---------------- 查询 ----------------

def query_vehicles(f, page=1, per_page=24):
    """f: dict(kw, rv_type, chassis_brand, reg_year, pmin, pmax, sort, source)"""
    where, args = [], []
    if f.get("kw"):
        where.append("(title LIKE ? OR brand LIKE ? OR chassis_model LIKE ?)")
        like = f"%{f['kw']}%"
        args += [like, like, like]
    if f.get("rv_type"):
        where.append("rv_type=?"); args.append(f["rv_type"])
    if f.get("chassis_brand"):
        where.append("chassis_brand=?"); args.append(f["chassis_brand"])
    if f.get("reg_year"):
        where.append("reg_year=?"); args.append(int(f["reg_year"]))
    if f.get("source"):
        where.append("source=?"); args.append(f["source"])
    if f.get("pmin"):
        where.append("price>=?"); args.append(float(f["pmin"]))
    if f.get("pmax"):
        where.append("price<=?"); args.append(float(f["pmax"]))
    where_sql = ("WHERE " + " AND ".join(where)) if where else ""
    order = {
        "price_asc": "price ASC",
        "price_desc": "price DESC",
        "year_desc": "reg_year DESC, price ASC",
        "mileage_asc": "mileage_km ASC",
    }.get(f.get("sort"), "CAST(tid AS INTEGER) DESC")
    total_sql = f"SELECT COUNT(*) c FROM vehicles {where_sql}"
    sql = (f"SELECT * FROM vehicles {where_sql} ORDER BY {order} "
           f"LIMIT ? OFFSET ?")
    with get_conn() as conn:
        total = conn.execute(total_sql, args).fetchone()["c"]
        rows = conn.execute(sql, args + [per_page, (page - 1) * per_page]).fetchall()
    return rows, total


def get_vehicle(tid):
    with get_conn() as conn:
        return conn.execute("SELECT * FROM vehicles WHERE tid=?", (tid,)).fetchone()


def filter_options():
    with get_conn() as conn:
        types = [r["rv_type"] for r in conn.execute(
            "SELECT rv_type FROM vehicles WHERE rv_type!='' GROUP BY rv_type ORDER BY COUNT(*) DESC")]
        cbs = [r["chassis_brand"] for r in conn.execute(
            "SELECT chassis_brand FROM vehicles WHERE chassis_brand!='' GROUP BY chassis_brand ORDER BY COUNT(*) DESC")]
        years = [r["reg_year"] for r in conn.execute(
            "SELECT reg_year FROM vehicles WHERE reg_year IS NOT NULL GROUP BY reg_year ORDER BY reg_year DESC")]
    return types, cbs, years


def sources():
    """可选数据来源及其车源数，用于列表页"来源"筛选"""
    with get_conn() as conn:
        return conn.execute(
            "SELECT source, COUNT(*) n FROM vehicles GROUP BY source ORDER BY n DESC").fetchall()


def compare_groups():
    """按底盘型号聚合，作为对比入口"""
    with get_conn() as conn:
        return conn.execute("""
            SELECT chassis_model,
                   COUNT(*) AS n,
                   COUNT(DISTINCT brand) AS n_brand,
                   MIN(reg_year) AS y1, MAX(reg_year) AS y2,
                   ROUND(AVG(price),1) AS avg_price,
                   ROUND(MIN(price),1) AS min_price,
                   ROUND(MAX(price),1) AS max_price
            FROM vehicles
            WHERE chassis_model IS NOT NULL AND chassis_model != '' AND price IS NOT NULL
            GROUP BY chassis_model
            HAVING n >= 1
            ORDER BY n DESC, chassis_model
        """).fetchall()


def compare_vehicles(chassis_model, brand="", reg_year=""):
    where = ["chassis_model=?"]
    args = [chassis_model]
    if brand:
        where.append("brand=?"); args.append(brand)
    if reg_year:
        where.append("reg_year=?"); args.append(int(reg_year))
    sql = ("SELECT * FROM vehicles WHERE " + " AND ".join(where) +
           " AND price IS NOT NULL ORDER BY reg_year DESC, price ASC")
    with get_conn() as conn:
        return conn.execute(sql, args).fetchall()


def stats_overview():
    with get_conn() as conn:
        total = conn.execute("SELECT COUNT(*) c FROM vehicles").fetchone()["c"]
        by_brand = conn.execute(
            "SELECT brand k, COUNT(*) n, ROUND(AVG(price),1) avg_p FROM vehicles "
            "WHERE brand!='' GROUP BY brand ORDER BY n DESC LIMIT 20").fetchall()
        by_type = conn.execute(
            "SELECT rv_type k, COUNT(*) n FROM vehicles WHERE rv_type!='' "
            "GROUP BY rv_type ORDER BY n DESC").fetchall()
        by_chassis = conn.execute(
            "SELECT chassis_brand k, COUNT(*) n FROM vehicles WHERE chassis_brand!='' "
            "GROUP BY chassis_brand ORDER BY n DESC LIMIT 15").fetchall()
        by_year = conn.execute(
            "SELECT reg_year k, COUNT(*) n, ROUND(AVG(price),1) avg_p FROM vehicles "
            "WHERE reg_year IS NOT NULL GROUP BY reg_year ORDER BY k").fetchall()
        prices = conn.execute(
            "SELECT price FROM vehicles WHERE price IS NOT NULL").fetchall()
    bins = [(0, 10), (10, 20), (20, 30), (30, 50), (50, 100), (100, 10**9)]
    price_hist = []
    for lo, hi in bins:
        n = sum(1 for p in prices if lo <= p["price"] < hi)
        label = f"{lo}-{hi}万" if hi < 10**8 else f"{lo}万以上"
        price_hist.append({"k": label, "n": n})
    return {
        "total": total,
        "by_brand": by_brand, "by_type": by_type,
        "by_chassis": by_chassis, "by_year": by_year,
        "price_hist": price_hist,
    }
