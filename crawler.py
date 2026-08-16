#!/usr/bin/env python3
"""Buzzing.cc 新闻 RSS 爬虫：抓取最新20条，并将标题和摘要翻译成中文"""

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
MEDIA_MAX_NEWS = 20

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
# 新闻处理
# =========================
def build_news_pool(items, source_name="Buzzing.cc", max_news=20):
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
            "summary": it["desc"],
            "summary_cn": "",
            "pub_raw": it["pub"],
            "pub_sort_dt": dt,
        })

    # 按发布时间从新到旧排列
    news_pool.sort(key=lambda x: x["pub_sort_dt"], reverse=True)

    # 只保留最新20条
    return news_pool[:max_news]


# =========================
# 批量翻译
# =========================
def translate_news(news_pool):
    if not news_pool:
        return

    # 标题
    titles = [item["title_en"] for item in news_pool]
    try:
        translated_titles = batch_translate(titles)
    except Exception as e:
        print(f"[翻译] 标题翻译失败: {e}")
        translated_titles = titles

    # 防止翻译服务返回数量不一致
    for i, item in enumerate(news_pool):
        if i < len(translated_titles):
            item["title_cn"] = translated_titles[i]
        else:
            item["title_cn"] = item["title_en"]

    # 摘要
    summaries = [item["summary"] for item in news_pool]

    # 空摘要不送翻译 API，避免浪费请求
    non_empty_indexes = [
        i for i, text in enumerate(summaries) if text.strip()
    ]
    non_empty_summaries = [summaries[i] for i in non_empty_indexes]

    translated_summaries = []
    if non_empty_summaries:
        try:
            translated_summaries = batch_translate(non_empty_summaries)
        except Exception as e:
            print(f"[翻译] 摘要翻译失败: {e}")
            translated_summaries = non_empty_summaries

    translated_map = {}
    for i, text in enumerate(translated_summaries):
        if i < len(non_empty_indexes):
            translated_map[non_empty_indexes[i]] = text

    for i, item in enumerate(news_pool):
        if not item["summary"].strip():
            item["summary_cn"] = ""
        else:
            item["summary_cn"] = translated_map.get(
                i, item["summary"]
            )


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
            media_all_news.extend(build_news_pool(items, source_name, len(items)))
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
