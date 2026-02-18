"""
Script crawl XSMB (Miền Bắc) - chạy bởi GitHub Actions job 01
"""
import asyncio
import sys
import os
from datetime import datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from src.crawler.xsmb_crawler import XSMBCrawler
from src.database.supabase_client import LotteryDB
from src.bot.telegram_bot import LotteryNotifier


async def main():
    print('🚀 Starting XSMB crawler...')

    crawler = XSMBCrawler()
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

    try:
        results = crawler.fetch_results(today)

        if results:
            db.upsert_draw(results)
            db.log_crawler_status({
                'crawl_date': today,
                'region': 'XSMB',
                'status': 'success',
                'records_inserted': 1
            })
            msg = f'✅ <b>Crawl XSMB Success</b>\n📅 {today}\n🏆 ĐB: {results["special_prize"]}'
            print(msg)
            if bot:
                await bot.send_message(msg)

        else:
            msg = 'No data found (Holiday?)'
            db.log_crawler_status({
                'crawl_date': today,
                'region': 'XSMB',
                'status': 'success',
                'error_message': msg,
                'records_inserted': 0
            })
            print(f'⚠️ {msg}')
            if bot:
                await bot.send_message(
                    f'⚠️ <b>XSMB: No data found</b>\n📅 {today}\n(Likely holiday/off)'
                )

    except Exception as e:
        error_msg = str(e)
        try:
            db.log_crawler_status({
                'crawl_date': today,
                'region': 'XSMB',
                'status': 'failed',
                'error_message': error_msg,
                'records_inserted': 0
            })
        except Exception as db_err:
            print(f'⚠️ Could not log to DB: {db_err}')
        print(f'❌ Error: {e}')
        if bot:
            await bot.send_error_alert(f'XSMB Crawl Error: {e}')
        sys.exit(1)


if __name__ == '__main__':
    asyncio.run(main())
