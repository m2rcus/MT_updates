
import os
import sys
import time
import json
import threading
import logging
from dataclasses import dataclass
from typing import List, Tuple, Optional, Iterable

import requests
from requests.adapters import HTTPAdapter, Retry
from flask import Flask, render_template_string, request, jsonify
from bs4 import BeautifulSoup
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta
import pytz
import feedparser

# ---------------------------------------------------------------------------
# Environment & Configuration
# ---------------------------------------------------------------------------

BOT_TOKEN = os.environ.get("BOT_TOKEN")
CHANNEL = os.environ.get("CHANNEL")
CMC_API_KEY = os.environ.get("COINMARKETCAP_API_KEY")
DEBUG_MODE = os.environ.get("DEBUG") in {"1", "true", "True", "yes", "on"}

if not BOT_TOKEN:
    print("❌ ERROR: BOT_TOKEN environment variable is not set!", file=sys.stderr)
    sys.exit(1)
if not CHANNEL:
    print("❌ ERROR: CHANNEL environment variable is not set!", file=sys.stderr)
    sys.exit(1)

LOG_LEVEL = logging.DEBUG if DEBUG_MODE else logging.INFO
logging.basicConfig(
    level=LOG_LEVEL,
    format="%(asctime)s [%(levelname)s] %(threadName)s: %(message)s",
    stream=sys.stdout,
)
logger = logging.getLogger(__name__)
logger.info("BOT_TOKEN: set")
logger.info("CHANNEL: %s", CHANNEL)
logger.info("Debug mode: %s", DEBUG_MODE)

TZ = pytz.timezone("America/Los_Angeles")
TELEGRAM_MAX_CHARS = 4096
SENT_HEADLINES_FILE = "sent_headlines.json"

sent_headlines_lock = threading.Lock()
bot_quiet_lock = threading.Lock()
_pitchbook_fail_ts_lock = threading.Lock()
_pitchbook_next_html_try: Optional[float] = None

sent_headlines: set[str] = set()
bot_quiet_until: Optional[datetime] = None


# ---------------------------------------------------------------------------
# Utils
# ---------------------------------------------------------------------------

def md_escape(text: str) -> str:
    if not text:
        return text
    return (text.replace("_", "\\_")
                .replace("*", "\\*")
                .replace("[", "\\[")
                .replace("]", "\\]")
                .replace("(", "\\(")
                .replace(")", "\\)"))


def chunk_message(text: str, max_len: int = TELEGRAM_MAX_CHARS) -> Iterable[str]:
    if len(text) <= max_len:
        yield text
        return
    lines = text.splitlines(keepends=True)
    buf, total = [], 0
    for line in lines:
        if total + len(line) > max_len and buf:
            yield ''.join(buf)
            buf, total = [line], len(line)
        else:
            buf.append(line)
            total += len(line)
    if buf:
        yield ''.join(buf)


def load_sent_headlines() -> set[str]:
    if not os.path.exists(SENT_HEADLINES_FILE):
        logger.info("No existing %s; starting fresh.", SENT_HEADLINES_FILE)
        return set()
    try:
        with open(SENT_HEADLINES_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        return set(data) if isinstance(data, list) else set()
    except Exception:  # noqa: BLE001
        logger.exception("Error loading sent headlines")
        return set()


def save_sent_headlines(headlines) -> None:
    try:
        with open(SENT_HEADLINES_FILE, "w", encoding="utf-8") as f:
            json.dump(sorted(set(headlines)), f, ensure_ascii=False, indent=2)
    except Exception:  # noqa: BLE001
        logger.exception("Error saving sent headlines")


sent_headlines = load_sent_headlines()
logger.info("Loaded %d previously sent headlines.", len(sent_headlines))


def set_bot_quiet(hours: int = 6) -> None:
    global bot_quiet_until
    with bot_quiet_lock:
        bot_quiet_until = datetime.now() + timedelta(hours=hours)
        logger.info("Bot quiet until %s", bot_quiet_until)


def is_bot_quiet() -> bool:
    global bot_quiet_until
    with bot_quiet_lock:
        if bot_quiet_until is None:
            return False
        if datetime.now() >= bot_quiet_until:
            bot_quiet_until = None
            return False
        return True


# ---------------------------------------------------------------------------
# HTTP Sessions
# ---------------------------------------------------------------------------

def build_session(base_headers: Optional[dict] = None) -> requests.Session:
    sess = requests.Session()
    if base_headers:
        sess.headers.update(base_headers)
    retries = Retry(total=3, backoff_factor=1.5,
                    status_forcelist=[403, 429, 500, 502, 503, 504],
                    allowed_methods=["GET", "HEAD"], raise_on_status=False)
    adapter = HTTPAdapter(max_retries=retries)
    sess.mount("http://", adapter)
    sess.mount("https://", adapter)
    return sess

GENERIC_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Cache-Control": "no-cache",
    "Connection": "keep-alive",
    "Pragma": "no-cache",
    "Upgrade-Insecure-Requests": "1",
}
IGAMING_HEADERS = {**GENERIC_HEADERS, "Accept": "application/rss+xml,application/xml;q=0.9,*/*;q=0.8", "Referer": "https://www.google.com/"}
PITCHBOOK_HEADERS = {**GENERIC_HEADERS, "Accept": "application/rss+xml,application/xml;q=0.9,*/*;q=0.8", "Referer": "https://www.google.com/"}
CNBC_HEADERS = GENERIC_HEADERS.copy()
CRUNCHBASE_HEADERS = GENERIC_HEADERS.copy()

REQUEST_TIMEOUT = 15

igaming_session = build_session(IGAMING_HEADERS)
pitchbook_session = build_session(PITCHBOOK_HEADERS)
cnbc_session = build_session(CNBC_HEADERS)
cmc_session = build_session({"Accepts": "application/json", "X-CMC_PRO_API_KEY": CMC_API_KEY or ""})
crunchbase_session = build_session(CRUNCHBASE_HEADERS)


# ---------------------------------------------------------------------------
# Data Classes
# ---------------------------------------------------------------------------

@dataclass
class NewsItem:
    source: str
    title: str
    url: str
    emoji: str
    def to_markdown_line(self) -> str:
        return f"{self.emoji} *{md_escape(self.source)}*\n[{md_escape(self.title)}]({self.url})"


# ---------------------------------------------------------------------------
# Fetchers
# ---------------------------------------------------------------------------

def fetch_crypto_prices() -> Tuple[str, str, str, str, str, str]:
    btc_price = eth_price = hype_price = "N/A"
    sp500_price = gold_price = titan_price = "N/A"
    if CMC_API_KEY:
        try:
            url = "https://pro-api.coinmarketcap.com/v1/cryptocurrency/quotes/latest"
            params = {"symbol": "BTC,ETH,HYPE", "convert": "USD"}
            resp = cmc_session.get(url, params=params, timeout=REQUEST_TIMEOUT)
            logger.debug("CMC status %s", resp.status_code)
            if resp.ok:
                data = resp.json()
                btc_price = f"${data['data']['BTC']['quote']['USD']['price']:,.0f}" if 'BTC' in data['data'] else "N/A"
                eth_price = f"${data['data']['ETH']['quote']['USD']['price']:,.0f}" if 'ETH' in data['data'] else "N/A"
                hype_price = f"${data['data']['HYPE']['quote']['USD']['price']:,.2f}" if 'HYPE' in data['data'] else "N/A"
        except Exception:  # noqa: BLE001
            logger.exception("Error fetching CoinMarketCap prices")
    else:
        logger.warning("COINMARKETCAP_API_KEY not set; crypto prices will be N/A.")
    try:
        sp_resp = requests.get("https://query1.finance.yahoo.com/v8/finance/chart/%5EGSPC", headers=GENERIC_HEADERS, timeout=REQUEST_TIMEOUT)
        if sp_resp.ok:
            sp_data = sp_resp.json()
            sp500_price = f"${sp_data['chart']['result'][0]['meta']['regularMarketPrice']:,.0f}"
    except Exception:  # noqa: BLE001
        logger.exception("Error fetching S&P 500 price")
    try:
        gold_resp = requests.get("https://query1.finance.yahoo.com/v8/finance/chart/GC=F", headers=GENERIC_HEADERS, timeout=REQUEST_TIMEOUT)
        if gold_resp.ok:
            gold_data = gold_resp.json()
            gold_price = f"${gold_data['chart']['result'][0]['meta']['regularMarketPrice']:,.0f}"
    except Exception:  # noqa: BLE001
        logger.exception("Error fetching gold price")
    try:
        titan_resp = requests.get("https://query1.finance.yahoo.com/v8/finance/chart/TTM.AX", headers=GENERIC_HEADERS, timeout=REQUEST_TIMEOUT)
        if titan_resp.ok:
            titan_data = titan_resp.json()
            titan_price = f"${titan_data['chart']['result'][0]['meta']['regularMarketPrice']:,.2f}"
    except Exception:  # noqa: BLE001
        logger.exception("Error fetching Titan Minerals price")
    return btc_price, eth_price, hype_price, sp500_price, gold_price, titan_price


def _parse_rss_items(xml_bytes: bytes) -> List[dict]:
    try:
        root = ET.fromstring(xml_bytes)
    except ET.ParseError as e:  # noqa: BLE001
        logger.warning("RSS parse error: %s", e)
        return []
    items = []
    for item in root.findall(".//item"):
        title_el = item.find("title")
        link_el = item.find("link")
        if title_el is None or link_el is None:
            continue
        title = (title_el.text or "").strip()
        link = (link_el.text or "").strip()
        items.append({"title": title, "link": link})
    return items


def _igaming_fallback() -> List[NewsItem]:
    url = "https://api.rss2json.com/v1/api.json?rss_url=https://igamingbusiness.com/feed/"
    try:
        r = requests.get(url, headers=GENERIC_HEADERS, timeout=REQUEST_TIMEOUT)
        if not r.ok:
            logger.warning("iGaming fallback rss2json failed: %s", r.status_code)
            return []
        data = r.json()
        items = data.get("items", [])
        news = []
        for item in items[:10]:
            title = item.get("title", "").strip()
            link = item.get("link", "").strip()
            if not title or not link:
                continue
            with sent_headlines_lock:
                if title in sent_headlines:
                    continue
            news.append(NewsItem("iGaming Business", title, link, "📰"))
        return news
    except Exception:  # noqa: BLE001
        logger.exception("iGaming fallback error")
        return []


def get_igaming_news(mark_sent: bool = False) -> List[NewsItem]:
    url = "https://igamingbusiness.com/feed/"
    news: List[NewsItem] = []
    try:
        r = igaming_session.get(url, timeout=REQUEST_TIMEOUT)
        logger.debug("iGaming RSS status: %s", r.status_code)
        if r.status_code == 403:
            logger.warning("iGaming RSS 403; trying rss2json fallback")
            news = _igaming_fallback()
            _maybe_mark_sent(news, mark_sent)
            return news
        if not r.ok:
            logger.warning("iGaming RSS request failed: %s", r.status_code)
            return []
        articles = _parse_rss_items(r.content)
    except Exception:  # noqa: BLE001
        logger.exception("Error fetching iGaming RSS; attempting fallback")
        news = _igaming_fallback()
        _maybe_mark_sent(news, mark_sent)
        return news
    important_keywords = {'breaking','major','launch','acquisition','merger','regulation','partnership','expansion','funding','investment','deal','announcement','strategic','milestone','record','growth','new market'}
    for art in articles[:10]:
        title, link = art['title'], art['link']
        lower = title.lower()
        if any(kw in lower for kw in important_keywords):
            with sent_headlines_lock:
                if title in sent_headlines:
                    continue
            news.append(NewsItem("iGaming Business", title, link, "📰"))
    _maybe_mark_sent(news, mark_sent)
    return news


# --- Crunchbase News ------------------------------------------------------
def get_crunchbase_news(mark_sent: bool = False) -> List[NewsItem]:
    url = "https://news.crunchbase.com/"
    news: List[NewsItem] = []
    keywords = {'crypto', 'blockchain', 'bitcoin', 'ethereum', 'igaming', 'gambling', 'casino', 'betting'}
    try:
        r = crunchbase_session.get(url, timeout=REQUEST_TIMEOUT)
        logger.debug("Crunchbase status: %s", r.status_code)
        if not r.ok:
            logger.warning("Crunchbase request failed: %s", r.status_code)
            return []
        soup = BeautifulSoup(r.text, "html.parser")
        # Main selector for Crunchbase headlines
        articles = soup.select("article h2 a")
        if not articles:
            # Fallback: try all links in articles
            articles = soup.select("article a")
        for a in articles:
            title = a.get_text(strip=True)
            link = a.get("href", "")
            if not link or not title:
                continue
            if not link.startswith("http"):
                link = f"https://news.crunchbase.com{link}" if link.startswith("/") else f"https://news.crunchbase.com/{link}"
            lower = title.lower()
            if any(k in lower for k in keywords):
                with sent_headlines_lock:
                    if title in sent_headlines:
                        continue
                news.append(NewsItem("Crunchbase News", title, link, "🦀"))
            if len(news) >= 10:
                break
    except Exception:  # noqa: BLE001
        logger.exception("Error fetching Crunchbase news")
        return []
    _maybe_mark_sent(news, mark_sent)
    return news


# --- CNBC Crypto News ------------------------------------------------------
def get_cnbc_crypto_news(mark_sent: bool = False) -> List[NewsItem]:
    url = "https://www.cnbc.com/cryptoworld/"
    news: List[NewsItem] = []
    try:
        r = cnbc_session.get(url, timeout=REQUEST_TIMEOUT)
        logger.debug("CNBC status: %s", r.status_code)
        if not r.ok:
            logger.warning("CNBC request failed: %s", r.status_code)
            return []
        soup = BeautifulSoup(r.text, "html.parser")
        anchors = soup.select("a.Card-title")
        if not anchors:
            anchors = [a for a in soup.select("a") if 'crypto' in (a.get('href') or '')]
        for a in anchors[:15]:
            title = a.get_text(strip=True)
            link = a.get("href", "")
            if not link:
                continue
            if link.startswith('/'):
                link = f"https://www.cnbc.com{link}"
            with sent_headlines_lock:
                if title in sent_headlines:
                    continue
            news.append(NewsItem("CNBC Crypto World", title, link, "💰"))
    except Exception:  # noqa: BLE001
        logger.exception("Error fetching CNBC crypto news")
        return []
    _maybe_mark_sent(news, mark_sent)
    return news


# --- WSJ News ------------------------------------------------------
def get_wsj_news(mark_sent: bool = False) -> List[NewsItem]:
    feeds = [
        "https://news.google.com/rss/search?q=site:wsj.com+crypto",
        "https://news.google.com/rss/search?q=site:wsj.com+igaming"
    ]
    news: List[NewsItem] = []
    keywords = {'crypto', 'blockchain', 'bitcoin', 'ethereum', 'igaming', 'gambling', 'casino', 'betting'}
    for feed_url in feeds:
        try:
            d = feedparser.parse(feed_url)
            for entry in d.entries:
                title = entry.title.strip()
                link = entry.link.strip()
                lower = title.lower()
                if any(k in lower for k in keywords) and 'wsj.com' in link:
                    with sent_headlines_lock:
                        if title in sent_headlines:
                            continue
                    news.append(NewsItem("WSJ (via Google News)", title, link, "📰"))
                if len(news) >= 10:
                    break
        except Exception:
            logger.exception(f"Error fetching WSJ Google News RSS from {feed_url}")
            continue
    _maybe_mark_sent(news, mark_sent)
    return news


# --- Medium News ------------------------------------------------------
def get_medium_news(mark_sent: bool = False) -> List[NewsItem]:
    feeds = [
        "https://medium.com/feed/tag/crypto",
        "https://medium.com/feed/tag/igaming"
    ]
    news: List[NewsItem] = []
    keywords = {'crypto', 'blockchain', 'bitcoin', 'ethereum', 'igaming', 'gambling', 'casino', 'betting'}
    for feed_url in feeds:
        try:
            d = feedparser.parse(feed_url)
            for entry in d.entries:
                title = entry.title.strip()
                link = entry.link.strip()
                lower = title.lower()
                if any(k in lower for k in keywords):
                    with sent_headlines_lock:
                        if title in sent_headlines:
                            continue
                    news.append(NewsItem("Medium", title, link, "✍️"))
                if len(news) >= 10:
                    break
        except Exception:
            logger.exception(f"Error fetching Medium RSS from {feed_url}")
            continue
    _maybe_mark_sent(news, mark_sent)
    return news


# --- CryptoHeadlines News ------------------------------------------------------
def get_cryptoheadlines_news(mark_sent: bool = False) -> List[NewsItem]:
    url = "https://cryptoheadlines.com/"
    news: List[NewsItem] = []
    keywords = {'crypto', 'blockchain', 'bitcoin', 'ethereum', 'igaming', 'gambling', 'casino', 'betting'}
    try:
        r = requests.get(url, headers=GENERIC_HEADERS, timeout=REQUEST_TIMEOUT)
        logger.debug(f"CryptoHeadlines status: %s", r.status_code)
        if not r.ok:
            logger.warning(f"CryptoHeadlines request failed: %s", r.status_code)
            return []
        soup = BeautifulSoup(r.text, "html.parser")
        articles = soup.select(".news-list .news-item a")
        for a in articles:
            title = a.get_text(strip=True)
            link = a.get("href", "")
            if not link or not title:
                continue
            if not link.startswith("http"):
                link = f"https://cryptoheadlines.com{link}" if link.startswith("/") else f"https://cryptoheadlines.com/{link}"
            lower = title.lower()
            if any(k in lower for k in keywords):
                with sent_headlines_lock:
                    if title in sent_headlines:
                        continue
                news.append(NewsItem("CryptoHeadlines", title, link, "📰"))
            if len(news) >= 10:
                break
    except Exception:
        logger.exception(f"Error fetching CryptoHeadlines news")
        return []
    _maybe_mark_sent(news, mark_sent)
    return news


# --- The Defiant Newsletter News ------------------------------------------------------
def get_defiant_newsletter_news(mark_sent: bool = False) -> List[NewsItem]:
    url = "https://thedefiant.io/newsletter/"
    news: List[NewsItem] = []
    keywords = {'crypto', 'blockchain', 'bitcoin', 'ethereum', 'igaming', 'gambling', 'casino', 'betting'}
    try:
        r = requests.get(url, headers=GENERIC_HEADERS, timeout=REQUEST_TIMEOUT)
        logger.debug(f"Defiant Newsletter status: %s", r.status_code)
        if not r.ok:
            logger.warning(f"Defiant Newsletter request failed: %s", r.status_code)
            return []
        soup = BeautifulSoup(r.text, "html.parser")
        articles = soup.select("a.chakra-link[href*='/newsletter/']")
        for a in articles:
            title = a.get_text(strip=True)
            link = a.get("href", "")
            if not link or not title:
                continue
            if not link.startswith("http"):
                link = f"https://thedefiant.io{link}" if link.startswith("/") else f"https://thedefiant.io/{link}"
            lower = title.lower()
            if any(k in lower for k in keywords):
                with sent_headlines_lock:
                    if title in sent_headlines:
                        continue
                news.append(NewsItem("The Defiant Newsletter", title, link, "📰"))
            if len(news) >= 10:
                break
    except Exception:
        logger.exception(f"Error fetching Defiant Newsletter news")
        return []
    _maybe_mark_sent(news, mark_sent)
    return news


# --- Ecuador Mining & Gold Assets News ------------------------------------------------------
def _ecuador_mining_fallback() -> List[NewsItem]:
    """Fallback for Ecuador mining news using alternative sources"""
    fallback_urls = [
        "https://api.rss2json.com/v1/api.json?rss_url=https://feeds.reuters.com/reuters/businessNews",
        "https://api.rss2json.com/v1/api.json?rss_url=https://feeds.bloomberg.com/markets/news.rss",
        "https://api.rss2json.com/v1/api.json?rss_url=https://www.ambito.com/rss/economia.xml",
        "https://api.rss2json.com/v1/api.json?rss_url=https://www.infobae.com/feed/economia/",
        "https://api.rss2json.com/v1/api.json?rss_url=https://www.lanacion.com.ar/rss/economia.xml",
        "https://api.rss2json.com/v1/api.json?rss_url=https://www.elcomercio.com/rss/economia.xml",
        "https://api.rss2json.com/v1/api.json?rss_url=https://www.eluniverso.com/rss/economia.xml"
    ]
    news: List[NewsItem] = []
    keywords = {'ecuador', 'mining', 'gold', 'copper', 'silver', 'mineral', 'exploration', 'drill', 'assay', 'resource', 'reserve', 'production', 'development', 'permit', 'concession', 'titan minerals', 'tttnf'}
    
    for fallback_url in fallback_urls:
        try:
            r = requests.get(fallback_url, headers=GENERIC_HEADERS, timeout=REQUEST_TIMEOUT)
            if not r.ok:
                continue
            data = r.json()
            items = data.get("items", [])
            
            for item in items[:15]:
                title = item.get("title", "").strip()
                link = item.get("link", "").strip()
                if not title or not link:
                    continue
                lower = title.lower()
                if 'ecuador' in lower and any(k in lower for k in keywords):
                    with sent_headlines_lock:
                        if title in sent_headlines:
                            continue
                    news.append(NewsItem("Ecuador Mining News (Fallback)", title, link, "⛏️"))
                if len(news) >= 5:
                    break
            if news:
                break
        except Exception:
            continue
    return news


def get_ecuador_mining_news(mark_sent: bool = False) -> List[NewsItem]:
    feeds = [
        "https://news.google.com/rss/search?q=ecuador+mining+gold",
        "https://news.google.com/rss/search?q=ecuador+gold+mines",
        "https://news.google.com/rss/search?q=ecuador+mining+companies",
        "https://news.google.com/rss/search?q=site:elcomercio.com+ecuador+mining",
        "https://news.google.com/rss/search?q=site:eluniverso.com+ecuador+mining",
        "https://news.google.com/rss/search?q=site:expreso.ec+ecuador+mining",
        "https://news.google.com/rss/search?q=site:primicias.ec+ecuador+mining",
        "https://news.google.com/rss/search?q=site:eltelegrafo.com.ec+ecuador+mining",
        "https://news.google.com/rss/search?q=site:diarioextra.com+ecuador+mining",
        "https://news.google.com/rss/search?q=site:lahora.com.ec+ecuador+mining"
    ]
    news: List[NewsItem] = []
    keywords = {'ecuador', 'mining', 'gold', 'copper', 'silver', 'mineral', 'exploration', 'drill', 'assay', 'resource', 'reserve', 'production', 'development', 'permit', 'concession', 'titan minerals', 'tttnf', 'australian mining', 'canadian mining'}
    
    # Try primary Google News feeds
    for feed_url in feeds:
        try:
            d = feedparser.parse(feed_url)
            for entry in d.entries:
                title = entry.title.strip()
                link = entry.link.strip()
                lower = title.lower()
                if 'ecuador' in lower and any(k in lower for k in keywords):
                    with sent_headlines_lock:
                        if title in sent_headlines:
                            continue
                    news.append(NewsItem("Ecuador Mining News", title, link, "⛏️"))
                if len(news) >= 8:
                    break
            if len(news) >= 5:  # If we got some news, break early
                break
        except Exception:
            logger.exception(f"Error fetching Ecuador mining news from {feed_url}")
            continue
    
    # If primary sources failed, try fallback
    if not news:
        logger.warning("Primary Ecuador mining sources failed, trying fallback")
        news = _ecuador_mining_fallback()
    
    _maybe_mark_sent(news, mark_sent)
    return news





# ---------------------------------------------------------------------------
# Sent-headlines marking helper
# ---------------------------------------------------------------------------

def _maybe_mark_sent(items: List[NewsItem], mark: bool) -> None:
    if not mark or not items:
        return
    with sent_headlines_lock:
        before = len(sent_headlines)
        for itm in items:
            sent_headlines.add(itm.title)
        after = len(sent_headlines)
        save_sent_headlines(sent_headlines)
    logger.debug("Marked %d new headlines as sent", after - before)


def _mark_titles_as_sent(titles: Iterable[str]) -> None:
    if not titles:
        return
    with sent_headlines_lock:
        before = len(sent_headlines)
        for t in titles:
            sent_headlines.add(t)
        after = len(sent_headlines)
        save_sent_headlines(sent_headlines)
    logger.info("Marked %d new headlines as sent.", after - before)


# ---------------------------------------------------------------------------
# Digest Building
# ---------------------------------------------------------------------------

@dataclass
class Digest:
    text: str
    included_titles: List[str]


def build_digest() -> Digest:
    btc_price, eth_price, hype_price, sp500_price, gold_price, titan_price = fetch_crypto_prices()
    igaming_news_all = get_igaming_news(mark_sent=False)
    cnbc_news_all = get_cnbc_crypto_news(mark_sent=False)
    crunchbase_news_all = get_crunchbase_news(mark_sent=False)
    wsj_news_all = get_wsj_news(mark_sent=False)
    medium_news_all = get_medium_news(mark_sent=False)
    cryptoheadlines_news_all = get_cryptoheadlines_news(mark_sent=False)
    defiant_news_all = get_defiant_newsletter_news(mark_sent=False)
    ecuador_mining_news_all = get_ecuador_mining_news(mark_sent=False)
    with sent_headlines_lock:
        sent_copy = set(sent_headlines)
    igaming_news = [n for n in igaming_news_all if n.title not in sent_copy]
    cnbc_news = [n for n in cnbc_news_all if n.title not in sent_copy]
    crunchbase_news = [n for n in crunchbase_news_all if n.title not in sent_copy]
    wsj_news = [n for n in wsj_news_all if n.title not in sent_copy]
    medium_news = [n for n in medium_news_all if n.title not in sent_copy]
    cryptoheadlines_news = [n for n in cryptoheadlines_news_all if n.title not in sent_copy]
    defiant_news = [n for n in defiant_news_all if n.title not in sent_copy]
    ecuador_mining_news = [n for n in ecuador_mining_news_all if n.title not in sent_copy]
    def format_section(title: str, items: List[NewsItem]) -> str:
        if items:
            return f"*{md_escape(title)}:*\n" + "\n".join(f"{i+1}. [{md_escape(item.title)}]({item.url})" for i, item in enumerate(items))
        return f"*{md_escape(title)}:*\n_No pertinent news_"
    digest_text = (
        "🌅 Good Morning Sam and Lucas! Here's your daily digest:\n\n"
        f"*Market Outlook:*\n"
        f"• Bitcoin: {btc_price}\n"
        f"• Ethereum: {eth_price}\n"
        f"• $HYPE: {hype_price}\n"
        f"• Gold: {gold_price}\n"
        f"• S&P 500: {sp500_price}\n"
        f"• Titan Minerals: {titan_price}\n\n"
        + format_section("iGaming News", igaming_news) + "\n\n"
        + format_section("Crunchbase News", crunchbase_news) + "\n\n"
        + format_section("CNBC Crypto News", cnbc_news) + "\n\n"
        + format_section("WSJ News", wsj_news) + "\n\n"
        + format_section("Medium News", medium_news) + "\n\n"
        + format_section("CryptoHeadlines News", cryptoheadlines_news) + "\n\n"
        + format_section("The Defiant Newsletter", defiant_news) + "\n\n"
        + format_section("Ecuador Mining & Gold News", ecuador_mining_news)
    )
    included_titles = [n.title for n in igaming_news + crunchbase_news + cnbc_news + wsj_news + medium_news + cryptoheadlines_news + defiant_news + ecuador_mining_news]
    return Digest(digest_text, included_titles)


# ---------------------------------------------------------------------------
# Telegram Messaging
# ---------------------------------------------------------------------------

def send_telegram_message(message: str, chat_id: Optional[str | int] = None) -> bool:
    dest = chat_id if chat_id is not None else CHANNEL
    ok = True
    for chunk in chunk_message(message):
        data = {'chat_id': dest, 'text': chunk, 'parse_mode': 'Markdown', 'disable_web_page_preview': True}
        try:
            url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
            resp = requests.post(url, data=data, timeout=REQUEST_TIMEOUT)
            if resp.status_code != 200:
                logger.error("Telegram send failure %s: %s", resp.status_code, resp.text[:200])
                ok = False
            else:
                logger.debug("Telegram chunk sent (%d chars)", len(chunk))
        except Exception:  # noqa: BLE001
            logger.exception("Telegram send error")
            ok = False
    return ok


def welcome_message() -> str:
    btc, eth, hype, sp500, gold, titan = fetch_crypto_prices()
    return (
        "Good Morning Sam and Lucas! 🌅\n\n"
        "Breaking news in crypto, iGaming, and cap raises will be sent here periodically.\n\n"
        "*Bot Features:*\n"
        "• `/start` - Get this welcome message and current market prices\n"
        "• `/bignews` - Get the latest news immediately\n"
        "• `/shutup` - Make me quiet for 6 hours\n\n"
        "*Market Outlook:*\n"
        f"• Bitcoin: {btc}\n"
        f"• Ethereum: {eth}\n"
        f"• $HYPE: {hype}\n"
        f"• Gold: {gold}\n"
        f"• S&P 500: {sp500}\n"
        f"• Titan Minerals: {titan}\n\n"
        "Will update you periodically! 📈"
    )


# ---------------------------------------------------------------------------
# Flask App
# ---------------------------------------------------------------------------

app = Flask(__name__)


@app.route('/')
def home() -> str:
    now = datetime.now(TZ).strftime('%Y-%m-%d %H:%M:%S %Z')
    return render_template_string('''
    <!DOCTYPE html>
    <html lang="en">
    <head>
        <meta charset="utf-8" />
        <title>MT Updates Bot</title>
        <style>
            body { font-family: Arial, sans-serif; margin: 40px; background: #f0f0f0; }
            .container { background: white; padding: 30px; border-radius: 10px; box-shadow: 0 2px 10px rgba(0,0,0,0.1); max-width:600px; }
            .status { color: #28a745; font-weight: bold; }
        </style>
    </head>
    <body>
        <div class="container">
            <h1>🤖 MT Updates Bot</h1>
            <p class="status">✅ Bot is running and active!</p>
            <p>This page helps keep the bot alive.</p>
            <p><small>Last updated: {{ now }}</small></p>
        </div>
    </body>
    </html>
    ''', now=now)


@app.route('/health')
def health():
    return {
        'status': 'healthy',
        'timestamp': datetime.now(TZ).isoformat(),
        'sent_headlines_count': len(sent_headlines),
        'bot_quiet_until': bot_quiet_until.isoformat() if bot_quiet_until else None,
    }


@app.route('/webhook', methods=['POST'])
def telegram_webhook():
    data = request.get_json(force=True, silent=True) or {}
    logger.debug("Webhook received: %s", data)
    if 'message' in data:
        message = data['message']
        chat_id = message['chat']['id']
        text = (message.get('text') or '').strip()
        if text == '/start':
            send_telegram_message(welcome_message(), chat_id=chat_id)
        elif text == '/bignews':
            send_telegram_message("Fetching the latest news for you...", chat_id=chat_id)
            digest = build_digest()
            send_telegram_message(digest.text, chat_id=chat_id)
            _mark_titles_as_sent(digest.included_titles)
        elif text == '/shutup':
            set_bot_quiet(6)
            send_telegram_message("My bad Senor and Losh 😅\n\nI'll be quiet for the next 6 hours.", chat_id=chat_id)
        else:
            send_telegram_message("Unknown command. Try /start, /bignews, or /shutup.", chat_id=chat_id)
    return jsonify({'ok': True})


# ---------------------------------------------------------------------------
# Sending / Posting News
# ---------------------------------------------------------------------------

def send_morning_digest() -> None:
    if is_bot_quiet():
        logger.info("Bot quiet; skipping morning digest.")
        return
    try:
        logger.info("Preparing morning digest...")
        digest = build_digest()
        success = send_telegram_message(digest.text)
        if success:
            logger.info("Sent morning digest (%d titles).", len(digest.included_titles))
            _mark_titles_as_sent(digest.included_titles)
        else:
            logger.error("Failed to send morning digest.")
    except Exception:  # noqa: BLE001
        logger.exception("Error sending morning digest")


def post_news() -> None:
    if is_bot_quiet():
        logger.info("Bot quiet; skipping hourly news fetch.")
        return
    try:
        igaming = get_igaming_news(mark_sent=False)
        cnbc = get_cnbc_crypto_news(mark_sent=False)
        crunchbase = get_crunchbase_news(mark_sent=False)
        wsj = get_wsj_news(mark_sent=False)
        medium = get_medium_news(mark_sent=False)
        cryptoheadlines = get_cryptoheadlines_news(mark_sent=False)
        defiant = get_defiant_newsletter_news(mark_sent=False)
        ecuador_mining = get_ecuador_mining_news(mark_sent=False)
        logger.info("Hourly fetch: %d iGaming, %d CNBC, %d Crunchbase, %d WSJ, %d Medium, %d CryptoHeadlines, %d Defiant, %d Ecuador Mining (unfiltered).", len(igaming), len(cnbc), len(crunchbase), len(wsj), len(medium), len(cryptoheadlines), len(defiant), len(ecuador_mining))
    except Exception:  # noqa: BLE001
        logger.exception("Error in post_news")


# ---------------------------------------------------------------------------
# Scheduling Loop
# ---------------------------------------------------------------------------

_last_status_lock = threading.Lock()
_last_status = {
    'igaming_rss': None,
    'pitchbook_rss': None,
    'pitchbook_rss2json': None,
    'pitchbook_html': None,
    'cnbc_html': None,
}

_last_digest_date_lock = threading.Lock()
_last_digest_date: Optional[str] = None


def should_send_morning_digest(now: Optional[datetime] = None) -> bool:
    if now is None:
        now = datetime.now(TZ)
    return now.hour == 9 and now.minute == 0


def main_loop() -> None:
    global _last_digest_date
    logger.info("Bot main loop running (hourly checks; 60s poll).")
    poll_interval = 60
    hourly_counter = 0
    while True:
        try:
            now_local = datetime.now(TZ)
            if should_send_morning_digest(now_local):
                today_str = now_local.strftime('%Y-%m-%d')
                with _last_digest_date_lock:
                    if _last_digest_date != today_str:
                        logger.info("It's 9:00 AM local; sending daily digest.")
                        send_morning_digest()
                        _last_digest_date = today_str
                    else:
                        logger.debug("Digest already sent today (%s).", today_str)
            if hourly_counter >= 3600:
                post_news()
                hourly_counter = 0
            time.sleep(poll_interval)
            hourly_counter += poll_interval
        except Exception:  # noqa: BLE001
            logger.exception("Error in main loop; retrying in 60s")
            time.sleep(60)


# ---------------------------------------------------------------------------
# Flask Thread Wrapper
# ---------------------------------------------------------------------------

def run_flask() -> None:
    port = int(os.environ.get('PORT', 8080))
    logger.info("Starting Flask keep-alive server on port %s", port)
    app.run(host='0.0.0.0', port=port, threaded=True)


# ---------------------------------------------------------------------------
# Entrypoint
# ---------------------------------------------------------------------------

def main() -> None:
    logger.info("🚀 Starting MT Updates Bot...")
    flask_thread = threading.Thread(target=run_flask, name="FlaskThread", daemon=True)
    flask_thread.start()
    now_local = datetime.now(TZ)
    if not (now_local.hour == 9 and now_local.minute < 5):
        logger.info("Sending initial startup digest...")
        send_morning_digest()
    else:
        logger.info("Skipping startup digest (near scheduled 9am).")
    main_loop()


if __name__ == "__main__":
    main()

