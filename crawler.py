#!/usr/bin/env python3
"""China Foreign News Aggregator v4.0
仅抓取境外媒体涉华新闻，单源最多15条，标题翻译输出news.json
"""
import urllib.request, ssl, json, time, re, xml.etree.ElementTree as ET, sys, html as html_mod
from datetime import datetime, timedelta, timezone
from translator import batch_translate

# SSL关闭校验
ctx = ssl.create_default_context()
ctx.check_hostname = False
ctx.verify_mode = ssl.CERT_NONE
HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120.0.0.0 Safari/537.36"}
MAX_PER_SOURCE = 15  # 每家媒体最多15条（需求修改）
CUTOFF = datetime.now(timezone.utc) - timedelta(days=4)
# 涉华关键词过滤（保留原规则）
CKW = ["china","chinese","beijing","xi jinping","li qiang","wang yi",
       "taiwan","hong kong","xinjiang","tibet","south china sea",
       "belt and road","huawei","tencent","alibaba","tiktok","shein",
       "temu","cpec","renminbi","yuan","pboc","deepseek","baidu",
       "xiaomi","chinese economy","chinese market","chinese official",
       "sino-","brics","shanghai","shenzhen","guangzhou",
       "people's liberation army","chinese military","chinese army",
       "ccp","chinese communist party","communist party of china",
       "pla navy","pla air force","eastern theatre command",
       "south china sea","taiwan strait"]
PLA_RE = re.compile(r"\bpla\b", re.I)

def is_cn(t):
    tl = (t or "").lower()
    for k in CKW:
        if k in tl: return True
    if PLA_RE.search(tl): return True
    return False

def fetch(url, t=20, retries=2):
    last_err = None
    for attempt in range(retries + 1):
        try:
            req = urllib.request.Request(url, headers=HEADERS)
            with urllib.request.urlopen(req, timeout=t, context=ctx) as r:
                return r.read().decode("utf-8","replace")
        except Exception as e:
            last_err = e
            if attempt < retries:
                time.sleep(2)
    fetch_log.append((url[:100], 'ERR:' + repr(last_err)[:200]))
    raise last_err

def extract(html_text, source_hint=""):
    if not html_text: return ""
    h = re.sub(r"<(script|style|nav|footer|header|aside|noscript|iframe|form)[^>]*>.*?</\1>",
               "", html_text, flags=re.DOTALL|re.IGNORECASE)
    patterns = []
    if source_hint == "BBC":
        patterns = [r'<div[^>]*data-component="text-block"[^>]*>(.*?)</div>']
    elif source_hint == "APP":
        patterns = [r'<div[^>]*class="[^"]*entry-content[^"]*"[^>]*>(.*?)</div>']
    elif source_hint == "IRNA":
        patterns = [r'<div[^>]*class="[^"]*(?:body|news-body|item-text|text|content)[^"]*"[^>]*>(.*?)</div>']
    generic = [
        r'<div[^>]*data-component="text-block"[^>]*>(.*?)</div>',
        r'<article[^>]*>(.*?)</article>',
        r'<div[^>]*class="[^"]*(?:article-body|story-body|entry-content|content-body|field-item|news-body|article-text|post-content|content__body|Paywall|article__content|article_body|rich-text|post-body|Article__content)[^"]*"[^>]*>(.*?)</div>',
        r'<body[^>]*>(.*?)</body>',
    ]
    patterns.extend(generic)
    for pat in patterns:
        matches = re.findall(pat, h, re.DOTALL)
        if matches:
            combined = []
            for m in matches:
                b = m
                b = re.sub(r"<br\s*/?>", "\n", b)
                b = re.sub(r"<p[^>]*>", "\n", b)
                b = re.sub(r"<li[^>]*>", "\n- ", b)
                b = re.sub(r"</li>", "", b)
                b = re.sub(r"<h[1-6][^>]*>", "\n", b)
                b = re.sub(r"</h[1-6]>", "\n", b)
                b = re.sub(r"<[^>]+>", " ", b)
                b = html_mod.unescape(b)
                combined.append(b)
            text = "\n".join(combined)
            text = re.sub(r"\n\s*\n", "\n", text)
            text = re.sub(r"[ \t]+", " ", text)
            text = re.sub(r"\n +", "\n", text)
            text = text.strip()
            if len(text) > 150:
                return text[:15000]
    paragraphs = re.findall(r'<p[^>]*>(.*?)</p>', h, re.DOTALL)
    if paragraphs:
        text = "\n".join([re.sub(r"<[^>]+>", " ", p).strip() for p in paragraphs if len(p.strip()) > 10])
        text = html_mod.unescape(text)
        text = re.sub(r"\s+", " ", text).strip()
        if len(text) > 150:
            return text[:15000]
    return ""

def fetch_article_text(url, hint="", t=15):
    try:
        html = fetch(url, t, retries=0)
        text = extract(html, hint)
        if text and len(text) > 200:
            return text[:15000]
    except:
        pass
    return ""

def parse_date(date_str):
    if not date_str:
        return None
    s = date_str.strip()
    m = re.match(r'\w{3}, (\d{1,2}) (\w{3}) (\d{4}) (\d{2}):(\d{2}):(\d{2})', s)
    if m:
        day, mon, year, hh, mm, ss = m.groups()
        months = {"Jan":1,"Feb":2,"Mar":3,"Apr":4,"May":5,"Jun":6,
                  "Jul":7,"Aug":8,"Sep":9,"Oct":10,"Nov":11,"Dec":12}
        if mon in months:
            return datetime(int(year), months[mon], int(day), int(hh), int(mm), int(ss), tzinfo=timezone.utc)
    m = re.match(r'(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2}):(\d{2})', s)
    if m:
        return datetime(*[int(x) for x in m.groups()], tzinfo=timezone.utc)
    return None

def is_recent(pub):
    dt = parse_date(pub)
    if dt is None:
        return False
    return dt >= CUTOFF

def parse_rss(text):
    root = ET.fromstring(text)
    res = []
    for item in root.findall(".//item"):
        t = (item.findtext("title") or "").strip()
        link = (item.findtext("link") or "").strip()
        if not link:
            for ln in item.findall("{http://www.w3.org/2005/Atom}link"):
                link = ln.get("href", "")
                break
        d = re.sub(r"<[^>]+>", "", (item.findtext("description") or "")[:2000])
        pub = item.findtext("pubDate") or ""
        if t: res.append({"t": t, "l": link, "d": d, "pub": pub})
    if not res:
        ns = "{http://www.w3.org/2005/Atom}"
        for entry in root.findall(ns + "entry"):
            t = (entry.findtext(ns + "title") or "").strip()
            link = ""
            for ln in entry.findall(ns + "link"):
                link = ln.get("href", "")
                break
            d = re.sub(r"<[^>]+>", "", (entry.findtext(ns + "summary") or "")[:300])
            pub = entry.findtext(ns + "published") or entry.findtext(ns + "updated") or ""
            if t: res.append({"t": t, "l": link, "d": d, "pub": pub})
    return res

def hp_links_container(html):
    links = set()
    for m in re.finditer(r'<(?:h[1-4]|div)[^>]*>\s*<a[^>]*href=[\"\'](https?://[^\"\']+)[\"\'][^>]*>(.*?)</a>', html, re.DOTALL):
        text = re.sub(r"<[^>]+>", "", m.group(2)).strip()
        if len(text) > 15 and not any(skip in text.lower() for skip in ["read more","click here","ad","subscribe","cookie","privacy"]):
            links.add((m.group(1), text))
    if len(links) < 3:
        for m in re.finditer(r'<a[^>]*href=[\"\'](https?://[^\"\']+)[\"\'][^>]*>([^<]{20,})</a>', html):
            text = m.group(2).strip()
            if not any(skip in text.lower() for skip in ["read more","click here","ad","subscribe","cookie","privacy"]):
                links.add((m.group(1), text))
    return list(links)

results = []
source_counts = {}
fetch_log = []

def add(s, t, u, sm, ft, pub=""):
    if source_counts.get(s, 0) >= MAX_PER_SOURCE:
        return False
    if len(results) >= 2000:
        return False
    if pub and not is_recent(pub):
        return False
    t_norm = t.lower().strip()
    if any(r["title_en"].lower().strip() == t_norm for r in results):
        return False
    results.append({
        "source": s,
        "title_en": t,
        "title_cn": "",
        "url": u,
        "summary": sm[:2000],
        "full_text": ft[:15000],
        "pub_date_raw": pub,
        "pub_sort": parse_date(pub) if pub else datetime.now(timezone.utc)
    })
    source_counts[s] = source_counts.get(s, 0) + 1
    return True

def fetch_rss_items(url, name="RSS"):
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        resp = urllib.request.urlopen(req, timeout=20)
        raw = resp.read().decode()
        items = []
        entries = re.findall(r'<item>(.*?)</item>', raw, re.S)
        if not entries:
            entries = re.findall(r'<entry>(.*?)</entry>', raw, re.S)
        for e in entries:
            t = re.findall(r'<title>(.*?)</title>', e, re.S)
            l = re.findall(r'<link>(.*?)</link>', e, re.S)
            if not l:
                l = re.findall(r'<link[^>]*href="([^"]*)"', e)
            d = re.findall(r'<description>(.*?)</description>', e, re.S)
            if not d:
                d = re.findall(r'<summary>(.*?)</summary>', e)
            title = re.sub(r'<[^>]+>', '', (t[0] if t else '')).strip()
            link = l[0] if l else ''
            desc = re.sub(r'<[^>]+>', '', (d[0] if d else title)).strip()
            if title and link:
                items.append({"title": title, "link": link, "desc": desc[:2000]})
        return items
    except Exception as e:
        print(f"  ⚠️ {name} RSS: {e}", file=sys.stderr)
        return []

def safe_fetch_json(url, name="API"):
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        resp = urllib.request.urlopen(req, timeout=20)
        return json.loads(resp.read().decode())
    except Exception as e:
        print(f"  ⚠️ {name}: {e}", file=sys.stderr)
        fetch_log.append(('JSON:' + name, 'ERR:' + repr(e)[:160]))
        return None

def run():
    # ========== 境外政府RSS源（保留全部）==========
    GOV_RSS_FEEDS = [
        {"name": "US State Dept","url": "https://www.state.gov/press-releases/feed/","filter": ["China", "Chinese", "Beijing", "Taiwan", "South China Sea", "Indo-Pacific"]},
        {"name": "US DoD","url": "https://www.defense.gov/DesktopModules/ArticleCS/RSS.ashx?ContentType=1&Site=945&max=20","filter": ["China", "Chinese", "PLA", "Taiwan", "South China Sea", "Pacific"]},
        {"name": "EU EEAS","url": "https://www.eeas.europa.eu/eeas/taxonomy/term/397/feed","filter": ["China", "Chinese", "Beijing", "Taiwan", "Indo-Pacific"]},
        {"name": "UK FCDO","url": "https://www.gov.uk/government/organisations/foreign-commonwealth-development-office.atom","filter": ["China", "Chinese", "Beijing", "Taiwan", "Hong Kong"]},
    ]
    print("Fetching government RSS feeds...", file=sys.stderr)
    for feed in GOV_RSS_FEEDS:
        items = fetch_rss_items(feed["url"], feed["name"])
        added = 0
        for it in items:
            text = it["title"] + " " + it["desc"]
            if any(kw.lower() in text.lower() for kw in feed["filter"]):
                add(feed["name"], it["title"], it["link"], it["desc"][:2000], it["desc"][:15000], "")
                added += 1
        print(f"  {feed['name']}: {len(items)} items, {added} China-related", file=sys.stderr)

    # ========== 灾害API（保留，非媒体新闻不影响）==========
    print("Fetching WorldMonitor API feeds...", file=sys.stderr)
    eonet = safe_fetch_json("https://eonet.gsfc.nasa.gov/api/v3/events?status=open&limit=10", "NASA EONET")
    if eonet:
        for ev in eonet.get("events", []):
            title = ev.get("title", "?")
            cat = ev.get("categories", [{}])[0].get("title", "自然灾害")
            desc = f"{cat}：{title}。来源：NASA EONET全球事件观测系统。"
            url = f"https://eonet.gsfc.nasa.gov/api/v3/events/{ev.get('id','')}"
            add("NASA EONET", title, url, desc[:2000], desc[:15000], time.strftime("%Y-%m-%d"))
    usgs = safe_fetch_json("https://earthquake.usgs.gov/earthquakes/feed/v1.0/summary/2.5_day.geojson", "USGS")
    if usgs:
        for eq in usgs.get("features", [])[:8]:
            mag = eq["properties"]["mag"]
            place = eq["properties"]["place"]
            title = f"M{mag}地震 - {place}"
            desc = f"美国地质调查局(USGS)记录到{place}发生M{mag}级地震。"
            url = eq["properties"]["url"]
            add("USGS", title, url, desc[:2000], desc[:15000], time.strftime("%Y-%m-%d"))
    fng = safe_fetch_json("https://api.alternative.me/fng/?limit=2", "Fear&Greed")
    if fng and fng.get("data"):
        d = fng["data"][0]
        val = d.get("value", "?")
        cls = d.get("value_classification", "?")
        title = f"恐惧贪婪指数：{val}（{cls}）"
        desc = f"加密货币市场恐惧与贪婪指数当前为{val}，处于「{cls}」区间。0=极度恐惧，100=极度贪婪。该指数综合波动率、交易量、社交媒体、市场占比和趋势五个维度计算。"
        add("Market", title, "https://alternative.me/crypto/fear-and-greed-index/", desc[:2000], desc[:15000], time.strftime("%Y-%m-%d"))
    gdacs = safe_fetch_json("https://www.gdacs.org/gdacsapi/api/events/geteventlist/SEARCH?fromDate=" + (datetime.utcnow() - timedelta(days=3)).strftime("%Y-%m-%d"), "GDACS")
    if gdacs:
        for ev in gdacs[:5] if isinstance(gdacs, list) else []:
            title = ev.get("eventname", ev.get("name", "?"))
            etype = ev.get("eventtype", "灾害")
            desc = f"GDACS全球灾害预警系统：{etype}「{title}」正在活跃。严重程度：{ev.get('severity', '?')}。"
            add("GDACS", str(title), f"https://www.gdacs.org/report.aspx?eventid={ev.get('eventid','')}", desc[:2000], desc[:15000], time.strftime("%Y-%m-%d"))
    print("WorldMonitor API feeds done.", file=sys.stderr)

    # ========== 外媒RSS新闻源（全部境外媒体，无国内源）==========
    bbc_feeds = ["https://feeds.bbci.co.uk/news/world/asia/china/rss.xml","https://feeds.bbci.co.uk/news/world/asia/rss.xml","https://feeds.bbci.co.uk/news/world/rss.xml"]
    for feed_url in bbc_feeds:
        try:
            items = parse_rss(fetch(feed_url))
            for it in items:
                if source_counts.get("BBC", 0) >= MAX_PER_SOURCE: break
                head = it["t"] + " " + it.get("d", "")
                passed = ("china" in feed_url) or is_cn(head)
                ft = it.get("d", "")
                if passed:
                    if it["l"]:
                        try: a = fetch_article_text(it["l"], "BBC", 15); ft = a if a else ft
                        except: pass
                    add("BBC", it["t"], it["l"], it["d"], ft, it.get("pub",""))
                else:
                    body = fetch_article_text(it["l"], "BBC",15) if it["l"] else ""
                    if body and is_cn(body):
                        add("BBC", it["t"], it["l"], it["d"], body, it.get("pub",""))
        except Exception as e:
            print(f"  BBC feed {feed_url}: {e}", file=sys.stderr)
    print(f"BBC collected: {source_counts.get('BBC',0)}", file=sys.stderr)

    SITE_CN_SOURCES = {"The Atlantic", "Nature", "Cell", "Science", "The Lancet", "NEJM", "PNAS"}
    gn_sources = [
        ("Reuters", "site:reuters.com+china", "Reuters"),
        ("Bloomberg", "site:bloomberg.com+china", "Bloomberg"),
        ("AP", "site:apnews.com+china", "AP"),
        ("Nikkei Asia", "site:asia.nikkei.com+china", "Nikkei"),
        ("Financial Times", "site:ft.com+china", "FT"),
        ("New York Times", "site:nytimes.com+china", "NYT"),
        ("CNN", "site:cnn.com+china", "CNN"),
        ("AFP", "site:afp.com+china", "AFP"),
        ("The Economist", "site:economist.com+china", "Economist"),
        ("MIT Tech Review", "site:technologyreview.com+china", "MIT Tech"),
        ("The Guardian", "site:theguardian.com+china", "Guardian"),
        ("VOA News", "site:voanews.com+china", "VOA"),
        ("The Atlantic", "site:theatlantic.com+china", "Atlantic"),
        ("EurAsian Times", "site:eurasiantimes.com+china", "EurAsia"),
        ("Nature", "site:nature.com+china", "Nature"),
        ("Cell", "site:cell.com+china", "Cell"),
        ("Science", "site:science.org+china", "Science"),
        ("The Lancet", "site:thelancet.com+china", "Lancet"),
        ("NEJM", "site:nejm.org+china", "NEJM"),
        ("PNAS", "site:pnas.org+china", "PNAS"),
    ]
    for src, query, hint in gn_sources:
        try:
            time.sleep(1.2)
            items = parse_rss(fetch(f"https://news.google.com/rss/search?q={query}&hl=en-US&gl=US&ceid=US:en"))
            for it in items:
                if source_counts.get(src,0)>=MAX_PER_SOURCE: break
                head = it["t"] + " " + it.get("d", "")
                ft = it.get("d", "")
                if src in SITE_CN_SOURCES or is_cn(head):
                    if it["l"]:
                        try: a = fetch_article_text(it["l"], hint,15); ft = a if a else ft
                        except: pass
                    add(src, it["t"], it["l"], it["d"], ft, it.get("pub",""))
                else:
                    body = fetch_article_text(it["l"], hint,15) if it["l"] else ""
                    if body and is_cn(body):
                        add(src, it["t"], it["l"], it["d"], body, it.get("pub",""))
        except Exception as e:
            print(f"{src} error: {e}", file=sys.stderr)
        print(f"{src}: {source_counts.get(src,0)}", file=sys.stderr)

    # ========== 全局排序：按发布时间倒序 ==========
    global results
    results = sorted(results, key=lambda x: x["pub_sort"], reverse=True)

    # ========== 批量翻译英文标题 ==========
    en_titles = [item["title_en"] for item in results]
    cn_titles = batch_translate(en_titles)
    for idx, item in enumerate(results):
        item["title_cn"] = cn_titles[idx]
        # 序列化时间字段，方便前端读取
        item["pub_sort"] = item["pub_sort"].strftime("%Y-%m-%d %H:%M:%S UTC")

    # ========== 输出news.json给前端页面 ==========
    output_data = {
        "update_time": datetime.now(timezone(timedelta(hours=8))).strftime("%Y-%m-%d %H:%M:%S 北京时间"),
        "total": len(results),
        "source_stats": source_counts,
        "news_list": results
    }
    with open("news.json", "w", encoding="utf-8") as f:
        json.dump(output_data, f, ensure_ascii=False, indent=2)
    print(f"\n✅ 采集完成，共{len(results)}条新闻，已写入news.json", file=sys.stderr)

if __name__ == "__main__":
    try:
        run()
    except Exception as e:
        fetch_log.append(("FATAL", repr(e)[:400]))
        traceback.print_exc()
        with open("news.json", "w", encoding="utf-8") as f:
            json.dump({"error": str(e)}, f, ensure_ascii=False, indent=2)
