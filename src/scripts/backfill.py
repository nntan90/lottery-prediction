"""
Initial Data Backfill Script - Run LOCALLY only
================================================
Dùng để crawl dữ liệu lịch sử lần đầu khi setup project.
KHÔNG chạy trên GitHub Actions (tốn tài nguyên, dễ bị timeout).

Usage:
    # Backfill 365 ngày gần nhất, cả XSMB và XSMN
    python src/scripts/backfill.py

    # Backfill số ngày cụ thể
    python src/scripts/backfill.py --days 90

    # Chỉ backfill một miền
    python src/scripts/backfill.py --region XSMB --days 180
    python src/scripts/backfill.py --region XSMN --days 180

    # Backfill từ ngày cụ thể
    python src/scripts/backfill.py --from-date 2025-01-01

Requirements:
    - Set SUPABASE_URL and SUPABASE_SERVICE_KEY in .env or environment
"""
import argparse
import sys
import os
import time
from datetime import datetime, timedelta, date

# Allow running from project root
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from src.crawler.xsmb_crawler import XSMBCrawler
from src.crawler.xsmn_crawler import XSMNCrawler
from src.database.supabase_client import LotteryDB


def backfill_xsmb(db: LotteryDB, start_date: date, end_date: date, delay: float = 2.0):
    """Backfill XSMB data from start_date to end_date (inclusive)."""
    crawler = XSMBCrawler()
    
    # Generate list of dates (newest first)
    delta = (end_date - start_date).days + 1
    dates = [end_date - timedelta(days=i) for i in range(delta)]
    
    print(f"\n{'='*60}")
    print(f"🚀 XSMB Backfill: {start_date} → {end_date} ({len(dates)} days)")
    print(f"{'='*60}")
    
    success_count = 0
    skipped_count = 0
    failed_count = 0
    
    for i, target_date in enumerate(dates):
        print(f"\n[{i+1}/{len(dates)}] {target_date} ...", end=" ", flush=True)
        
        try:
            results = crawler.fetch_results(target_date)
            
            if results:
                try:
                    db.upsert_draw(results)
                    success_count += 1
                    print(f"✅ ĐB={results.get('special_prize', '?')}")
                except Exception as e:
                    if 'duplicate' in str(e).lower():
                        skipped_count += 1
                        print(f"⏭️  Already exists")
                    else:
                        failed_count += 1
                        print(f"❌ DB error: {e}")
            else:
                skipped_count += 1
                print(f"⚠️  No data (holiday/off)")
                
        except Exception as e:
            failed_count += 1
            print(f"❌ Crawl error: {e}")
        
        # Rate limiting
        if i < len(dates) - 1:
            time.sleep(delay)
    
    print(f"\n{'='*60}")
    print(f"📊 XSMB Summary: ✅ {success_count} saved | ⏭️  {skipped_count} skipped | ❌ {failed_count} failed")
    print(f"{'='*60}")
    
    db.log_crawler_status({
        'crawl_date': date.today(),
        'region': 'XSMB',
        'status': 'success' if failed_count == 0 else 'partial',
        'error_message': f'Backfill {start_date}→{end_date}: {success_count} saved, {skipped_count} skipped, {failed_count} failed',
        'records_inserted': success_count
    })
    
    return success_count, skipped_count, failed_count


def backfill_xsmn(db: LotteryDB, start_date: date, end_date: date, delay: float = 2.0):
    """Backfill XSMN data (all provinces) from start_date to end_date."""
    crawler = XSMNCrawler()
    
    delta = (end_date - start_date).days + 1
    dates = [end_date - timedelta(days=i) for i in range(delta)]
    
    print(f"\n{'='*60}")
    print(f"🚀 XSMN Backfill: {start_date} → {end_date} ({len(dates)} days)")
    print(f"{'='*60}")
    
    success_count = 0
    skipped_count = 0
    failed_count = 0
    
    for i, target_date in enumerate(dates):
        print(f"\n[{i+1}/{len(dates)}] {target_date} ...")
        
        try:
            results_list = crawler.fetch_batch_results(target_date)
            
            if results_list:
                for res in results_list:
                    try:
                        db.upsert_draw(res)
                        success_count += 1
                        print(f"  ✅ {res['province']}: ĐB={res.get('special_prize', '?')}")
                    except Exception as e:
                        if 'duplicate' in str(e).lower():
                            skipped_count += 1
                            print(f"  ⏭️  {res['province']}: Already exists")
                        else:
                            failed_count += 1
                            print(f"  ❌ {res['province']}: DB error: {e}")
            else:
                skipped_count += 1
                print(f"  ⚠️  No data (holiday/off)")
                
        except Exception as e:
            failed_count += 1
            print(f"  ❌ Crawl error: {e}")
        
        if i < len(dates) - 1:
            time.sleep(delay)
    
    print(f"\n{'='*60}")
    print(f"📊 XSMN Summary: ✅ {success_count} saved | ⏭️  {skipped_count} skipped | ❌ {failed_count} failed")
    print(f"{'='*60}")
    
    db.log_crawler_status({
        'crawl_date': date.today(),
        'region': 'XSMN',
        'status': 'success' if failed_count == 0 else 'partial',
        'error_message': f'Backfill {start_date}→{end_date}: {success_count} saved, {skipped_count} skipped, {failed_count} failed',
        'records_inserted': success_count
    })
    
    return success_count, skipped_count, failed_count


def main():
    parser = argparse.ArgumentParser(
        description='Backfill historical lottery data locally',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__
    )
    parser.add_argument('--days', type=int, default=365,
                        help='Number of days to backfill (default: 365)')
    parser.add_argument('--region', choices=['XSMB', 'XSMN', 'BOTH'], default='BOTH',
                        help='Region to backfill (default: BOTH)')
    parser.add_argument('--from-date', type=str, default=None,
                        help='Start date in YYYY-MM-DD format (overrides --days)')
    parser.add_argument('--delay', type=float, default=2.0,
                        help='Delay between requests in seconds (default: 2.0)')
    
    args = parser.parse_args()
    
    # Determine date range
    end_date = date.today() - timedelta(days=1)  # Yesterday
    if args.from_date:
        start_date = datetime.strptime(args.from_date, '%Y-%m-%d').date()
    else:
        start_date = end_date - timedelta(days=args.days - 1)
    
    print(f"\n🗓️  Backfill range: {start_date} → {end_date}")
    print(f"📍 Region: {args.region}")
    print(f"⏱️  Delay: {args.delay}s between requests")
    
    db = LotteryDB()
    
    total_saved = 0
    
    if args.region in ('XSMB', 'BOTH'):
        s, sk, f = backfill_xsmb(db, start_date, end_date, args.delay)
        total_saved += s
    
    if args.region in ('XSMN', 'BOTH'):
        if args.region == 'BOTH':
            print("\n⏳ Waiting 10s before XSMN...")
            time.sleep(10)
        s, sk, f = backfill_xsmn(db, start_date, end_date, args.delay)
        total_saved += s
    
    print(f"\n🎉 Backfill complete! Total records saved: {total_saved}")


if __name__ == '__main__':
    main()
