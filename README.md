# 房车比价通（RV-Compare）

二手房车信息聚合与比价工具。自动爬取多个主流二手房车网站的挂牌车源（3000+ 台），
整合入库后提供：车源列表浏览、多维度筛选、**跨源同款**（同底盘型号）比价、
同年限行情对比、**价格历史与降价监测**、收藏、多车横向对比、JSON API 与 CSV 导出。

线上版（GitHub Pages 静态站，手机/电脑可访问）：`docs/` 目录随每次刷新自动导出部署。

## 数据源

| 来源 | 标识 | 说明 |
|---|---|---|
| 房车猫（rv28.com） | `rv28` | 二手房车论坛专区，HTML 列表+详情页 |
| 21世纪房车（wanfangche API） | `21rv` | 经其二手板块 JSON API（约 3200 台在售），底盘型号从标题/配置自动规范化，与房车猫同款可聚到同一比价组 |
| 舒旅二手房车网（cn2rv.com） | `cn2rv` | 专注二手房车的直卖平台（约 230 台在售），列表页+详情页均为静态 HTML，上牌时间/公里数/所在地/排放/价格以成对标签呈现，可直接解析 |

`vehicles.source` 字段区分来源；列表页可按来源筛选，卡片/详情/比价页标注来源。

## 环境要求

- Windows 10/11，Python 3.10+
- 依赖：`requests`、`beautifulsoup4`、`flask`
  ```
  pip install requests beautifulsoup4 flask
  ```

## 快速开始

```
# 1. 抓取房车猫（前10页列表+详情，约160条，礼貌限速）
python crawl.py --pages 10

# 2. 抓取21世纪房车（前15页=180台；API 限速宽松）
python crawl_21rv.py --pages 15

# 3. 抓取舒旅二手房车网（全量约230台）
python crawl_cn2rv.py

# 4. 启动比价应用
python app.py
# 浏览器打开 http://127.0.0.1:5000
```

## 功能

| 页面 | 说明 |
|---|---|
| 车源列表 | 关键词/类型/底盘品牌/年份/价格区间/来源筛选，排序分页；卡片含图片、降价标签、收藏（🤍，本地保存）、多选对比勾选 |
| 车源详情 | 完整车况：上牌时间、里程、排放、过户、底盘、水电配置清单；**价格记录图**（历史挂牌价柱状）；同款快速比价入口 |
| 多车对比 | 列表页勾选 2~4 台，横向逐项并排对比，自动标注最低价 |
| 降价榜 | 对比每台车最近两次抓取的挂牌价，降幅排序；列表卡片同步显示"较首录降价" |
| 同款比价 | 按底盘型号聚合（如依维柯欧盛、大通V90），展示最低/均价/最高，可再按上装品牌、年份筛选，横向对比同年限车源 |
| 行情总览 | 价格区间分布、类型/底盘品牌分布、各数据源统计、各年份上牌均价、品牌TOP20、降价车源数 |
| 深色模式 | 顶栏 🌙 切换，偏好本地记忆 |

JSON API（与页面同源，可直接调用）：

```
GET /api/vehicles?kw=V90&pmin=10&pmax=30&page=1&per_page=24
GET /api/vehicle/<tid>          # 含 specs 与 price_history
GET /api/stats                  # 行情总览统计
GET /api/drops                  # 降价榜
GET /export.csv?kw=&pmin=...    # 按筛选条件导出 CSV（Excel 友好）
```

## 爬虫命令

```
# 房车猫（HTML，礼貌限速，请勿调高频率）
python crawl.py --pages 10          # 抓前10页列表+详情（推荐）
python crawl.py --pages 30          # 抓前30页
python crawl.py --pages 5 --no-details   # 只抓列表摘要（快）
python crawl.py --details-only      # 为已入库但缺详情的记录补抓详情

# 21世纪房车（JSON API，每页12条，限速较宽松）
python crawl_21rv.py --pages 15     # 抓前15页=180台 列表+详情
python crawl_21rv.py --pages 50 --no-details   # 只抓列表摘要（快）
python crawl_21rv.py --details-only          # 为缺详情的记录补抓
python crawl_21rv.py --details-only --force  # 强制重新抓取已有详情（改版后使用）

# 舒旅二手房车网（静态 HTML，全量约230台）
python crawl_cn2rv.py               # 列表+全部详情
python crawl_cn2rv.py --no-details  # 只抓列表摘要
python crawl_cn2rv.py --details-only   # 补抓缺详情的记录
python crawl_cn2rv.py --force          # 强制重抓已有详情
```

- 反爬策略：房车猫首次请求返回403并种cookie，爬虫自动携带cookie立即重试同一地址放行；21世纪房车走公开 API。
- 限速：房车猫每页/每详情随机等待1.2~2.2秒，21世纪房车 0.4~1.0 秒，请勿调高频率。
- 去重：`tid` 为主键，跨源以 `21rv_`/`rv28_` 前缀命名空间化避免撞号，重复运行只增量更新；
  已入库车源在列表刷新时会同步更新挂牌价，价格变化写入 `price_history` 表（降价榜数据来源）。
- 数据文件：`data/rv.db`（SQLite，可直接用工具查看）。

## 项目结构

```
rv-crawler/
├── crawl.py          # 房车猫爬虫（列表页+详情页解析入库）
├── crawl_21rv.py     # 21世纪房车爬虫（JSON API 列表+详情入库，含底盘型号规范化）
├── crawl_cn2rv.py    # 舒旅二手房车网爬虫（静态 HTML 列表+详情入库，复用底盘规范化）
├── database.py       # SQLite 存储与查询层（含价格历史）
├── export_data.py    # 导出 docs/data.js（GitHub Pages 静态站数据）
├── deploy/refresh_deploy.py  # 定时刷新：爬虫 -> 导出 -> git 提交推送
├── app.py            # Flask Web 应用（页面 + JSON API + CSV 导出）
├── templates/        # 页面模板
│   ├── base.html     # 公共布局（深色模式/收藏/对比栏/返回顶部）
│   ├── index.html    # 车源列表（筛选/收藏/多选对比/降价标签）
│   ├── detail.html   # 车源详情（配置清单/价格记录图）
│   ├── side.html     # 多车横向对比（2~4台并排）
│   ├── drops.html    # 降价榜
│   ├── compare.html  # 同款比价（跨源聚合，按来源标注）
│   └── stats.html    # 行情总览
├── docs/             # GitHub Pages 静态站（index.html + data.js）
└── data/rv.db        # 数据库（运行后生成）
```

## 后续扩展方向

1. **继续扩源**：房车猫、21世纪房车已接入；二手房车网（cn2rv.com）、58同城、瓜子/闲鱼等站点结构不同，
   按同样"列表+详情"模式新增解析器即可，`vehicles.source` 字段已预留来源标识。
2. **宿营车专区**：房车猫列表页已有"露营车"分类筛选项（leixing=9）；21世纪房车可经品牌/标题含"宿营/露营"
   车源单独提取，后续可建独立入口与筛选，覆盖宿营车品类。
3. **同款匹配增强**：当前按底盘型号聚合并做别名规范化（欧胜/依维柯欧胜→欧盛），后续可引入车辆名称相似度
   （如"上汽大通V80"与"大通V80"）做更细的跨源同款识别。
4. **定时增量更新**：用系统计划任务每天跑一次 `python crawl.py --pages 2` 与
   `python crawl_21rv.py --pages 5`，增量入库。
5. **打包为APP**：本原型为 Web 应用，后续可用 Capacitor/uni-app 封装为移动端，
   或部署到服务器后做小程序前端。

## 合规提示

- 爬取仅用于个人比价研究，请勿将抓取数据用于商业用途或二次分发。
- 目标网站内容版权归原平台及车商所有；挂牌价仅供参考，以实际沟通为准。
- 请遵守目标网站 robots 协议与服务条款，控制爬取频率。
