"""
Script crawl XSMN (Miền Nam) - chạy bởi GitHub Actions job 01
"""
import asyncio
import sys
import os
from datetime import datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from src.crawler.xsmn_crawler import XSMNCrawler
from src.database.supabase_client import LotteryDB
from src.bot.telegram_bot import LotteryNotifier


async def main():
    print('🚀 Starting XSMN crawler...')

    crawler = XSMNCrawler()
    db = LotteryDB()
    try:
        bot = LotteryNotifier()
    except Exception as e:
        print(f'⚠️ Could not init bot: {e}')
        bot = None

    # Use Vietnam Time (UTC+7)
    vn_time = datetime.utcnow() + timedelta(hours=7)
    today = vn_time.date()
    print(f'Current Vietnam Time: {vn_time}')
    print(f'Crawling for date: {today}')

    print(f'Target: Fetching all provinces for {today} in one request...')
    results_list = crawler.fetch_batch_results(today)

    success_count = 0
    if results_list:
        for res in results_list:
            try:
                db.upsert_draw(res)
                success_count += 1
                print(f"   ✅ Saved {res['province']}")
            except Exception as e:
                print(f"   ❌ Error saving {res['province']}: {e}")

    if success_count > 0:
        db.log_crawler_status({
            'crawl_date': today,
            'region': 'XSMN',
            'status': 'success',
            'records_inserted': success_count,
            'error_message': f'Batch crawled {success_count} provinces'
        })
        msg = f'✅ <b>Crawl XSMN Success</b>\n📅 {today}\n📊 Saved: {success_count} provinces'
        print(msg)
        if bot:
            await bot.send_message(msg)

    else:
        msg = 'No data found (Holiday/Batch Mode)'
        db.log_crawler_status({
            'crawl_date': today,
            'region': 'XSMN',
            'status': 'success',
            'error_message': msg,
            'records_inserted': 0
        })
        print(f'⚠️ {msg}')
        if bot:
            await bot.send_message(
                f'⚠️ <b>XSMN: No data found</b>\n📅 {today}\n(Likely holiday/off)'
            )


if __name__ == '__main__':
    asyncio.run(main())
