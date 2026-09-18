"""
Database initialization and helper functions.
Provides the SQLAlchemy db instance for use throughout the application.
"""

from flask_sqlalchemy import SQLAlchemy
from sqlalchemy import inspect
from pathlib import Path
import os

# Initialize SQLAlchemy
db = SQLAlchemy()


def init_db(app, config):
    """
    Initialize the database with the Flask application.
    
    Args:
        app: Flask application instance
        config: Configuration object
    """
    db.init_app(app)

    with app.app_context():
        # Create all tables if they don't exist
        db.create_all()
        print("[OK] Database initialized successfully")


def create_all_tables(app):
    """Create all database tables (idempotent)."""
    with app.app_context():
        db.create_all()
        print("[OK] All tables created")


def drop_all_tables(app):
    """Drop all database tables (use with caution)."""
    with app.app_context():
        response = input("WARNING: This will delete all data. Are you sure? (yes/no): ")
        if response.lower() == "yes":
            db.drop_all()
            print("[OK] All tables dropped")
        else:
            print("[CANCELLED] Cancelled")


def migrate_csv_to_db(app, csv_path="Attendance.csv"):
    """
    Migrate existing CSV attendance data to database.
    
    Args:
        app: Flask application instance
        csv_path: Path to the CSV file (relative to project root)
    """
    import csv
    from datetime import datetime
    from models import User, Student, AttendanceRecord, AttendanceSession

    csv_file = Path(csv_path)
    if not csv_file.exists():
        print(f"[ERROR] CSV file not found: {csv_path}")
        return

    with app.app_context():
        try:
            with open(csv_file, "r", encoding="utf-8") as f:
                reader = csv.DictReader(f)
                count = 0

                for row in reader:
                    try:
                        # Parse the CSV row
                        # Expected format: ID, Student Name, Date, Time, Status
                        student_id = int(row.get("ID", 0))
                        student_name = row.get("Student Name", "Unknown")
                        date_str = row.get("Date", "")
                        time_str = row.get("Time", "")
                        status = row.get("Status", "present").lower()

                        # Try to parse date and time
                        try:
                            attendance_date = datetime.strptime(
                                date_str, "%Y-%m-%d"
                            ).date()
                        except ValueError:
                            # Try alternative date format
                            try:
                                attendance_date = datetime.strptime(
                                    date_str, "%d/%m/%Y"
                                ).date()
                            except ValueError:
                                print(
                                    f"  ⚠ Skipping row - invalid date format: {date_str}"
                                )
                                continue

                        try:
                            attendance_time = datetime.strptime(
                                time_str, "%H:%M:%S"
                            ).time()
                        except ValueError:
                            attendance_time = datetime.strptime(
                                time_str, "%H:%M"
                            ).time()

                        # Check if student exists
                        student = Student.query.filter_by(id=student_id).first()
                        if not student:
                            # Create student if doesn't exist
                            student = Student(
                                id=student_id,
                                name=student_name,
                                roll_no=f"AUTO_{student_id}",
                                face_registered=False,
                            )
                            db.session.add(student)
                            db.session.flush()

                        # Create a default session if needed
                        from datetime import datetime as dt

                        existing_session = AttendanceSession.query.filter_by(
                            date=attendance_date
                        ).first()
                        if not existing_session:
                            session = AttendanceSession(
                                date=attendance_date,
                                start_time=datetime.strptime("09:00", "%H:%M").time(),
                                end_time=datetime.strptime("17:00", "%H:%M").time(),
                                status="completed",
                            )
                            db.session.add(session)
                            db.session.flush()
                            existing_session = session

                        # Create attendance record
                        record = AttendanceRecord(
                            session_id=existing_session.id,
                            student_id=student.id,
                            timestamp=datetime.combine(attendance_date, attendance_time),
                            status=status,
                            confidence=None,  # CSV doesn't have confidence
                        )
                        db.session.add(record)
                        count += 1

                        if count % 100 == 0:
                            db.session.commit()
                            print(f"  [OK] Migrated {count} records...")

                    except Exception as e:
                        print(f"  ⚠ Error processing row: {e}")
                        db.session.rollback()
                        continue

                db.session.commit()
                print(f"[OK] Migration complete: {count} attendance records migrated")

        except Exception as e:
            print(f"[ERROR] Migration failed: {e}")
            db.session.rollback()


def get_table_columns(model_class):
    """
    Get column names for a given model class.
    
    Args:
        model_class: SQLAlchemy model class
        
    Returns:
        List of column names
    """
    mapper = inspect(model_class)
    return [column.name for column in mapper.columns]


def table_exists(table_name):
    """Check if a table exists in the database."""
    inspector = inspect(db.engine)
    return table_name in inspector.get_table_names()
