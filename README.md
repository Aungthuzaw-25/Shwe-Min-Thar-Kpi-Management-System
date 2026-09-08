# 🚀 Enterprise KPI Management Platform

A highly scalable, secure, and asynchronous Key Performance Indicator (KPI) Management System built with **FastAPI**, **PostgreSQL**, and **Jinja2**. Designed for enterprise-level organizations to track employee performance, manage multi-tier approval workflows, and generate dynamic reports.

![FastAPI](https://img.shields.io/badge/FastAPI-005571?style=for-the-badge&logo=fastapi)
![Python](https://img.shields.io/badge/Python-3.11+-blue?style=for-the-badge&logo=python)
![PostgreSQL](https://img.shields.io/badge/PostgreSQL-316192?style=for-the-badge&logo=postgresql)
![Docker](https://img.shields.io/badge/Docker-2496ED?style=for-the-badge&logo=docker)
![TailwindCSS](https://img.shields.io/badge/Tailwind_CSS-38B2AC?style=for-the-badge&logo=tailwind-css)

---

## ✨ Key Features

*   **🔐 Granular RBAC (Role-Based Access Control):** Multi-tier organizational hierarchy support including Super Admin, System Super Admin, Admin, HR, Department Head (L0), Supervisor (L1), and Staff (L2).
*   **📊 Dynamic KPI Engine:** Configurable KPI formulas, weighted categories, and natural language deduction rules.
*   **✅ Multi-Stage Approval Workflow:** Task submissions with evidence upload. Requires sequential verification (L2 → L1 → L0) with features like Resubmit (V2), Delay, and Penalties.
*   **📂 Secure Evidence Management:** Integrated with **MinIO/AWS S3** for secure file storage. Built-in Anti-Malware protection (strict MIME type checking via `python-magic`) and max file size limits.
*   **📈 Real-Time Dashboards:** Interactive UI with Chart.js, department rankings, and real-time process tracking.
*   **📥 Data Import Engine:** Background processing for heavy Excel/CSV data imports using Pandas and FastAPI `BackgroundTasks`.
*   **🔔 Notification System:** Centralized in-app alerts and background email notifications.
*   **🛡️ Enterprise Security:** XSS Prevention (Global HTML Escaping), SQL Injection protection, Rate Limiting (`slowapi`), JWT Authentication, and Universal Auth Guards.

---

## Initial Super Admin provisioning

Application startup never runs the database seed. For a local or test database,
configure `SEED_SUPERADMIN_EMAIL` and `SEED_SUPERADMIN_PASSWORD` in the
environment and run the seed explicitly:

```bash
python -m app.database.seed
```

The Super Admin portion of the seed is create-only. If a Super Admin already
exists, the seed preserves its password and account state. Do not configure
seed credentials in a production runtime. For production, run the explicit
command once as a controlled provisioning job using secret-manager-injected
environment variables, then remove those variables from the application
runtime.

## 🛠️ Tech Stack

*   **Backend:** Python 3.11+, FastAPI, Uvicorn
*   **Database & ORM:** PostgreSQL, SQLAlchemy 2.0 (Async), Alembic
*   **Storage:** MinIO (S3 Compatible), Boto3
*   **Frontend:** Jinja2 Templates, Tailwind CSS, Vanilla JS, Chart.js
*   **Security:** Passlib (Bcrypt), python-jose (JWT), python-magic
*   **Deployment:** Docker, Docker Compose, Nginx

---

## 📁 Project Architecture (Domain-Driven Design)

```text
kpi-platform/
├── app/
│   ├── background_jobs/     # Schedulers and background email tasks
│   ├── core/                # Config, dependencies, security, auth guards
│   ├── database/            # Database connection and base models
│   ├── models/              # Cross-module shared models
│   ├── modules/             # DDD Modules (Business Logic)
│   │   ├── auth/
│   │   ├── departments/
│   │   ├── kpi/             # KPI Engine, Uploads, Periods, Reports
│   │   ├── notifications/
│   │   ├── system/
│   │   ├── targets/
│   │   ├── tasks/           # Task Schedulers, Reviews, Evidence
│   │   └── users/
│   ├── services/            # Global services (StorageService, DocumentService)
│   ├── web/                 # FastAPI HTML Template Routers
│   └── main.py              # Application entry point
├── static/                  # CSS, JS, Images, PWA Manifest
├── templates/               # Jinja2 HTML Templates (Layouts, Dashboard, etc.)
├── .env                     # Environment variables
├── docker-compose.yml       # Docker orchestration
├── Dockerfile               # Backend container build instructions
├── nginx.conf               # Nginx reverse proxy configuration
└── requirements.txt         # Python dependencies