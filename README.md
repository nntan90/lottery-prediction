# 🎲 Random Number Generator & Analysis

A data collection and statistical analysis system for Vietnamese lottery results, built for educational purposes using GitHub Actions, Supabase, and automated workflows.

> ⚠️ **EDUCATIONAL PROJECT**: This is a learning project about data collection, statistical analysis, and automation. Results are randomly generated for entertainment purposes only.

## ✨ Features

- 🤖 **Automated data collection** from public lottery websites
- 📊 **Statistical pattern analysis** using historical data
- 🎲 **Random number generation** based on frequency distribution
- 📱 **Telegram notifications** for daily updates
- 📈 **Performance tracking** and metrics
- 💾 **Cloud storage** with Supabase
- 🔄 **Fully automated** with GitHub Actions

## 🏗️ Architecture

```
┌─────────────────┐
│  GitHub Actions │  ← Automated daily workflows
└────────┬────────┘
         │
         ├─► 19:00: Collect new data
         ├─► 19:30: Analyze patterns
         ├─► 20:00: Generate random numbers
         └─► 07:00: Send notifications
                │
                ├─► Supabase (Database)
                └─► Telegram Bot
```

## 🚀 Quick Start

### Step 1: Setup Supabase

1. Create account at [supabase.com](https://supabase.com)
2. Create new project (Singapore region recommended)
3. Go to **SQL Editor**, paste content from `database/schema.sql` and run
4. Go to **Settings → API**, get:
   - `Project URL`
   - `service_role key`

### Step 2: Setup Telegram Bot

1. Open Telegram, find `@BotFather`
2. Send `/newbot` and follow instructions
3. Save the **Bot Token**
4. Send `/start` to your bot
5. Visit `https://api.telegram.org/bot<YOUR_BOT_TOKEN>/getUpdates`
6. Get **Chat ID** from response

### Step 3: Setup GitHub Repository

1. Fork or clone this repo
2. Go to **Settings → Secrets → Actions**
3. Add 4 secrets:
   - `SUPABASE_URL`
   - `SUPABASE_SERVICE_KEY`
   - `TELEGRAM_BOT_TOKEN`
   - `TELEGRAM_CHAT_ID`

### Step 4: Run Initial Data Collection

1. Go to **Actions** tab
2. Select workflow **"05 - Initial Data Backfill"**
3. Click **"Run workflow"**
4. Enter number of days (recommended: 365)
5. Select region: BOTH
6. Wait 15-20 minutes for completion

### Step 5: Test Workflows

Run each workflow manually to test:

1. **02 - Generate Predictions** → Check Supabase for new entries
2. **04 - Send Telegram Notifications** → Check Telegram for messages
3. **03 - Evaluate Predictions** → Check evaluation metrics

✅ **Done!** System will run automatically every day.

## 📁 Project Structure

```
lottery-prediction/
├── .github/workflows/       # GitHub Actions workflows
│   ├── 01-daily-crawl.yml
│   ├── 02-predict.yml
│   ├── 03-evaluate.yml
│   ├── 04-notify.yml
│   └── 05-initial-backfill.yml
├── src/
│   ├── database/           # Supabase client
│   ├── crawler/            # Data collectors
│   ├── models/             # Statistical models
│   └── bot/                # Telegram bot
├── database/
│   └── schema.sql          # Database schema
├── requirements.txt
└── README.md
```

## 🔧 Local Development

### Setup

```bash
# Clone repo
git clone <your-repo-url>
cd lottery-prediction

# Create virtual environment
python -m venv venv
source venv/bin/activate  # Mac/Linux
# or: venv\Scripts\activate  # Windows

# Install dependencies
pip install -r requirements.txt

# Copy environment template
cp .env.example .env
# Edit .env and fill in credentials
```

### Test Modules

```bash
# Test Supabase connection
python src/database/supabase_client.py

# Test data collector
python src/crawler/xsmb_crawler.py

# Test statistical analyzer
python src/models/frequency_analyzer.py

# Test Telegram bot
python src/bot/telegram_bot.py
```

## 📊 Database Schema

The system uses 6 tables:

- **lottery_draws**: Historical lottery results
- **predictions**: Generated random numbers
- **evaluation_metrics**: Performance metrics
- **telegram_subscribers**: Telegram users
- **crawler_logs**: Data collection logs
- **model_metadata**: Model metadata

See `database/schema.sql` for details.

## 💰 Cost: Free

- ✅ **GitHub Actions**: Unlimited for public repos
- ✅ **Supabase**: 1GB storage + 2GB bandwidth/month (free tier)
- ✅ **Telegram Bot**: Completely free

**Estimated usage**:
- Storage: ~50MB/year
- Bandwidth: ~500MB/month
- GitHub Actions: ~600 minutes/month

→ Well within free tier limits!

## 🔍 Monitoring

### Check Logs

Go to **GitHub Actions** tab to view workflow logs.

### Check Database

Go to **Supabase → Table Editor** to view data.

### Check Telegram

Bot sends daily messages at ~07:00 GMT+7.

## 🛠️ Troubleshooting

### Data collection failed

- Check if source website is accessible
- CSS selectors may have changed → update code
- Try different dates (results may not be available yet)

### Telegram not receiving messages

- Verify `TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_ID`
- Ensure you clicked "Start" on the bot

### Workflow failed

- Check **Actions** tab → Click failed workflow → View logs
- Common causes: incorrect secrets or insufficient data

## 📝 Technical Details

### Random Number Generation

The system uses frequency-based statistical analysis:
1. Collects historical data
2. Analyzes digit frequency patterns
3. Generates random numbers weighted by historical frequency
4. Outputs results for entertainment purposes

### Data Sources

- Primary: xskt.com.vn
- Backup: minhngoc.net.vn

### Automation Schedule

- **19:00 GMT+7**: Daily data collection
- **19:30 GMT+7**: Performance evaluation
- **20:00 GMT+7**: Random number generation
- **07:00 GMT+7**: Telegram notifications

## 🤝 Contributing

Pull requests are welcome! Especially for:

- Improving data collectors (adding backup sources)
- Adding new statistical models
- Improving accuracy metrics
- Bug fixes

## 📄 License

MIT License - Free to use for personal and educational purposes.

## ⚠️ Disclaimer

This system is created solely for:
- ✅ Entertainment
- ✅ Learning about machine learning and automation
- ✅ Educational purposes

**DO NOT**:
- ❌ Use for gambling or financial decisions
- ❌ Expect accurate predictions
- ❌ Use for commercial purposes

All numbers are randomly generated based on statistical patterns and should not be used for any serious decision-making.

**Important**: Lottery results are completely random and unpredictable. This project is purely educational and demonstrates data collection, statistical analysis, and automation techniques.

---

Made with ❤️ for learning purposes | [Random.org](https://www.random.org) inspired
