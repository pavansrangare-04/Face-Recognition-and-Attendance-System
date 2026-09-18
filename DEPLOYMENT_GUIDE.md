# 🚀 Complete Deployment Guide: Supabase + Live Web Hosting

This guide will walk you through hosting your **Face Recognition & Attendance System** live on the web using **Supabase** for the PostgreSQL cloud database and **Render** (or **Railway**) for the live Python web application.

---

## 📌 Architecture Overview

```
[ Web Browser ]
      │
      ▼ (HTTPS)
[ Live Web Host: Render / Railway ]  <--- Runs Flask & OpenCV
      │
      ▼ (Encrypted PostgreSQL Connection)
[ Cloud Database: Supabase ]        <--- Stores Users, Attendance, Classes, Logs
```

---

## 🛠️ Step 1: Create Your Free Supabase Project

1. Go to [supabase.com](https://supabase.com) and click **"Start your project"** (Sign in with GitHub or Email).
2. Click **"+ New project"**.
3. Fill in the project details:
   - **Name**: `face-attendance-system`
   - **Database Password**: Choose a strong password (copy/save this somewhere safe!).
   - **Region**: Choose the region closest to you (e.g., `Central India`, `Singapore`, `Frankfurt`, or `US East`).
   - **Pricing Plan**: Free.
4. Click **"Create new project"** and wait ~1 minute for Supabase to finish provisioning.

---

## 🔑 Step 2: Get Your Supabase Database Connection URI

1. In your Supabase dashboard, click the **"Connect"** button in the top navigation bar (or go to **Project Settings** (gear icon) -> **Database**).
2. Look for **Connection string** and select the **URI** tab.
3. Select **Session Pooler** mode (Port `6543`) or **Direct** (Port `5432`).
4. Copy the connection string. It looks like:
   ```text
   postgresql://postgres.[PROJECT-REF]:[YOUR-PASSWORD]@aws-0-[REGION].pooler.supabase.com:6543/postgres
   ```
5. Replace `[YOUR-PASSWORD]` with your actual database password that you created in Step 1.

---

## 📦 Step 3: Configure Local `.env` and Initialize Supabase Tables

1. Open `.env` in the project root (or copy `.env.example` to `.env` if you don't have one):
   ```env
   ENVIRONMENT=development
   SECRET_KEY=your-super-secret-key-change-this
   DATABASE_URL=postgresql://postgres.[PROJECT-REF]:[YOUR-PASSWORD]@aws-0-[REGION].pooler.supabase.com:6543/postgres
   ```

2. Test the connection to your Supabase database:
   ```bash
   python migrate_to_supabase.py --check
   ```
   *(You should see `✅ Connection established successfully!`)*

3. Create all tables and transfer your local SQLite data to Supabase:
   ```bash
   python migrate_to_supabase.py --all
   ```
   *Alternatively, if starting fresh:*
   ```bash
   python migrate_to_supabase.py --init --demo
   ```

4. Verify in Supabase:
   - Go to your **Supabase Dashboard** -> **Table Editor**.
   - You will see all tables created: `users`, `departments`, `classes`, `subjects`, `students`, `teachers`, `attendance_sessions`, `attendance_records`, `audit_logs`!

---

## 🌐 Step 4: Host the Website Live on Render (Free & Recommended)

Render provides free hosting for web services with automatic HTTPS and GitHub integration.

### Method A: Connect your GitHub Repository (Easiest)

1. Push this project to your GitHub account:
   ```bash
   git init
   git add .
   git commit -m "Configure Supabase and deployment"
   git remote add origin https://github.com/YOUR_USERNAME/YOUR_REPO.git
   git branch -M main
   git push -u origin main
   ```
2. Go to [render.com](https://render.com) and log in.
3. Click **"New +"** -> **"Web Service"**.
4. Choose **"Build and deploy from a Git repository"** and select your repo.
5. Fill in the settings:
   - **Name**: `face-attendance-system`
   - **Region**: Same region as your Supabase database.
   - **Branch**: `main`
   - **Runtime**: `Python 3`
   - **Build Command**: `pip install -r requirements.txt`
   - **Start Command**: `gunicorn web_app:app --bind 0.0.0.0:$PORT --workers 2 --threads 2 --timeout 120`
   - **Instance Type**: `Free`
6. Scroll down to **Environment Variables** and add:
   - `ENVIRONMENT` = `production`
   - `SECRET_KEY` = `(generate a random 32-character string)`
   - `DATABASE_URL` = `postgresql://postgres.[PROJECT-REF]:[PASSWORD]@aws-0-[REGION].pooler.supabase.com:6543/postgres`
   - `RECOGNITION_THRESHOLD` = `0.55`
   - `COOLDOWN_SECONDS` = `10`
   - `ATTENDANCE_ALERT_THRESHOLD` = `75`
7. Click **"Create Web Service"**.
8. Render will build and deploy your app. Once deployed, Render will provide your public URL:
   `https://face-attendance-system.onrender.com`!

---

## 🚂 Step 5 (Alternative): Host on Railway

1. Go to [railway.app](https://railway.app) and sign in with GitHub.
2. Click **"New Project"** -> **"Deploy from GitHub repo"**.
3. Select your repository.
4. Click **"Add Variables"** and paste the same environment variables as above (`DATABASE_URL`, `SECRET_KEY`, `ENVIRONMENT=production`).
5. Under Settings -> **Networking**, click **"Generate Domain"**.
6. Railway automatically uses the included `Procfile` or `Dockerfile` and deploys your site!

---

## 📋 Default Login Credentials (if seeded with `--demo` or `--all`)

| Role | Email | Password |
| :--- | :--- | :--- |
| **Admin** | `admin@school.edu` | `admin123` |
| **Faculty** | `faculty@school.edu` | `faculty123` |
| **Teacher** | `teacher@school.edu` | `password123` |
| **Student** | `student1@school.edu` | `student123` |

---

## 🔧 Useful Management Commands

- **Check database connection**:
  ```bash
  python migrate_to_supabase.py --check
  ```
- **View row count in all Supabase tables**:
  ```bash
  python migrate_to_supabase.py --status
  ```
- **Re-sync local SQLite data to Supabase**:
  ```bash
  python migrate_to_supabase.py --transfer-sqlite
  ```
