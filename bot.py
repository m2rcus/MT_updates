import os
import requests
from bs4 import BeautifulSoup
from telegram import Bot
import time

BOT_TOKEN = os.environ['BOT_TOKEN']
CHANNEL = os.environ['CHANNEL']

bot = Bot(token=BOT_TOKEN)

sent_headlines = set()

def get_igaming_news():
    url = 'https://www.igamingtoday.com/category/breaking-news/'
    r = requests.get(url)
    soup = BeautifulSoup(r.text, 'html.parser')
    articles = soup.select('.jeg_post_title > a')
    news = []
    for a in articles:
        title = a.get_text(strip=True)
        link = a['href']
        if title not in sent_headlines:
            sent_headlines.add(title)
            news.append(f"📰 *iGaming Today*\n[{title}]({link})")
    return news

def get_cnbc_crypto_news():
    url = 'https://www.cnbc.com/cryptoworld/'
    r = requests.get(url)
    soup = BeautifulSoup(r.text, 'html.parser')
    articles = soup.select('a.Card-title')
    news = []
    for a in articles:
        title = a.get_text(strip=True)
        link = a['href']
        if link.startswith('/'):
            link = f'https://www.cnbc.com{link}'
        if title not in sent_headlines:
            sent_headlines.add(title)
            news.append(f"💰 *CNBC Crypto World*\n[{title}]({link})")
    return news

def post_news():
    updates = get_igaming_news() + get_cnbc_crypto_news()
    for update in updates:
        try:
            bot.send_message(chat_id=CHANNEL, text=update, parse_mode='Markdown')
            print(f"Posted: {update}")
        except Exception as e:
            print(f"Error posting message: {e}")

def main():
    while True:
        post_news()
        time.sleep(300)

if __name__ == "__main__":
    main() 