# MT Updates Bot

A Telegram bot that monitors and sends updates about crypto, iGaming, and capital raises.

## 🚀 How to Keep Alive on Replit

### Method 1: Use Replit's Built-in Keep Alive (Recommended)
1. The `.replit` file is already configured
2. Simply run your bot and it will stay alive
3. Replit will automatically restart it if it goes down

### Method 2: Web Server Keep Alive
- The bot now includes a Flask web server that runs on port 8080
- This creates a web endpoint that helps keep the repl active
- Access your bot's web interface at: `https://your-repl-name.your-username.repl.co`

### Method 3: External Keep Alive Service
You can use external services to ping your bot:

#### Option A: UptimeRobot (Free)
1. Go to [UptimeRobot](https://uptimerobot.com/)
2. Create a free account
3. Add a new monitor
4. Set the URL to: `https://your-repl-name.your-username.repl.co/health`
5. Set check interval to 5 minutes

#### Option B: Cron-job.org (Free)
1. Go to [Cron-job.org](https://cron-job.org/)
2. Create an account
3. Add a new cronjob
4. Set URL to: `https://your-repl-name.your-username.repl.co/health`
5. Set schedule to every 5 minutes

### Method 4: Use the Keep Alive Script
1. Update the URL in `keep_alive.py` with your actual repl URL
2. Run it on another service (like a VPS or another repl)

## 🔧 Setup Instructions

1. Set environment variables in Replit:
   - `BOT_TOKEN`: Your Telegram bot token
   - `CHANNEL`: Your Telegram channel ID

2. Install dependencies:
   ```bash
   pip install -r requirements.txt
   ```

3. Test your setup:
   ```bash
   python test_bot.py
   ```

4. Run the bot:
   ```bash
   python bot_simple.py
   ```

## 🛠️ Troubleshooting

### "Internal Server Error" or Startup Issues

1. **Test your environment first:**
   ```bash
   python test_bot.py
   ```

2. **Check environment variables:**
   - Go to Tools > Secrets in Replit
   - Make sure `BOT_TOKEN` and `CHANNEL` are set
   - Values should not have quotes around them

3. **Install missing packages:**
   ```bash
   pip install -r requirements.txt
   ```

4. **Common issues:**
   - **Missing BOT_TOKEN**: Get from @BotFather on Telegram
   - **Missing CHANNEL**: Should be like `-1001234567890` (negative number)
   - **Import errors**: Run `pip install -r requirements.txt`
   - **Network timeouts**: The bot will retry automatically

5. **If still having issues:**
   - Check the console output for specific error messages
   - Make sure your bot token is valid
   - Ensure your bot is added to the channel as an admin

## 📱 Bot Commands

- `/start` - Get welcome message and current market prices
- `/bignews` - Get latest news immediately
- `/shutup` - Make bot quiet for 6 hours

## 🌐 Web Endpoints

- `/` - Bot status page
- `/health` - Health check endpoint (returns JSON)

## ⚠️ Important Notes

- Replit free tier has limitations on uptime
- Consider upgrading to Replit Hacker plan for better reliability
- The web server method is the most reliable for keeping the bot alive
- Always test your bot after deployment to ensure it's working correctly
- The bot will automatically retry on errors and continue running
