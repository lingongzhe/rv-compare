# -*- coding: utf-8 -*-
"""舒旅二手房车网（cn2rv.com）爬虫

数据源：舒旅二手房车网（专注二手房车的直卖平台）
   列表  GET https://www.cn2rv.com/buycar/list 及 /buycar/list-ob{n} 各品牌分类页
   详情  GET https://www.cn2rv.com/cars/{id}
无需反爬绕行即可访问；详情字段以上牌时间/公里数/所在地/排放标准等成对标签呈现。
与房车猫/21世纪房车共库存储，tid 加 "cn2rv_" 前缀命名空间化避免撞主键，
底盘型号复用 crawl_21rv 的规范化规则，可与 21rv/房车猫 同款聚合到同一比价组。

用法:
    python crawl_cn2rv.py --no-details   # 只抓列表摘要（快速收集车源ID+标题）
    python crawl_cn2rv.py                # 列表 + 全部详情（230台左右，礼貌限速）
    python crawl_cn2rv.py --details-only # 只补抓未抓详情的记录
    python crawl_cn2rv.py --force        # 强制重抓已存在详情
"""
import argparse
import json
import random
import re
import time
from datetime import datetime

import requests

import database
from crawl_21rv import norm_chassis, _marge_brand, infer_rv_type

BASE = "https://www.cn2rv.com"
LIST_URL = BASE + "/buycar/list"
DETAIL_URL = BASE + "/cars/{cid}"
SOURCE = "cn2rv"
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/127.0.0.0 Safari/537.36")


def make_session():
    s = requests.Session()
    s.headers.update({
        "User-Agent": UA,
        "Accept-Language": "zh-CN,zh;q=0.9",
        "Referer": BASE + "/buycar/list",
    })
    return s


def polite_sleep(lo=0.6, hi=1.4):
    time.sleep(random.uniform(lo, hi))


def get_html(session, url):
    for attempt in range(3):
        try:
            r = session.get(url, timeout=30)
        except requests.RequestException as e:
            print(f"   ! 请求异常({e.__class__.__name__})，重试")
            polite_sleep(1, 2)
            continue
        if r.status_code != 200:
            print(f"   ! HTTP {r.status_code}: {url}")
            polite_sleep(1, 2)
            continue
        r.encoding = "utf-8"
        return r.text
    return None


# ---------------- 列表 ----------------

def _link_items(html):
    """从一页列表HTML里提取 (car_id, title)"""
    out = {}
    for mm in re.finditer(r'href="/cars/(\d+)"[^>]*>(.*?)</a>', html, re.S):
        cid = mm.group(1)
        title = re.sub(r"<[^>]+>", "", mm.group(2)).strip()
        title = re.sub(r"\s+", " ", title)
        if title:
            out[cid] = title
    return out


def crawl_list(session):
    """收集主列表 + 各分类页，去重得到 {cid: title}"""
    seen = {}
    # 主列表 + 各上装品牌分类页（ob id 1..160 覆盖全部品类）
    urls = [LIST_URL] + [f"{BASE}/buycar/list-ob{n}" for n in range(1, 161)]
    for i, u in enumerate(urls, 1):
        html = get_html(session, u)
        if html is None:
            polite_sleep(1, 2)
            continue
        items = _link_items(html)
        added = 0
        for cid, title in items.items():
            if cid not in seen:
                seen[cid] = title
                added += 1
        print(f"[列表 {i}/{len(urls)}] {u.split('/buycar')[-1]} 车源{len(items)} 新增{added} 累计{len(seen)}")
        polite_sleep()
    return seen


# ---------------- 详情 ----------------

def _pairs(html):
    """值/标签成对字段 {label: value}"""
    return {lab: re.sub(r"<[^>]+>", "", val).strip()
            for val, lab in re.findall(
                r'<dt class="jx_timeNum">(.*?)</dt>\s*<dd class="jx_timeCh">([^<]+)</dd>',
                html, re.S)}


def _parse_price(html):
    m = re.search(r"[¥￥]\s*(\d+(?:\.\d+)?)\s*万元", html)
    if not m:
        return None
    v = float(m.group(1))
    # 个别页面以"元"为单位（如 ¥68000），需换算成万元
    return v / 10000 if v > 500 else v


def _mileage_from_text(s):
    """'7.40万' / '12345' → (km, text)"""
    if not s:
        return None, ""
    m = re.match(r"([\d.]+)\s*(万|km|公里)?", str(s).strip(), re.I)
    if not m:
        return None, ""
    num = float(m.group(1))
    unit = m.group(2)
    if unit == "万" or (unit is None and num < 1000):
        return int(round(num * 10000)), f"{num:.2f}万公里"
    return int(round(num)), f"{int(num)}公里"


def _parse_mileage(pairs, html):
    # 优先用成对字段里的"公里数/表显里程"，其次是车况描述文本
    raw = pairs.get("公里数") or pairs.get("表显里程") or ""
    if raw:
        return _mileage_from_text(raw)
    m = re.search(r"(?:表显里程|公里数)[：: ]*([\d.]+)\s*(万)?\s*公?里?", html)
    if not m:
        return None, ""
    num = float(m.group(1))
    if m.group(2) == "万":
        return int(round(num * 10000)), f"{num:.2f}万公里"
    return int(round(num)), f"{int(num)}公里"


def parse_detail(html, cid, fallback_title=""):
    pairs = _pairs(html)
    title = fallback_title or ""
    m = re.search(r"<title>(.*?)</title>", html, re.S)
    if m:
        title = m.group(1).strip().split(" - ")[0].strip() or title

    mileage_km, mileage_text = _parse_mileage(pairs, html)
    # 上牌时间兜底：优先成对字段，其次车况描述里的"上牌日期"
    reg_date = pairs.get("上牌时间", "")
    if not reg_date:
        rm = re.search(r"上牌日?期?[：: ]*(\d{4}年\d{1,2}月)", html)
        reg_date = rm.group(1) if rm else ""
    ry = int(reg_date[:4]) if re.match(r"\d{4}", reg_date) else None

    # 排放标准只取 国x，去掉页脚免责声明等干扰文本
    em = pairs.get("排放标准", "")
    em_m = re.search(r"(国[一二三四五六])", em)
    emission = em_m.group(1) if em_m else em

    c_brand, c_model = norm_chassis(title)
    brand = _marge_brand(title.split(" ", 1)[0] if title else "")

    m_desc = re.search(r'<p class="jx_ex">(.*?)</p>', html, re.S)
    description = re.sub(r"<[^>]+>", "", m_desc.group(1)).strip() if m_desc else ""

    return {
        "tid": f"{SOURCE}_{cid}",
        "source": SOURCE,
        "url": DETAIL_URL.format(cid=cid),
        "title": title,
        "price": _parse_price(html),
        "new_price": None,
        "mileage_km": mileage_km,
        "mileage_text": mileage_text,
        "reg_date": reg_date,
        "reg_year": ry,
        "emission": emission,
        "transfer_count": "",
        "usage_type": "",
        "location": pairs.get("所在地", ""),
        "chassis_brand": c_brand,
        "chassis_model": c_model,
        "brand": brand,
        "rv_type": infer_rv_type(title),
        "tags": "",
        "image_url": "",
        "specs_json": json.dumps(pairs, ensure_ascii=False),
        "description": description,
        "fetched_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }


def crawl_details(session, items, force=False):
    """items: {cid: title}"""
    ok = fail = skipped = 0
    lst = list(items.items())
    for i, (cid, title) in enumerate(lst, 1):
        tid = f"{SOURCE}_{cid}"
        if not force and database.detail_exists(tid):
            skipped += 1
            continue
        print(f"[详情 {i}/{len(lst)}] {title[:40]}")
        html = get_html(session, DETAIL_URL.format(cid=cid))
        if html is None:
            fail += 1
            polite_sleep(1, 2)
            continue
        d = parse_detail(html, cid, fallback_title=title)
        if d["price"] is None and d["title"]:
            pass
        database.upsert_vehicle(d)
        ok += 1
        polite_sleep()
    print(f"详情完成: 新增/更新 {ok}，跳过已存在 {skipped}，失败 {fail}")


# ---------------- 主流程 ----------------

def main():
    ap = argparse.ArgumentParser(description="舒旅二手房车网（cn2rv.com）爬虫")
    ap.add_argument("--no-details", action="store_true", help="只抓列表摘要")
    ap.add_argument("--details-only", action="store_true", help="只补抓未抓详情的记录")
    ap.add_argument("--force", action="store_true", help="强制重抓已存在详情")
    args = ap.parse_args()

    database.init_db()
    session = make_session()

    if args.details_only:
        with database.get_conn() as conn:
            rows = conn.execute(
                "SELECT tid,title,description FROM vehicles WHERE source=?",
                (SOURCE,)).fetchall()
        items = {r["tid"].split("_", 1)[1]: r["title"] or "" for r in rows}
        crawl_details(session, items, force=args.force)
        return

    seen = crawl_list(session)
    print(f"\n列表共采集 {len(seen)} 条")
    # 先写列表摘要（缺详情时列表页也能展示），再抓详情
    for cid, title in seen.items():
        if not database.vehicle_exists(f"{SOURCE}_{cid}"):
            database.upsert_vehicle({
                "tid": f"{SOURCE}_{cid}", "source": SOURCE,
                "url": DETAIL_URL.format(cid=cid), "title": title,
                "price": None, "mileage_text": "", "reg_year": None,
                "mileage_km": None, "location": "",
                "emission": "", "description": "",
                "chassis_brand": norm_chassis(title)[0],
                "chassis_model": norm_chassis(title)[1],
                "brand": _marge_brand(title.split(" ", 1)[0] if title else ""),
                "rv_type": infer_rv_type(title),
                "fetched_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            })
    print(f"当前库内共 {database.count_vehicles()} 条")
    if not args.no_details:
        crawl_details(session, seen, force=args.force)
        print(f"最终库内共 {database.count_vehicles()} 条")


if __name__ == "__main__":
    main()