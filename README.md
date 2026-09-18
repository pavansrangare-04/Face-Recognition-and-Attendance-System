# Face Recognition Attendance System

A real-time face recognition attendance system built using Python, OpenCV, and the `face_recognition` library. The system detects faces through a webcam, matches them against stored student images, and automatically records attendance in a CSV file.

---
## Live Demo

🌐 Website: https://pavansrangare-04.github.io/Face-Recognition-and-Attendance-System/

## Features

* Real-time webcam face detection
* Face recognition using stored images
* Automatic attendance marking
* CSV-based attendance logging
* Duplicate attendance prevention
* Simple and lightweight implementation

---

## Technologies Used

* Python
* OpenCV
* face_recognition
* NumPy

---

## Project Structure

```text
Face-Recognition-Attendance-System/
│
├── ImagesAttendance/
│   ├── Pavan.jpg
│   ├── Neha.jpg
│   └── StudentName.jpg
│
├── attendanceProject.py
├── basics.py
├── README.md
├── Attendance.csv
└── .gitignore
```

---

## How It Works

1. Student images are stored inside the `ImagesAttendance` folder.
2. The webcam captures live video.
3. The system detects and encodes faces.
4. Live faces are compared with stored face encodings.
5. If a match is found, attendance is automatically recorded.
6. Attendance is saved in `Attendance.csv`.

---

## Attendance Format

Example attendance output:

```text
Name,Date,Time,Status
PAVAN,2026-05-17,11:15:30,Present
PRAGATI,2026-05-17,11:16:10,Present
```

---

## Installation

### 1. Clone the Repository

```bash
git clone https://github.com/pavansrangare-04/Face-Recognition-and-Attendance-System.git
cd Face-Recognition-and-Attendance-System
```

### 2. Create Virtual Environment

```bash
python3 -m venv venv
source venv/bin/activate
```

### 3. Install Dependencies

```bash
python -m pip install -r requirements.txt
```

---

## Running the Project

Run the attendance system:

```bash
python attendanceProject.py
```

If needed:

```bash
python3 attendanceProject.py
```

---

## Adding Students

1. Add a clear face image to the `ImagesAttendance` folder.
2. Name the image using the student's name.

Example:

```text
ImagesAttendance/
├── Aryan.jpg
├── Neha.jpg
├── Pavan.jpg
```

The filename becomes the recognized attendance name.

---

## Future Improvements

* GUI interface using Tkinter
* Database integration (SQLite/MySQL)
* Excel attendance export
* Cloud/Firebase integration
* Email notifications
* Anti-spoofing detection
* Flask/Django web dashboard

---

## Resume Description

**Face Recognition Attendance System | Python, OpenCV**

* Developed a real-time face recognition attendance system using Python and OpenCV with webcam-based face detection and automated attendance recording.
* Implemented live face matching and CSV-based attendance logging to eliminate manual paper-based tracking.

---

## License

This project is licensed under the MIT License.

---

## Author

**Pavan Srangare**

GitHub: [https://github.com/pavansrangare-04](https://github.com/pavansrangare-04)
