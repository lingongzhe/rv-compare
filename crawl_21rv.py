# -*- coding: utf-8 -*-
"""21世纪房车二手房车爬虫（经 public JSON API，礼貌限速）

数据源：21世纪房车二手房车板块
   列表  GET https://api.wanfangche.com/community/public/fc/rvSecond/list?page=N&size=12
   详情  GET https://api.wanfangche.com/community/public/fc/rvSecond/detail?id=X
与房车猫共库存储，tid 加 "21rv_" 前缀命名空间化，避免与房车猫帖子 id 撞主键。

用法:
    python crawl_21rv.py --pages 10          # 抓前10页列表 + 所有详情
    python crawl_21rv.py --pages 10 --no-details   # 只抓列表摘要
    python crawl_21rv.py --details-only      # 只补抓未抓详情的记录
"""
import argparse
import json
import os
import random
import re
import time
from datetime import datetime
from urllib.parse import urlencode

import requests

import database

# 限速覆盖（秒）：默认 0.4~1.0；设置 RV_CRAWL_DELAY=n 时改为 n~2n（自己用可调快，公开环境勿滥用）
RV_DELAY = float(os.environ.get("RV_CRAWL_DELAY", "0") or 0)

API_BASE = "https://api.wanfangche.com"
LIST_URL = API_BASE + "/community/public/fc/rvSecond/list"
DETAIL_URL = API_BASE + "/community/public/fc/rvSecond/detail"
PAGE_SIZE = 12
SOURCE = "21rv"
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/127.0.0.0 Safari/537.36")


def make_session():
    s = requests.Session()
    s.headers.update({
        "User-Agent": UA,
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "zh-CN,zh;q=0.9",
        "Referer": "https://www.21rv.com/used/filter/",
    })
    return s


def get_json(session, url, params):
    """请求 JSON 接口，失败时短暂重试"""
    for attempt in range(3):
        try:
            r = session.get(url, params=params, timeout=30)
        except requests.RequestException as e:
            print(f"   ! 请求异常({e.__class__.__name__})，重试")
            polite_sleep(1, 2)
            continue
        if r.status_code != 200:
            print(f"   ! HTTP {r.status_code}: {url}?{urlencode(params)[:80]}")
            polite_sleep(1, 2)
            continue
        try:
            return r.json()
        except ValueError:
            print("   ! 非法 JSON，重试")
            polite_sleep(1, 2)
    return None


def polite_sleep(lo=0.4, hi=1.0):
    if RV_DELAY:
        time.sleep(RV_DELAY + random.uniform(0, RV_DELAY))
    else:
        time.sleep(random.uniform(lo, hi))


# ---------------- 数值/文本解析 ----------------

def clean_price(s):
    """把 '¥11.00' / 11 / '38.8' 解析为万元数值"""
    if s in (None, ""):
        return None
    m = re.search(r"[\d.]+", str(s).replace(",", ""))
    return float(m.group()) if m else None


# 底盘型号规范化：把 21rv 标题/字段里的叫法统一成与房车猫一致的 canonical
CHASSIS_ALIASES = {
    "依维柯欧胜": "欧盛", "欧胜": "欧盛", "依维柯欧盛": "欧盛",
    "rv80": "V80", "rv90": "V90", "rv100": "V100",
    "大通v80": "V80", "大通v90": "V90",
    "福特transit": "全顺", "新全顺": "全顺", "全顺": "全顺",
    "图雅诺": "图雅诺", "福顺": "福顺", "江铃大道": "大道",
}
# 底盘品牌：由型号/标题中出现的品牌词反推
BRAND_TAGS = [
    "依维柯", "大通", "福特", "五十铃", "长城", "比亚迪", "江铃", "解放",
    "东风", "奔驰", "福田", "奔驰斯宾特", "斯宾特", "金杯", "华晨",
    "黄海", "一汽", "庆铃", "长安", "重汽", "汕德卡",
]


def norm_chassis(raw):
    """从 raw 文本提取 (chassis_brand, chassis_model) 规范化"""
    t = (raw or "").strip()
    if not t or t == "未知":
        return "", ""
    low = t.lower()
    for k, v in CHASSIS_ALIASES.items():
        if k in low:
            return _brand_of(low), v
    # 型号 token：排除单字母（B/C/A 是车身类型而非底盘）
    m = re.search(r"([A-Z]{2,6}\d{0,3}(?:x4x4|x4)?|[A-Z][a-zA-Z0-9]{1,7}\d{0,2})", t)
    if m and m.group(1).upper() not in ("A", "B", "C"):
        return _brand_of(low), m.group(1).upper()
    return _brand_of(low), ""


def _brand_of(low):
    for b in BRAND_TAGS:
        if b in low:
            return "上汽大通" if b == "大通" else b
    return ""


def _marge_brand(brand):
    """去掉房车/品牌后缀，与房车猫品牌叫法对齐（法美瑞房车→法美瑞）"""
    if not brand:
        return ""
    return re.sub(r"(房车|汽车)$", "", brand.strip())


def infer_rv_type(title):
    """从标题推断类型，与房车猫 rv_type 分类保持一致"""
    t = title or ""
    if "宿营" in t or "露营" in t:
        return "露营车"
    if "拖挂" in t or "拖拽" in t or "房车挂车" in t:
        return "拖挂A型"
    if "B型" in t or "B类" in t:
        return "自行B型"
    if "A型" in t or "A类" in t:
        return "自行A型"
    # 默认 C 型（多数整体式房车）
    return "自行C型"


def conflist_to_dict(detail):
    """把瞬息配置 conflist([{title,value}]) 转 dict，并识别关键列"""
    specs, maped = {}, {}
    for it in (detail.get("conflist") or []):
        k = (it.get("title") or "").strip()
        v = (it.get("value") or "").strip()
        if k:
            specs[k] = v
            if k == "底盘型号":
                maped["chassis"] = v
            elif k in ("排放", "排放标准"):
                maped["emission"] = v
            elif k == "使用性质":
                maped["usage_type"] = v
    return specs, maped


def describe_specs(describe):
    """从描述【字段】中兜底提取配置"""
    specs = {}
    for key, val in re.findall(r"【([^】]{1,20})】[:：\s]*([^【]*)", describe or "", re.S):
        specs[key.strip()] = val.strip()
    return specs


# ---------------- 列表页 ----------------

def parse_list_item(it):
    plate_year = str(it.get("plate_year") or "")
    return {
        "tid": f"{SOURCE}_{it['id']}",
        "url": f"https://mp.21rv.com/second/{it['id']}",
        "title": it.get("title") or "",
        "price": clean_price(it.get("price")),
        "mileage_text": (it.get("mileage_title") or ""),
        "reg_year": int(plate_year) if plate_year.isdigit() else None,
        "plate_year": plate_year,
        "city": it.get("city") or "",
        "location": it.get("city") or "",
        "poster": it.get("poster") or "",
    }


def crawl_list(session, max_pages):
    all_items, seen = [], set()
    for page in range(1, max_pages + 1):
        print(f"[列表 {page}/{max_pages}] rvSecond/list page={page}")
        j = get_json(session, LIST_URL, {"page": page, "size": PAGE_SIZE})
        if j is None:
            print("   跳过本页")
            polite_sleep(1, 2)
            continue
        data = j.get("data") or {}
        lst = data.get("list") or []
        n_new = 0
        for it in lst:
            pid = f"{SOURCE}_{it['id']}"
            if pid not in seen:
                seen.add(pid)
                all_items.append(parse_list_item(it))
                n_new += 1
        total = data.get("count")
        print(f"   本页 {len(lst)} 条，新增 {n_new} 条，累计 {len(all_items)}"
              + (f"（库内全量约 {total} 条）" if total else ""))
        if not lst:
            print("   空页，提前结束")
            break
        polite_sleep()
    return all_items


# ---------------- 详情页 ----------------

def parse_detail(j):
    d = j.get("data") or {}
    if not d or not d.get("id"):
        return None
    title = d.get("title") or ""
    specs, maped = conflist_to_dict(d)
    ds = describe_specs(d.get("describe"))
    if not specs and ds:
        specs = ds

    # 底盘型号优先取配置项，其次用整条标题提取（品牌+型号）
    chassis_raw = maped.get("chassis") or ""
    c_brand, c_model = norm_chassis(chassis_raw if chassis_raw and chassis_raw != "未知"
                                     else title)

    mileage = d.get("mileage")
    mileage_km = int(mileage) if mileage else None
    if mileage_km:
        mileage_text = (f"{mileage_km/10000:.1f}万公里" if mileage_km >= 10000
                        else f"{mileage_km}公里")
    else:
        mileage_text = ""

    return {
        "tid": f"{SOURCE}_{d['id']}",
        "source": SOURCE,
        "url": f"https://mp.21rv.com/second/{d['id']}",
        "title": title,
        "price": clean_price(d.get("price")),
        "new_price": clean_price(d.get("original_price")),
        "mileage_km": mileage_km,
        "mileage_text": mileage_text,
        "reg_date": d.get("plate_date") or "",
        "reg_year": int(d["plate_year"]) if str(d.get("plate_year") or "").isdigit() else None,
        "emission": maped.get("emission", ""),
        "transfer_count": str(d["transfer_num"]) if d.get("transfer_num") else "",
        "usage_type": maped.get("usage_type", ""),
        "location": d.get("city") or "",
        "chassis_brand": c_brand,
        "chassis_model": c_model,
        "brand": _marge_brand(d.get("brand_name") or ""),
        "rv_type": infer_rv_type(title),
        "tags": "有检测认证" if d.get("is_certificate") else "",
        "image_url": d.get("poster") or "",
        "specs_json": json.dumps(specs, ensure_ascii=False),
        "description": d.get("describe") or "",
        "fetched_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }


def crawl_details(session, items, force=False):
    ok = fail = skipped = 0
    for i, it in enumerate(items, 1):
        if not force and database.detail_exists(it["tid"]):
            skipped += 1
            continue
        print(f"[详情 {i}/{len(items)}] {it['title'][:40]}")
        j = get_json(session, DETAIL_URL, {"id": it["tid"].split("_", 1)[1]})
        if j is None:
            fail += 1
            polite_sleep(1, 2)
            continue
        d = parse_detail(j)
        if d is None:
            fail += 1
            polite_sleep(1, 2)
            continue
        # 列表字段兜底
        for k in ("title", "price", "mileage_km", "mileage_text", "reg_date",
                  "reg_year", "location", "image_url"):
            if not d.get(k) and it.get(k):
                d[k] = it[k]
        database.upsert_vehicle(d)
        ok += 1
        polite_sleep()
    print(f"详情完成: 新增/更新 {ok}，跳过已存在 {skipped}，失败 {fail}")


# ---------------- 主流程 ----------------

def main():
    ap = argparse.ArgumentParser(description="21世纪房车二手房车爬虫")
    ap.add_argument("--pages", type=int, default=10, help="抓取列表页数（默认10，每页12条）")
    ap.add_argument("--no-details", action="store_true", help="只抓列表摘要不抓详情")
    ap.add_argument("--details-only", action="store_true", help="只补抓未抓详情的记录")
    ap.add_argument("--force", action="store_true", help="强制重新抓取已存在详情")
    args = ap.parse_args()

    database.init_db()
    session = make_session()

    if args.details_only:
        with database.get_conn() as conn:
            rows = conn.execute(
                "SELECT tid,url,title,mileage_text,reg_date,location,tags,image_url,price "
                "FROM vehicles WHERE source=?", (SOURCE,)).fetchall()
        items = [{
            "tid": r["tid"], "url": r["url"], "title": r["title"],
            "mileage_text": r["mileage_text"] or "", "reg_date": r["reg_date"] or "",
            "city": r["location"] or "", "tags": r["tags"] or "",
            "image_url": r["image_url"] or "", "price": r["price"],
        } for r in rows]
        crawl_details(session, items, force=args.force)
        return

    items = crawl_list(session, args.pages)
    print(f"\n列表共采集 {len(items)} 条")
    n_new = n_upd = 0
    for it in items:
        if not database.vehicle_exists(it["tid"]):
            database.upsert_vehicle({
                "tid": it["tid"], "source": SOURCE, "url": it["url"],
                "title": it["title"], "price": it["price"],
                "mileage_text": it["mileage_text"],
                "reg_year": int(it["plate_year"]) if it["plate_year"].isdigit() else None,
                "mileage_km": None,
                "location": it["city"], "image_url": it["poster"],
                "fetched_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            })
            n_new += 1
        else:
            # 已入库车源刷新挂牌价等摘要字段（价格变化会记入 price_history）
            database.update_summary({
                "tid": it["tid"], "price": it["price"],
                "mileage_text": it["mileage_text"], "location": it["city"],
            })
            n_upd += 1
    print(f"列表处理完成: 新增 {n_new}，刷新 {n_upd}")
    print(f"当前库内共 {database.count_vehicles()} 条")
    if not args.no_details:
        crawl_details(session, items, force=args.force)
        print(f"最终库内共 {database.count_vehicles()} 条")


if __name__ == "__main__":
    main()