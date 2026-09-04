# -*- coding: utf-8 -*-
"""房车猫二手房车爬虫（礼貌爬取：限速+重试）

用法:
    python crawl.py --pages 10          # 抓取前10页列表 + 所有详情
    python crawl.py --pages 10 --no-details   # 只抓列表页摘要
    python crawl.py --details-only      # 只补抓未抓详情的记录

反爬说明:
    目标站首次请求返回403并种下cookie，携带cookie立即重试同一地址即放行(200)。
"""
import argparse
import json
import random
import re
import time
from datetime import datetime
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

import database

LIST_BASE = ("https://www.rv28.com/forum.php?mod=forumdisplay&fid=49"
             "&filter=sortid&sortid=1&searchsort=1&ortid=1")
DETAIL_BASE = "https://used.rv28.com/"
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/127.0.0.0 Safari/537.36")


def make_session():
    s = requests.Session()
    s.headers.update({
        "User-Agent": UA,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "zh-CN,zh;q=0.9",
    })
    return s


def fetch(session, url, referer=None):
    """两段式请求：403 时等待后用已种 cookie 重试"""
    for attempt in range(3):
        try:
            r = session.get(url, headers={"Referer": referer or url}, timeout=30)
        except requests.RequestException as e:
            print(f"   ! 请求异常({e.__class__.__name__})，3秒后重试")
            time.sleep(3)
            continue
        if r.status_code == 403:
            time.sleep(random.uniform(1.2, 2.2))
            continue
        if r.status_code != 200:
            print(f"   ! HTTP {r.status_code}: {url}")
            return None
        r.encoding = "utf-8"
        return r
    print(f"   ! 连续失败: {url}")
    return None


def polite_sleep(lo=1.2, hi=2.2):
    time.sleep(random.uniform(lo, hi))


# ---------------- 数值解析 ----------------

def parse_mileage(text):
    if not text:
        return None
    m = re.search(r"([\d.]+)\s*万", text)
    if m:
        return int(float(m.group(1)) * 10000)
    m = re.search(r"([\d.]+)\s*公里", text)
    if m:
        return int(float(m.group(1)))
    return None


def parse_year(text):
    m = re.search(r"(20\d{2})", text or "")
    return int(m.group(1)) if m else None


def to_float(s):
    m = re.search(r"[\d.]+", (s or "").replace(",", ""))
    return float(m.group()) if m else None


def parse_price(s):
    """挂牌价规范化为万元：带'万'直接取值；纯数字且>=1000视为元；
    结果超过500万视为异常输入返回 None"""
    t = (s or "").replace(",", "")
    m = re.search(r"[\d.]+", t)
    if not m:
        return None
    v = float(m.group())
    if "万" not in t and v >= 1000:
        v /= 10000
    return v if 0 < v <= 500 else None


# ---------------- 列表页 ----------------

def parse_list_page(html, page_url):
    """解析列表页，返回车源摘要列表"""
    soup = BeautifulSoup(html, "html.parser")
    items = []
    for li in soup.select("li.comiis_flxx_li"):
        a = li.find("a", class_="flxx_li_a")
        if not a or not a.get("href"):
            continue
        m = re.search(r"thread-(\d+)-1", a["href"])
        if not m:
            continue
        tid = m.group(1)
        url = urljoin(DETAIL_BASE, a["href"])
        h2 = a.find("h2")
        title = h2.get_text(strip=True) if h2 else ""
        # 摘要行: "1.8万公里 / 2024-10-27/ 成都"
        mid = a.find("span", class_="f_d")
        mileage_text = reg_date = city = ""
        if mid:
            parts = [p.strip() for p in mid.get_text(strip=True).split("/")]
            if len(parts) >= 1:
                mileage_text = parts[0]
            if len(parts) >= 2:
                reg_date = parts[1]
            if len(parts) >= 3:
                city = parts[2]
        # 价格
        price = None
        pspan = a.find("span", class_="f18")
        if pspan:
            price = parse_price(pspan.get_text(strip=True))
        # 标签
        tags = [t.get_text(strip=True) for t in a.find_all("span", class_="car_tag")]
        # 图片
        img = ""
        img_tag = a.find("img", attrs={"comiis_loadimages": True})
        if img_tag:
            img = urljoin(page_url, img_tag["comiis_loadimages"])
        items.append({
            "tid": tid, "url": url, "title": title, "price": price,
            "mileage_text": mileage_text, "reg_date": reg_date,
            "city": city, "tags": "、".join(tags), "image_url": img,
        })
    return items


def crawl_list(session, max_pages):
    all_items, seen = [], set()
    for page in range(1, max_pages + 1):
        url = LIST_BASE if page == 1 else f"{LIST_BASE}&page={page}"
        print(f"[列表 {page}/{max_pages}] {url}")
        r = fetch(session, url, referer="https://used.rv28.com/")
        if r is None:
            print("   跳过本页")
            polite_sleep(2, 4)
            continue
        items = parse_list_page(r.text, url)
        n_new = 0
        for it in items:
            if it["tid"] not in seen:
                seen.add(it["tid"])
                all_items.append(it)
                n_new += 1
        print(f"   本页 {len(items)} 条，新增 {n_new} 条，累计 {len(all_items)}")
        if not items:
            print("   空页，提前结束")
            break
        polite_sleep()
    return all_items


# ---------------- 详情页 ----------------

def parse_specs(text):
    """解析正文中的【字段】：值 结构"""
    specs = {}
    for key, val in re.findall(r"【([^】]{1,20})】[:：\s]*([^【]*)", text, re.S):
        specs[key.strip()] = val.strip()
    return specs


def parse_detail(html):
    soup = BeautifulSoup(html, "html.parser")
    rec = {"specs": {}}

    # 挂牌价
    p = soup.find("span", class_=re.compile(r"number-medium"))
    if p:
        rec["price"] = parse_price(p.get_text(strip=True))

    # 新车含税价（可能为空）
    m = re.search(r"新车含税价[:：]\s*([\d.]+)\s*万", soup.get_text())
    if m:
        rec["new_price"] = float(m.group(1))

    # 参数组：行驶里程 / 排放 / 上牌时间
    for div in soup.find_all("div", class_=re.compile(r"^param_")):
        quota = div.find("p", class_=re.compile(r"quota"))
        name = div.find("p", class_=re.compile(r"param-name"))
        if not (quota and name):
            continue
        label = name.get_text(strip=True)
        val = quota.get_text(strip=True)
        if label == "行驶里程":
            rec["mileage_text"] = val
        elif label == "排放":
            rec["emission"] = val
        elif label == "上牌时间":
            rec["reg_date"] = val

    # 标签组：过户次数 / 使用性质 / 车辆所在地 / 底盘 / 品牌 / 类型
    for sp in soup.find_all("span", class_=re.compile(r"label_")):
        label = sp.get_text(strip=True)
        nxt = sp.find_next_sibling()
        val = nxt.get_text(strip=True) if nxt else ""
        if label == "过户次数":
            rec["transfer_count"] = val
        elif label == "使用性质":
            rec["usage_type"] = val
        elif label == "车辆所在地":
            rec["location"] = val.replace("»", " ").strip()
        elif label == "底盘":
            parts = [x.strip() for x in val.split("»")]
            rec["chassis_brand"] = parts[0]
            if len(parts) > 1:
                rec["chassis_model"] = parts[1]
        elif label == "品牌":
            rec["brand"] = val
        elif label == "类型":
            rec["rv_type"] = val

    # 正文描述与配置
    td = soup.find("td", class_="j_conteir")
    if td:
        text = td.get_text("\n", strip=True)
        rec["description"] = text
        rec["specs"] = parse_specs(text)
        # 正文兜底：上牌时间、排放标准
        if not rec.get("reg_date") and rec["specs"].get("上牌时间"):
            rec["reg_date"] = rec["specs"]["上牌时间"]
        if not rec.get("emission") and rec["specs"].get("排放标准"):
            rec["emission"] = rec["specs"]["排放标准"]

    return rec


def crawl_details(session, items, force=False):
    ok = fail = skipped = 0
    for i, it in enumerate(items, 1):
        if not force and database.detail_exists(it["tid"]):
            skipped += 1
            continue
        print(f"[详情 {i}/{len(items)}] {it['title'][:40]}")
        r = fetch(session, it["url"], referer=LIST_BASE)
        if r is None:
            fail += 1
            polite_sleep(2, 4)
            continue
        d = parse_detail(r.text)

        mileage_text = d.get("mileage_text") or it["mileage_text"]
        reg_date = d.get("reg_date") or it["reg_date"]
        rec = {
            "tid": it["tid"], "source": "rv28", "url": it["url"],
            "title": d.get("title") or it["title"],
            "price": d.get("price") or it["price"],
            "new_price": d.get("new_price"),
            "mileage_km": parse_mileage(mileage_text),
            "mileage_text": mileage_text,
            "reg_date": reg_date,
            "reg_year": parse_year(reg_date),
            "emission": d.get("emission", ""),
            "transfer_count": d.get("transfer_count", ""),
            "usage_type": d.get("usage_type", ""),
            "location": d.get("location") or it["city"],
            "chassis_brand": d.get("chassis_brand", ""),
            "chassis_model": d.get("chassis_model", ""),
            "brand": d.get("brand", ""),
            "rv_type": d.get("rv_type", ""),
            "tags": it["tags"],
            "image_url": it["image_url"],
            "specs_json": json.dumps(d["specs"], ensure_ascii=False),
            "description": d.get("description", ""),
            "fetched_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        }
        database.upsert_vehicle(rec)
        ok += 1
        polite_sleep()
    print(f"详情完成: 新增/更新 {ok}，跳过已存在 {skipped}，失败 {fail}")


# ---------------- 主流程 ----------------

def main():
    ap = argparse.ArgumentParser(description="房车猫二手房车爬虫")
    ap.add_argument("--pages", type=int, default=10, help="抓取列表页数（默认10）")
    ap.add_argument("--no-details", action="store_true", help="只抓列表摘要不抓详情")
    ap.add_argument("--details-only", action="store_true", help="只补抓未抓详情的记录")
    args = ap.parse_args()

    database.init_db()
    session = make_session()
    # 预热两个子域
    fetch(session, "https://used.rv28.com/")
    polite_sleep(1, 2)

    if args.details_only:
        with database.get_conn() as conn:
            rows = conn.execute(
                "SELECT tid,url,title,mileage_text,reg_date,location,tags,image_url,price "
                "FROM vehicles").fetchall()
        items = [{
            "tid": r["tid"], "url": r["url"], "title": r["title"],
            "mileage_text": r["mileage_text"] or "", "reg_date": r["reg_date"] or "",
            "city": r["location"] or "", "tags": r["tags"] or "",
            "image_url": r["image_url"] or "", "price": r["price"],
        } for r in rows]
        crawl_details(session, items)
    else:
        items = crawl_list(session, args.pages)
        print(f"\n列表共采集 {len(items)} 条")
        # 列表摘要先入库（保证即使详情失败也有基础数据）
        n_new = n_upd = 0
        for it in items:
            if not database.vehicle_exists(it["tid"]):
                database.upsert_vehicle({
                    "tid": it["tid"], "source": "rv28", "url": it["url"],
                    "title": it["title"], "price": it["price"],
                    "mileage_text": it["mileage_text"], "reg_date": it["reg_date"],
                    "reg_year": parse_year(it["reg_date"]),
                    "mileage_km": parse_mileage(it["mileage_text"]),
                    "location": it["city"], "tags": it["tags"],
                    "image_url": it["image_url"],
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
            crawl_details(session, items)
            print(f"最终库内共 {database.count_vehicles()} 条")


if __name__ == "__main__":
    main()
