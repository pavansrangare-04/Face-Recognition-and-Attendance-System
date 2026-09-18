import csv
import os
import threading
import time
import urllib.request
from datetime import datetime
from pathlib import Path
from flask import Flask, render_template, Response, jsonify, request, send_from_directory, send_file, session, redirect, url_for
from functools import wraps

import cv2
import numpy as np

# Import configuration and database
from config import config
from database import db, init_db
from models import (User, Department, Class, Subject, Teacher, Student, 
                    FaceEmbedding, AttendanceSession, AttendanceRecord, 
                    AuditLog, FaceRecognitionLog, SystemSetting)
from auth import login_required, role_required, admin_required, teacher_required, student_required, faculty_required, log_audit_action, get_current_user

# Directory Setup
BASE_DIR = Path(__file__).resolve().parent
IMAGE_DIR = BASE_DIR / "ImagesAttendance"
ATTENDANCE_FILE = BASE_DIR / "Attendance.csv"
DATA_DIR = BASE_DIR / "data"

FACE_DETECTOR_MODEL = DATA_DIR / "face_detection_yunet_2023mar.onnx"
FACE_DETECTOR_MODEL_URL = "https://github.com/opencv/opencv_zoo/raw/refs/heads/main/models/face_detection_yunet/face_detection_yunet_2023mar.onnx"
FACE_RECOGNIZER_MODEL = DATA_DIR / "face_recognition_sface_2021dec.onnx"
FACE_RECOGNIZER_MODEL_URL = "https://github.com/opencv/opencv_zoo/raw/refs/heads/main/models/face_recognition_sface/face_recognition_sface_2021dec.onnx"

COOLDOWN_SECONDS = 10
RECOGNITION_THRESHOLD = 0.55  # cosine similarity threshold for SF embeddings

# Create Flask app with configuration
app = Flask(__name__)
app.config.from_object(config)

# Initialize database
init_db(app, config)

# State variables
camera = None
camera_active = False
camera_lock = threading.Lock()

# Model and training state
model_lock = threading.Lock()
class_names = []
recognizer = None
face_embeddings = []
training_error = None

# Track last marked attendance to prevent multiple rapid logs
last_marked = {}


def get_face_detector():
    """Load or download the supported OpenCV face detector model."""
    if FACE_DETECTOR_MODEL.exists():
        detector = cv2.FaceDetectorYN_create(str(FACE_DETECTOR_MODEL), "", (320, 320))
        if detector is not None:
            return detector

    DATA_DIR.mkdir(parents=True, exist_ok=True)
    try:
        print("Downloading face detector model...")
        urllib.request.urlretrieve(FACE_DETECTOR_MODEL_URL, FACE_DETECTOR_MODEL)
    except Exception as exc:
        raise RuntimeError(f"Unable to download the face detector model: {exc}") from exc

    detector = cv2.FaceDetectorYN_create(str(FACE_DETECTOR_MODEL), "", (320, 320))
    if detector is None:
        raise RuntimeError("The downloaded face detector model could not be loaded")
    return detector


def get_face_recognizer():
    """Load or download the supported OpenCV face recognition model."""
    if FACE_RECOGNIZER_MODEL.exists():
        recognizer = cv2.FaceRecognizerSF_create(str(FACE_RECOGNIZER_MODEL), "")
        if recognizer is not None:
            return recognizer

    DATA_DIR.mkdir(parents=True, exist_ok=True)
    try:
        print("Downloading face recognizer model...")
        urllib.request.urlretrieve(FACE_RECOGNIZER_MODEL_URL, FACE_RECOGNIZER_MODEL)
    except Exception as exc:
        raise RuntimeError(f"Unable to download the face recognizer model: {exc}") from exc

    recognizer = cv2.FaceRecognizerSF_create(str(FACE_RECOGNIZER_MODEL), "")
    if recognizer is None:
        raise RuntimeError("The downloaded face recognizer model could not be loaded")
    return recognizer


def detect_faces(image, detector):
    """Detect faces using FaceDetectorYN and return bounding boxes."""
    if detector is None:
        return []

    height, width = image.shape[:2]
    detector.setInputSize((width, height))
    _, faces = detector.detect(image)
    boxes = []

    if faces is None:
        return boxes

    for face in faces:
        confidence = float(face[4])
        if confidence < 0.5:
            continue
        x1 = int(face[0])
        y1 = int(face[1])
        x2 = int(face[0] + face[2])
        y2 = int(face[1] + face[3])
        boxes.append((x1, y1, x2, y2))

    return boxes


def train_model():
    """Train the facial embedding database thread-safely."""
    global class_names, recognizer, face_embeddings, training_error
    
    IMAGE_DIR.mkdir(parents=True, exist_ok=True)
    names = []
    embeddings = []

    try:
        detector = get_face_detector()
        recognizer = get_face_recognizer()
    except Exception as e:
        with model_lock:
            training_error = f"Model initialization failed: {e}"
            recognizer = None
            class_names = []
            face_embeddings = []
        return False

    image_files = sorted(IMAGE_DIR.iterdir())
    valid_images = [img for img in image_files if img.suffix.lower() in {".jpg", ".jpeg", ".png", ".bmp", ".webp"}]
    
    if not valid_images:
        with model_lock:
            training_error = "No student photos registered. Add a student to start."
            recognizer = None
            class_names = []
            face_embeddings = []
        return False

    for image_path in valid_images:
        image = cv2.imread(str(image_path))
        if image is None:
            continue

        faces = detect_faces(image, detector)
        if len(faces) == 0:
            print(f"No face detected in registration photo: {image_path.name}")
            continue

        x1, y1, x2, y2 = faces[0]
        face_roi = image[y1:y2, x1:x2]
        if face_roi.size == 0:
            continue

        face_roi = cv2.resize(face_roi, (112, 112))
        try:
            embedding = recognizer.feature(face_roi)
            # Normalize the descriptor for more stable similarity matching.
            norm = np.linalg.norm(embedding)
            if norm > 1e-8:
                embedding = embedding / norm
        except Exception as e:
            print(f"Failed to extract embedding from {image_path.name}: {e}")
            continue

        name = image_path.stem.upper().replace("_", " ")
        names.append(name)
        embeddings.append(embedding)

    if not embeddings:
        with model_lock:
            training_error = "None of the registered photos contain a detectable face."
            recognizer = None
            class_names = []
            face_embeddings = []
        return False

    with model_lock:
        class_names = names
        face_embeddings = embeddings
        training_error = None

    print("Face embeddings registered successfully! Classes:", class_names)
    return True


def repair_attendance_file():
    """Ensure the CSV exists, has headers, and is well-formatted (fixing line joins)."""
    ATTENDANCE_FILE.parent.mkdir(parents=True, exist_ok=True)
    records = []
    
    if ATTENDANCE_FILE.exists():
        try:
            with ATTENDANCE_FILE.open("r", encoding="utf-8") as f:
                content = f.read()
            
            # Normalise line endings and split
            lines = content.replace("\r", "").split("\n")
            for line in lines:
                line = line.strip()
                if not line:
                    continue
                
                # Check for merged header line issue (e.g. StatusPAVAN)
                if "Status" in line and "Name" in line:
                    idx = line.find("Status")
                    record_part = line[idx + len("Status"):].strip()
                    if record_part:
                        parts = [p.strip() for p in record_part.split(",") if p.strip()]
                        if len(parts) >= 3:
                            name = parts[0]
                            date = parts[1]
                            time_val = parts[2]
                            status = parts[3] if len(parts) > 3 else "Present"
                            records.append((name, date, time_val, status))
                    continue
                
                parts = [p.strip() for p in line.split(",")]
                if len(parts) >= 3 and parts[0].upper() != "NAME":
                    name = parts[0]
                    date = parts[1]
                    time_val = parts[2]
                    status = parts[3] if len(parts) > 3 else "Present"
                    records.append((name, date, time_val, status))
        except Exception as e:
            print(f"Error repairing CSV: {e}")

    # Rewrite CSV cleanly
    try:
        with ATTENDANCE_FILE.open("w", encoding="utf-8", newline="") as f:
            f.write("Name,Date,Time,Status\n")
            for r in records:
                f.write(f"{r[0]},{r[1]},{r[2]},{r[3]}\n")
        print("Attendance CSV checked and formatted.")
    except Exception as e:
        print(f"Error writing CSV file: {e}")


def mark_attendance(name: str, confidence: float = None) -> bool:
    """Record student attendance inside Attendance.csv AND SQLite DB if active session exists."""
    now = datetime.now()
    today_str = now.strftime("%Y-%m-%d")
    timestamp_str = now.strftime("%H:%M:%S")

    # 1. Write to Attendance.csv
    if not ATTENDANCE_FILE.exists() or ATTENDANCE_FILE.stat().st_size == 0:
        with ATTENDANCE_FILE.open("w", encoding="utf-8", newline="") as f:
            f.write("Name,Date,Time,Status\n")

    csv_marked = False
    try:
        already_present = False
        with ATTENDANCE_FILE.open("r", encoding="utf-8") as f:
            reader = csv.reader(f)
            next(reader, None)  # Skip header
            for row in reader:
                if len(row) >= 2 and row[0].strip().upper() == name.strip().upper() and row[1].strip() == today_str:
                    already_present = True
                    break
        
        if not already_present:
            with ATTENDANCE_FILE.open("a", encoding="utf-8", newline="") as f:
                writer = csv.writer(f)
                writer.writerow([name.upper(), today_str, timestamp_str, "Present"])
            csv_marked = True
    except Exception as e:
        print(f"Error writing attendance CSV: {e}")

    # 2. Write to SQLite DB (AttendanceSession & AttendanceRecord)
    try:
        with app.app_context():
            # Find student by name or roll_no
            student = Student.query.filter(
                db.or_(
                    db.func.upper(Student.name) == name.strip().upper(),
                    db.func.upper(Student.roll_no) == name.strip().upper()
                )
            ).first()

            if student:
                # Find active session today matching student's class
                active_session = AttendanceSession.query.filter(
                    AttendanceSession.status == 'active',
                    AttendanceSession.date == now.date()
                ).first()

                if not active_session and student.class_id:
                    # Fallback to any active session for student's class
                    active_session = AttendanceSession.query.filter(
                        AttendanceSession.class_id == student.class_id,
                        AttendanceSession.status == 'active'
                    ).first()

                if active_session:
                    # Duplicate check
                    existing_rec = AttendanceRecord.query.filter_by(
                        session_id=active_session.id,
                        student_id=student.id
                    ).first()

                    if not existing_rec:
                        rec = AttendanceRecord(
                            session_id=active_session.id,
                            student_id=student.id,
                            timestamp=now,
                            status='present',
                            confidence=confidence,
                            marked_by_teacher=False,
                            notes='Auto-marked via Face Recognition Stream'
                        )
                        db.session.add(rec)
                        db.session.commit()
                        print(f"Recorded DB attendance for {student.name} in Session {active_session.id}")
    except Exception as e:
        print(f"Error writing DB attendance record: {e}")

    return csv_marked



def start_camera():
    """Start the video capture device."""
    global camera, camera_active
    with camera_lock:
        if not camera_active:
            camera = cv2.VideoCapture(0)
            if camera.isOpened():
                camera_active = True
                print("Webcam started successfully.")
                return True
            else:
                camera = None
                print("Failed to open webcam.")
                return False
    return True


def stop_camera():
    """Stop the video capture device and release lock."""
    global camera, camera_active
    with camera_lock:
        if camera_active:
            camera_active = False
            if camera is not None:
                camera.release()
                camera = None
                print("Webcam released.")


def generate_frames():
    """Generate camera stream frames with face recognition overlay."""
    global camera, camera_active, last_marked, class_names, recognizer, training_error
    
    # Load face detector locally for the generator thread
    try:
        detector = get_face_detector()
    except Exception as e:
        print(f"Face Detector load failed: {e}")
        return
        
    while True:
        with camera_lock:
            if not camera_active or camera is None:
                # Sleep if inactive to prevent CPU spinning
                time.sleep(0.2)
                continue
            
            success, frame = camera.read()
            
        if not success:
            print("Failed to read frame from webcam.")
            time.sleep(0.1)
            continue

        # Resize image for processing speed
        h, w = frame.shape[:2]
        small_frame = cv2.resize(frame, (640, int(640 * h / w))) if w > 640 else frame
        gray_frame = cv2.cvtColor(small_frame, cv2.COLOR_BGR2GRAY)
        
        faces = detect_faces(small_frame, detector)
        
        for (x1, y1, x2, y2) in faces:
            name = "Unknown"
            confidence_str = ""
            color = (0, 0, 255)  # Red for unknown
            
            with model_lock:
                current_recognizer = recognizer
                current_classes = class_names
                current_embeddings = face_embeddings

            if current_recognizer is not None and len(current_classes) > 0 and current_embeddings:
                face_roi = small_frame[y1:y2, x1:x2]
                if face_roi.size == 0:
                    continue
                face_roi = cv2.resize(face_roi, (112, 112))

                try:
                    embedding = current_recognizer.feature(face_roi)
                    norm = np.linalg.norm(embedding)
                    if norm > 1e-8:
                        embedding = embedding / norm

                    distances = [float(np.linalg.norm(embedding - known)) for known in current_embeddings]
                    cosines = [float(np.dot(embedding.flatten(), known.flatten())) for known in current_embeddings]
                    best_index = int(np.argmin(distances)) if distances else -1

                    if best_index >= 0 and (distances[best_index] < 0.85 or cosines[best_index] > 0.62):
                        name = current_classes[best_index]
                        color = (0, 255, 0)
                        confidence_score = int(max(0, min(100, cosines[best_index] * 100)))
                        confidence_str = f" ({confidence_score}%)"

                        now = datetime.now()
                        conf_val = float(cosines[best_index]) if best_index >= 0 else None
                        if name not in last_marked or (now - last_marked[name]).total_seconds() >= COOLDOWN_SECONDS:
                            if mark_attendance(name, confidence=conf_val):
                                print(f"Attendance marked for {name} via Stream")
                            last_marked[name] = now

                    else:
                        confidence_str = " (Unknown)"
                except Exception as e:
                    print(f"Error during SF prediction: {e}")
            
            # Draw overlay on camera frame
            cv2.rectangle(small_frame, (x1, y1), (x2, y2), color, 2)
            cv2.rectangle(small_frame, (x1, y2 - 25), (x2, y2), color, cv2.FILLED)
            cv2.putText(small_frame, f"{name}{confidence_str}", (x1 + 6, y2 - 6),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.5, (255, 255, 255), 1)

        # Encode frame as JPEG
        ret, jpeg = cv2.imencode('.jpg', small_frame)
        if not ret:
            continue
        
        frame_bytes = jpeg.tobytes()
        yield (b'--frame\r\n'
               b'Content-Type: image/jpeg\r\n\r\n' + frame_bytes + b'\r\n')

@app.route('/download-demo-video')
def download_demo_video():
    """Route to download the demo video walkthrough MP4 file."""
    video_path = os.path.join(app.root_path, 'AttendAI_Demo_Walkthrough.mp4')
    if os.path.exists(video_path):
        return send_file(video_path, as_attachment=True, download_name='AttendAI_Demo_Walkthrough.mp4', mimetype='video/mp4')
    return jsonify({"success": False, "message": "Demo video file not found"}), 404


@app.route('/login', methods=['GET', 'POST'])
def login_page():
    """Display the login page or handle login POST."""
    if request.method == 'POST':
        return api_login()
    if 'user_id' in session:
        return redirect(url_for('index'))
    return render_template('login.html')


@app.route('/api/login', methods=['POST'])
def api_login():
    """Handle user login (supports all roles: admin, faculty, teacher, student)."""
    data = request.get_json(silent=True) or request.form or {}
    email = data.get('email', '').strip().lower()
    password = data.get('password', '').strip()

    if not email or not password:
        return jsonify({'success': False, 'message': 'Email and password are required.'}), 400

    try:
        # Query user from database
        user = User.query.filter_by(email=email, is_active=True).first()
        
        if user and user.check_password(password):
            # Update last login
            user.last_login = datetime.utcnow()
            db.session.commit()
            
            # Store user info in session
            session['user_id'] = user.id
            session['user_email'] = user.email
            session['user_name'] = user.name
            session['user_role'] = user.role
            
            # For backward compatibility with existing frontend (expects teacher_email, teacher_name)
            session['teacher_email'] = user.email
            session['teacher_name'] = user.name
            
            # Log login action
            log_audit_action(user.id, 'user_login', details=f'Logged in as {user.role}')
            
            return jsonify({
                'success': True,
                'message': 'Login successful.',
                'user_name': user.name,
                'user_role': user.role
            }), 200
        else:
            # Log failed login attempt
            if user:
                log_audit_action(user.id, 'login_failed', details='Failed login attempt (wrong password)')
            return jsonify({'success': False, 'message': 'Invalid email or password.'}), 401
            
    except Exception as e:
        print(f"Login error: {e}")
        return jsonify({'success': False, 'message': 'Login failed. Please try again.'}), 500


@app.route('/api/logout', methods=['POST'])
def api_logout():
    """Handle user logout."""
    user_id = session.get('user_id')
    if user_id:
        log_audit_action(user_id, 'user_logout', details='User logged out')
    session.clear()
    return jsonify({'success': True, 'message': 'Logged out successfully.'}), 200


@app.route('/')
@login_required
def index():
    """Main dashboard - teacher view."""
    user_role = session.get('user_role', 'teacher')
    if user_role == 'admin':
        return redirect(url_for('admin_dashboard_page'))
    if user_role == 'faculty':
        return redirect(url_for('faculty_dashboard_page'))
    if user_role == 'student':
        return redirect(url_for('student_dashboard_page'))
    return render_template('index.html')


@app.route('/admin')
@login_required
def admin_dashboard_page():
    """Admin dashboard page."""
    if session.get('user_role') != 'admin':
        return redirect(url_for('index'))
    return render_template('admin.html')


@app.route('/faculty')
@login_required
def faculty_dashboard_page():
    """Faculty dashboard page."""
    if session.get('user_role') not in ('faculty', 'admin'):
        return redirect(url_for('index'))
    return render_template('faculty.html')


@app.route('/student')
@login_required
def student_dashboard_page():
    """Student dashboard page."""
    if session.get('user_role') != 'student':
        return redirect(url_for('index'))
    return render_template('student.html')


@app.route('/api/me', methods=['GET'])
@login_required
def api_me():
    """Return current logged-in user info."""
    return jsonify({
        'id': session.get('user_id'),
        'name': session.get('user_name'),
        'email': session.get('user_email'),
        'role': session.get('user_role'),
    })


@app.route('/video_feed')
def video_feed():
    """Video streaming route. Put this in the src attribute of an img tag."""
    # Ensure camera starts up
    start_camera()
    return Response(generate_frames(), mimetype='multipart/x-mixed-replace; boundary=frame')


@app.route('/api/camera/toggle', methods=['POST'])
def toggle_camera():
    """Start or stop the webcam."""
    global camera_active
    data = request.json or {}
    action = data.get("action", "")
    
    if action == "start":
        success = start_camera()
        return jsonify({"success": success, "camera_active": camera_active})
    elif action == "stop":
        stop_camera()
        return jsonify({"success": True, "camera_active": camera_active})
    else:
        # Toggle current state
        if camera_active:
            stop_camera()
        else:
            start_camera()
        return jsonify({"success": True, "camera_active": camera_active})


@app.route('/api/camera/status', methods=['GET'])
def camera_status():
    global camera_active
    return jsonify({"camera_active": camera_active})


@app.route('/api/camera/snapshot', methods=['GET'])
def camera_snapshot():
    """Return a single JPEG frame from the server webcam for registration capture."""
    if not start_camera():
        return jsonify({"success": False, "message": "Unable to open webcam."}), 500

    with camera_lock:
        if camera is None or not camera.isOpened():
            return jsonify({"success": False, "message": "Webcam is unavailable."}), 500
        success, frame = camera.read()

    if not success or frame is None:
        return jsonify({"success": False, "message": "Failed to capture camera frame."}), 500

    ret, jpeg = cv2.imencode('.jpg', frame)
    if not ret:
        return jsonify({"success": False, "message": "Failed to encode captured image."}), 500

    response = Response(jpeg.tobytes(), mimetype='image/jpeg')
    response.headers['Cache-Control'] = 'no-store, no-cache, must-revalidate, max-age=0'
    return response

@app.route('/api/students', methods=['GET'])
@login_required
def list_students():
    """List registered student details (names, image links, date modified)."""
    IMAGE_DIR.mkdir(parents=True, exist_ok=True)
    students = []
    
    image_files = sorted(IMAGE_DIR.iterdir())
    valid_images = [img for img in image_files if img.suffix.lower() in {".jpg", ".jpeg", ".png", ".bmp", ".webp"}]
    
    for img_path in valid_images:
        stat = img_path.stat()
        created_time = datetime.fromtimestamp(stat.st_mtime).strftime("%Y-%m-%d %H:%M:%S")
        name = img_path.stem.upper().replace("_", " ")
        students.append({
            "name": name,
            "filename": img_path.name,
            "registered_at": created_time,
            "photo_url": f"/api/students/photo/{img_path.name}"
        })
    
    with model_lock:
        curr_error = training_error
        curr_classes = class_names
        
    return jsonify({
        "students": students,
        "classes_loaded": curr_classes,
        "training_error": curr_error
    })


@app.route('/api/students/photo/<filename>')
def get_student_photo(filename):
    """Serve student photo files securely."""
    return send_from_directory(str(IMAGE_DIR), filename)


@app.route('/api/students', methods=['POST'])
@teacher_required
def add_student():
    """Register a new student by uploading their face photo."""
    name = request.form.get('name', '').strip()
    if not name:
        return jsonify({"success": False, "message": "Name is required"}), 400

    # Sanitize name
    name_sanitized = "".join(c for c in name if c.isalnum() or c in (" ", "_", "-")).strip()
    name_sanitized = name_sanitized.upper().replace(" ", "_")
    if not name_sanitized:
        return jsonify({"success": False, "message": "Invalid student name"}), 400

    file = request.files.get('image')
    if not file:
        return jsonify({"success": False, "message": "Face photo is required"}), 400

    try:
        file_bytes = np.frombuffer(file.read(), np.uint8)
        img = cv2.imdecode(file_bytes, cv2.IMREAD_COLOR)
        if img is None:
            return jsonify({"success": False, "message": "Uploaded file is not a valid image"}), 400

        # Validate that a face is detected in the image first!
        net = get_face_detector()
        faces = detect_faces(img, net)
        if not faces:
            return jsonify({
                "success": False, 
                "message": "No face detected in the photo. Please capture/upload a clear frontal photo of the face."
            }), 400

        # Save to ImagesAttendance folder
        filename = f"{name_sanitized}.jpg"
        target_path = IMAGE_DIR / filename
        cv2.imwrite(str(target_path), img)
        
        # Log student registration
        user_id = session.get('user_id')
        log_audit_action(user_id, 'student_registered', 'students', details=f'Student registered: {name}')

        # Retrain the FaceRecognizerSF embedding model
        success = train_model()
        
        with model_lock:
            err = training_error
            
        if success:
            return jsonify({"success": True, "message": f"Student '{name.upper()}' registered and trained successfully!"})
        else:
            # Delete file if training fails or has issues
            if target_path.exists():
                target_path.unlink()
            return jsonify({"success": False, "message": f"Failed to register: {err}"}), 500

    except Exception as e:
        return jsonify({"success": False, "message": f"Server error: {e}"}), 500


@app.route('/api/students/<filename>', methods=['DELETE'])
@teacher_required
def delete_student(filename):
    """Delete a student and retrain the model."""
    target_path = IMAGE_DIR / filename
    if not target_path.exists():
        return jsonify({"success": False, "message": "Student record not found"}), 404
        
    try:
        # Log deletion
        user_id = session.get('user_id')
        log_audit_action(user_id, 'student_deleted', 'students', details=f'Student deleted: {filename}')
        
        target_path.unlink()
        print(f"Deleted student image: {filename}")
        
        # Retrain face recognition model
        train_model()
        return jsonify({"success": True, "message": "Student profile deleted successfully and model retrained."})
    except Exception as e:
        return jsonify({"success": False, "message": f"Error deleting student: {e}"}), 500


@app.route('/api/attendance', methods=['GET'])
@login_required
def get_attendance():
    """Retrieve full attendance list from CSV."""
    records = []
    if not ATTENDANCE_FILE.exists():
        return jsonify({"records": []})

    try:
        with ATTENDANCE_FILE.open("r", encoding="utf-8") as f:
            reader = csv.reader(f)
            next(reader, None)  # Skip header
            for i, row in enumerate(reader):
                if len(row) >= 4:
                    records.append({
                        "id": i + 1,
                        "name": row[0],
                        "date": row[1],
                        "time": row[2],
                        "status": row[3]
                    })
    except Exception as e:
        return jsonify({"success": False, "message": f"Error parsing CSV: {e}"}), 500

    # Return list reversed so latest marks show up first
    return jsonify({"records": list(reversed(records))})


def build_export_response(rows, filename_prefix, format_type='csv'):
    """Helper to convert list of dicts into CSV, Excel (.xlsx), or PDF (.pdf) Flask Response."""
    format_type = (format_type or 'csv').lower()
    
    if format_type in ('xlsx', 'excel'):
        import pandas as pd
        import io
        df = pd.DataFrame(rows)
        output = io.BytesIO()
        with pd.ExcelWriter(output, engine='openpyxl') as writer:
            df.to_excel(writer, index=False, sheet_name='Report')
        output.seek(0)
        return send_file(output, mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet', as_attachment=True, download_name=f"{filename_prefix}.xlsx")
        
    elif format_type == 'pdf':
        import io
        from reportlab.lib.pagesizes import letter
        from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle
        from reportlab.lib.styles import getSampleStyleSheet
        from reportlab.lib import colors
        
        output = io.BytesIO()
        doc = SimpleDocTemplate(output, pagesize=letter)
        elements = []
        styles = getSampleStyleSheet()
        
        elements.append(Paragraph(f"<b>{filename_prefix.replace('_', ' ').title()}</b>", styles['Heading1']))
        elements.append(Spacer(1, 12))
        
        if rows:
            headers = list(rows[0].keys())
            table_data = [headers]
            for row in rows:
                table_data.append([str(row.get(h, '')) for h in headers])
            
            t = Table(table_data)
            t.setStyle(TableStyle([
                ('BACKGROUND', (0,0), (-1,0), colors.HexColor('#22D3EE')),
                ('TEXTCOLOR', (0,0), (-1,0), colors.black),
                ('ALIGN', (0,0), (-1,-1), 'LEFT'),
                ('FONTNAME', (0,0), (-1,0), 'Helvetica-Bold'),
                ('FONTSIZE', (0,0), (-1,0), 10),
                ('BOTTOMPADDING', (0,0), (-1,0), 6),
                ('GRID', (0,0), (-1,-1), 0.5, colors.grey),
            ]))
            elements.append(t)
        else:
            elements.append(Paragraph("No records found.", styles['Normal']))
            
        doc.build(elements)
        output.seek(0)
        return send_file(output, mimetype='application/pdf', as_attachment=True, download_name=f"{filename_prefix}.pdf")
        
    else:
        import csv
        import io
        output = io.StringIO()
        if rows:
            writer = csv.DictWriter(output, fieldnames=list(rows[0].keys()))
            writer.writeheader()
            writer.writerows(rows)
        return Response(output.getvalue(), mimetype='text/csv', headers={"Content-Disposition": f"attachment; filename={filename_prefix}.csv"})


@app.route('/api/attendance/download', methods=['GET'])
@teacher_required
def download_attendance():
    """Download Attendance report file (supports format=csv, xlsx, pdf)."""
    user_id = session.get('user_id')
    format_type = request.args.get('format', 'csv')
    log_audit_action(user_id, 'attendance_downloaded', 'attendance_records', details=f'User downloaded attendance report ({format_type})')
    
    rows = []
    if ATTENDANCE_FILE.exists():
        try:
            with ATTENDANCE_FILE.open("r", encoding="utf-8") as f:
                reader = csv.DictReader(f)
                rows = list(reader)
        except Exception as e:
            print(f"Error reading attendance file for download: {e}")
            
    return build_export_response(rows, "Attendance_Report", format_type)



@app.route('/api/attendance', methods=['DELETE'])
@admin_required
def clear_attendance():
    """Clear all attendance history."""
    user_id = session.get('user_id')
    log_audit_action(user_id, 'attendance_cleared', 'attendance_records', details='Admin cleared all attendance records')
    
    try:
        with ATTENDANCE_FILE.open("w", encoding="utf-8") as f:
            f.write("Name,Date,Time,Status\n")
        return jsonify({"success": True, "message": "Attendance history cleared successfully."})
    except Exception as e:
        return jsonify({"success": False, "message": f"Error resetting logs: {e}"}), 500


@app.route('/api/stats', methods=['GET'])
@login_required
def get_stats():
    """Calculate attendance statistics for Dashboard widgets."""
    today = datetime.now().strftime("%Y-%m-%d")
    
    # Students count
    image_files = sorted(IMAGE_DIR.iterdir()) if IMAGE_DIR.exists() else []
    total_students = len([img for img in image_files if img.suffix.lower() in {".jpg", ".jpeg", ".png", ".bmp", ".webp"}])
    
    # Read CSV records
    records_today = 0
    unique_present_today = set()
    last_present = "N/A"
    last_time = ""

    if ATTENDANCE_FILE.exists():
        try:
            with ATTENDANCE_FILE.open("r", encoding="utf-8") as f:
                reader = csv.reader(f)
                next(reader, None)  # header
                for row in reader:
                    if len(row) >= 3:
                        name, r_date, r_time = row[0], row[1], row[2]
                        if r_date == today:
                            records_today += 1
                            unique_present_today.add(name.upper())
                            last_present = name.upper()
                            last_time = r_time
        except Exception as e:
            print(f"Error compiling stats: {e}")

    present_count = len(unique_present_today)
    absent_count = max(0, total_students - present_count)
    rate = int((present_count / total_students * 100)) if total_students > 0 else 0

    return jsonify({
        "total_students": total_students,
        "total_present_today": present_count,
        "total_absent_today": absent_count,
        "attendance_rate": rate,
        "last_student": f"{last_present} ({last_time})" if last_present != "N/A" else "N/A"
    })


# ============================================================================
# PHASE 3: ADMIN MANAGEMENT & DASHBOARD
# ============================================================================

@app.route('/api/admin/dashboard', methods=['GET'])
@admin_required
def admin_dashboard():
    """Get admin dashboard summary statistics."""
    user_id = session.get('user_id')
    log_audit_action(user_id, 'admin_dashboard_viewed', 'admin_access', details='Admin viewed dashboard')
    
    try:
        total_users = User.query.count()
        admin_count = User.query.filter_by(role='admin').count()
        faculty_count = User.query.filter_by(role='faculty').count()
        teacher_count = User.query.filter_by(role='teacher').count()
        student_count = User.query.filter_by(role='student').count()
        
        total_departments = Department.query.count()
        total_classes = Class.query.count()
        total_subjects = Subject.query.count()
        
        total_sessions = AttendanceSession.query.count()
        completed_sessions = AttendanceSession.query.filter_by(status='completed').count()
        active_sessions = AttendanceSession.query.filter_by(status='active').count()
        
        return jsonify({
            "success": True,
            "users": {
                "total": total_users,
                "admins": admin_count,
                "faculty": faculty_count,
                "teachers": teacher_count,
                "students": student_count
            },
            "structure": {
                "departments": total_departments,
                "classes": total_classes,
                "subjects": total_subjects
            },
            "sessions": {
                "total": total_sessions,
                "completed": completed_sessions,
                "active": active_sessions
            }
        })
    except Exception as e:
        return jsonify({"success": False, "message": f"Error fetching dashboard data: {e}"}), 500


# ============================================================================
# ADMIN USERS MANAGEMENT
# ============================================================================

@app.route('/api/admin/users', methods=['GET'])
@admin_required
def list_users():
    """List all users with their roles and status."""
    try:
        users = User.query.all()
        user_list = []
        for u in users:
            user_list.append({
                "id": u.id,
                "email": u.email,
                "name": u.name,
                "role": u.role,
                "is_active": u.is_active,
                "created_at": u.created_at.isoformat() if u.created_at else None,
                "last_login": u.last_login.isoformat() if u.last_login else None
            })
        return jsonify({"success": True, "users": user_list})
    except Exception as e:
        return jsonify({"success": False, "message": f"Error fetching users: {e}"}), 500


@app.route('/api/admin/users', methods=['POST'])
@admin_required
def create_user():
    """Create a new user account."""
    user_id = session.get('user_id')
    
    try:
        data = request.get_json()
        email = data.get('email', '').strip().lower()
        password = data.get('password', '').strip()
        name = data.get('name', '').strip()
        role = data.get('role', 'student').lower()
        
        if not email or not password or not name:
            return jsonify({"success": False, "message": "Email, password, and name are required"}), 400
        
        if role not in ['admin', 'faculty', 'teacher', 'student']:
            return jsonify({"success": False, "message": "Invalid role. Must be admin, faculty, teacher, or student"}), 400
        
        if User.query.filter_by(email=email).first():
            return jsonify({"success": False, "message": "Email already registered"}), 409
        
        if len(password) < 6:
            return jsonify({"success": False, "message": "Password must be at least 6 characters"}), 400
        
        new_user = User(email=email, name=name, role=role, is_active=True)
        new_user.set_password(password)
        
        db.session.add(new_user)
        db.session.commit()
        
        log_audit_action(user_id, 'user_created', 'users', record_id=new_user.id, 
                        details=f'New user created: {email} ({role})')
        
        return jsonify({
            "success": True, 
            "message": f"User '{email}' created successfully",
            "user_id": new_user.id
        }), 201
    except Exception as e:
        db.session.rollback()
        return jsonify({"success": False, "message": f"Error creating user: {e}"}), 500


@app.route('/api/admin/users/<int:target_user_id>', methods=['GET'])
@admin_required
def get_user(target_user_id):
    """Get specific user details."""
    try:
        user = User.query.get(target_user_id)
        if not user:
            return jsonify({"success": False, "message": "User not found"}), 404
        
        return jsonify({
            "success": True,
            "user": {
                "id": user.id,
                "email": user.email,
                "name": user.name,
                "role": user.role,
                "is_active": user.is_active,
                "created_at": user.created_at.isoformat() if user.created_at else None,
                "updated_at": user.updated_at.isoformat() if user.updated_at else None,
                "last_login": user.last_login.isoformat() if user.last_login else None
            }
        })
    except Exception as e:
        return jsonify({"success": False, "message": f"Error fetching user: {e}"}), 500


@app.route('/api/admin/users/<int:target_user_id>', methods=['PUT'])
@admin_required
def update_user(target_user_id):
    """Update user details (name, role, active status)."""
    admin_user_id = session.get('user_id')
    
    try:
        user = User.query.get(target_user_id)
        if not user:
            return jsonify({"success": False, "message": "User not found"}), 404
        
        if user.id == admin_user_id:
            return jsonify({"success": False, "message": "Cannot modify your own account"}), 403
        
        data = request.get_json()
        old_data = {
            "name": user.name,
            "role": user.role,
            "is_active": user.is_active
        }
        
        if 'name' in data:
            user.name = data['name'].strip()
        if 'role' in data:
            new_role = data['role'].lower()
            if new_role not in ['admin', 'faculty', 'teacher', 'student']:
                return jsonify({"success": False, "message": "Invalid role"}), 400
            user.role = new_role
        if 'is_active' in data:
            user.is_active = bool(data['is_active'])
        
        db.session.commit()
        
        log_audit_action(admin_user_id, 'user_updated', 'users', record_id=user.id,
                        old_value=str(old_data), new_value=str({"name": user.name, "role": user.role, "is_active": user.is_active}),
                        details=f'User updated: {user.email}')
        
        return jsonify({"success": True, "message": "User updated successfully"})
    except Exception as e:
        db.session.rollback()
        return jsonify({"success": False, "message": f"Error updating user: {e}"}), 500


@app.route('/api/admin/users/<int:target_user_id>/reset-password', methods=['PUT'])
@admin_required
def admin_reset_password(target_user_id):
    """Admin endpoint to reset a user's password."""
    admin_user_id = session.get('user_id')
    try:
        user = User.query.get(target_user_id)
        if not user:
            return jsonify({"success": False, "message": "User not found"}), 404
        data = request.get_json() or {}
        new_password = data.get('new_password', '').strip()
        if not new_password or len(new_password) < 6:
            return jsonify({"success": False, "message": "Password must be at least 6 characters"}), 400
        
        user.set_password(new_password)
        db.session.commit()
        log_audit_action(admin_user_id, 'password_reset', 'users', record_id=user.id, details=f'Reset password for {user.email}')
        return jsonify({"success": True, "message": f"Password reset successfully for {user.email}"})
    except Exception as e:
        db.session.rollback()
        return jsonify({"success": False, "message": str(e)}), 500


# ============================================================================
# ADMIN STUDENT & FACE MANAGEMENT
# ============================================================================

@app.route('/api/admin/students', methods=['GET'])
@admin_required
def admin_list_students():
    """List all students with class & department info."""
    try:
        students = Student.query.all()
        result = []
        for s in students:
            result.append({
                "id": s.id,
                "name": s.name,
                "roll_no": s.roll_no,
                "admission_no": s.admission_no,
                "email": s.email,
                "class_id": s.class_id,
                "class_name": s.class_.name if s.class_ else None,
                "department_id": s.department_id,
                "department_name": s.department.name if s.department else None,
                "face_registered": s.face_registered,
                "user_id": s.user_id
            })
        return jsonify({"success": True, "students": result})
    except Exception as e:
        return jsonify({"success": False, "message": str(e)}), 500


@app.route('/api/admin/students', methods=['POST'])
@admin_required
def admin_create_student():
    """Create a new student."""
    admin_user_id = session.get('user_id')
    try:
        data = request.get_json() or {}
        name = data.get('name', '').strip()
        roll_no = data.get('roll_no', '').strip()
        class_id = data.get('class_id')
        dept_id = data.get('department_id')
        email = data.get('email', '').strip().lower()
        
        if not name or not roll_no:
            return jsonify({"success": False, "message": "Name and Roll No are required"}), 400
        if Student.query.filter_by(roll_no=roll_no).first():
            return jsonify({"success": False, "message": "Roll No already exists"}), 409
            
        student = Student(name=name, roll_no=roll_no, class_id=class_id, department_id=dept_id, email=email)
        db.session.add(student)
        db.session.commit()
        log_audit_action(admin_user_id, 'student_created', 'students', record_id=student.id, details=f'Created student: {name} ({roll_no})')
        return jsonify({"success": True, "message": f"Student '{name}' created successfully", "student_id": student.id}), 201
    except Exception as e:
        db.session.rollback()
        return jsonify({"success": False, "message": str(e)}), 500


@app.route('/api/admin/students/<int:student_id>/register-face', methods=['POST'])
@admin_required
def admin_register_student_face(student_id):
    """Register or re-capture facial data for a student."""
    admin_user_id = session.get('user_id')
    try:
        student = Student.query.get(student_id)
        if not student:
            return jsonify({"success": False, "message": "Student not found"}), 404
        
        if 'image' in request.files:
            file = request.files['image']
            filename = secure_filename(file.filename)
            filepath = IMAGE_DIR / f"{student.roll_no}_{filename}"
            file.save(filepath)
            student.face_registered = True
            student.face_images_count += 1
            db.session.commit()
            train_model()
            log_audit_action(admin_user_id, 'face_registered', 'students', record_id=student.id, details=f'Registered face for {student.name}')
            return jsonify({"success": True, "message": "Face registered and model retrained successfully."})
        else:
            data = request.get_json() or {}
            if data.get('face_registered') is not None:
                student.face_registered = bool(data['face_registered'])
                db.session.commit()
                return jsonify({"success": True, "message": "Student face status updated."})
            return jsonify({"success": False, "message": "No image file provided"}), 400
    except Exception as e:
        db.session.rollback()
        return jsonify({"success": False, "message": str(e)}), 500


# ============================================================================
# ACADEMIC SUBJECTS MANAGEMENT
# ============================================================================

@app.route('/api/admin/subjects', methods=['GET'])
@admin_required
def admin_list_subjects():
    try:
        subjects = Subject.query.all()
        res = []
        for s in subjects:
            res.append({
                "id": s.id,
                "name": s.name,
                "code": s.code,
                "class_id": s.class_id,
                "class_name": s.class_.name if s.class_ else None,
                "credits": s.credits
            })
        return jsonify({"success": True, "subjects": res})
    except Exception as e:
        return jsonify({"success": False, "message": str(e)}), 500


@app.route('/api/admin/subjects', methods=['POST'])
@admin_required
def admin_create_subject():
    admin_user_id = session.get('user_id')
    try:
        data = request.get_json() or {}
        name = data.get('name', '').strip()
        code = data.get('code', '').strip().upper()
        class_id = data.get('class_id')
        credits = data.get('credits', 3)
        if not name or not code or not class_id:
            return jsonify({"success": False, "message": "Name, code, and class_id are required"}), 400
        
        subj = Subject(name=name, code=code, class_id=class_id, credits=credits)
        db.session.add(subj)
        db.session.commit()
        log_audit_action(admin_user_id, 'subject_created', 'subjects', record_id=subj.id, details=f'Created subject: {name}')
        return jsonify({"success": True, "message": f"Subject '{name}' created successfully", "subject_id": subj.id}), 201
    except Exception as e:
        db.session.rollback()
        return jsonify({"success": False, "message": str(e)}), 500


# ============================================================================
# SYSTEM SETTINGS & AUDIT LOGS
# ============================================================================

@app.route('/api/admin/settings', methods=['GET'])
@admin_required
def admin_get_settings():
    try:
        settings = SystemSetting.query.all()
        res = {s.key: {"value": s.value, "description": s.description} for s in settings}
        defaults = {
            "confidence_threshold": "0.6",
            "session_duration_minutes": "60",
            "late_threshold_minutes": "15",
            "academic_year": "2025-2026",
            "academic_semester": "Spring"
        }
        for k, v in defaults.items():
            if k not in res:
                res[k] = {"value": v, "description": f"Default {k}"}
        return jsonify({"success": True, "settings": res})
    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({"success": False, "message": f"Settings error: {e}"}), 500


@app.route('/api/admin/settings', methods=['PUT'])
@admin_required
def admin_update_settings():
    admin_user_id = session.get('user_id')
    try:
        data = request.get_json() or {}
        for key, value in data.items():
            setting = SystemSetting.query.filter_by(key=key).first()
            if setting:
                setting.value = str(value)
            else:
                setting = SystemSetting(key=key, value=str(value), description=f"Setting for {key}")
                db.session.add(setting)
        db.session.commit()
        log_audit_action(admin_user_id, 'settings_updated', 'system_settings', details=f'Updated settings: {list(data.keys())}')
        return jsonify({"success": True, "message": "Settings updated successfully"})
    except Exception as e:
        db.session.rollback()
        import traceback
        traceback.print_exc()
        return jsonify({"success": False, "message": f"Update error: {e}"}), 500


@app.route('/api/admin/audit-logs', methods=['GET'])
@admin_required
def admin_get_audit_logs():
    try:
        user_id = request.args.get('user_id', type=int)
        action = request.args.get('action')
        limit = request.args.get('limit', 100, type=int)
        
        query = AuditLog.query
        if user_id:
            query = query.filter_by(user_id=user_id)
        if action:
            query = query.filter(AuditLog.action.ilike(f"%{action}%"))
        
        logs = query.order_by(AuditLog.timestamp.desc()).limit(limit).all()
        res = []
        for l in logs:
            user_name = "System"
            if l.user_id:
                u = User.query.get(l.user_id)
                if u:
                    user_name = u.name
            res.append({
                "id": l.id,
                "user_id": l.user_id,
                "user_name": user_name,
                "action": l.action,
                "table_name": l.table_name,
                "record_id": l.record_id,
                "old_value": l.old_value,
                "new_value": l.new_value,
                "details": l.details,
                "ip_address": l.ip_address,
                "timestamp": l.timestamp.isoformat() if l.timestamp else None
            })
        return jsonify({"success": True, "logs": res})
    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({"success": False, "message": f"Audit log error: {e}"}), 500







@app.route('/api/admin/users/<int:target_user_id>', methods=['DELETE'])
@admin_required
def delete_user(target_user_id):
    """Delete a user account."""
    admin_user_id = session.get('user_id')
    
    try:
        user = User.query.get(target_user_id)
        if not user:
            return jsonify({"success": False, "message": "User not found"}), 404
        
        if user.id == admin_user_id:
            return jsonify({"success": False, "message": "Cannot delete your own account"}), 403
        
        email = user.email
        role = user.role
        
        db.session.delete(user)
        db.session.commit()
        
        log_audit_action(admin_user_id, 'user_deleted', 'users', record_id=target_user_id,
                        details=f'User deleted: {email} ({role})')
        
        return jsonify({"success": True, "message": f"User '{email}' deleted successfully"})
    except Exception as e:
        db.session.rollback()
        return jsonify({"success": False, "message": f"Error deleting user: {e}"}), 500


# ============================================================================
# ADMIN DEPARTMENTS MANAGEMENT
# ============================================================================

@app.route('/api/admin/departments', methods=['GET'])
@admin_required
def list_departments():
    """List all departments."""
    try:
        departments = Department.query.all()
        dept_list = []
        for d in departments:
            dept_list.append({
                "id": d.id,
                "name": d.name,
                "code": d.code,
                "description": d.description,
                "class_count": len(d.classes) if d.classes else 0
            })
        return jsonify({"success": True, "departments": dept_list})
    except Exception as e:
        return jsonify({"success": False, "message": f"Error fetching departments: {e}"}), 500


@app.route('/api/admin/departments', methods=['POST'])
@admin_required
def create_department():
    """Create a new department."""
    user_id = session.get('user_id')
    
    try:
        data = request.get_json()
        name = data.get('name', '').strip()
        code = data.get('code', '').strip().upper()
        description = data.get('description', '').strip()
        
        if not name or not code:
            return jsonify({"success": False, "message": "Name and code are required"}), 400
        
        if Department.query.filter_by(name=name).first():
            return jsonify({"success": False, "message": "Department name already exists"}), 409
        if Department.query.filter_by(code=code).first():
            return jsonify({"success": False, "message": "Department code already exists"}), 409
        
        new_dept = Department(name=name, code=code, description=description)
        db.session.add(new_dept)
        db.session.commit()
        
        log_audit_action(user_id, 'department_created', 'departments', record_id=new_dept.id,
                        details=f'Department created: {name} ({code})')
        
        return jsonify({
            "success": True,
            "message": f"Department '{name}' created successfully",
            "department_id": new_dept.id
        }), 201
    except Exception as e:
        db.session.rollback()
        return jsonify({"success": False, "message": f"Error creating department: {e}"}), 500


@app.route('/api/admin/departments/<int:dept_id>', methods=['PUT'])
@admin_required
def update_department(dept_id):
    """Update department details."""
    user_id = session.get('user_id')
    
    try:
        dept = Department.query.get(dept_id)
        if not dept:
            return jsonify({"success": False, "message": "Department not found"}), 404
        
        data = request.get_json()
        old_data = {"name": dept.name, "code": dept.code, "description": dept.description}
        
        if 'name' in data:
            new_name = data['name'].strip()
            if new_name != dept.name and Department.query.filter_by(name=new_name).first():
                return jsonify({"success": False, "message": "Department name already exists"}), 409
            dept.name = new_name
        
        if 'code' in data:
            new_code = data['code'].strip().upper()
            if new_code != dept.code and Department.query.filter_by(code=new_code).first():
                return jsonify({"success": False, "message": "Department code already exists"}), 409
            dept.code = new_code
        
        if 'description' in data:
            dept.description = data['description'].strip()
        
        db.session.commit()
        
        log_audit_action(user_id, 'department_updated', 'departments', record_id=dept.id,
                        old_value=str(old_data), new_value=str({"name": dept.name, "code": dept.code, "description": dept.description}),
                        details=f'Department updated: {dept.name}')
        
        return jsonify({"success": True, "message": "Department updated successfully"})
    except Exception as e:
        db.session.rollback()
        return jsonify({"success": False, "message": f"Error updating department: {e}"}), 500


@app.route('/api/admin/departments/<int:dept_id>', methods=['DELETE'])
@admin_required
def delete_department(dept_id):
    """Delete a department."""
    user_id = session.get('user_id')
    
    try:
        dept = Department.query.get(dept_id)
        if not dept:
            return jsonify({"success": False, "message": "Department not found"}), 404
        
        if dept.classes and len(dept.classes) > 0:
            return jsonify({"success": False, "message": "Cannot delete department with existing classes"}), 409
        
        name = dept.name
        db.session.delete(dept)
        db.session.commit()
        
        log_audit_action(user_id, 'department_deleted', 'departments', record_id=dept_id,
                        details=f'Department deleted: {name}')
        
        return jsonify({"success": True, "message": f"Department '{name}' deleted successfully"})
    except Exception as e:
        db.session.rollback()
        return jsonify({"success": False, "message": f"Error deleting department: {e}"}), 500


# ============================================================================
# ADMIN CLASSES MANAGEMENT
# ============================================================================

@app.route('/api/admin/classes', methods=['GET'])
@admin_required
def list_classes():
    """List all classes with department info."""
    try:
        classes = Class.query.all()
        class_list = []
        for c in classes:
            class_list.append({
                "id": c.id,
                "name": c.name,
                "code": c.code,
                "department_id": c.department_id,
                "department_name": c.department.name if c.department else None,
                "capacity": c.capacity,
                "student_count": len(c.students) if c.students else 0
            })
        return jsonify({"success": True, "classes": class_list})
    except Exception as e:
        return jsonify({"success": False, "message": f"Error fetching classes: {e}"}), 500


@app.route('/api/admin/classes', methods=['POST'])
@admin_required
def create_class():
    """Create a new class."""
    user_id = session.get('user_id')
    
    try:
        data = request.get_json()
        name = data.get('name', '').strip()
        code = data.get('code', '').strip().upper()
        department_id = data.get('department_id')
        capacity = data.get('capacity', 30)
        
        if not name or not code or not department_id:
            return jsonify({"success": False, "message": "Name, code, and department_id are required"}), 400
        
        if not Department.query.get(department_id):
            return jsonify({"success": False, "message": "Department not found"}), 404
        
        if Class.query.filter_by(code=code).first():
            return jsonify({"success": False, "message": "Class code already exists"}), 409
        
        new_class = Class(name=name, code=code, department_id=department_id, capacity=capacity)
        db.session.add(new_class)
        db.session.commit()
        
        log_audit_action(user_id, 'class_created', 'classes', record_id=new_class.id,
                        details=f'Class created: {name} ({code})')
        
        return jsonify({
            "success": True,
            "message": f"Class '{name}' created successfully",
            "class_id": new_class.id
        }), 201
    except Exception as e:
        db.session.rollback()
        return jsonify({"success": False, "message": f"Error creating class: {e}"}), 500


@app.route('/api/admin/classes/<int:class_id>', methods=['PUT'])
@admin_required
def update_class(class_id):
    """Update class details."""
    user_id = session.get('user_id')
    
    try:
        cls = Class.query.get(class_id)
        if not cls:
            return jsonify({"success": False, "message": "Class not found"}), 404
        
        data = request.get_json()
        old_data = {"name": cls.name, "code": cls.code, "capacity": cls.capacity}
        
        if 'name' in data:
            cls.name = data['name'].strip()
        if 'code' in data:
            new_code = data['code'].strip().upper()
            if new_code != cls.code and Class.query.filter_by(code=new_code).first():
                return jsonify({"success": False, "message": "Class code already exists"}), 409
            cls.code = new_code
        if 'capacity' in data:
            cls.capacity = int(data['capacity'])
        if 'department_id' in data:
            if not Department.query.get(data['department_id']):
                return jsonify({"success": False, "message": "Department not found"}), 404
            cls.department_id = data['department_id']
        
        db.session.commit()
        
        log_audit_action(user_id, 'class_updated', 'classes', record_id=cls.id,
                        old_value=str(old_data), new_value=str({"name": cls.name, "code": cls.code, "capacity": cls.capacity}),
                        details=f'Class updated: {cls.name}')
        
        return jsonify({"success": True, "message": "Class updated successfully"})
    except Exception as e:
        db.session.rollback()
        return jsonify({"success": False, "message": f"Error updating class: {e}"}), 500


@app.route('/api/admin/classes/<int:class_id>', methods=['DELETE'])
@admin_required
def delete_class(class_id):
    """Delete a class."""
    user_id = session.get('user_id')
    
    try:
        cls = Class.query.get(class_id)
        if not cls:
            return jsonify({"success": False, "message": "Class not found"}), 404
        
        if cls.students and len(cls.students) > 0:
            return jsonify({"success": False, "message": "Cannot delete class with enrolled students"}), 409
        
        name = cls.name
        db.session.delete(cls)
        db.session.commit()
        
        log_audit_action(user_id, 'class_deleted', 'classes', record_id=class_id,
                        details=f'Class deleted: {name}')
        
        return jsonify({"success": True, "message": f"Class '{name}' deleted successfully"})
    except Exception as e:
        db.session.rollback()
        return jsonify({"success": False, "message": f"Error deleting class: {e}"}), 500


# ==================== TEACHER DASHBOARD & FEATURES ====================

@app.route('/api/teacher/dashboard', methods=['GET'])
@teacher_required
def teacher_dashboard():
    """Get teacher dashboard summary with statistics and overview."""
    user_id = session.get('user_id')
    
    try:
        user = User.query.get(user_id)
        if not user or not user.teacher:
            return jsonify({"success": False, "message": "Teacher profile not found"}), 404
        
        teacher = user.teacher
        dept_id = teacher.department_id
        
        # Get today's date
        from datetime import date
        today = date.today()
        
        # Count statistics
        assigned_classes = Class.query.filter(
            Class.subjects.any(AttendanceSession.teacher_id == teacher.id)
        ).distinct().count()
        
        assigned_subjects = Subject.query.filter(
            Subject.sessions.any(AttendanceSession.teacher_id == teacher.id)
        ).distinct().count()
        
        # Today's sessions
        todays_sessions = AttendanceSession.query.filter(
            AttendanceSession.teacher_id == teacher.id,
            AttendanceSession.date == today
        ).all()
        
        # Attendance stats for today
        total_present_today = 0
        total_marked_today = 0
        for session in todays_sessions:
            marked = AttendanceRecord.query.filter(
                AttendanceRecord.session_id == session.id
            ).count()
            total_marked_today += marked
            present = AttendanceRecord.query.filter(
                AttendanceRecord.session_id == session.id,
                AttendanceRecord.status == 'present'
            ).count()
            total_present_today += present
        
        # Overall class attendance percentage (last 30 days)
        from datetime import timedelta
        thirty_days_ago = today - timedelta(days=30)
        
        recent_sessions = AttendanceSession.query.filter(
            AttendanceSession.teacher_id == teacher.id,
            AttendanceSession.date >= thirty_days_ago
        ).all()
        
        total_attendance_records = 0
        total_present_records = 0
        for session in recent_sessions:
            records = AttendanceRecord.query.filter(
                AttendanceRecord.session_id == session.id
            ).all()
            for record in records:
                if record.status != 'absent':
                    total_present_records += 1
                total_attendance_records += 1
        
        class_avg_attendance = (total_present_records / total_attendance_records * 100) if total_attendance_records > 0 else 0
        
        # Low attendance students (less than 75% in last 30 days)
        low_attendance_threshold = 75
        low_attendance_count = 0
        
        for session in recent_sessions:
            for student in session.class_.students:
                student_records = AttendanceRecord.query.filter(
                    AttendanceRecord.student_id == student.id,
                    AttendanceRecord.session_id.in_([s.id for s in recent_sessions])
                ).all()
                
                if len(student_records) > 0:
                    present_count = sum(1 for r in student_records if r.status != 'absent')
                    attendance_pct = (present_count / len(student_records)) * 100
                    if attendance_pct < low_attendance_threshold:
                        low_attendance_count += 1
        
        return jsonify({
            "success": True,
            "data": {
                "teacher_name": user.name,
                "assigned_classes": assigned_classes,
                "assigned_subjects": assigned_subjects,
                "todays_sessions_count": len(todays_sessions),
                "todays_attendance": {
                    "marked": total_marked_today,
                    "present": total_present_today
                },
                "class_avg_attendance": round(class_avg_attendance, 2),
                "low_attendance_students": low_attendance_count,
                "department": user.teacher.department.name if user.teacher.department else "N/A"
            }
        })
    except Exception as e:
        return jsonify({"success": False, "message": f"Error fetching dashboard: {e}"}), 500


@app.route('/api/teacher/classes', methods=['GET'])
@teacher_required
def teacher_get_classes():
    """Get all classes assigned to the teacher."""
    user_id = session.get('user_id')
    
    try:
        user = User.query.get(user_id)
        if not user or not user.teacher:
            return jsonify({"success": False, "message": "Teacher profile not found"}), 404
        
        teacher = user.teacher
        
        # Get classes for which teacher has created attendance sessions
        classes = Class.query.filter(
            Class.sessions.any(AttendanceSession.teacher_id == teacher.id)
        ).distinct().all()
        
        classes_data = []
        for cls in classes:
            classes_data.append({
                "id": cls.id,
                "name": cls.name,
                "code": cls.code,
                "capacity": cls.capacity,
                "department": cls.department.name if cls.department else "N/A"
            })
        
        return jsonify({
            "success": True,
            "data": classes_data
        })
    except Exception as e:
        return jsonify({"success": False, "message": f"Error fetching classes: {e}"}), 500


@app.route('/api/teacher/classes/<int:class_id>/attendance-stats', methods=['GET'])
@teacher_required
def teacher_class_attendance_stats(class_id):
    """Get attendance statistics for a specific class."""
    user_id = session.get('user_id')
    
    try:
        user = User.query.get(user_id)
        if not user or not user.teacher:
            return jsonify({"success": False, "message": "Teacher profile not found"}), 404
        
        teacher = user.teacher
        cls = Class.query.get(class_id)
        
        if not cls:
            return jsonify({"success": False, "message": "Class not found"}), 404
        
        # Get all sessions for this teacher and class
        sessions = AttendanceSession.query.filter(
            AttendanceSession.teacher_id == teacher.id,
            AttendanceSession.class_id == class_id
        ).all()
        
        if not sessions:
            return jsonify({
                "success": True,
                "data": {
                    "class_name": cls.name,
                    "class_code": cls.code,
                    "total_students": len(cls.students),
                    "average_attendance": 0,
                    "total_sessions": 0,
                    "student_attendance": []
                }
            })
        
        # Calculate per-student attendance
        student_attendance = []
        for student in cls.students:
            records = AttendanceRecord.query.filter(
                AttendanceRecord.student_id == student.id,
                AttendanceRecord.session_id.in_([s.id for s in sessions])
            ).all()
            
            if len(records) > 0:
                present = sum(1 for r in records if r.status != 'absent')
                percentage = (present / len(records)) * 100
            else:
                present = 0
                percentage = 0
            
            student_attendance.append({
                "student_id": student.id,
                "name": student.name,
                "roll_no": student.roll_no,
                "present": present,
                "total": len(records),
                "percentage": round(percentage, 2)
            })
        
        # Calculate class average
        if student_attendance:
            avg_attendance = sum(s["percentage"] for s in student_attendance) / len(student_attendance)
        else:
            avg_attendance = 0
        
        return jsonify({
            "success": True,
            "data": {
                "class_name": cls.name,
                "class_code": cls.code,
                "total_students": len(cls.students),
                "average_attendance": round(avg_attendance, 2),
                "total_sessions": len(sessions),
                "student_attendance": student_attendance
            }
        })
    except Exception as e:
        return jsonify({"success": False, "message": f"Error fetching attendance stats: {e}"}), 500


@app.route('/api/teacher/low-attendance', methods=['GET'])
@teacher_required
def teacher_low_attendance():
    """Get students with low attendance across all classes."""
    user_id = session.get('user_id')
    threshold = request.args.get('threshold', 75, type=float)
    
    try:
        user = User.query.get(user_id)
        if not user or not user.teacher:
            return jsonify({"success": False, "message": "Teacher profile not found"}), 404
        
        teacher = user.teacher
        
        # Get all classes taught by this teacher
        classes = Class.query.filter(
            Class.sessions.any(AttendanceSession.teacher_id == teacher.id)
        ).distinct().all()
        
        low_attendance_students = []
        
        for cls in classes:
            sessions = AttendanceSession.query.filter(
                AttendanceSession.teacher_id == teacher.id,
                AttendanceSession.class_id == cls.id
            ).all()
            
            for student in cls.students:
                records = AttendanceRecord.query.filter(
                    AttendanceRecord.student_id == student.id,
                    AttendanceRecord.session_id.in_([s.id for s in sessions])
                ).all()
                
                if len(records) > 0:
                    present = sum(1 for r in records if r.status != 'absent')
                    percentage = (present / len(records)) * 100
                    
                    if percentage < threshold:
                        low_attendance_students.append({
                            "student_id": student.id,
                            "name": student.name,
                            "roll_no": student.roll_no,
                            "class": cls.name,
                            "attendance_percentage": round(percentage, 2),
                            "present": present,
                            "total": len(records)
                        })
        
        # Sort by attendance percentage
        low_attendance_students.sort(key=lambda x: x["attendance_percentage"])
        
        return jsonify({
            "success": True,
            "data": {
                "threshold": threshold,
                "students": low_attendance_students
            }
        })
    except Exception as e:
        return jsonify({"success": False, "message": f"Error fetching low attendance: {e}"}), 500


@app.route('/api/teacher/attendance-sessions', methods=['GET'])
@teacher_required
def teacher_get_sessions():
    """Get all attendance sessions created by the teacher."""
    user_id = session.get('user_id')
    
    try:
        user = User.query.get(user_id)
        if not user or not user.teacher:
            return jsonify({"success": False, "message": "Teacher profile not found"}), 404
        
        teacher = user.teacher
        
        sessions = AttendanceSession.query.filter(
            AttendanceSession.teacher_id == teacher.id
        ).order_by(AttendanceSession.date.desc(), AttendanceSession.start_time.desc()).all()
        
        sessions_data = []
        for sess in sessions:
            sessions_data.append({
                "id": sess.id,
                "class_name": sess.class_.name,
                "subject_name": sess.subject.name if sess.subject else "N/A",
                "date": sess.date.isoformat(),
                "start_time": sess.start_time.isoformat() if sess.start_time else "N/A",
                "end_time": sess.end_time.isoformat() if sess.end_time else "N/A",
                "status": sess.status,
                "total_expected": sess.total_students_expected,
                "notes": sess.notes
            })
        
        return jsonify({
            "success": True,
            "data": sessions_data
        })
    except Exception as e:
        return jsonify({"success": False, "message": f"Error fetching sessions: {e}"}), 500


@app.route('/api/teacher/attendance-sessions', methods=['POST'])
@teacher_required
def teacher_create_session():
    """Create a new attendance session."""
    user_id = session.get('user_id')
    data = request.get_json()
    
    try:
        user = User.query.get(user_id)
        if not user or not user.teacher:
            return jsonify({"success": False, "message": "Teacher profile not found"}), 404
        
        teacher = user.teacher
        
        # Validate required fields
        if not data.get('class_id') or not data.get('date') or not data.get('start_time'):
            return jsonify({"success": False, "message": "Missing required fields"}), 400
        
        class_id = data.get('class_id')
        subject_id = data.get('subject_id')
        date_str = data.get('date')
        start_time_str = data.get('start_time')
        end_time_str = data.get('end_time')
        notes = data.get('notes', '')
        
        cls = Class.query.get(class_id)
        if not cls:
            return jsonify({"success": False, "message": "Class not found"}), 404
        
        if subject_id:
            subject = Subject.query.get(subject_id)
            if not subject:
                return jsonify({"success": False, "message": "Subject not found"}), 404
        
        # Parse dates/times
        from datetime import datetime as dt
        date_obj = dt.strptime(date_str, '%Y-%m-%d').date()
        start_time_obj = dt.strptime(start_time_str, '%H:%M:%S').time()
        end_time_obj = dt.strptime(end_time_str, '%H:%M:%S').time() if end_time_str else None
        
        # Check for duplicate session
        existing = AttendanceSession.query.filter(
            AttendanceSession.teacher_id == teacher.id,
            AttendanceSession.class_id == class_id,
            AttendanceSession.date == date_obj
        ).first()
        
        if existing:
            return jsonify({"success": False, "message": "Session already exists for this class on this date"}), 409
        
        # Create new session
        session_obj = AttendanceSession(
            teacher_id=teacher.id,
            class_id=class_id,
            subject_id=subject_id if subject_id else None,
            date=date_obj,
            start_time=start_time_obj,
            end_time=end_time_obj,
            status='pending',
            total_students_expected=len(cls.students),
            notes=notes
        )
        
        db.session.add(session_obj)
        db.session.commit()
        
        log_audit_action(user_id, 'attendance_session_created', 'attendance_sessions', 
                        record_id=session_obj.id,
                        details=f'Session created: {cls.name} on {date_obj}')
        
        return jsonify({
            "success": True,
            "message": "Attendance session created successfully",
            "data": {
                "session_id": session_obj.id,
                "class_name": cls.name,
                "date": date_obj.isoformat(),
                "status": session_obj.status
            }
        }), 201
    except Exception as e:
        db.session.rollback()
        return jsonify({"success": False, "message": f"Error creating session: {e}"}), 500


@app.route('/api/teacher/attendance-sessions/<int:session_id>', methods=['GET'])
@teacher_required
def teacher_get_session(session_id):
    """Get details of a specific attendance session."""
    user_id = session.get('user_id')
    
    try:
        user = User.query.get(user_id)
        if not user or not user.teacher:
            return jsonify({"success": False, "message": "Teacher profile not found"}), 404
        
        teacher = user.teacher
        session_obj = AttendanceSession.query.get(session_id)
        
        if not session_obj:
            return jsonify({"success": False, "message": "Session not found"}), 404
        
        if session_obj.teacher_id != teacher.id:
            return jsonify({"success": False, "message": "Access denied"}), 403
        
        # Get attendance records for this session
        records = AttendanceRecord.query.filter(
            AttendanceRecord.session_id == session_id
        ).all()
        
        attendance_data = []
        for record in records:
            attendance_data.append({
                "id": record.id,
                "student_name": record.student.name,
                "student_id": record.student.id,
                "roll_no": record.student.roll_no,
                "status": record.status,
                "timestamp": record.timestamp.isoformat() if record.timestamp else None,
                "confidence": record.confidence,
                "marked_by_teacher": record.marked_by_teacher,
                "notes": record.notes
            })
        
        return jsonify({
            "success": True,
            "data": {
                "session_id": session_obj.id,
                "class_name": session_obj.class_.name,
                "subject_name": session_obj.subject.name if session_obj.subject else "N/A",
                "date": session_obj.date.isoformat(),
                "start_time": session_obj.start_time.isoformat() if session_obj.start_time else None,
                "end_time": session_obj.end_time.isoformat() if session_obj.end_time else None,
                "status": session_obj.status,
                "total_expected": session_obj.total_students_expected,
                "notes": session_obj.notes,
                "attendance_records": attendance_data
            }
        })
    except Exception as e:
        return jsonify({"success": False, "message": f"Error fetching session: {e}"}), 500


@app.route('/api/teacher/attendance-sessions/<int:session_id>', methods=['PUT'])
@teacher_required
def teacher_update_session(session_id):
    """Update an attendance session."""
    user_id = session.get('user_id')
    data = request.get_json()
    
    try:
        user = User.query.get(user_id)
        if not user or not user.teacher:
            return jsonify({"success": False, "message": "Teacher profile not found"}), 404
        
        teacher = user.teacher
        session_obj = AttendanceSession.query.get(session_id)
        
        if not session_obj:
            return jsonify({"success": False, "message": "Session not found"}), 404
        
        if session_obj.teacher_id != teacher.id:
            return jsonify({"success": False, "message": "Access denied"}), 403
        
        # Can only update pending sessions
        if session_obj.status != 'pending':
            return jsonify({"success": False, "message": "Can only update pending sessions"}), 409
        
        # Update allowed fields
        if 'status' in data:
            session_obj.status = data['status']
        if 'notes' in data:
            session_obj.notes = data['notes']
        if 'end_time' in data and data['end_time']:
            from datetime import datetime as dt
            session_obj.end_time = dt.strptime(data['end_time'], '%H:%M:%S').time()
        
        session_obj.updated_at = datetime.utcnow()
        db.session.commit()
        
        log_audit_action(user_id, 'attendance_session_updated', 'attendance_sessions',
                        record_id=session_id,
                        details=f'Session updated: {session_obj.class_.name}')
        
        return jsonify({
            "success": True,
            "message": "Session updated successfully"
        })
    except Exception as e:
        db.session.rollback()
        return jsonify({"success": False, "message": f"Error updating session: {e}"}), 500


@app.route('/api/teacher/attendance-sessions/<int:session_id>', methods=['DELETE'])
@teacher_required
def teacher_delete_session(session_id):
    """Delete an attendance session."""
    user_id = session.get('user_id')
    
    try:
        user = User.query.get(user_id)
        if not user or not user.teacher:
            return jsonify({"success": False, "message": "Teacher profile not found"}), 404
        
        teacher = user.teacher
        session_obj = AttendanceSession.query.get(session_id)
        
        if not session_obj:
            return jsonify({"success": False, "message": "Session not found"}), 404
        
        if session_obj.teacher_id != teacher.id:
            return jsonify({"success": False, "message": "Access denied"}), 403
        
        # Can only delete pending or cancelled sessions
        if session_obj.status not in ('pending', 'cancelled'):
            return jsonify({"success": False, "message": "Can only delete pending or cancelled sessions"}), 409
        
        class_name = session_obj.class_.name
        db.session.delete(session_obj)
        db.session.commit()
        
        log_audit_action(user_id, 'attendance_session_deleted', 'attendance_sessions',
                        record_id=session_id,
                        details=f'Session deleted: {class_name}')
        
        return jsonify({
            "success": True,
            "message": "Session deleted successfully"
        })
    except Exception as e:
        db.session.rollback()
        return jsonify({"success": False, "message": f"Error deleting session: {e}"}), 500


@app.route('/api/teacher/attendance-sessions/<int:session_id>/start', methods=['POST'])
@teacher_required
def teacher_start_session(session_id):
    """Start an attendance session."""
    user_id = session.get('user_id')
    
    try:
        user = User.query.get(user_id)
        if not user or not user.teacher:
            return jsonify({"success": False, "message": "Teacher profile not found"}), 404
        
        teacher = user.teacher
        session_obj = AttendanceSession.query.get(session_id)
        
        if not session_obj:
            return jsonify({"success": False, "message": "Session not found"}), 404
        
        if session_obj.teacher_id != teacher.id:
            return jsonify({"success": False, "message": "Access denied"}), 403
        
        if session_obj.status != 'pending':
            return jsonify({"success": False, "message": "Session can only be started from pending status"}), 409
        
        session_obj.status = 'active'
        session_obj.updated_at = datetime.utcnow()
        db.session.commit()
        
        log_audit_action(user_id, 'attendance_session_started', 'attendance_sessions',
                        record_id=session_id,
                        details=f'Session started: {session_obj.class_.name}')
        
        return jsonify({
            "success": True,
            "message": "Attendance session started successfully",
            "data": {
                "session_id": session_obj.id,
                "status": session_obj.status
            }
        })
    except Exception as e:
        db.session.rollback()
        return jsonify({"success": False, "message": f"Error starting session: {e}"}), 500


@app.route('/api/teacher/attendance-sessions/<int:session_id>/complete', methods=['POST'])
@teacher_required
def teacher_complete_session(session_id):
    """Complete an attendance session."""
    user_id = session.get('user_id')
    data = request.get_json() or {}
    
    try:
        user = User.query.get(user_id)
        if not user or not user.teacher:
            return jsonify({"success": False, "message": "Teacher profile not found"}), 404
        
        teacher = user.teacher
        session_obj = AttendanceSession.query.get(session_id)
        
        if not session_obj:
            return jsonify({"success": False, "message": "Session not found"}), 404
        
        if session_obj.teacher_id != teacher.id:
            return jsonify({"success": False, "message": "Access denied"}), 403
        
        if session_obj.status != 'active':
            return jsonify({"success": False, "message": "Session must be active to complete"}), 409
        
        session_obj.status = 'completed'
        session_obj.end_time = datetime.utcnow().time()
        session_obj.updated_at = datetime.utcnow()
        
        # Mark absent students who weren't marked present
        class_students = session_obj.class_.students
        for student in class_students:
            existing = AttendanceRecord.query.filter(
                AttendanceRecord.session_id == session_id,
                AttendanceRecord.student_id == student.id
            ).first()
            
            if not existing:
                # Mark as absent if not already marked
                attendance = AttendanceRecord(
                    session_id=session_id,
                    student_id=student.id,
                    timestamp=datetime.utcnow(),
                    status='absent',
                    marked_by_teacher=True,
                    notes='Auto-marked absent at session completion'
                )
                db.session.add(attendance)
        
        db.session.commit()
        
        log_audit_action(user_id, 'attendance_session_completed', 'attendance_sessions',
                        record_id=session_id,
                        details=f'Session completed: {session_obj.class_.name}')
        
        return jsonify({
            "success": True,
            "message": "Attendance session completed successfully",
            "data": {
                "session_id": session_obj.id,
                "status": session_obj.status
            }
        })
    except Exception as e:
        db.session.rollback()
        return jsonify({"success": False, "message": f"Error completing session: {e}"}), 500


# ==================== ATTENDANCE CORRECTION & MANUAL MARKING ====================

@app.route('/api/attendance/mark-manual', methods=['POST'])
@teacher_required
def mark_attendance_manual():
    """Manually mark a student present or absent in an attendance session."""
    user_id = session.get('user_id')
    data = request.get_json()
    
    try:
        user = User.query.get(user_id)
        if not user or not user.teacher:
            return jsonify({"success": False, "message": "Teacher profile not found"}), 404
        
        teacher = user.teacher
        
        # Validate required fields
        if not data.get('session_id') or not data.get('student_id') or not data.get('status'):
            return jsonify({"success": False, "message": "Missing required fields"}), 400
        
        session_id = data['session_id']
        student_id = data['student_id']
        status = data['status'].lower()
        notes = data.get('notes', '')
        
        if status not in ('present', 'absent', 'late'):
            return jsonify({"success": False, "message": "Invalid status"}), 400
        
        # Verify session belongs to this teacher
        session_obj = AttendanceSession.query.get(session_id)
        if not session_obj:
            return jsonify({"success": False, "message": "Session not found"}), 404
        
        if session_obj.teacher_id != teacher.id:
            return jsonify({"success": False, "message": "Access denied"}), 403
        
        # Verify student is in the class
        student = Student.query.get(student_id)
        if not student:
            return jsonify({"success": False, "message": "Student not found"}), 404
        
        if student.class_id != session_obj.class_id:
            return jsonify({"success": False, "message": "Student not in this class"}), 409
        
        # Check for existing attendance record
        attendance = AttendanceRecord.query.filter(
            AttendanceRecord.session_id == session_id,
            AttendanceRecord.student_id == student_id
        ).first()
        
        if attendance:
            # Update existing record
            old_status = attendance.status
            attendance.status = status
            attendance.marked_by_teacher = True
            attendance.notes = notes
            attendance.updated_at = datetime.utcnow()
            
            log_audit_action(user_id, 'attendance_corrected', 'attendance_records',
                            record_id=attendance.id,
                            old_value=str({"status": old_status}),
                            new_value=str({"status": status}),
                            details=f'Attendance corrected for {student.name}: {old_status} -> {status}')
        else:
            # Create new record
            attendance = AttendanceRecord(
                session_id=session_id,
                student_id=student_id,
                timestamp=datetime.utcnow(),
                status=status,
                marked_by_teacher=True,
                notes=notes
            )
            db.session.add(attendance)
            
            log_audit_action(user_id, 'attendance_marked_manual', 'attendance_records',
                            record_id=None,
                            details=f'Attendance marked for {student.name}: {status}')
        
        db.session.commit()
        
        return jsonify({
            "success": True,
            "message": f"Attendance marked successfully",
            "data": {
                "session_id": session_id,
                "student_id": student_id,
                "status": status,
                "marked_by_teacher": True
            }
        }), 201
    except Exception as e:
        db.session.rollback()
        return jsonify({"success": False, "message": f"Error marking attendance: {e}"}), 500


@app.route('/api/attendance/<int:record_id>/correct', methods=['PUT'])
@teacher_required
def correct_attendance(record_id):
    """Correct an existing attendance record."""
    user_id = session.get('user_id')
    data = request.get_json()
    
    try:
        user = User.query.get(user_id)
        if not user or not user.teacher:
            return jsonify({"success": False, "message": "Teacher profile not found"}), 404
        
        teacher = user.teacher
        
        # Get the attendance record
        attendance = AttendanceRecord.query.get(record_id)
        if not attendance:
            return jsonify({"success": False, "message": "Attendance record not found"}), 404
        
        # Verify teacher owns the session
        if attendance.session.teacher_id != teacher.id:
            return jsonify({"success": False, "message": "Access denied"}), 403
        
        old_status = attendance.status
        
        # Update allowed fields
        if 'status' in data:
            new_status = data['status'].lower()
            if new_status not in ('present', 'absent', 'late', 'unknown'):
                return jsonify({"success": False, "message": "Invalid status"}), 400
            attendance.status = new_status
        
        if 'notes' in data:
            attendance.notes = data['notes']
        
        attendance.updated_at = datetime.utcnow()
        
        db.session.commit()
        
        log_audit_action(user_id, 'attendance_corrected', 'attendance_records',
                        record_id=record_id,
                        old_value=str({"status": old_status}),
                        new_value=str({"status": attendance.status}),
                        details=f'Attendance corrected for {attendance.student.name}')
        
        return jsonify({
            "success": True,
            "message": "Attendance record updated successfully",
            "data": {
                "record_id": record_id,
                "old_status": old_status,
                "new_status": attendance.status
            }
        })
    except Exception as e:
        db.session.rollback()
        return jsonify({"success": False, "message": f"Error correcting attendance: {e}"}), 500


@app.route('/api/attendance/<int:record_id>', methods=['DELETE'])
@teacher_required
def delete_attendance(record_id):
    """Delete an attendance record (only for pending sessions)."""
    user_id = session.get('user_id')
    
    try:
        user = User.query.get(user_id)
        if not user or not user.teacher:
            return jsonify({"success": False, "message": "Teacher profile not found"}), 404
        
        teacher = user.teacher
        
        # Get the attendance record
        attendance = AttendanceRecord.query.get(record_id)
        if not attendance:
            return jsonify({"success": False, "message": "Attendance record not found"}), 404
        
        # Verify teacher owns the session
        if attendance.session.teacher_id != teacher.id:
            return jsonify({"success": False, "message": "Access denied"}), 403
        
        # Can only delete from active sessions
        if attendance.session.status not in ('pending', 'active'):
            return jsonify({"success": False, "message": "Cannot delete attendance from completed/cancelled sessions"}), 409
        
        student_name = attendance.student.name
        db.session.delete(attendance)
        db.session.commit()
        
        log_audit_action(user_id, 'attendance_deleted', 'attendance_records',
                        record_id=record_id,
                        details=f'Attendance deleted for {student_name}')
        
        return jsonify({
            "success": True,
            "message": "Attendance record deleted successfully"
        })
    except Exception as e:
        db.session.rollback()
        return jsonify({"success": False, "message": f"Error deleting attendance: {e}"}), 500


# ==================== FACULTY DASHBOARD & FEATURES ====================

@app.route('/api/faculty/dashboard', methods=['GET'])
@faculty_required
def faculty_dashboard():
    """Get faculty dashboard summary with department-level statistics."""
    user_id = session.get('user_id')
    
    try:
        user = User.query.get(user_id)
        if not user or not user.teacher:
            return jsonify({"success": False, "message": "Faculty profile not found"}), 404
        
        teacher = user.teacher
        dept = teacher.department
        
        if not dept:
            return jsonify({"success": False, "message": "Faculty not assigned to a department"}), 404
        
        from datetime import date, timedelta
        today = date.today()
        
        # Department statistics
        dept_classes = Class.query.filter(Class.department_id == dept.id).all()
        dept_subjects = Subject.query.filter(
            Subject.class_id.in_([cls.id for cls in dept_classes])
        ).all()
        
        total_students = Student.query.filter(Student.department_id == dept.id).count()
        total_classes = len(dept_classes)
        total_subjects = len(dept_subjects)
        
        # Department average attendance (last 30 days)
        thirty_days_ago = today - timedelta(days=30)
        dept_sessions = AttendanceSession.query.filter(
            AttendanceSession.class_id.in_([cls.id for cls in dept_classes]),
            AttendanceSession.date >= thirty_days_ago
        ).all()
        
        total_records = 0
        present_records = 0
        for session in dept_sessions:
            records = AttendanceRecord.query.filter(
                AttendanceRecord.session_id == session.id
            ).all()
            for record in records:
                if record.status != 'absent':
                    present_records += 1
                total_records += 1
        
        dept_avg_attendance = (present_records / total_records * 100) if total_records > 0 else 0
        
        # Low attendance students in department
        low_threshold = 75
        low_attendance_count = 0
        
        for student in Student.query.filter(Student.department_id == dept.id).all():
            student_records = AttendanceRecord.query.filter(
                AttendanceRecord.student_id == student.id,
                AttendanceRecord.session_id.in_([s.id for s in dept_sessions])
            ).all()
            
            if len(student_records) > 0:
                present = sum(1 for r in student_records if r.status != 'absent')
                pct = (present / len(student_records)) * 100
                if pct < low_threshold:
                    low_attendance_count += 1
        
        return jsonify({
            "success": True,
            "data": {
                "faculty_name": user.name,
                "department": dept.name,
                "total_students": total_students,
                "total_classes": total_classes,
                "total_subjects": total_subjects,
                "dept_avg_attendance": round(dept_avg_attendance, 2),
                "low_attendance_students": low_attendance_count,
                "dept_sessions_last_30_days": len(dept_sessions)
            }
        })
    except Exception as e:
        return jsonify({"success": False, "message": f"Error fetching dashboard: {e}"}), 500


@app.route('/api/faculty/attendance-trends', methods=['GET'])
@faculty_required
def faculty_attendance_trends():
    """Get attendance trends for the faculty's department."""
    user_id = session.get('user_id')
    days = request.args.get('days', 30, type=int)
    
    try:
        user = User.query.get(user_id)
        if not user or not user.teacher:
            return jsonify({"success": False, "message": "Faculty profile not found"}), 404
        
        teacher = user.teacher
        dept = teacher.department
        
        if not dept:
            return jsonify({"success": False, "message": "Faculty not assigned to a department"}), 404
        
        from datetime import date, timedelta
        today = date.today()
        start_date = today - timedelta(days=days)
        
        dept_classes = Class.query.filter(Class.department_id == dept.id).all()
        
        # Get daily attendance stats
        daily_stats = {}
        
        for i in range(days):
            current_date = start_date + timedelta(days=i)
            sessions = AttendanceSession.query.filter(
                AttendanceSession.class_id.in_([cls.id for cls in dept_classes]),
                AttendanceSession.date == current_date
            ).all()
            
            total = 0
            present = 0
            for session in sessions:
                records = AttendanceRecord.query.filter(
                    AttendanceRecord.session_id == session.id
                ).all()
                for record in records:
                    if record.status != 'absent':
                        present += 1
                    total += 1
            
            if total > 0:
                daily_stats[current_date.isoformat()] = {
                    "date": current_date.isoformat(),
                    "present": present,
                    "total": total,
                    "percentage": round((present / total) * 100, 2)
                }
        
        return jsonify({
            "success": True,
            "data": {
                "department": dept.name,
                "period_days": days,
                "trends": daily_stats
            }
        })
    except Exception as e:
        return jsonify({"success": False, "message": f"Error fetching trends: {e}"}), 500


# ==================== REPORT GENERATION ====================

@app.route('/api/teacher/reports/attendance', methods=['GET'])
@teacher_required
def teacher_attendance_report():
    """Generate attendance report for a teacher's classes (CSV)."""
    user_id = session.get('user_id')
    class_id = request.args.get('class_id', type=int)
    start_date = request.args.get('start_date')
    end_date = request.args.get('end_date')
    format_type = request.args.get('format', 'csv')  # csv, json
    
    try:
        user = User.query.get(user_id)
        if not user or not user.teacher:
            return jsonify({"success": False, "message": "Teacher profile not found"}), 404
        
        teacher = user.teacher
        
        # Build query for sessions
        query = AttendanceSession.query.filter(AttendanceSession.teacher_id == teacher.id)
        
        if class_id:
            query = query.filter(AttendanceSession.class_id == class_id)
        
        if start_date:
            from datetime import datetime as dt
            start = dt.strptime(start_date, '%Y-%m-%d').date()
            query = query.filter(AttendanceSession.date >= start)
        
        if end_date:
            from datetime import datetime as dt
            end = dt.strptime(end_date, '%Y-%m-%d').date()
            query = query.filter(AttendanceSession.date <= end)
        
        sessions = query.order_by(AttendanceSession.date.desc()).all()
        
        if not sessions:
            return jsonify({"success": False, "message": "No sessions found for the given criteria"}), 404
        
        # Prepare report data
        report_data = []
        
        for session in sessions:
            records = AttendanceRecord.query.filter(
                AttendanceRecord.session_id == session.id
            ).all()
            
            for record in records:
                report_data.append({
                    'Date': session.date.strftime('%Y-%m-%d'),
                    'Class': session.class_.name,
                    'Subject': session.subject.name if session.subject else 'N/A',
                    'Student Name': record.student.name,
                    'Roll No': record.student.roll_no,
                    'Status': record.status.capitalize(),
                    'Time': record.timestamp.strftime('%H:%M:%S') if record.timestamp else 'N/A',
                    'Marked By Teacher': 'Yes' if record.marked_by_teacher else 'No',
                    'Notes': record.notes or ''
                })
        
        if format_type == 'json':
            return jsonify({
                "success": True,
                "data": report_data
            })
        
        # CSV format
        import io
        output = io.StringIO()
        if report_data:
            fieldnames = report_data[0].keys()
            writer = csv.DictWriter(output, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(report_data)
        
        response = Response(output.getvalue(), mimetype='text/csv')
        response.headers['Content-Disposition'] = 'attachment; filename=attendance_report.csv'
        return response
    
    except Exception as e:
        return jsonify({"success": False, "message": f"Error generating report: {e}"}), 500


@app.route('/api/teacher/reports/class-summary', methods=['GET'])
@teacher_required
def teacher_class_summary_report():
    """Generate class attendance summary report."""
    user_id = session.get('user_id')
    class_id = request.args.get('class_id', type=int)
    
    try:
        user = User.query.get(user_id)
        if not user or not user.teacher:
            return jsonify({"success": False, "message": "Teacher profile not found"}), 404
        
        teacher = user.teacher
        
        if not class_id:
            return jsonify({"success": False, "message": "class_id is required"}), 400
        
        cls = Class.query.get(class_id)
        if not cls:
            return jsonify({"success": False, "message": "Class not found"}), 404
        
        sessions = AttendanceSession.query.filter(
            AttendanceSession.teacher_id == teacher.id,
            AttendanceSession.class_id == class_id
        ).all()
        
        # Generate per-student summary
        summary_data = []
        
        for student in cls.students:
            records = AttendanceRecord.query.filter(
                AttendanceRecord.student_id == student.id,
                AttendanceRecord.session_id.in_([s.id for s in sessions])
            ).all()
            
            if len(records) > 0:
                present = sum(1 for r in records if r.status == 'present')
                absent = sum(1 for r in records if r.status == 'absent')
                late = sum(1 for r in records if r.status == 'late')
                percentage = (present / len(records)) * 100
            else:
                present = absent = late = 0
                percentage = 0
            
            summary_data.append({
                'Student Name': student.name,
                'Roll No': student.roll_no,
                'Total Sessions': len(records),
                'Present': present,
                'Absent': absent,
                'Late': late,
                'Attendance %': round(percentage, 2)
            })
        
        return jsonify({
            "success": True,
            "data": {
                "class_name": cls.name,
                "total_sessions": len(sessions),
                "summary": summary_data
            }
        })
    except Exception as e:
        return jsonify({"success": False, "message": f"Error generating report: {e}"}), 500


@app.route('/api/faculty/reports/department-summary', methods=['GET'])
@faculty_required
def faculty_department_report():
    """Generate department attendance summary report."""
    user_id = session.get('user_id')
    
    try:
        user = User.query.get(user_id)
        if not user or not user.teacher:
            return jsonify({"success": False, "message": "Faculty profile not found"}), 404
        
        teacher = user.teacher
        dept = teacher.department
        
        if not dept:
            return jsonify({"success": False, "message": "Faculty not assigned to department"}), 404
        
        from datetime import date, timedelta
        today = date.today()
        thirty_days_ago = today - timedelta(days=30)
        
        # Get all classes in department
        dept_classes = Class.query.filter(Class.department_id == dept.id).all()
        
        report_data = []
        
        for cls in dept_classes:
            sessions = AttendanceSession.query.filter(
                AttendanceSession.class_id == cls.id,
                AttendanceSession.date >= thirty_days_ago
            ).all()
            
            total_records = 0
            present_records = 0
            
            for session in sessions:
                records = AttendanceRecord.query.filter(
                    AttendanceRecord.session_id == session.id
                ).all()
                
                for record in records:
                    if record.status != 'absent':
                        present_records += 1
                    total_records += 1
            
            avg_attendance = (present_records / total_records * 100) if total_records > 0 else 0
            
            report_data.append({
                'Class': cls.name,
                'Code': cls.code,
                'Capacity': cls.capacity,
                'Total Sessions': len(sessions),
                'Total Marked': total_records,
                'Present': present_records,
                'Absent': total_records - present_records,
                'Avg Attendance %': round(avg_attendance, 2)
            })
        
        return jsonify({
            "success": True,
            "data": {
                "department": dept.name,
                "period": f"Last 30 days",
                "report": report_data
            }
        })
    except Exception as e:
        return jsonify({"success": False, "message": f"Error generating report: {e}"}), 500


# ==================== PHASE 5: STUDENT DASHBOARD ====================

@app.route('/api/student/dashboard', methods=['GET'])
@student_required
def student_dashboard():
    """Get student dashboard with overall attendance statistics."""
    user_id = session.get('user_id')
    
    try:
        user = User.query.get(user_id)
        if not user or not user.student:
            return jsonify({"success": False, "message": "Student profile not found"}), 404
        
        student = user.student
        
        # Get all attendance records for this student
        all_records = AttendanceRecord.query.filter(
            AttendanceRecord.student_id == student.id
        ).all()
        
        if not all_records:
            return jsonify({
                "success": True,
                "data": {
                    "student_name": student.name,
                    "roll_no": student.roll_no,
                    "overall_percentage": 0,
                    "total_classes": 0,
                    "present": 0,
                    "absent": 0,
                    "late": 0,
                    "recent_records": []
                }
            })
        
        # Calculate overall statistics
        present = sum(1 for r in all_records if r.status == 'present')
        absent = sum(1 for r in all_records if r.status == 'absent')
        late = sum(1 for r in all_records if r.status == 'late')
        total = len(all_records)
        overall_percentage = (present / total) * 100 if total > 0 else 0
        
        # Get recent records (last 10)
        recent_records = sorted(all_records, key=lambda x: x.timestamp, reverse=True)[:10]
        recent_data = []
        for record in recent_records:
            session_obj = AttendanceSession.query.get(record.session_id)
            subject = Subject.query.get(session_obj.subject_id) if session_obj else None
            recent_data.append({
                "date": session_obj.date.isoformat() if session_obj else "N/A",
                "subject": subject.name if subject else "Unknown",
                "status": record.status,
                "time": record.timestamp.strftime('%H:%M:%S') if record.timestamp else "N/A"
            })
        
        return jsonify({
            "success": True,
            "data": {
                "student_name": student.name,
                "roll_no": student.roll_no,
                "overall_percentage": round(overall_percentage, 2),
                "total_classes": total,
                "present": present,
                "absent": absent,
                "late": late,
                "recent_records": recent_data
            }
        })
    except Exception as e:
        return jsonify({"success": False, "message": f"Error fetching dashboard: {e}"}), 500


@app.route('/api/student/attendance/summary', methods=['GET'])
@student_required
def student_attendance_summary():
    """Get overall attendance summary for student."""
    user_id = session.get('user_id')
    
    try:
        user = User.query.get(user_id)
        if not user or not user.student:
            return jsonify({"success": False, "message": "Student profile not found"}), 404
        
        student = user.student
        records = AttendanceRecord.query.filter(
            AttendanceRecord.student_id == student.id
        ).all()
        
        if not records:
            return jsonify({
                "success": True,
                "data": {
                    "total_classes": 0,
                    "present": 0,
                    "absent": 0,
                    "late": 0,
                    "percentage": 0,
                    "status": "No attendance records yet"
                }
            })
        
        present = sum(1 for r in records if r.status == 'present')
        absent = sum(1 for r in records if r.status == 'absent')
        late = sum(1 for r in records if r.status == 'late')
        total = len(records)
        percentage = (present / total) * 100 if total > 0 else 0
        
        # Determine status based on threshold (75%)
        if percentage >= 75:
            status = "Good"
        elif percentage >= 65:
            status = "At Risk"
        else:
            status = "Critical"
        
        return jsonify({
            "success": True,
            "data": {
                "total_classes": total,
                "present": present,
                "absent": absent,
                "late": late,
                "percentage": round(percentage, 2),
                "status": status
            }
        })
    except Exception as e:
        return jsonify({"success": False, "message": f"Error: {e}"}), 500


@app.route('/api/student/notifications', methods=['GET'])
@student_required
def student_notifications():
    """Get in-app notifications for logged-in student."""
    user_id = session.get('user_id')
    try:
        user = User.query.get(user_id)
        if not user or not user.student:
            return jsonify({"success": True, "notifications": []})
        
        notifications = []
        student = user.student
        records = AttendanceRecord.query.filter_by(student_id=student.id).all()
        
        if records:
            present = sum(1 for r in records if r.status == 'present')
            total = len(records)
            pct = (present / total * 100) if total > 0 else 0
            if pct < 75:
                notifications.append({
                    "id": 1,
                    "title": "Low Attendance Warning",
                    "message": f"Your overall attendance is {pct:.1f}%, which is below the 75% threshold.",
                    "type": "warning",
                    "date": datetime.utcnow().strftime('%Y-%m-%d')
                })
        
        recent_records = sorted(records, key=lambda x: x.timestamp, reverse=True)[:5]
        for idx, rec in enumerate(recent_records, start=2):
            session_obj = AttendanceSession.query.get(rec.session_id)
            subject = Subject.query.get(session_obj.subject_id) if session_obj and session_obj.subject_id else None
            notifications.append({
                "id": idx,
                "title": f"Attendance Marked ({rec.status.capitalize()})",
                "message": f"Marked {rec.status} for {subject.name if subject else 'Session'} on {rec.timestamp.strftime('%Y-%m-%d %H:%M')}",
                "type": "info" if rec.status == 'present' else "danger",
                "date": rec.timestamp.strftime('%Y-%m-%d')
            })
            
        return jsonify({"success": True, "notifications": notifications})
    except Exception as e:
        return jsonify({"success": False, "message": str(e)}), 500


@app.route('/api/student/attendance/history', methods=['GET'])
@student_required
def student_attendance_history():
    """Get complete attendance history for student (paginated)."""
    user_id = session.get('user_id')
    page = request.args.get('page', 1, type=int)
    limit = request.args.get('limit', 20, type=int)
    sort_by = request.args.get('sort', 'date_desc')  # date_desc, date_asc, status
    
    try:
        user = User.query.get(user_id)
        if not user or not user.student:
            return jsonify({"success": False, "message": "Student profile not found"}), 404
        
        student = user.student
        query = AttendanceRecord.query.filter(
            AttendanceRecord.student_id == student.id
        )
        
        # Apply sorting
        if sort_by == 'date_asc':
            query = query.join(AttendanceSession).order_by(AttendanceSession.date.asc())
        elif sort_by == 'status':
            query = query.order_by(AttendanceRecord.status.asc())
        else:  # date_desc (default)
            query = query.join(AttendanceSession).order_by(AttendanceSession.date.desc())
        
        total_records = query.count()
        total_pages = (total_records + limit - 1) // limit
        
        records = query.offset((page - 1) * limit).limit(limit).all()
        
        history_data = []
        for record in records:
            session_obj = AttendanceSession.query.get(record.session_id)
            subject = Subject.query.get(session_obj.subject_id) if session_obj else None
            cls = Class.query.get(session_obj.class_id) if session_obj else None
            
            history_data.append({
                "record_id": record.id,
                "date": session_obj.date.isoformat() if session_obj else "N/A",
                "class": cls.name if cls else "Unknown",
                "subject": subject.name if subject else "Unknown",
                "status": record.status,
                "time": record.timestamp.strftime('%H:%M:%S') if record.timestamp else "N/A",
                "marked_by_teacher": record.marked_by_teacher or False,
                "notes": record.notes or ""
            })
        
        return jsonify({
            "success": True,
            "data": {
                "total_records": total_records,
                "page": page,
                "limit": limit,
                "total_pages": total_pages,
                "records": history_data
            }
        })
    except Exception as e:
        return jsonify({"success": False, "message": f"Error fetching history: {e}"}), 500


@app.route('/api/student/attendance/by-subject', methods=['GET'])
@student_required
def student_attendance_by_subject():
    """Get subject-wise attendance breakdown."""
    user_id = session.get('user_id')
    
    try:
        user = User.query.get(user_id)
        if not user or not user.student:
            return jsonify({"success": False, "message": "Student profile not found"}), 404
        
        student = user.student
        
        # Get all unique subjects the student has attended
        all_records = AttendanceRecord.query.filter(
            AttendanceRecord.student_id == student.id
        ).all()
        
        if not all_records:
            return jsonify({
                "success": True,
                "data": {
                    "subjects": [],
                    "message": "No attendance records found"
                }
            })
        
        # Group by subject
        subject_data = {}
        for record in all_records:
            session_obj = AttendanceSession.query.get(record.session_id)
            if not session_obj:
                continue
            
            subject = Subject.query.get(session_obj.subject_id)
            if not subject:
                continue
            
            if subject.id not in subject_data:
                subject_data[subject.id] = {
                    "subject_name": subject.name,
                    "subject_code": subject.code,
                    "present": 0,
                    "absent": 0,
                    "late": 0,
                    "total": 0
                }
            
            subject_data[subject.id]["total"] += 1
            if record.status == "present":
                subject_data[subject.id]["present"] += 1
            elif record.status == "absent":
                subject_data[subject.id]["absent"] += 1
            elif record.status == "late":
                subject_data[subject.id]["late"] += 1
        
        # Calculate percentages
        subjects_list = []
        for subject_id, data in subject_data.items():
            total = data["total"]
            percentage = ((data["present"] + data["late"]) / total * 100) if total > 0 else 0
            subjects_list.append({
                "subject_name": data["subject_name"],
                "subject_code": data["subject_code"],
                "present": data["present"],
                "late": data["late"],
                "absent": data["absent"],
                "total": total,
                "percentage": round(percentage, 2)
            })
        
        # Sort by percentage (highest first)
        subjects_list.sort(key=lambda x: x["percentage"], reverse=True)
        
        return jsonify({
            "success": True,
            "data": {
                "subjects": subjects_list,
                "total_subjects": len(subjects_list)
            }
        })
    except Exception as e:
        return jsonify({"success": False, "message": f"Error fetching subject breakdown: {e}"}), 500


@app.route('/api/student/low-attendance-warning', methods=['GET'])
@student_required
def student_low_attendance_warning():
    """Check if student has low attendance and return warning."""
    user_id = session.get('user_id')
    threshold = request.args.get('threshold', 75, type=int)
    
    try:
        user = User.query.get(user_id)
        if not user or not user.student:
            return jsonify({"success": False, "message": "Student profile not found"}), 404
        
        student = user.student
        records = AttendanceRecord.query.filter(
            AttendanceRecord.student_id == student.id
        ).all()
        
        if not records:
            return jsonify({
                "success": True,
                "data": {
                    "has_warning": False,
                    "message": "No attendance records yet"
                }
            })
        
        present = sum(1 for r in records if r.status == 'present')
        total = len(records)
        percentage = (present / total) * 100 if total > 0 else 0
        
        has_warning = percentage < threshold
        
        warning_data = {
            "has_warning": has_warning,
            "current_percentage": round(percentage, 2),
            "threshold": threshold,
            "classes_needed": 0
        }
        
        if has_warning:
            # Calculate classes needed to reach threshold
            # percentage_needed = threshold
            # (present + x) / (total + x) = threshold / 100
            # 100 * (present + x) = threshold * (total + x)
            # 100 * present + 100 * x = threshold * total + threshold * x
            # 100 * x - threshold * x = threshold * total - 100 * present
            # x * (100 - threshold) = threshold * total - 100 * present
            # x = (threshold * total - 100 * present) / (100 - threshold)
            
            if threshold < 100:
                classes_needed = max(0, int((threshold * total - 100 * present) / (100 - threshold)) + 1)
                warning_data["classes_needed"] = classes_needed
                warning_data["message"] = f"Attendance below {threshold}%. Need {classes_needed} more classes to reach target."
            else:
                warning_data["message"] = "Invalid threshold"
        else:
            warning_data["message"] = "Attendance is good"
        
        return jsonify({
            "success": True,
            "data": warning_data
        })
    except Exception as e:
        return jsonify({"success": False, "message": f"Error checking attendance warning: {e}"}), 500


@app.route('/api/student/reports/attendance', methods=['GET'])
@student_required
def student_attendance_report():
    """Generate attendance report for student (CSV/JSON)."""
    user_id = session.get('user_id')
    format_type = request.args.get('format', 'json')  # json, csv
    start_date = request.args.get('start_date')
    end_date = request.args.get('end_date')
    
    try:
        user = User.query.get(user_id)
        if not user or not user.student:
            return jsonify({"success": False, "message": "Student profile not found"}), 404
        
        student = user.student
        
        # Build query
        query = AttendanceRecord.query.filter(
            AttendanceRecord.student_id == student.id
        )
        
        if start_date:
            from datetime import datetime as dt
            start = dt.strptime(start_date, '%Y-%m-%d').date()
            query = query.join(AttendanceSession).filter(AttendanceSession.date >= start)
        
        if end_date:
            from datetime import datetime as dt
            end = dt.strptime(end_date, '%Y-%m-%d').date()
            query = query.join(AttendanceSession).filter(AttendanceSession.date <= end)
        
        records = query.order_by(AttendanceRecord.timestamp.desc()).all()
        
        if not records:
            return jsonify({"success": False, "message": "No attendance records found"}), 404
        
        # Prepare report data
        report_rows = []
        for record in records:
            session_obj = AttendanceSession.query.get(record.session_id)
            subject = Subject.query.get(session_obj.subject_id) if session_obj else None
            cls = Class.query.get(session_obj.class_id) if session_obj else None
            
            report_rows.append({
                'Date': session_obj.date.isoformat() if session_obj else 'N/A',
                'Class': cls.name if cls else 'Unknown',
                'Subject': subject.name if subject else 'Unknown',
                'Status': record.status.capitalize(),
                'Time': record.timestamp.strftime('%H:%M:%S') if record.timestamp else 'N/A',
                'Notes': record.notes or ''
            })
        
        if format_type == 'csv':
            import io
            output = io.StringIO()
            writer = csv.DictWriter(output, fieldnames=['Date', 'Class', 'Subject', 'Status', 'Time', 'Notes'])
            writer.writeheader()
            writer.writerows(report_rows)
            
            return send_file(
                io.BytesIO(output.getvalue().encode()),
                mimetype='text/csv',
                as_attachment=True,
                download_name=f"attendance_report_{student.roll_no}.csv"
            )
        else:
            return jsonify({
                "success": True,
                "data": {
                    "student_name": student.name,
                    "roll_no": student.roll_no,
                    "report": report_rows
                }
            })
    except Exception as e:
        return jsonify({"success": False, "message": f"Error generating report: {e}"}), 500


@app.route('/api/student/reports/semester-summary', methods=['GET'])
@student_required
def student_semester_summary():
    """Generate semester summary report for student."""
    user_id = session.get('user_id')
    
    try:
        user = User.query.get(user_id)
        if not user or not user.student:
            return jsonify({"success": False, "message": "Student profile not found"}), 404
        
        student = user.student
        records = AttendanceRecord.query.filter(
            AttendanceRecord.student_id == student.id
        ).all()
        
        if not records:
            return jsonify({
                "success": True,
                "data": {
                    "message": "No attendance records",
                    "summary": {}
                }
            })
        
        # Group by subject
        subject_summary = {}
        for record in records:
            session_obj = AttendanceSession.query.get(record.session_id)
            if not session_obj:
                continue
            
            subject = Subject.query.get(session_obj.subject_id)
            if not subject:
                continue
            
            if subject.id not in subject_summary:
                subject_summary[subject.id] = {
                    "subject_name": subject.name,
                    "subject_code": subject.code,
                    "present": 0,
                    "absent": 0,
                    "late": 0,
                    "total": 0
                }
            
            subject_summary[subject.id]["total"] += 1
            if record.status == "present":
                subject_summary[subject.id]["present"] += 1
            elif record.status == "absent":
                subject_summary[subject.id]["absent"] += 1
            elif record.status == "late":
                subject_summary[subject.id]["late"] += 1
        
        # Calculate summary statistics
        summary_list = []
        total_present = 0
        total_absent = 0
        total_late = 0
        total_classes = 0
        
        for subject_id, data in subject_summary.items():
            total = data["total"]
            percentage = ((data["present"] + data["late"]) / total * 100) if total > 0 else 0
            
            total_present += data["present"]
            total_absent += data["absent"]
            total_late += data["late"]
            total_classes += total
            
            summary_list.append({
                "subject_name": data["subject_name"],
                "subject_code": data["subject_code"],
                "present": data["present"],
                "late": data["late"],
                "absent": data["absent"],
                "total": total,
                "percentage": round(percentage, 2)
            })
        
        overall_percentage = ((total_present + total_late) / total_classes * 100) if total_classes > 0 else 0
        
        return jsonify({
            "success": True,
            "data": {
                "student_name": student.name,
                "roll_no": student.roll_no,
                "overall_summary": {
                    "total_classes": total_classes,
                    "present": total_present,
                    "late": total_late,
                    "absent": total_absent,
                    "overall_percentage": round(overall_percentage, 2)
                },
                "subject_wise_summary": summary_list
            }
        })
    except Exception as e:
        return jsonify({"success": False, "message": f"Error generating semester summary: {e}"}), 500


# ==================== INITIALIZATION ====================

# Initialise on server start
with app.app_context():
    db.create_all()

repair_attendance_file()
train_model()

if __name__ == '__main__':
    # Initialise camera on run (optional, toggled via web UI)
    app.run(host='0.0.0.0', port=5000, debug=False)

