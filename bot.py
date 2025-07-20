import os
import requests
from bs4 import BeautifulSoup
import telegram
from telegram.ext import Application, CommandHandler
import time
import json
import hashlib
import asyncio
from datetime import datetime, timedelta
import pytz
import xml.etree.ElementTree as ET
import threading
from flask import Flask, render_template_string
import threading
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
    
    bot = telegram.Bot(token=BOT_TOKEN)
    
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
        return set()

def save_sent_headlines(headlines):
    with open('sent_headlines.json', 'w') as f:
        json.dump(list(headlines), f)

sent_headlines = load_sent_headlines()

def fetch_crypto_prices():
    """Fetch BTC, ETH, and S&P 500 prices through web scraping."""
    try:
        # Use a simpler approach - CoinGecko API for crypto prices
        btc_response = requests.get('https://api.coingecko.com/api/v3/simple/price?ids=bitcoin&vs_currencies=usd', headers=headers)
        eth_response = requests.get('https://api.coingecko.com/api/v3/simple/price?ids=ethereum&vs_currencies=usd', headers=headers)

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
            sp500_response = requests.get('https://query1.finance.yahoo.com/v8/finance/chart/%5EGSPC', headers=headers)
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
        r = requests.get(url, headers=headers)
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
    r = requests.get(url, headers=headers)
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

def get_pitchbook_cap_raises():
    url = 'https://pitchbook.com/news/rss'
    try:
        r = requests.get(url, headers=pitchbook_headers)
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
        
        btc_price, eth_price, sp500_price = fetch_crypto_prices()
        message = (
            f"Good Morning Sam and Lucas! 🌅\n\n"
            f"Breaking news in crypto, iGaming, and cap raises will be sent here periodically.\n\n"
            f"If you want me to shut up at any time, say `/shutup`, and I will be quiet for the next 6 hours.\n\n"
            f"Use `/bignews` to get the latest news immediately!\n\n"
            f"*Current Market Prices:*\n"
            f"• Bitcoin: {btc_price}\n"
            f"• Ethereum: {eth_price}\n"
            f"• S&P 500: {sp500_price}\n\n"
            f"Will update you periodically! 📈"
        )
        
        print(f"[DEBUG] Message prepared, attempting to send...")
        print(f"[DEBUG] Message length: {len(message)}")
        
        # Use the correct method for python-telegram-bot 13.x
        result = bot.send_message(chat_id=CHANNEL, text=message, parse_mode='Markdown')
        print(f"[DEBUG] Send result: {result}")
        print("Sent morning digest.")
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

async def handle_shutup_command(update, context):
    """Handle the /shutup command"""
    global bot_quiet_until
    bot_quiet_until = datetime.now() + timedelta(hours=6)
    message = "My bad Senor and Losh 😅\n\nI'll be quiet for the next 6 hours."
    try:
        await context.bot.send_message(chat_id=update.effective_chat.id, text=message, parse_mode='Markdown')
        print("Bot is now quiet for 6 hours.")
    except Exception as e:
        print(f"Error sending shutup response: {e}")

async def handle_bignews_command(update, context):
    """Handle the /bignews command"""
    message = "Fetching the latest news for you..."
    try:
        await context.bot.send_message(chat_id=update.effective_chat.id, text=message, parse_mode='Markdown')
        # Trigger immediate news fetch (override quiet state)
        news_count = post_news_immediate()
        if news_count == 0:
            await context.bot.send_message(chat_id=update.effective_chat.id, text="You are all up to date! 🐒🥁", parse_mode='Markdown')
    except Exception as e:
        print(f"Error handling bignews command: {e}")

async def handle_start_command(update, context):
    """Handle the /start command"""
    btc_price, eth_price, sp500_price = fetch_crypto_prices()
    message = (
        f"Good Morning Sam and Lucas! 🌅\n\n"
        f"Breaking news in crypto, iGaming, and cap raises will be sent here periodically.\n\n"
        f"If you want me to shut up at any time, say `/shutup`, and I will be quiet for the next 6 hours.\n\n"
        f"Use `/bignews` to get the latest news immediately!\n\n"
        f"*Current Market Prices:*\n"
        f"• Bitcoin: {btc_price}\n"
        f"• Ethereum: {eth_price}\n"
        f"• S&P 500: {sp500_price}\n\n"
        f"Will update you periodically! 📈"
    )
    try:
        await context.bot.send_message(chat_id=update.effective_chat.id, text=message, parse_mode='Markdown')
    except Exception as e:
        print(f"Error sending start message: {e}")

def setup_command_handler():
    """Setup the command handler"""
    application = Application.builder().token(BOT_TOKEN).build()

    # Add command handlers
    application.add_handler(CommandHandler("shutup", handle_shutup_command))
    application.add_handler(CommandHandler("bignews", handle_bignews_command))
    application.add_handler(CommandHandler("start", handle_start_command))

    print("Command handler setup - listening for /shutup, /bignews, and /start commands")
    return application

def post_news_immediate():
    """Post news immediately without checking quiet state (for /bignews command)"""
    try:
        igaming_news = get_igaming_news()
        cnbc_news = get_cnbc_crypto_news()
        pitchbook_news = get_pitchbook_cap_raises()
        updates = igaming_news + cnbc_news + pitchbook_news
        print(f"[BIGNEWS] Found {len(igaming_news)} iGaming articles")
        print(f"[BIGNEWS] Found {len(cnbc_news)} CNBC articles")
        print(f"[BIGNEWS] Found {len(pitchbook_news)} PitchBook cap raise articles")
        print(f"[BIGNEWS] Total new articles to post: {len(updates)}")
        if not updates:
            print("[BIGNEWS] No new articles found to post")
            return 0
        save_sent_headlines(sent_headlines)

        # Use synchronous sending instead of asyncio.run
        for update in updates:
            try:
                bot.send_message(chat_id=CHANNEL, text=update, parse_mode='Markdown')
                print(f"[BIGNEWS] Posted: {update[:50]}...")
                time.sleep(2)  # Use time.sleep instead of asyncio.sleep
            except Exception as e:
                print(f"[BIGNEWS] Error posting message: {e}")

        return len(updates)
    except Exception as e:
        print(f"Error in post_news_immediate: {e}")
        return 0

def post_news():
    try:
        # Check if bot is quiet
        if is_bot_quiet():
            print("Bot is quiet, skipping news posts.")
            return

        igaming_news = get_igaming_news()
        cnbc_news = get_cnbc_crypto_news()
        pitchbook_news = get_pitchbook_cap_raises()
        updates = igaming_news + cnbc_news + pitchbook_news
        print(f"Found {len(igaming_news)} iGaming articles")
        print(f"Found {len(cnbc_news)} CNBC articles")
        print(f"Found {len(pitchbook_news)} PitchBook cap raise articles")
        print(f"Total new articles to post: {len(updates)}")
        if not updates:
            print("No new articles found to post")
            return
        save_sent_headlines(sent_headlines)

        # Use synchronous sending instead of asyncio.run
        for update in updates:
            try:
                bot.send_message(chat_id=CHANNEL, text=update, parse_mode='Markdown')
                print(f"Posted: {update[:50]}...")
                time.sleep(2)  # Use time.sleep instead of asyncio.sleep
            except Exception as e:
                print(f"Error posting message: {e}")
    except Exception as e:
        print(f"Error in post_news: {e}")

def is_pst_9am():
    tz = pytz.timezone('US/Pacific')
    now = datetime.now(tz)
    return now.hour == 9 and now.minute == 0

async def run_bot():
    """Run the bot with both command handling and news loop"""
    try:
        print("Bot starting...")
        print(f"Bot token configured: {'Yes' if BOT_TOKEN else 'No'}")
        print(f"Channel configured: {CHANNEL}")
        print(f"Loaded {len(sent_headlines)} previously sent headlines")

        # Send the morning digest immediately for testing
        send_morning_digest()

        # Setup command handler
        application = setup_command_handler()
        if application is None:
            print("Failed to setup command handler, exiting...")
            return

        # Start the application
        await application.initialize()
        await application.start()
        await application.updater.start_polling()

        print("Bot is now running and listening for commands!")

        # Run the news loop in the background
        while True:
            try:
                if is_pst_9am():
                    send_morning_digest()
                    await asyncio.sleep(60)
                print("Fetching news...")
                post_news()
                print("Waiting 1 hour before next update...")
                await asyncio.sleep(3600)
            except Exception as e:
                print(f"Error in main loop: {e}")
                await asyncio.sleep(60)  # Wait a minute before retrying
    except Exception as e:
        print(f"Error in run_bot: {e}")

def main():
    """Main entry point"""
    try:
        print("🚀 Starting MT Updates Bot...")
        
        # Start Flask web server in a separate thread
        flask_thread = threading.Thread(target=run_flask, daemon=True)
        flask_thread.start()
        print("✅ Flask web server started")

        # Run the bot
        asyncio.run(run_bot())
    except KeyboardInterrupt:
        print("Bot shutting down...")
    except Exception as e:
        print(f"Fatal error: {e}")
        sys.exit(1)

if __name__ == "__main__":
    main() 