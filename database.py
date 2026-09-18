# -*- coding: utf-8 -*-
"""SQLite 数据库层：车源存储与查询"""
import os
import sqlite3

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BASE_DIR, "data", "rv.db")

# 上游不提供发布时间时，用"本站多久没收录到新车源"推断停更的天数阈值。
# 比源站口径（RV_STALE_DAYS，默认 14 天）更宽松：小众源本来就可能几周没新车，
# 收紧会把正常源误报成故障，那比漏报更伤信任。
LOCAL_STALE_DAYS = int(os.environ.get("RV_LOCAL_STALE_DAYS", "45"))

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
    posted_at      TEXT,                 -- 源站挂牌/发布时间（用于判断数据源是否停更）
    first_seen     TEXT,                 -- 本站首次收录时间（上游不提供发布时间时的停更推断依据）
    fetched_at     TEXT                  -- 本地抓取时间
);
CREATE INDEX IF NOT EXISTS idx_vehicles_brand ON vehicles(brand);
CREATE INDEX IF NOT EXISTS idx_vehicles_chassis ON vehicles(chassis_model);
CREATE INDEX IF NOT EXISTS idx_vehicles_year ON vehicles(reg_year);
CREATE INDEX IF NOT EXISTS idx_vehicles_source ON vehicles(source);
CREATE INDEX IF NOT EXISTS idx_vehicles_type ON vehicles(rv_type);
CREATE INDEX IF NOT EXISTS idx_vehicles_price ON vehicles(price);

-- 价格历史：每次爬取发现挂牌价变化（或首次入库）时记录一条
CREATE TABLE IF NOT EXISTS price_history (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    tid         TEXT NOT NULL,
    price       REAL,
    captured_at TEXT,
    UNIQUE(tid, captured_at)
);
CREATE INDEX IF NOT EXISTS idx_price_history_tid ON price_history(tid);

-- 每次爬虫巡检记录：用来区分"抓取失败"和"源站没新车源"，
-- 页面据此显示"某源今天巡检过、接口正常、只是上游停更"
CREATE TABLE IF NOT EXISTS crawl_runs (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    source         TEXT,
    started_at     TEXT,
    ok             INTEGER,   -- 1 成功 / 0 失败
    scanned        INTEGER,   -- 本次读到的车源条数
    added          INTEGER,   -- 其中新增条数
    upstream_total INTEGER,   -- 源站在售总量（源站自己报的）
    note           TEXT
);
CREATE INDEX IF NOT EXISTS idx_crawl_runs_source ON crawl_runs(source, started_at);
"""


def get_conn():
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    with get_conn() as conn:
        conn.executescript(SCHEMA)
        _migrate(conn)
        # 价格历史基线回填：已有车源且无任何历史记录时，以当前挂牌价作为首次收录价
        conn.execute(
            "INSERT OR IGNORE INTO price_history(tid, price, captured_at) "
            "SELECT tid, price, datetime('now','localtime') FROM vehicles "
            "WHERE price IS NOT NULL "
            "AND tid NOT IN (SELECT DISTINCT tid FROM price_history)")


def _migrate(conn):
    """老库补列：SQLite 的 CREATE TABLE IF NOT EXISTS 不会给已存在的表加字段"""
    have = {r["name"] for r in conn.execute("PRAGMA table_info(vehicles)")}
    for col, ddl in (("posted_at", "ALTER TABLE vehicles ADD COLUMN posted_at TEXT"),
                     ("first_seen", "ALTER TABLE vehicles ADD COLUMN first_seen TEXT")):
        if col not in have:
            conn.execute(ddl)
    # 首见时间回填：老库没记录过，只能用抓取时间近似。
    # 宁可记成"偏晚"，也不谎称很早就见过——偏晚只会让停更告警更保守（少报），不会误报。
    conn.execute(
        "UPDATE vehicles SET first_seen=COALESCE(fetched_at, datetime('now','localtime')) "
        "WHERE first_seen IS NULL OR first_seen=''")
    _fix_price_units(conn)


def _fix_price_units(conn):
    """一次性修正"元/万元"混用（幂等，修完后再次运行不再有命中）。

    2026-09 排查发现 21rv 源约 16 条挂牌价与新车价按"元"入库（如 218000），
    与其余"万元"口径（如 11.0）混在一起，导致均价、比价、价格筛选、降价榜全部失真。
    实测两套口径有清晰断层（万元口径最大 300，元口径最小 20000），
    故 price>1000 一律 /10000。crawl_21rv.clean_price 已同步加了归一化，
    此处负责把历史脏数据（含 price_history）一并洗掉。
    0/负数是源站占位值（"0 元"面议），置 NULL 让前端显示"面议"。
    """
    conn.execute("UPDATE vehicles SET price=price/10000.0 WHERE price>1000")
    conn.execute("UPDATE vehicles SET new_price=new_price/10000.0 WHERE new_price>1000")
    conn.execute("UPDATE price_history SET price=price/10000.0 WHERE price>1000")
    conn.execute("UPDATE vehicles SET price=NULL WHERE price<=0")
    conn.execute("UPDATE vehicles SET new_price=NULL WHERE new_price<=0")
    conn.execute("DELETE FROM price_history WHERE price<=0")


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
    "rv_type", "tags", "image_url", "specs_json", "description",
    "posted_at", "first_seen", "fetched_at",
]

# 这些列在整行 upsert 时用 COALESCE 保护：爬虫没提供（None）就保留库里原值，
# 避免"只抓列表摘要"的调用把详情字段/挂牌时间清空
KEEP_IF_NULL = {"posted_at"}

# 只允许首次插入时写入、后续永不被覆盖的列（新老抓取都不能改写"首次收录"这个事实）
FROZEN_ON_INSERT = {"first_seen"}


def _now():
    from datetime import datetime
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def upsert_vehicle(rec):
    rec = dict(rec)
    if not rec.get("first_seen"):
        rec["first_seen"] = _now()
    vals = [rec.get(c) for c in COLUMNS]
    upd = ",".join(
        (f"{c}=COALESCE(excluded.{c},vehicles.{c})" if c in KEEP_IF_NULL
         else f"{c}=excluded.{c}")
        for c in COLUMNS if c != "tid" and c not in FROZEN_ON_INSERT)
    sql = (
        "INSERT INTO vehicles ({cols}) VALUES ({ph}) "
        "ON CONFLICT(tid) DO UPDATE SET {upd}"
    ).format(
        cols=",".join(COLUMNS),
        ph=",".join(["?"] * len(COLUMNS)),
        upd=upd,
    )
    with get_conn() as conn:
        old = conn.execute(
            "SELECT price FROM vehicles WHERE tid=?", (rec.get("tid"),)).fetchone()
        conn.execute(sql, vals)
        _record_price(conn, rec.get("tid"), rec.get("price"),
                      old["price"] if old else None)


def _record_price(conn, tid, price, old_price):
    """价格变化（或首次入库）时写一条价格历史"""
    if price is None:
        return
    if old_price is not None and abs((old_price or 0) - price) < 1e-9:
        return
    from datetime import datetime
    conn.execute(
        "INSERT OR IGNORE INTO price_history(tid, price, captured_at) VALUES (?,?,?)",
        (tid, price, datetime.now().strftime("%Y-%m-%d %H:%M:%S")))


def update_summary(rec):
    """部分更新：只刷新爬虫提供的非空字段（用于对已入库记录刷新挂牌价等），
    避免整行 upsert 把详情字段覆盖为 NULL"""
    if not rec.get("tid"):
        return
    sets = {k: v for k, v in rec.items() if k != "tid" and v not in (None, "")}
    if not sets:
        return
    old = None
    with get_conn() as conn:
        old = conn.execute(
            "SELECT price FROM vehicles WHERE tid=?", (rec["tid"],)).fetchone()
        if old is None:
            return
        sql = "UPDATE vehicles SET " + ",".join(f"{k}=?" for k in sets) + \
              " WHERE tid=?"
        conn.execute(sql, list(sets.values()) + [rec["tid"]])
        _record_price(conn, rec["tid"], sets.get("price", old["price"]),
                      old["price"])


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
        try:
            rz = int(float(f["reg_year"]))
        except (TypeError, ValueError):
            rz = None
        if rz is not None:
            where.append("reg_year=?"); args.append(rz)
    if f.get("source"):
        where.append("source=?"); args.append(f["source"])
    if f.get("pmin"):
        try:
            lo = float(f["pmin"])
        except (TypeError, ValueError):
            lo = None
        if lo is not None:
            where.append("price>=?"); args.append(lo)
    if f.get("pmax"):
        try:
            hi = float(f["pmax"])
        except (TypeError, ValueError):
            hi = None
        if hi is not None:
            where.append("price<=?"); args.append(hi)
    where_sql = ("WHERE " + " AND ".join(where)) if where else ""
    order = {
        "price_asc": "price ASC",
        "price_desc": "price DESC",
        "year_desc": "reg_year DESC, price ASC",
        "mileage_asc": "mileage_km ASC",
        "fetched": "fetched_at DESC",
    }.get(f.get("sort"), "fetched_at DESC, tid DESC")
    total_sql = f"SELECT COUNT(*) c FROM vehicles {where_sql}"
    # JOIN 必须先于 WHERE，否则有筛选时 SQL 报 near "LEFT" 语法错
    sql = (f"SELECT vehicles.*, fp.first_price FROM vehicles "
           f"LEFT JOIN (SELECT tid, MIN(price) AS first_price FROM price_history "
           f"GROUP BY tid) fp ON fp.tid = vehicles.tid "
           f"{where_sql} ORDER BY {order} LIMIT ? OFFSET ?")
    with get_conn() as conn:
        total = conn.execute(total_sql, args).fetchone()["c"]
        rows = conn.execute(sql, args + [per_page, (page - 1) * per_page]).fetchall()
    return rows, total


def get_vehicle(tid):
    with get_conn() as conn:
        return conn.execute("SELECT * FROM vehicles WHERE tid=?", (tid,)).fetchone()


def get_price_history(tid):
    with get_conn() as conn:
        return conn.execute(
            "SELECT price, captured_at FROM price_history WHERE tid=? "
            "ORDER BY captured_at", (tid,)).fetchall()


def recent_price_drops(limit=50):
    """最近降价的车源：取每台车最近两次价格记录，前次高于后次视为降价"""
    with get_conn() as conn:
        return conn.execute("""
            SELECT v.*, h.old_price, h.old_captured_at,
                   ROUND(h.old_price - v.price, 1) AS drop_amount,
                   ROUND((h.old_price - v.price) * 100.0 / h.old_price, 1) AS drop_pct
            FROM (
                SELECT tid,
                       MAX(CASE WHEN rn = 2 THEN price END) AS old_price,
                       MAX(CASE WHEN rn = 2 THEN captured_at END) AS old_captured_at
                FROM (
                    SELECT tid, price, captured_at,
                           ROW_NUMBER() OVER (
                               PARTITION BY tid ORDER BY captured_at DESC) AS rn
                    FROM price_history WHERE price IS NOT NULL
                )
                WHERE rn <= 2
                GROUP BY tid
                HAVING COUNT(*) = 2
            ) h
            JOIN vehicles v ON v.tid = h.tid
            WHERE v.price IS NOT NULL AND h.old_price > v.price
            ORDER BY drop_pct DESC
            LIMIT ?
        """, (limit,)).fetchall()


def get_vehicles_by_ids(tids):
    if not tids:
        return []
    ph = ",".join("?" * len(tids))
    with get_conn() as conn:
        return conn.execute(
            f"SELECT * FROM vehicles WHERE tid IN ({ph})", tids).fetchall()


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


def log_run(source, ok=True, scanned=0, added=0, upstream_total=None, note=""):
    """记一次爬虫巡检。用它区分"抓取失败"和"源站没新车源"。"""
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO crawl_runs(source,started_at,ok,scanned,added,upstream_total,note) "
            "VALUES (?,?,?,?,?,?,?)",
            (source, _now(), 1 if ok else 0, scanned, added, upstream_total, note))


def recent_runs():
    """各源最近一次巡检记录 {source: {started_at, ok, scanned, added, upstream_total, note}}"""
    with get_conn() as conn:
        rows = conn.execute("""
            SELECT r.* FROM crawl_runs r
            JOIN (SELECT source, MAX(id) mid FROM crawl_runs GROUP BY source) m
              ON m.mid = r.id
        """).fetchall()
    return {r["source"]: dict(r) for r in rows}


def _days_since(day_str, today):
    """day_str（YYYY-MM-DD）距今天数；无法解析返回 None"""
    import datetime as _dt
    if not day_str:
        return None
    try:
        return (today - _dt.date.fromisoformat(day_str[:10])).days
    except ValueError:
        return None


def source_freshness():
    """各数据源的"健康状况"：车源数、最后入库、源站最新挂牌、最近收录、最近巡检。

    用于前端提示"某数据源已停更"，避免用户误以为是本站抓取坏了。

    停更判定分两种口径，用 basis 标明：
      - basis="source"：源站自己给出了发布时间（如 21rv 的 created_at、rv28 的
        帖子发表于时间），判断精确，用 STALE 阈值（由 export 侧按天比对）。
      - basis="local"：源站不给发布时间（如 cn2rv），退而用"本站最近收录到
        新车源的时间"保守推断，阈值更宽（LOCAL_STALE_DAYS），且措辞要软，
        否则会把"小众源本来就没新车"误报成故障。

    返回 {source: {n, last_fetch, last_post, last_new, basis, stale_days, last_run, run_ok}}
    """
    import datetime as _dt
    today = _dt.date.today()
    out = {}
    with get_conn() as conn:
        rows = conn.execute("""
            SELECT source,
                   COUNT(*)                                   AS n,
                   MAX(COALESCE(fetched_at,''))               AS last_fetch,
                   MAX(COALESCE(posted_at,''))                AS last_post,
                   MAX(COALESCE(first_seen,''))               AS last_new
            FROM vehicles GROUP BY source
        """).fetchall()
    runs = recent_runs()
    for r in rows:
        last_post = (r["last_post"] or "")[:10]
        last_new = (r["last_new"] or "")[:10]
        if last_post:
            basis = "source"
            stale = _days_since(last_post, today)
        elif last_new:
            basis = "local"
            gap = _days_since(last_new, today)
            # 未超过宽阈值就不算停更（stale_days 仍如实给出，供页面解释用）
            stale = gap if (gap is not None and gap > LOCAL_STALE_DAYS) else None
        else:
            basis = ""
            stale = None
        out[r["source"]] = {
            "n": r["n"],
            "last_fetch": (r["last_fetch"] or "")[:16],
            "last_post": last_post,
            "last_new": last_new,
            "basis": basis,
            "stale_days": stale,
            "last_run": (runs.get(r["source"]) or {}).get("started_at", "")[:16],
            "run_ok": (runs.get(r["source"]) or {}).get("ok"),
        }
    return out


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
        n_dropped = conn.execute("""
            SELECT COUNT(*) c FROM (
                SELECT tid FROM price_history GROUP BY tid
                HAVING MIN(price) > MAX(price)
            )""").fetchone()["c"]
        n_with_history = conn.execute(
            "SELECT COUNT(DISTINCT tid) c FROM price_history").fetchone()["c"]
        by_source = conn.execute(
            "SELECT source k, COUNT(*) n, ROUND(AVG(price),1) avg_p FROM vehicles "
            "WHERE price IS NOT NULL GROUP BY source ORDER BY n DESC").fetchall()
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
        "n_dropped": n_dropped,
        "n_with_history": n_with_history,
        "by_source": by_source,
    }
