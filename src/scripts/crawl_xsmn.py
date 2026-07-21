"""
Script crawl XSMN (Miền Nam) - chạy bởi GitHub Actions job 01
"""
import argparse
import asyncio
import sys
import os
from datetime import date

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from src.crawler.xsmn_crawler import XSMNCrawler
from src.database.supabase_client import LotteryDB
from src.bot.telegram_bot import LotteryNotifier
from src.utils.operational_date import resolve_operational_date, vietnam_now
from src.xsmn_ensemble.resolve_provinces import get_target_provinces


async def main():
    parser = argparse.ArgumentParser(description="Crawl XSMN lottery results")
    parser.add_argument("--date", type=str, help="Target draw date (YYYY-MM-DD)")
    args = parser.parse_args()

    print('🚀 Starting XSMN crawler...')

    crawler = XSMNCrawler()
    db = LotteryDB()
    try:
        bot = LotteryNotifier(db, default_config_key="crawl_xsmn")
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

    print(f'Target: Fetching all provinces for {today} in one request...')
    results_list = crawler.fetch_batch_results(today)

    success_count = 0
    saved_provinces: set[str] = set()
    if results_list:
        for res in results_list:
            is_valid, quality_errors = crawler.validate_result(res)
            if not is_valid:
                print(
                    f"   ❌ Rejected incomplete {res.get('province')}: "
                    f"{'; '.join(quality_errors)}"
                )
                continue
            try:
                db.upsert_draw(res)
                success_count += 1
                saved_provinces.add(res["province"])
                print(f"   ✅ Saved {res['province']}")
            except Exception as e:
                print(f"   ❌ Error saving {res['province']}: {e}")

    required_provinces = set(get_target_provinces(today))
    missing_required = sorted(required_provinces - saved_provinces)

    if success_count > 0 and not missing_required:
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
        msg = (
            f"Missing required XSMN provinces: {', '.join(missing_required)}"
            if missing_required
            else "No complete data found (Holiday/Batch Mode)"
        )
        db.log_crawler_status({
            'crawl_date': today,
            'region': 'XSMN',
            'status': 'failed',
            'error_message': msg,
            'records_inserted': success_count
        })
        print(f'⚠️ {msg}')
        if bot:
            await bot.send_message(
                f'⚠️ <b>XSMN Crawl Incomplete</b>\n📅 {today}\n{msg}'
            )
        sys.exit(1)


if __name__ == '__main__':
    asyncio.run(main())
