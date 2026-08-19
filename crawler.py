#!/usr/bin/env python3
"""Buzzing.cc 新闻 RSS 爬虫：China 10条 + 五大外媒科技/财经/商业20条；仅翻译标题，不输出摘要"""

import urllib.request
import ssl
import json
import time
import re
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from translator import batch_translate


# =========================
# 基础配置
# =========================
CHINA_RSS_URL = "https://china.buzzing.cc/feed.xml"
CHINA_MAX_NEWS = 10
MEDIA_RSS_SOURCES = [("The Economist", "https://economistnew.buzzing.cc/feed.xml"),("Bloomberg", "https://bbg.buzzing.cc/feed.xml"),("Financial Times", "https://ft.buzzing.cc/feed.xml"),("Wall Street Journal", "https://wsj.buzzing.cc/feed.xml"),("Reuters", "https://reuters.buzzing.cc/feed.xml")]
MEDIA_MAX_NEWS = 20  # 五大外媒过滤后混编，统一取最新20条

ctx = ssl.create_default_context()
ctx.check_hostname = False
ctx.verify_mode = ssl.CERT_NONE

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    )
}


# =========================
# 网络请求
# =========================
def fetch(url: str, timeout=20):
    req = urllib.request.Request(url, headers=HEADERS)
    with urllib.request.urlopen(req, timeout=timeout, context=ctx) as resp:
        return resp.read().decode("utf-8", "replace")


# =========================
# 清理 HTML / XML 文本
# =========================
def clean_text(text: str) -> str:
    if not text:
        return ""

    # 去掉 HTML 标签
    text = re.sub(r"<script[\s\S]*?</script>", " ", text, flags=re.I)
    text = re.sub(r"<style[\s\S]*?</style>", " ", text, flags=re.I)
    text = re.sub(r"<[^>]+>", " ", text)

    # XML/HTML 实体
    from html import unescape
    text = unescape(text)

    # 压缩多余空白
    text = re.sub(r"\s+", " ", text).strip()
    return text


# =========================
# 解析 RSS / Atom
# =========================
def parse_rss(raw_xml: str):
    res = []
    root = ET.fromstring(raw_xml)

    # -------- RSS --------
    for item in root.findall(".//item"):
        title = clean_text(item.findtext("title", ""))
        link = item.findtext("link", "").strip()

        # 优先 description；没有则尝试 content:encoded
        desc = item.findtext("description", "")
        if not desc:
            for child in list(item):
                if child.tag.endswith("encoded"):
                    desc = child.text or ""
                    break
        desc = clean_text(desc)[:2000]

        pub = (
            item.findtext("pubDate", "")
            or item.findtext("published", "")
            or item.findtext("date", "")
        ).strip()

        if title and link:
            res.append({
                "title": title,
                "link": link,
                "desc": desc,
                "pub": pub,
            })

    # -------- Atom --------
    ns = "{http://www.w3.org/2005/Atom}"
    for entry in root.findall(ns + "entry"):
        title = clean_text(entry.findtext(ns + "title", ""))

        link = ""
        # 优先 alternate
        for ln in entry.findall(ns + "link"):
            href = ln.get("href", "")
            rel = ln.get("rel", "alternate")
            if href and rel == "alternate":
                link = href
                break
            if href and not link:
                link = href

        desc = (
            entry.findtext(ns + "summary", "")
            or entry.findtext(ns + "content", "")
        )
        desc = clean_text(desc)[:2000]

        pub = (
            entry.findtext(ns + "published", "")
            or entry.findtext(ns + "updated", "")
            or ""
        ).strip()

        if title and link:
            res.append({
                "title": title,
                "link": link,
                "desc": desc,
                "pub": pub,
            })

    return res


# =========================
# 发布时间解析
# =========================
def parse_pubdate(date_str: str):
    """尽可能兼容 RSS 常见的 RFC822 / ISO8601 时间格式。"""
    if not date_str:
        # 没有时间时放到最后，而不是假定为当前时间
        return datetime.min.replace(tzinfo=timezone.utc)

    date_str = date_str.strip()

    # RFC822，例如：
    # Wed, 12 Aug 2026 10:30:00 GMT
    try:
        dt = parsedate_to_datetime(date_str)
        if dt is not None:
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt.astimezone(timezone.utc)
    except Exception:
        pass

    # ISO8601，例如：
    # 2026-08-12T10:30:00Z
    try:
        iso = date_str.replace("Z", "+00:00")
        dt = datetime.fromisoformat(iso)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except Exception:
        pass

    # 最后兼容原来的格式
    m = re.match(
        r"(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2})",
        date_str
    )
    if m:
        return datetime(*map(int, m.groups()), tzinfo=timezone.utc)

    return datetime.min.replace(tzinfo=timezone.utc)



# =========================
# 第二部分：科技 / 财经 / 商业关键词过滤
# =========================
# Buzzing RSS 提供的标题已经是中文，因此主要使用中文关键词。
# 同时保留少量常见英文缩写/公司名，避免重要新闻因标题混用英文而漏掉。
#
# 说明：
# 1. 关键词只用于第二部分五大外媒。
# 2. China Buzzing 的前10条不经过此过滤。
# 3. 摘要只用于提高关键词召回率，不会写入最终 news.json。
# 4. 采用“宽进”策略：科技、财经、商业任一类别命中即可保留。

TECH_KEYWORDS = [
    # AI / 软件 / 互联网
    "人工智能", "生成式人工智能", "生成式AI", "大模型", "机器学习",
    "深度学习", "AI", "GPT", "OpenAI", "Anthropic", "Gemini",
    "微软", "谷歌", "Google", "苹果", "Apple", "亚马逊", "Amazon",
    "Meta", "脸书", "英伟达", "Nvidia", "英特尔", "Intel",
    "AMD", "台积电", "TSMC", "博通", "高通", "Arm",
    "软件", "云计算", "云服务", "数据中心", "数据中心",
    "网络安全", "网络攻击", "黑客", "数字化", "互联网",
    "科技", "技术", "科技公司", "科技巨头",

    # 半导体 / 芯片
    "半导体", "芯片", "晶圆", "晶圆厂", "光刻机", "光刻",
    "先进制程", "制程", "封装", "存储芯片", "HBM", "GPU",
    "CPU", "AI芯片", "芯片制造", "芯片出口", "芯片禁令",

    # 机器人 / 自动驾驶
    "机器人", "人形机器人", "工业机器人", "自动驾驶", "无人驾驶",
    "无人机", "自动驾驶汽车", "电动车", "新能源汽车", "新能源车",
    "电池", "动力电池", "储能", "充电桩",

]

FINANCE_KEYWORDS = [
    # 宏观经济
    "经济", "经济增长", "GDP", "国内生产总值", "通胀", "通货膨胀",
    "通缩", "就业", "失业率", "非农", "消费者价格指数", "CPI",
    "生产者价格指数", "PPI", "零售销售", "制造业PMI", "服务业PMI",
    "PMI", "经济数据", "经济衰退", "衰退",

    # 央行 / 利率 / 货币政策
    "美联储", "鲍威尔", "联储", "欧洲央行", "日本央行", "英国央行",
    "中国", "央行", "利率", "降息", "加息", "降准",
    "货币政策", "量化宽松", "缩表", "基准利率",

    # 股票 / 债券 / 市场
    "股市", "股票", "股价", "股市上涨", "股市下跌", "指数",
    "标普500", "标普", "纳斯达克", "道琼斯", "恒生指数",
    "上证指数", "深证成指", "债券", "国债", "美债", "收益率",
    "债券收益率", "信用债", "金融市场", "资本市场", "投资者",
    "基金", "对冲基金", "私募", "资产管理", "投资",

    # 银行 / 金融
    "银行", "银行业", "金融", "金融机构", "金融科技", "信贷",
    "贷款", "抵押贷款", "信用", "融资", "资本", "流动性",

    # 汇率
    "汇率", "美元", "欧元", "日元", "人民币", "港币",
    "外汇", "美元指数",

    # 大宗商品
    "大宗商品", "黄金", "白银", "原油", "石油", "油价",
    "布伦特", "天然气", "铜", "铁矿石", "煤炭", "商品市场",

]

BUSINESS_KEYWORDS = [
    # 企业 / 公司 / 管理层
    "营收", "收入", "利润",
    "净利润", "营业收入", "财报", "业绩", "季度业绩", "盈利",
    "亏损", "销售", "订单", "CEO", "CFO", "首席执行官",
    "首席财务官", "董事会", "管理层", "高管","突发", "独有",
    
]

# 明显非目标领域的噪声关键词。
# 只在新闻没有命中任何目标关键词时发挥作用，因此不会误删
# 同时涉及商业/科技/财经的重大体育或娱乐产业新闻。
NON_TARGET_KEYWORDS = [
    "足球", "英超", "世界杯", "欧冠", "网球", "篮球", "NBA",
    "奥运会", "金牌", "比赛", "球员", "球队",
    "电影", "电视剧", "明星", "演员", "歌手", "音乐", "演唱会",
    "颁奖礼", "奥斯卡", "格莱美",
]

def is_tech_finance_business(title: str, desc: str = "") -> bool:
    """
    判断五大外媒新闻是否属于科技、财经或商业。
    使用标题 + RSS摘要做召回，但摘要绝不输出到网页。
    """
    text = f"{title} {desc}".lower()

    if any(k.lower() in text for k in TECH_KEYWORDS):
        return True

    if any(k.lower() in text for k in FINANCE_KEYWORDS):
        return True

    if any(k.lower() in text for k in BUSINESS_KEYWORDS):
        return True

    # 对明显的非目标新闻不放行
    if any(k.lower() in text for k in NON_TARGET_KEYWORDS):
        return False

    return False

def filter_media_news(items):
    """只对五大外媒进行科技/财经/商业过滤。"""
    filtered = []

    for item in items:
        if is_tech_finance_business(
            item.get("title", ""),
            item.get("desc", "")
        ):
            filtered.append(item)

    return filtered

# =========================
# 新闻处理
# =========================
def build_news_pool(items, source_name="Buzzing.cc", max_news=30):
    news_pool = []
    seen = set()

    for it in items:
        title = it["title"].strip()
        link = it["link"].strip()

        # URL 或标题去重
        key = link or title.lower()
        if key in seen:
            continue
        seen.add(key)

        dt = parse_pubdate(it["pub"])

        news_pool.append({
            "source": source_name,
            "title_en": title,
            "title_cn": "",
            "url": link,
                        "pub_raw": it["pub"],
            "pub_sort_dt": dt,
        })

    # 按发布时间从新到旧排列
    news_pool.sort(key=lambda x: x["pub_sort_dt"], reverse=True)

    # 只保留最新30条
    return news_pool[:max_news]


# =========================
# 批量翻译
# =========================
def translate_news(news_pool):
    if not news_pool:
        return
    titles = [item["title_en"] for item in news_pool]
    try:
        translated_titles = batch_translate(titles)
    except Exception as e:
        print(f"[翻译] 标题翻译失败: {e}")
        translated_titles = titles
    for i, item in enumerate(news_pool):
        item["title_cn"] = translated_titles[i] if i < len(translated_titles) else item["title_en"]


# =========================
# 主程序
# =========================
def main():
    print(f"[RSS-中国] 正在抓取: {CHINA_RSS_URL}")
    china_xml = fetch(CHINA_RSS_URL)
    china_items = parse_rss(china_xml)
    china_news = build_news_pool(china_items, "China Buzzing", CHINA_MAX_NEWS)

    media_all_news = []
    for source_name, rss_url in MEDIA_RSS_SOURCES:
        print(f"[RSS-外媒] 正在抓取: {source_name} -> {rss_url}")
        try:
            xml = fetch(rss_url)
            items = parse_rss(xml)

            # 只过滤五大外媒；China Buzzing 不经过任何行业过滤
            filtered_items = filter_media_news(items)

            print(
                f"[RSS-外媒] {source_name}: "
                f"RSS原始 {len(items)} 条 -> "
                f"科技/财经/商业 {len(filtered_items)} 条"
            )

            media_all_news.extend(
                build_news_pool(
                    filtered_items,
                    source_name,
                    len(filtered_items)
                )
            )
        except Exception as e:
            print(f"[RSS-外媒] {source_name} 抓取失败: {e}")
            continue

    media_all_news.sort(key=lambda x: x["pub_sort_dt"], reverse=True)
    media_news = media_all_news[:MEDIA_MAX_NEWS]

    translate_news(china_news)
    translate_news(media_news)

    all_news = china_news + media_news
    for item in all_news:
        dt = item["pub_sort_dt"]
        if dt == datetime.min.replace(tzinfo=timezone.utc):
            item["pub_sort_dt"] = ""
        else:
            item["pub_sort_dt"] = dt.astimezone(timezone(timedelta(hours=8))).strftime("%Y-%m-%d %H:%M:%S")

    source_stat = {"China Buzzing": len(china_news)}
    for source_name, _ in MEDIA_RSS_SOURCES:
        source_stat[source_name] = sum(1 for x in media_news if x["source"] == source_name)

    output = {
        "update_cst": datetime.now(timezone(timedelta(hours=8))).strftime("%Y-%m-%d %H:%M:%S 北京时间"),
        "total_count": len(all_news),
        "source_stat": source_stat,
        "china_news": china_news,
        "media_news": media_news,
        "news": all_news
    }
    with open("news.json", "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)
    print(f"采集完成：前半部 {len(china_news)} 条 + 后半部 {len(media_news)} 条，共 {len(all_news)} 条")

if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        err_data = {"error": str(e)}
        with open("news.json", "w", encoding="utf-8") as f:
            json.dump(err_data, f, ensure_ascii=False, indent=2)
        print(f"[错误] {e}")
