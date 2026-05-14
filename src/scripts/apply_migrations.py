"""
apply_migrations.py — Chạy migration SQL files lên Supabase thông qua service_role key.

Sử dụng psycopg2 kết nối qua Supabase database URL (connection pooler).
Cách duy nhất để chạy DDL (CREATE TABLE, ALTER TABLE) mà không cần Supabase Dashboard.

Usage (trong GitHub Actions):
  python src/scripts/apply_migrations.py

Hoặc local (cần set SUPABASE_DB_URL):
  SUPABASE_DB_URL="postgresql://..." python src/scripts/apply_migrations.py
"""

import os
import sys
import glob
from dotenv import load_dotenv

load_dotenv()


def get_db_connection():
    """Kết nối trực tiếp tới Supabase PostgreSQL qua connection string."""
    db_url = os.getenv("SUPABASE_DB_URL")
    if not db_url:
        # Xây dựng từ components nếu có
        db_host = os.getenv("SUPABASE_DB_HOST")
        db_pass = os.getenv("SUPABASE_DB_PASSWORD")
        if db_host and db_pass:
            db_url = f"postgresql://postgres.{db_host}:{db_pass}@aws-0-ap-southeast-1.pooler.supabase.com:5432/postgres"
        else:
            print("❌ Cần set SUPABASE_DB_URL hoặc SUPABASE_DB_HOST + SUPABASE_DB_PASSWORD")
            sys.exit(1)

    import psycopg2
    conn = psycopg2.connect(db_url, sslmode='require')
    conn.autocommit = True
    return conn


def run_migration(conn, filepath: str) -> bool:
    """Chạy 1 file migration SQL."""
    filename = os.path.basename(filepath)
    print(f"\n{'='*50}")
    print(f"📄 Running: {filename}")
    print(f"{'='*50}")

    with open(filepath, 'r') as f:
        sql = f.read()

    cur = conn.cursor()
    try:
        cur.execute(sql)
        print(f"  ✅ {filename} — Success")
        return True
    except Exception as e:
        print(f"  ❌ {filename} — Error: {e}")
        return False
    finally:
        cur.close()


def main():
    migration_dir = os.path.join(os.path.dirname(__file__), '..', '..', 'database', 'migrations')
    migration_files = sorted(glob.glob(os.path.join(migration_dir, '*.sql')))

    if not migration_files:
        print("❌ Không tìm thấy file migration nào!")
        sys.exit(1)

    print(f"🔍 Tìm thấy {len(migration_files)} migration files:")
    for f in migration_files:
        print(f"  • {os.path.basename(f)}")

    conn = get_db_connection()
    results = []
    for f in migration_files:
        ok = run_migration(conn, f)
        results.append((os.path.basename(f), ok))

    conn.close()

    print(f"\n{'='*50}")
    print("📊 KẾT QUẢ:")
    print(f"{'='*50}")
    for name, ok in results:
        status = "✅" if ok else "❌"
        print(f"  {status} {name}")

    failed = [n for n, ok in results if not ok]
    if failed:
        print(f"\n⚠️ {len(failed)} migration(s) failed!")
        sys.exit(1)
    else:
        print(f"\n✅ Tất cả {len(results)} migration(s) thành công!")


if __name__ == "__main__":
    main()
