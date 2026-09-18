#!/usr/bin/env python
"""
Supabase Migration and Database Synchronization Utility.

This script manages connecting, initializing schema, transferring data from local
SQLite, and validating the PostgreSQL database hosted on Supabase.

Usage:
    python migrate_to_supabase.py --check             # Test connection to Supabase
    python migrate_to_supabase.py --init              # Create all tables on Supabase
    python migrate_to_supabase.py --demo              # Seed demo data on Supabase
    python migrate_to_supabase.py --transfer-sqlite   # Copy data from local SQLite to Supabase
    python migrate_to_supabase.py --status            # Show row count in each table
    python migrate_to_supabase.py --all               # Initialize tables + transfer data / demo
"""

import os
import sys
import argparse
from pathlib import Path
from dotenv import load_dotenv

# Ensure UTF-8 output on Windows terminals
if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass

# Ensure the app directory is on the path
BASE_DIR = Path(__file__).resolve().parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

# Load .env file
load_dotenv(dotenv_path=BASE_DIR / ".env")

import sqlalchemy
from sqlalchemy import text, inspect
from config import normalize_database_url


def get_target_db_url(cli_url=None):
    """Retrieve and normalize target DATABASE_URL."""
    url = cli_url or os.getenv("DATABASE_URL")
    if not url:
        print("\n❌ Error: DATABASE_URL is not set!")
        print("Please provide it via --url, or set DATABASE_URL in your .env file.")
        print("Example Supabase connection string:")
        print("  postgresql://postgres.[REF]:[PASSWORD]@aws-0-[REGION].pooler.supabase.com:6543/postgres\n")
        sys.exit(1)
    return normalize_database_url(url)


def check_connection(db_url):
    """Test connection to the target database and print diagnostics."""
    print(f"\n🔍 Testing database connection...")
    # Mask password for display
    masked_url = db_url
    if "@" in masked_url and ":" in masked_url.split("@")[0]:
        prefix, rest = masked_url.split("@", 1)
        protocol, auth = prefix.split("://", 1)
        if ":" in auth:
            user = auth.split(":")[0]
            masked_url = f"{protocol}://{user}:*****@{rest}"

    print(f"  Target URI: {masked_url}")

    try:
        engine = sqlalchemy.create_engine(
            db_url,
            pool_pre_ping=True,
            connect_args={"connect_timeout": 15} if "postgresql" in db_url else {}
        )
        with engine.connect() as conn:
            result = conn.execute(text("SELECT 1")).scalar()
            if result == 1:
                print("  ✅ Connection established successfully!")

            # Retrieve database version if PostgreSQL
            if "postgresql" in db_url:
                try:
                    version = conn.execute(text("SELECT version()")).scalar()
                    db_name = conn.execute(text("SELECT current_database()")).scalar()
                    print(f"  📊 Database: {db_name}")
                    print(f"  ℹ️  Version:  {version.split(',')[0] if version else 'PostgreSQL'}")
                except Exception:
                    pass
        return True
    except Exception as e:
        print(f"  ❌ Connection failed: {e}")
        print("\n💡 Troubleshooting Tips:")
        print("1. Ensure your database password does not contain unescaped special characters (e.g. '@', ':', '#'). If so, URL-encode them.")
        print("2. For Supabase, prefer the 'Session Pooler' or 'Transaction Pooler' connection string on port 6543.")
        print("3. Check that your network allows outbound connections to port 5432 / 6543.")
        return False


def init_schema(app, db):
    """Create all tables in the target database."""
    print("\n📦 Creating all tables in target database...")
    try:
        with app.app_context():
            db.create_all()
        print("  ✅ All database tables created successfully!")
        return True
    except Exception as e:
        print(f"  ❌ Failed to create tables: {e}")
        return False


def show_status(app, db):
    """Show status and row counts of all registered tables."""
    print("\n📊 Database Table Summary:")
    from models import (
        User, Department, Class, Subject, Teacher, Student,
        FaceEmbedding, AttendanceSession, AttendanceRecord,
        AuditLog, FaceRecognitionLog, SystemSetting
    )

    models = [
        ("Users", User),
        ("Departments", Department),
        ("Classes", Class),
        ("Subjects", Subject),
        ("Teachers", Teacher),
        ("Students", Student),
        ("Face Embeddings", FaceEmbedding),
        ("Attendance Sessions", AttendanceSession),
        ("Attendance Records", AttendanceRecord),
        ("Audit Logs", AuditLog),
        ("Face Recognition Logs", FaceRecognitionLog),
        ("System Settings", SystemSetting),
    ]

    with app.app_context():
        inspector = inspect(db.engine)
        existing_tables = set(inspector.get_table_names())

        print(f"  {'-' * 45}")
        print(f"  {'Table / Model':<28} | {'Rows':<12}")
        print(f"  {'-' * 45}")

        total_rows = 0
        for name, model in models:
            table_name = model.__tablename__
            if table_name in existing_tables:
                try:
                    count = db.session.query(model).count()
                    print(f"  {name:<28} | {count:<12}")
                    total_rows += count
                except Exception as e:
                    print(f"  {name:<28} | Error: {e}")
            else:
                print(f"  {name:<28} | [Missing Table]")

        print(f"  {'-' * 45}")
        print(f"  Total records across tables: {total_rows}\n")


def transfer_from_sqlite(app, db, sqlite_path="attendance_system.db"):
    """Transfer existing records from local SQLite to target database."""
    sqlite_file = BASE_DIR / sqlite_path
    if not sqlite_file.exists():
        print(f"\n⚠️  Local SQLite file not found at: {sqlite_file}")
        return False

    print(f"\n🚚 Migrating data from SQLite ({sqlite_path}) to target database...")
    from models import (
        Department, Class, Subject, User, Teacher, Student,
        AttendanceSession, AttendanceRecord, FaceEmbedding,
        AuditLog, SystemSetting
    )

    source_engine = sqlalchemy.create_engine(f"sqlite:///{sqlite_file}")

    tables_in_order = [
        ("departments", Department),
        ("classes", Class),
        ("subjects", Subject),
        ("users", User),
        ("teachers", Teacher),
        ("students", Student),
        ("attendance_sessions", AttendanceSession),
        ("attendance_records", AttendanceRecord),
        ("face_embeddings", FaceEmbedding),
        ("audit_logs", AuditLog),
        ("system_settings", SystemSetting),
    ]

    with app.app_context():
        # Make sure tables exist
        db.create_all()

        source_inspector = inspect(source_engine)
        source_tables = set(source_inspector.get_table_names())

        total_transferred = 0

        with source_engine.connect() as source_conn:
            for table_name, model in tables_in_order:
                if table_name not in source_tables:
                    continue

                rows = source_conn.execute(text(f"SELECT * FROM {table_name}")).mappings().all()
                if not rows:
                    continue

                print(f"  Migrating {len(rows)} records for table '{table_name}'...")
                mapper = inspect(model)
                valid_columns = {c.key for c in mapper.column_attrs}

                migrated_table_count = 0
                for r in rows:
                    row_dict = dict(r)
                    # Filter only valid columns that exist in target model
                    filtered_dict = {k: v for k, v in row_dict.items() if k in valid_columns}

                    # Check if already exists by primary key
                    pk_attr = mapper.primary_key[0].name
                    pk_val = filtered_dict.get(pk_attr)
                    if pk_val:
                        existing = db.session.query(model).filter(getattr(model, pk_attr) == pk_val).first()
                        if existing:
                            continue

                    instance = model(**filtered_dict)
                    db.session.merge(instance)
                    migrated_table_count += 1

                db.session.commit()
                total_transferred += migrated_table_count
                print(f"    ✓ {migrated_table_count} records migrated.")

        # Update sequences for PostgreSQL if needed
        if "postgresql" in str(db.engine.url):
            print("  Updating PostgreSQL ID auto-increment sequences...")
            for table_name, model in tables_in_order:
                mapper = inspect(model)
                pk_col = mapper.primary_key[0].name
                try:
                    db.session.execute(text(f"""
                        SELECT setval(
                            pg_get_serial_sequence('{table_name}', '{pk_col}'),
                            COALESCE((SELECT MAX({pk_col}) FROM {table_name}), 1),
                            (SELECT MAX({pk_col}) IS NOT NULL FROM {table_name})
                        )
                    """))
                    db.session.commit()
                except Exception:
                    db.session.rollback()

        print(f"  ✅ Data transfer complete! {total_transferred} total records transferred.")
        return True


def main():
    parser = argparse.ArgumentParser(
        description="Migrate and Synchronize Face Recognition Attendance System with Supabase"
    )
    parser.add_argument("--url", help="Database connection URL (defaults to DATABASE_URL in .env)")
    parser.add_argument("--check", action="store_true", help="Test connection to target database")
    parser.add_argument("--init", action="store_true", help="Create tables in target database")
    parser.add_argument("--demo", action="store_true", help="Seed default demo accounts and departments")
    parser.add_argument("--transfer-sqlite", action="store_true", help="Copy all rows from local SQLite to target database")
    parser.add_argument("--status", action="store_true", help="Show current row counts in database")
    parser.add_argument("--all", action="store_true", help="Check, initialize tables, transfer SQLite data (or add demo data)")

    args = parser.parse_args()

    # Determine command
    if not any([args.check, args.init, args.demo, args.transfer_sqlite, args.status, args.all]):
        parser.print_help()
        return

    target_url = get_target_db_url(args.url)

    # Set environment variable so the Flask app picks up the target URL
    os.environ["DATABASE_URL"] = target_url

    # Check connection first
    if not check_connection(target_url):
        sys.exit(1)

    if args.check and not any([args.init, args.demo, args.transfer_sqlite, args.status, args.all]):
        return

    # Import Flask app and db
    from web_app import app
    from database import db

    if args.init or args.all:
        init_schema(app, db)

    if args.transfer_sqlite or args.all:
        sqlite_file = BASE_DIR / "attendance_system.db"
        if sqlite_file.exists():
            transfer_from_sqlite(app, db)
        elif args.all:
            # If no SQLite file, seed demo data
            from init_db import add_demo_data
            add_demo_data()

    if args.demo and not args.all:
        from init_db import add_demo_data
        add_demo_data()

    show_status(app, db)


if __name__ == "__main__":
    main()
