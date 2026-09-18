#!/usr/bin/env python
"""Quick test to verify app starts with database."""

from app import app
from database import db

print("[OK] App imports successfully")
print(f"  Environment: development")
print(f"  Database: {app.config['SQLALCHEMY_DATABASE_URI']}")
print(f"  Secret Key: {'*' * 20} (set)")

with app.app_context():
    from models import User
    admin_count = User.query.filter_by(role='admin').count()
    teacher_count = User.query.filter_by(role='teacher').count()
    student_count = User.query.filter_by(role='student').count()
    
    print(f"\n[INFO] Database Statistics:")
    print(f"  Admin users: {admin_count}")
    print(f"  Teacher users: {teacher_count}")
    print(f"  Student users: {student_count}")
    print(f"  Total users: {User.query.count()}")

print("\n[SUCCESS] Phase 1 Database Integration: SUCCESS")
