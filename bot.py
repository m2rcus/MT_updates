import os
import requests
from bs4 import BeautifulSoup
import time
import json
import asyncio
from datetime import datetime, timedelta
import pytz
import xml.etree.ElementTree as ET
import threading
from flask import Flask, render_template_string, request, jsonify
import sys

# Check environment variables first
try:
    BOT_TOKEN = os.environ.get('BOT_TOKEN')
    CHANNEL = os.environ.get('CHANNEL')
    
    if not BOT_TOKEN:
        print("❌ ERROR: BOT_TOKEN environment variable is not set!")
        print("Please set BOT_TOKEN in your deployment environment variables.")
        sys.exit(1)
    
    if not CHANNEL:
        print("❌ ERROR: CHANNEL environment variable is not set!")
        print("Please set CHANNEL in your deployment environment variables.")
        sys.exit(1)
    
    print(f"✅ BOT_TOKEN: {'Set' if BOT_TOKEN else 'Not set'}")
    print(f"✅ CHANNEL: {CHANNEL}")
    
except Exception as e:
    print(f"❌ Error setting up bot: {e}")
    sys.exit(1)

# Flask app for keep-alive
app = Flask(__name__)

@app.route('/')
def home():
    return render_template_string('''
    <!DOCTYPE html>
    <html>
    <head>
        <title>MT Updates Bot</title>
        <style>
            body { font-family: Arial, sans-serif; margin: 40px; background: #f0f0f0; }
            .container { background: white; padding: 30px; border-radius: 10px; box-shadow: 0 2px 10px rgba(0,0,0,0.1); }
            .status { color: #28a745; font-weight: bold; }
        </style>
    </head>
    <body>
        <div class="container">
            <h1>🤖 MT Updates Bot</h1>
            <p class="status">✅ Bot is running and active!</p>
            <p>This page helps keep the bot alive.</p>
            <p><small>Last updated: {{ datetime.now().strftime('%Y-%m-%d %H:%M:%S') }}</small></p>
        </div>
    </body>
    </html>
    ''')

@app.route('/health')
def health():
    return {'status': 'healthy', 'timestamp': datetime.now().isoformat()}

def build_digest():
    btc_price, eth_price, sp500_price = fetch_crypto_prices()
    # Only include headlines not in sent_headlines
    igaming_news_all = get_igaming_news()
    cnbc_news_all = get_cnbc_crypto_news()
    pitchbook_news_all = get_pitchbook_cap_raises()
    igaming_news = [h for h in igaming_news_all if h not in sent_headlines]
    cnbc_news = [h for h in cnbc_news_all if h not in sent_headlines]
    pitchbook_news = [h for h in pitchbook_news_all if h not in sent_headlines]

    # Preview headlines (first from iGaming and PitchBook if available)
    preview = []
    if igaming_news:
        import re
        m = re.match(r"^.*\*iGaming Business\*\\n\[(.*?)\]", igaming_news[0])
        if m:
            preview.append(f"iGaming: {m.group(1)}")
        else:
            preview.append("iGaming: (headline)")
    if pitchbook_news:
        m = re.match(r"^.*\*PitchBook Cap Raise\*\\n\[(.*?)\]", pitchbook_news[0])
        if m:
            preview.append(f"PitchBook: {m.group(1)}")
        else:
            preview.append("PitchBook: (headline)")
    if not preview:
        preview.append("No top headlines today.")

    def format_section(title, news):
        if news:
            return f"*{title}:*\n" + "\n".join(news)
        else:
            return f"*{title}:*\n_No pertinent news_"

    digest = (
        "🌅 Good Morning Sam and Lucas! Here’s your daily digest:\n\n"
        f"*Crypto Prices:*\n"
        f"• Bitcoin: {btc_price}\n"
        f"• Ethereum: {eth_price}\n"
        f"• S&P 500: {sp500_price}\n\n"
        f"*Top Headlines Preview:*\n" + "\n".join(preview) + "\n\n"
        + format_section("iGaming News", igaming_news) + "\n\n"
        + format_section("PitchBook News", pitchbook_news) + "\n\n"
        + format_section("CNBC Crypto News", cnbc_news)
    )
    # Return both the digest and the headlines included
    included_headlines = igaming_news + pitchbook_news + cnbc_news
    return digest, included_headlines

@app.route('/webhook', methods=['POST'])
def telegram_webhook():
    data = request.get_json()
    print(f"[DEBUG] Webhook received: {data}")
    if 'message' in data:
        message = data['message']
        chat_id = message['chat']['id']
        text = message.get('text', '').strip()
        if text == '/start':
            send_telegram_message(welcome_message(), chat_id=chat_id)
        elif text == '/bignews':
            send_telegram_message("Fetching the latest news for you...", chat_id=chat_id)
            digest, included_headlines = build_digest()
            send_telegram_message(digest, chat_id=chat_id)
            # Mark as sent
            for h in included_headlines:
                sent_headlines.add(h)
            save_sent_headlines(sent_headlines)
        elif text == '/shutup':
            global bot_quiet_until
            bot_quiet_until = datetime.now() + timedelta(hours=6)
            send_telegram_message("My bad Senor and Losh 😅\n\nI'll be quiet for the next 6 hours.", chat_id=chat_id)
        else:
            send_telegram_message("Unknown command. Try /start, /bignews, or /shutup.", chat_id=chat_id)
    return jsonify({'ok': True})

def run_flask():
    """Run Flask app in a separate thread"""
    port = int(os.environ.get('PORT', 8080))
    app.run(host='0.0.0.0', port=port)

# Global variable to track if bot is quiet
bot_quiet_until = None

# Browser headers to avoid 403 Forbidden errors
headers = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36',
    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8',
    'Accept-Language': 'en-US,en;q=0.5',
    'Accept-Encoding': 'gzip, deflate',
    'Connection': 'keep-alive',
    'Upgrade-Insecure-Requests': '1',
}

# Special headers for iGaming Business
igaming_headers = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36',
    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8',
    'Accept-Language': 'en-US,en;q=0.9',
    'Accept-Encoding': 'gzip, deflate, br',
    'Connection': 'keep-alive',
    'Upgrade-Insecure-Requests': '1',
}

# Special headers for PitchBook
pitchbook_headers = {
    'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.7',
    'Accept-Language': 'en-US,en;q=0.9',
    'Accept-Encoding': 'gzip, deflate, br',
    'DNT': '1',
    'Connection': 'keep-alive',
    'Upgrade-Insecure-Requests': '1',
    'Sec-Fetch-Dest': 'document',
    'Sec-Fetch-Mode': 'navigate',
    'Sec-Fetch-Site': 'none',
    'Sec-Fetch-User': '?1',
    'Cache-Control': 'max-age=0',
}

# Load sent headlines from file to persist across restarts
def load_sent_headlines():
    try:
        with open('sent_headlines.json', 'r') as f:
            return set(json.load(f))
    except FileNotFoundError:
        print("No existing sent_headlines.json found, starting fresh.")
        return set()
    except Exception as e:
        print(f"Error loading sent headlines: {e}, starting fresh.")
        return set()

def save_sent_headlines(headlines):
    try:
        with open('sent_headlines.json', 'w') as f:
            json.dump(list(headlines), f)
    except Exception as e:
        print(f"Error saving sent headlines: {e}")

sent_headlines = load_sent_headlines()

def send_telegram_message(message, chat_id=None):
    """Send message to Telegram using simple HTTP requests"""
    try:
        url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
        data = {
            'chat_id': chat_id if chat_id else CHANNEL,
            'text': message,
            'parse_mode': 'Markdown'
        }
        response = requests.post(url, data=data, timeout=10)
        if response.status_code == 200:
            print(f"✅ Message sent successfully")
            return True
        else:
            print(f"❌ Failed to send message: {response.status_code} - {response.text}")
            return False
    except Exception as e:
        print(f"❌ Error sending message: {e}")
        return False

def welcome_message():
    return (
        "Good Morning Sam and Lucas! 🌅\n\n"
        "Breaking news in crypto, iGaming, and cap raises will be sent here periodically.\n\n"
        "*Bot Features:*\n"
        "• `/start` - Get this welcome message and current market prices\n"
        "• `/bignews` - Get the latest news immediately\n"
        "• `/shutup` - Make me quiet for 6 hours\n\n"
        "*Current Market Prices:*\n"
        f"• Bitcoin: {fetch_crypto_prices()[0]}\n"
        f"• Ethereum: {fetch_crypto_prices()[1]}\n"
        f"• S&P 500: {fetch_crypto_prices()[2]}\n\n"
        "Will update you periodically! 📈"
    )

def get_all_news():
    igaming_news = get_igaming_news()
    cnbc_news = get_cnbc_crypto_news()
    pitchbook_news = get_pitchbook_cap_raises()
    return igaming_news + cnbc_news + pitchbook_news

def fetch_crypto_prices():
    """Fetch BTC, ETH, and S&P 500 prices through web scraping."""
    try:
        btc_response = requests.get('https://api.coingecko.com/api/v3/simple/price?ids=bitcoin&vs_currencies=usd', headers=headers, timeout=10)
        eth_response = requests.get('https://api.coingecko.com/api/v3/simple/price?ids=ethereum&vs_currencies=usd', headers=headers, timeout=10)

        print(f"[DEBUG] BTC status: {btc_response.status_code}, response: {btc_response.text}")
        print(f"[DEBUG] ETH status: {eth_response.status_code}, response: {eth_response.text}")

        if btc_response.status_code == 200 and eth_response.status_code == 200:
            btc_data = btc_response.json()
            eth_data = eth_response.json()
            btc_price = f"${btc_data['bitcoin']['usd']:,.2f}"
            eth_price = f"${eth_data['ethereum']['usd']:,.2f}"
        else:
            btc_price = "N/A"
            eth_price = "N/A"

        # For S&P 500, try a simpler approach
        try:
            sp500_response = requests.get('https://query1.finance.yahoo.com/v8/finance/chart/%5EGSPC', headers=headers, timeout=10)
            if sp500_response.status_code == 200:
                sp500_data = sp500_response.json()
                sp500_price = f"${sp500_data['chart']['result'][0]['meta']['regularMarketPrice']:,.2f}"
            else:
                sp500_price = "N/A"
        except:
            sp500_price = "N/A"

        return btc_price, eth_price, sp500_price
    except Exception as e:
        print(f"Error fetching prices: {e}")
        return 'N/A', 'N/A', 'N/A'

def get_igaming_news():
    """Get iGaming news from RSS feed"""
    url = 'https://igamingbusiness.com/feed/'
    try:
        r = requests.get(url, headers=igaming_headers, timeout=10)
        print(f"[DEBUG] iGaming Business RSS response status: {r.status_code}")
        if r.status_code != 200:
            print(f"[DEBUG] iGaming Business RSS request failed: {r.status_code}")
            return []

        # Parse RSS feed
        root = ET.fromstring(r.content)
        articles = root.findall('.//item')
        print(f"[DEBUG] Found {len(articles)} raw iGaming Business articles")

        # Filter for important keywords
        important_keywords = [
            'breaking', 'major', 'launch', 'acquisition', 'merger', 'regulation', 
            'partnership', 'expansion', 'funding', 'investment', 'deal', 'announcement',
            'strategic', 'milestone', 'record', 'growth', 'new market'
        ]

        news = []
        skipped = 0
        important_count = 0
        # Check the 10 most recent articles
        for article in articles[:10]:
            title_elem = article.find('title')
            link_elem = article.find('link')
            if title_elem is not None and link_elem is not None:
                title = title_elem.text.strip()
                link = link_elem.text.strip()
                title_lower = title.lower()
                # Check if article contains important keywords
                matched_keywords = [kw for kw in important_keywords if kw in title_lower]
                print(f"[DEBUG] Checking iGaming article: {title}")
                if matched_keywords:
                    print(f"[DEBUG] Matched keywords: {matched_keywords}")
                is_important = bool(matched_keywords)
                if title not in sent_headlines:
                    if is_important:
                        sent_headlines.add(title)
                        news.append(f"📰 *iGaming Business*\n[{title}]({link})")
                        important_count += 1
                        print(f"[DEBUG] Added important iGaming article: {title}")
                    else:
                        print(f"[DEBUG] Skipped non-important iGaming article: {title}")
                else:
                    skipped += 1
                    print(f"[DEBUG] Skipped iGaming article (already sent): {title}")
        print(f"[DEBUG] iGaming Business: {important_count} important new, {skipped} skipped (already sent)")
        return news
    except Exception as e:
        print(f"Error fetching iGaming Business RSS: {e}")
        return []

def get_cnbc_crypto_news():
    url = 'https://www.cnbc.com/cryptoworld/'
    try:
        r = requests.get(url, headers=headers, timeout=10)
        print(f"[DEBUG] CNBC response status: {r.status_code}")
        if r.status_code != 200:
            print(f"[DEBUG] CNBC request failed: {r.status_code}")
            return []
        print(f"[DEBUG] CNBC HTML snippet: {r.text[:500]}...")
        soup = BeautifulSoup(r.text, 'html.parser')
        articles = soup.select('a.Card-title')
        print(f"[DEBUG] Found {len(articles)} raw CNBC articles")
        news = []
        skipped = 0
        for a in articles:
            title = a.get_text(strip=True)
            link = a['href']
            if link.startswith('/'):
                link = f'https://www.cnbc.com{link}'
            if title not in sent_headlines:
                sent_headlines.add(title)
                news.append(f"💰 *CNBC Crypto World*\n[{title}]({link})")
            else:
                skipped += 1
                print(f"[DEBUG] Skipped CNBC article (already sent): {title}")
        print(f"[DEBUG] CNBC: {len(news)} new, {skipped} skipped (already sent)")
        return news
    except Exception as e:
        print(f"Error fetching CNBC news: {e}")
        return []

def get_pitchbook_cap_raises():
    url = 'https://pitchbook.com/news/rss'
    try:
        r = requests.get(url, headers=pitchbook_headers, timeout=10)
        print(f"[DEBUG] PitchBook RSS response status: {r.status_code}")
        if r.status_code != 200:
            print(f"[DEBUG] PitchBook RSS request failed: {r.status_code}")
            return []
        # Parse RSS feed
        root = ET.fromstring(r.content)
        articles = root.findall('.//item')
        print(f"[DEBUG] Found {len(articles)} raw PitchBook RSS articles")
        keywords = ['crypto', 'blockchain', 'igaming', 'gambling']
        raise_terms = ['raise', 'funding', 'investment', 'seed', 'series a', 'series b', 'venture', 'capital']
        news = []
        skipped = 0
        for article in articles[:10]:
            title_elem = article.find('title')
            link_elem = article.find('link')
            if title_elem is not None and link_elem is not None:
                title = title_elem.text.strip()
                title_lower = title.lower()
                link = link_elem.text.strip()
                matched_keywords = [k for k in keywords if k in title_lower]
                matched_raises = [t for t in raise_terms if t in title_lower]
                print(f"[DEBUG] Checking PitchBook article: {title}")
                if matched_keywords and matched_raises:
                    print(f"[DEBUG] Matched keywords: {matched_keywords}, raise terms: {matched_raises}")
                is_important = bool(matched_keywords and matched_raises)
                if title not in sent_headlines:
                    if is_important:
                        sent_headlines.add(title)
                        news.append(f"🚀 *PitchBook Cap Raise*\n[{title}]({link})")
                        print(f"[DEBUG] Added important PitchBook article: {title}")
                    else:
                        print(f"[DEBUG] Skipped non-important PitchBook article: {title}")
                else:
                    skipped += 1
                    print(f"[DEBUG] Skipped PitchBook article (already sent): {title}")
        print(f"[DEBUG] PitchBook: {len(news)} important new, {skipped} skipped (already sent)")
        return news
    except Exception as e:
        print(f"Error fetching PitchBook RSS: {e}")
        return []

def send_morning_digest():
    try:
        print(f"[DEBUG] Starting morning digest...")
        print(f"[DEBUG] Channel ID: {CHANNEL}")
        print(f"[DEBUG] Bot token length: {len(BOT_TOKEN) if BOT_TOKEN else 0}")
        message, included_headlines = build_digest()
        print(f"[DEBUG] Message prepared, attempting to send...")
        print(f"[DEBUG] Message length: {len(message)}")
        success = send_telegram_message(message)
        if success:
            print("✅ Sent morning digest.")
            # Only mark as sent if actually sent
            for h in included_headlines:
                sent_headlines.add(h)
            save_sent_headlines(sent_headlines)
        else:
            print("❌ Failed to send morning digest.")
    except Exception as e:
        print(f"Error sending morning digest: {e}")
        print(f"[DEBUG] Exception type: {type(e)}")
        print(f"[DEBUG] Exception details: {str(e)}")

def is_bot_quiet():
    global bot_quiet_until
    if bot_quiet_until is None:
        return False
    if datetime.now() >= bot_quiet_until:
        bot_quiet_until = None
        return False
    return True

def post_news():
    try:
        # Check if bot is quiet
        if is_bot_quiet():
            print("Bot is quiet, skipping news posts.")
            return
        # Just fetch news, do not update sent_headlines
        get_igaming_news()
        get_cnbc_crypto_news()
        get_pitchbook_cap_raises()
        print(f"[DEBUG] Fetched news, no messages sent, sent_headlines not updated.")
    except Exception as e:
        print(f"Error in post_news: {e}")

def is_pst_9am():
    try:
        tz = pytz.timezone('US/Pacific')
        now = datetime.now(tz)
        return now.hour == 9 and now.minute == 0
    except Exception as e:
        print(f"Error checking PST time: {e}")
        return False

def main():
    """Main entry point"""
    try:
        print("🚀 Starting MT Updates Bot...")
        # Start Flask web server in a separate thread
        flask_thread = threading.Thread(target=run_flask, daemon=True)
        flask_thread.start()
        print("✅ Flask web server started")

        # Only send the morning digest if it's not 9am PST (let the scheduler handle the real one)
        if not is_pst_9am():
            print("📤 Sending initial morning digest...")
            send_morning_digest()

        print("✅ Bot is now running and will check for news every hour!")
        print(f"✅ Loaded {len(sent_headlines)} previously sent headlines")

        # Main loop
        while True:
            try:
                now_utc = datetime.utcnow()
                now_pst = datetime.now(pytz.timezone('US/Pacific'))
                print(f"[DEBUG] UTC now: {now_utc}, PST now: {now_pst}")
                # Check if it's 9 AM PST for morning digest
                if is_pst_9am():
                    print("🌅 It's 9 AM PST, sending morning digest...")
                    send_morning_digest()
                    time.sleep(60)  # Wait a minute to avoid multiple sends
                
                # Check for news
                print("📰 Fetching news...")
                post_news()
                
                print("⏰ Waiting 1 hour before next update...")
                time.sleep(3600)  # Wait 1 hour
                
            except Exception as e:
                print(f"Error in main loop: {e}")
                time.sleep(60)  # Wait a minute before retrying
                
    except KeyboardInterrupt:
        print("Bot shutting down...")
    except Exception as e:
        print(f"Fatal error: {e}")
        sys.exit(1)

if __name__ == "__main__":
    main() 