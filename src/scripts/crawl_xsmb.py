"""
Script crawl XSMB (Miền Bắc) - chạy bởi GitHub Actions job 01
"""
import argparse
import asyncio
import sys
import os
from datetime import date

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from src.crawler.xsmb_crawler import XSMBCrawler
from src.database.supabase_client import LotteryDB
from src.bot.telegram_bot import LotteryNotifier
from src.utils.operational_date import resolve_operational_date, vietnam_now


async def main():
    parser = argparse.ArgumentParser(description="Crawl XSMB lottery results")
    parser.add_argument("--date", type=str, help="Target draw date (YYYY-MM-DD)")
    args = parser.parse_args()

    print('🚀 Starting XSMB crawler...')

    crawler = XSMBCrawler()
    db = LotteryDB()
    try:
        bot = LotteryNotifier(db, default_config_key="crawl_xsmb")
    except Exception as e:
        print(f'⚠️ Could not init bot: {e}')
        bot = None

    vn_time = vietnam_now()
    today = date.fromisoformat(args.date) if args.date else resolve_operational_date(vn_time)
    print(f'Current Vietnam Time: {vn_time}')
    print(f'Crawling for date: {today}')
    if args.date:
        print(f'Manual target date override: {today}')
    elif today != vn_time.date():
        print(f'Operational date differs from calendar date due to rollover guard: {today}')

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
            msg = f'✅ <b>Crawl XSMB Success</b>\n📅 {today}'
            print(msg)
            if bot:
                await bot.send_message(msg)

        else:
            msg = 'No data found (Holiday?)'
            db.log_crawler_status({
                'crawl_date': today,
                'region': 'XSMB',
                'status': 'failed',
                'error_message': msg,
                'records_inserted': 0
            })
            print(f'⚠️ {msg}')
            if bot:
                await bot.send_message(
                    f'⚠️ <b>XSMB: No data found</b>\n📅 {today}\n(Likely holiday/off)'
                )
            sys.exit(1)

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
