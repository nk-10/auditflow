# AuditFlow: Production Deployment Guide

### AWS EC2 Free Tier · Elastic IP · Nginx · GitHub Actions CI/CD

---

## Execution Environment Key

Every command in this guide is prefixed with one of the following tags:

| Tag | Where to run |
|-----|-------------|
| `[LOCAL - VS Code Terminal]` | VS Code integrated terminal on your **Windows machine** (PowerShell or Git Bash shell inside VS Code) |
| `[LOCAL - PowerShell]` | Windows PowerShell opened separately (Win + X → Windows PowerShell) |
| `[AWS Console]` | AWS web interface at console.aws.amazon.com — browser only, no commands |
| `[GitHub]` | GitHub web interface — browser only, no commands |
| `[EC2 SSH]` | Inside the EC2 instance after connecting via SSH |

---

## Table of Contents

1. [Architecture Overview](#1-architecture-overview)
2. [Prerequisites](#2-prerequisites)
3. [EC2 Instance Setup](#3-ec2-instance-setup)
4. [Elastic IP Allocation & Association](#4-elastic-ip-allocation--association)
5. [Connect to EC2 via SSH](#5-connect-to-ec2-via-ssh)
6. [Server Provisioning](#6-server-provisioning)
7. [Application Deployment](#7-application-deployment)
8. [Nginx Configuration](#8-nginx-configuration)
9. [Process Management with systemd](#9-process-management-with-systemd)
10. [Verify the Full Stack](#10-verify-the-full-stack)
11. [CI/CD Pipeline with GitHub Actions](#11-cicd-pipeline-with-github-actions)
12. [Security Best Practices](#12-security-best-practices)
13. [Monitoring & Logging](#13-monitoring--logging)
14. [Scaling Considerations](#14-scaling-considerations)
15. [Troubleshooting Guide](#15-troubleshooting-guide)

---

## 1. Architecture Overview

### What Gets Deployed

AuditFlow runs two long-running processes on a single EC2 instance:

| Process | Framework | Internal Port | Purpose |
|---------|-----------|---------------|---------|
| Backend API | FastAPI + Uvicorn | `8000` | LangGraph workflow, `/analyze`, `/approve`, `/status` endpoints |
| Frontend UI | Streamlit | `8501` | Browser-based UI — polls backend over HTTP |

Both processes are managed by **systemd** as OS services. **Nginx** is the only public-facing entry point — it routes all incoming traffic on port 80 to the correct internal service. The **Elastic IP** gives the instance a permanent, non-changing public IP address.

### Traffic Flow Diagram

```
Your Browser
     │
     ▼
[ Elastic IP: <YOUR_ELASTIC_IP> ]  ← permanent, never changes on stop/start
     │
     ▼
[ EC2 Security Group ]
     │  Port 80  (HTTP)  — allowed from anywhere
     │  Port 443 (HTTPS) — allowed from anywhere
     │  Port 22  (SSH)   — allowed from YOUR IP only
     │
     ▼
┌──────────────────────────────────────────────┐
│          EC2 t2.micro  (Ubuntu 22.04 LTS)    │
│                                              │
│   ┌────────────────────────────────────┐     │
│   │         Nginx  — Port 80           │     │
│   │                                    │     │
│   │  /api/*          → localhost:8000  │     │
│   │  /health         → localhost:8000  │     │
│   │  /docs           → localhost:8000  │     │
│   │  /_stcore/stream → localhost:8501  │     │  ← Streamlit WebSocket
│   │  / (everything)  → localhost:8501  │     │
│   └────────────────────────────────────┘     │
│           │                   │              │
│           ▼                   ▼              │
│  ┌──────────────┐   ┌──────────────────┐    │
│  │  Streamlit   │   │  FastAPI/Uvicorn  │    │
│  │  :8501       │   │  :8000            │    │
│  │  (systemd)   │   │  (systemd)        │    │
│  └──────────────┘   └──────────────────┘    │
│                            │                 │
│                            ▼                 │
│                  ┌──────────────────┐        │
│                  │  SQLite DB        │        │
│                  │  data/checkpoints │        │
│                  │       .db         │        │
│                  └──────────────────┘        │
│                                              │
│  GitHub Actions ──SSH──▶ git pull + restart  │
└──────────────────────────────────────────────┘
```

### How the Pieces Interact

- **Elastic IP** is attached to the EC2 instance. It never changes even if the instance is stopped and restarted, giving CI/CD pipelines and DNS a stable target.
- **Nginx** terminates all public HTTP connections. It proxies `/api/*` to FastAPI and everything else to Streamlit, including WebSocket upgrade requests that Streamlit requires for real-time UI updates.
- **FastAPI** (port 8000) runs the LangGraph graph (`scanner → security → human_review → compiler`), calls the Groq API externally, and persists thread state in SQLite.
- **Streamlit** (port 8501) serves the browser UI. It calls back to the backend through Nginx at `/api/`.
- **GitHub Actions** deploys on every push to `main` by SSH-ing into the EC2 instance using a dedicated deploy key stored as a GitHub Secret, then pulling the latest code and restarting systemd services.

---

## 2. Prerequisites

### 2.1 AWS Account

- Create a free AWS account at https://aws.amazon.com/free if you do not have one.
- Sign in and confirm you are in your preferred **AWS region** (e.g., `ap-south-1` for Mumbai, `us-east-1` for N. Virginia). All resources in this guide must be created in the **same region**.

### 2.2 Required API Keys — Collect Before Starting

| Key | Where to Get | Required? |
|-----|-------------|-----------|
| `GROQ_API_KEY` | https://console.groq.com → API Keys | **Yes** |
| `GITHUB_TOKEN` | GitHub → Settings → Developer settings → Personal access tokens (classic) → Generate new token → scope: `repo` (read-only) | Optional but recommended to avoid GitHub API rate limits |

### 2.3 Local Software on Your Windows Machine

Open VS Code Terminal and verify both tools are installed:

```
[LOCAL - VS Code Terminal]
ssh -V
```
Expected: `OpenSSH_for_Windows_X.X` or similar. OpenSSH ships with Windows 10/11 by default.

```
[LOCAL - VS Code Terminal]
git --version
```
Expected: `git version 2.x.x`

### 2.4 GitHub Repository Requirements

- Your AuditFlow code must be pushed to a GitHub repository (public or private).
- You must have **admin access** to the repository in order to add Secrets and create Actions workflows.

---

## 3. EC2 Instance Setup

### 3.1 Create an SSH Key Pair

`[AWS Console]`

1. Navigate to **EC2** → **Network & Security** → **Key Pairs** (left sidebar).
2. Click **Create key pair**.
3. Fill in:
   - **Name:** `auditflow-key`
   - **Key pair type:** RSA
   - **Private key file format:** `.pem`
4. Click **Create key pair**. The file `auditflow-key.pem` downloads automatically to your browser's default downloads folder.

Move it to the correct location on your machine and restrict permissions:

```
[LOCAL - PowerShell]
# Create the .ssh folder if it does not exist
New-Item -ItemType Directory -Force -Path "$env:USERPROFILE\.ssh"

# Move the downloaded key into .ssh
Move-Item "$env:USERPROFILE\Downloads\auditflow-key.pem" "$env:USERPROFILE\.ssh\auditflow-key.pem"

# Restrict permissions so SSH accepts the key
# Removes all inherited permissions and grants read-only access to your account only
icacls "$env:USERPROFILE\.ssh\auditflow-key.pem" /inheritance:r /grant:r "${env:USERNAME}:(R)"
```

> **Why restrict permissions:** SSH on Windows refuses to use a private key file that is readable by other users. The `icacls` command strips inherited permissions and grants read access only to your Windows account.

### 3.2 Launch the EC2 Instance

`[AWS Console]`

1. Go to **EC2** → **Instances** → **Launch instances**.

2. **Name:** `auditflow-production`

3. **AMI (Application and OS Images):**
   - Click **Browse more AMIs**.
   - Search: `Ubuntu 22.04 LTS`
   - Select: **Ubuntu Server 22.04 LTS (HVM), SSD Volume Type**
   - Architecture: **64-bit (x86)**

   > **Why Ubuntu 22.04 LTS:** Long-term support until April 2027, excellent package availability for Python 3.11, and the most widely documented Linux distribution for server deployments.

4. **Instance type:** `t2.micro`

   > **Why t2.micro:** The only Free Tier eligible instance type — 1 vCPU, 1 GB RAM. Free for 750 hours/month for 12 months. Sufficient for AuditFlow because the heavy LLM computation happens externally on Groq's servers.

5. **Key pair:** Select `auditflow-key`.

6. **Network settings** → Click **Edit**:
   - **VPC:** Leave as default.
   - **Auto-assign public IP:** Enable.
   - **Firewall (security groups):** Select **Create security group**.
   - **Security group name:** `auditflow-sg`
   - Delete the default SSH rule and re-add rules precisely as follows:

   | Type | Protocol | Port | Source | Why |
   |------|----------|------|--------|-----|
   | SSH | TCP | 22 | **My IP** (click the dropdown — AWS auto-fills your current public IP) | Allows only you to log in over SSH. Restricting to your IP prevents brute-force attacks from the internet. |
   | HTTP | TCP | 80 | `0.0.0.0/0` | Public web traffic enters through Nginx on port 80. |
   | HTTPS | TCP | 443 | `0.0.0.0/0` | Future TLS termination. Including it now avoids reconfiguring the security group later. |

   > **Do NOT open ports 8000 or 8501 to the public.** Those are internal-only ports. Nginx is the sole public entry point.

7. **Configure storage:** `8 GiB` gp2 (default). Do not exceed 30 GB total to remain within Free Tier storage limits.

8. Click **Launch instance**.

9. Click **View all instances**. Wait until the **Instance State** column shows `running` and **Status check** shows `2/2 checks passed` (approximately 2 minutes).

---

## 4. Elastic IP Allocation & Association

> **Why Elastic IP:** By default, EC2 instances receive a new public IP address each time they are stopped and started. This breaks CI/CD pipelines (which store the IP as a Secret), DNS records, and SSH host key verification. An Elastic IP is a static public address permanently assigned to your AWS account. It is **free as long as it is associated with a running instance**.

### Step 1 — Allocate an Elastic IP

`[AWS Console]`

1. In the left sidebar under **Network & Security**, click **Elastic IPs**.
2. Click **Allocate Elastic IP address**.
3. Settings:
   - **Network Border Group:** Leave as the default for your region.
   - **Public IPv4 address pool:** Amazon's pool of IPv4 addresses (default).
4. Click **Allocate**.
5. A new entry appears with an IP address. **Copy this IP — this is your permanent public address.** It is referred to as `<YOUR_ELASTIC_IP>` throughout this guide.

### Step 2 — Associate the Elastic IP with Your Instance

`[AWS Console]`

1. Select the Elastic IP you just allocated (checkbox on the left).
2. Click **Actions** → **Associate Elastic IP address**.
3. Settings:
   - **Resource type:** Instance
   - **Instance:** Select `auditflow-production` from the dropdown.
   - **Private IP address:** Leave as default (auto-selected).
4. Click **Associate**.

### Step 3 — Verify the Association

`[AWS Console]`

1. Go to **EC2** → **Instances** → click on `auditflow-production`.
2. In the **Details** tab, confirm:
   - **Public IPv4 address** = the Elastic IP you allocated
   - **Elastic IP addresses** = same IP

### Step 4 — Update the SSH Security Group Rule

`[AWS Console]`

Your IP may have changed since the instance was launched. Refresh it:

1. Go to **EC2** → **Security Groups** → select `auditflow-sg`.
2. Click **Inbound rules** → **Edit inbound rules**.
3. On the SSH rule (port 22), change **Source** to **My IP**.
4. Click **Save rules**.

---

## 5. Connect to EC2 via SSH

### 5.1 Initial Connection

```
[LOCAL - VS Code Terminal]
ssh -i "$env:USERPROFILE\.ssh\auditflow-key.pem" ubuntu@<YOUR_ELASTIC_IP>
```

Replace `<YOUR_ELASTIC_IP>` with your actual Elastic IP address.

When prompted:
```
Are you sure you want to continue connecting (yes/no/[fingerprint])?
```
Type `yes` and press Enter. This permanently records the server's host key in your `known_hosts` file.

You should see the Ubuntu welcome banner. You are now inside the EC2 instance.

> **Why `ubuntu` as the username:** Ubuntu AMIs on EC2 use `ubuntu` as the default non-root sudo user. Direct `root` login is disabled for security.

### 5.2 Create an SSH Config Entry for Convenience (Optional but Recommended)

```
[LOCAL - VS Code Terminal]
notepad "$env:USERPROFILE\.ssh\config"
```

Add the following block (create the file if it does not exist):

```
Host auditflow
    HostName <YOUR_ELASTIC_IP>
    User ubuntu
    IdentityFile ~/.ssh/auditflow-key.pem
    ServerAliveInterval 60
```

Save and close. You can now connect with just:

```
[LOCAL - VS Code Terminal]
ssh auditflow
```

> **Why `ServerAliveInterval 60`:** Sends a keepalive packet every 60 seconds so idle SSH connections do not drop during long-running commands.

---

## 6. Server Provisioning

> All commands in this section are run **inside the EC2 instance via SSH**.

### 6.1 Update the Operating System

```
[EC2 SSH]
sudo apt update && sudo apt upgrade -y
```

> **Why:** Applies all security patches released since the Ubuntu AMI was published. AMI images can be months old — always update first on any new server.

### 6.2 Install System Dependencies

```
[EC2 SSH]
sudo apt install -y \
  python3.11 \
  python3.11-venv \
  python3-pip \
  git \
  nginx \
  curl \
  ufw
```

> **Why each package:**
> - `python3.11` + `python3.11-venv`: AuditFlow requires Python 3.11+.
> - `git`: Required for cloning the repo and for `git pull` during CI/CD deployments.
> - `nginx`: Reverse proxy — routes public traffic to FastAPI and Streamlit.
> - `curl`: Used to test HTTP endpoints from the command line.
> - `ufw`: Uncomplicated Firewall — Ubuntu's user-friendly frontend for iptables.

### 6.3 Verify Python Version

```
[EC2 SSH]
python3.11 --version
```

Expected output: `Python 3.11.x`

### 6.4 Configure UFW Firewall

```
[EC2 SSH]
# Step 1: Allow SSH FIRST — doing this before enabling UFW is critical.
# If you enable UFW without this rule, you will lock yourself out immediately.
sudo ufw allow OpenSSH

# Step 2: Allow Nginx (opens both port 80 and port 443)
sudo ufw allow 'Nginx Full'

# Step 3: Enable the firewall
sudo ufw enable
```

When prompted `Command may disrupt existing ssh connections. Proceed with operation (y|n)?` — type `y` and press Enter.

```
[EC2 SSH]
# Verify the rules are correct before proceeding
sudo ufw status verbose
```

Expected output:
```
Status: active
To                         Action      From
--                         ------      ----
OpenSSH                    ALLOW IN    Anywhere
Nginx Full                 ALLOW IN    Anywhere
```

> **Why UFW in addition to the EC2 Security Group:** The Security Group is an AWS-level (network) firewall. UFW is an OS-level firewall. Running both means an attacker who bypasses one layer still faces the other — this is called defense in depth.

### 6.5 Create a Dedicated Application User

```
[EC2 SSH]
sudo useradd -m -s /bin/bash auditflow
sudo usermod -aG www-data auditflow
```

> **Why a separate user:** Never run application code as `root` or the admin `ubuntu` user. The `auditflow` user has no sudo privileges by default, limiting the damage a compromised process can cause.

---

## 7. Application Deployment

> All commands in this section are run **inside the EC2 instance via SSH**.

### 7.1 Create the Application Directory

```
[EC2 SSH]
sudo mkdir -p /opt/auditflow
sudo chown auditflow:auditflow /opt/auditflow
```

> **Why `/opt/`:** The Linux Filesystem Hierarchy Standard designates `/opt/` for optional third-party applications, keeping them separate from system directories.

### 7.2 Switch to the Application User

```
[EC2 SSH]
sudo -u auditflow -i
```

Your prompt changes to `auditflow@ip-xxx-xxx-xxx-xxx:~$`. All remaining commands in this section run as this user.

### 7.3 Clone Your Repository

```
[EC2 SSH]
git clone https://github.com/<YOUR_GITHUB_USERNAME>/<YOUR_REPO_NAME>.git /opt/auditflow
cd /opt/auditflow
```

Replace `<YOUR_GITHUB_USERNAME>` and `<YOUR_REPO_NAME>` with your actual GitHub details.

> If your repository is **private**, use your GitHub username and a Personal Access Token (not your password) when prompted for credentials.

### 7.4 Create a Python Virtual Environment

```
[EC2 SSH]
cd /opt/auditflow
python3.11 -m venv venv
source venv/bin/activate
```

Verify you are using the virtualenv's Python:

```
[EC2 SSH]
which python
```

Expected: `/opt/auditflow/venv/bin/python`

> **Why a virtual environment:** Isolates AuditFlow's packages from Ubuntu's system Python, preventing version conflicts and making dependency management reproducible.

### 7.5 Install Python Dependencies

```
[EC2 SSH]
pip install --upgrade pip
pip install -r requirements.txt
```

This installs all packages from `requirements.txt`:
- `langgraph`, `langchain`, `langchain-groq`, `langchain-core`
- `fastapi`, `uvicorn`, `pydantic`, `pydantic-settings`
- `streamlit`
- `PyGithub`, `python-dotenv`, `tenacity`, `sqlalchemy`, `requests`

This step takes 1–3 minutes.

### 7.6 Create Required Directories

```
[EC2 SSH]
mkdir -p /opt/auditflow/data
mkdir -p /opt/auditflow/logs
```

> `data/` stores `checkpoints.db` — the SQLite database for LangGraph state persistence across API calls. `logs/` is available for application log output.

### 7.7 Configure Environment Variables

```
[EC2 SSH]
cp /opt/auditflow/.env.example /opt/auditflow/.env
nano /opt/auditflow/.env
```

Set the following values. Replace all angle-bracket placeholders with your real credentials:

```dotenv
# =============================================
# Required — the application will not start without this
# =============================================
GROQ_API_KEY=<YOUR_GROQ_API_KEY>

# =============================================
# Optional — avoids GitHub API rate limits
# =============================================
GITHUB_TOKEN=<YOUR_GITHUB_PERSONAL_ACCESS_TOKEN>

# =============================================
# Server configuration
# Both services listen on localhost only.
# Nginx is the only publicly exposed service.
# =============================================
API_HOST=127.0.0.1
API_PORT=8000

STREAMLIT_HOST=127.0.0.1
STREAMLIT_PORT=8501

# Since both services run on the same server, use localhost
BACKEND_URL=http://127.0.0.1:8000

# =============================================
# LangGraph SQLite state storage
# =============================================
DB_PATH=/opt/auditflow/data/checkpoints.db

# =============================================
# LLM settings (defaults are fine)
# =============================================
LLM_MODEL=llama-3.3-70b-versatile
LLM_TEMPERATURE=0.3
LLM_MAX_TOKENS=4000

# =============================================
# Logging
# =============================================
LOG_LEVEL=INFO
DEBUG=false
```

Save: `Ctrl+O`, then `Enter`. Close: `Ctrl+X`.

Restrict file permissions so only the `auditflow` user can read it:

```
[EC2 SSH]
chmod 600 /opt/auditflow/.env
```

> **Why `chmod 600`:** The `.env` file contains your API keys. This permission means only the file owner (`auditflow`) can read or write it — no other OS user can access it.

### 7.8 Return to the Ubuntu User

```
[EC2 SSH]
exit
```

Your prompt returns to `ubuntu@ip-xxx-xxx-xxx-xxx:~$`.

---

## 8. Nginx Configuration

### 8.1 Routing Strategy

| URL Pattern | Proxied To | Why |
|------------|------------|-----|
| `/api/*` | FastAPI on `127.0.0.1:8000` | All backend API calls |
| `/health`, `/docs`, `/openapi.json` | FastAPI on `127.0.0.1:8000` | Health checks and Swagger UI |
| `/_stcore/stream` | Streamlit on `127.0.0.1:8501` | Streamlit's WebSocket (required for real-time UI) |
| `/_stcore/*`, `/static/*` | Streamlit on `127.0.0.1:8501` | Streamlit static assets |
| `/` (everything else) | Streamlit on `127.0.0.1:8501` | The main application UI |

### 8.2 Create the Nginx Configuration File

```
[EC2 SSH]
sudo nano /etc/nginx/sites-available/auditflow
```

Paste the entire configuration below:

```nginx
# /etc/nginx/sites-available/auditflow
# Nginx reverse proxy for AuditFlow
# Routes public port 80 traffic to FastAPI (:8000) and Streamlit (:8501)

upstream fastapi_backend {
    server 127.0.0.1:8000;
}

upstream streamlit_frontend {
    server 127.0.0.1:8501;
}

server {
    listen 80;

    # Replace <YOUR_ELASTIC_IP> with your actual Elastic IP address.
    # If you have a domain name pointing to the Elastic IP, use that instead.
    server_name <YOUR_ELASTIC_IP>;

    client_max_body_size 10M;

    # ------------------------------------------------------------------
    # Security headers — applied to every response
    # ------------------------------------------------------------------
    add_header X-Frame-Options        "SAMEORIGIN"                     always;
    add_header X-Content-Type-Options "nosniff"                        always;
    add_header Referrer-Policy        "strict-origin-when-cross-origin" always;

    # ------------------------------------------------------------------
    # FastAPI Backend — all /api/* requests
    # The rewrite strips the /api prefix before forwarding because FastAPI
    # routes are /analyze, /approve, /status (no /api prefix internally).
    # ------------------------------------------------------------------
    location /api/ {
        rewrite ^/api(/.*)$ $1 break;

        proxy_pass http://fastapi_backend;
        proxy_http_version 1.1;

        proxy_set_header Host              $http_host;
        proxy_set_header X-Real-IP         $remote_addr;
        proxy_set_header X-Forwarded-For   $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;

        # LLM analysis can take 30–90 seconds — set generous timeouts
        proxy_connect_timeout  60s;
        proxy_send_timeout    120s;
        proxy_read_timeout    120s;
    }

    # FastAPI health check endpoint
    location /health {
        proxy_pass http://fastapi_backend;
        proxy_set_header Host $http_host;
    }

    # FastAPI auto-generated API docs (Swagger UI)
    location ~ ^/(docs|redoc|openapi\.json)$ {
        proxy_pass http://fastapi_backend;
        proxy_set_header Host      $http_host;
        proxy_set_header X-Real-IP $remote_addr;
    }

    # ------------------------------------------------------------------
    # Streamlit WebSocket — REQUIRED
    # Streamlit uses a persistent WebSocket at /_stcore/stream for all
    # real-time UI updates. Without this block, the UI loads blank or
    # shows "Please wait..." indefinitely.
    # ------------------------------------------------------------------
    location /_stcore/stream {
        proxy_pass http://streamlit_frontend/_stcore/stream;
        proxy_http_version 1.1;

        # These headers tell Nginx to upgrade the HTTP connection to WebSocket
        proxy_set_header Upgrade    $http_upgrade;
        proxy_set_header Connection "upgrade";

        proxy_set_header Host              $http_host;
        proxy_set_header X-Real-IP         $remote_addr;
        proxy_set_header X-Forwarded-For   $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;

        # WebSocket connections are persistent — do not time them out
        proxy_read_timeout 86400s;
    }

    # Streamlit internal assets
    location /_stcore/ {
        proxy_pass http://streamlit_frontend/_stcore/;
        proxy_http_version 1.1;
        proxy_set_header Host $http_host;
    }

    location /static/ {
        proxy_pass http://streamlit_frontend/static/;
        proxy_http_version 1.1;
        proxy_set_header Host $http_host;
    }

    # ------------------------------------------------------------------
    # All remaining requests → Streamlit frontend
    # This must be the LAST location block
    # ------------------------------------------------------------------
    location / {
        proxy_pass http://streamlit_frontend;
        proxy_http_version 1.1;

        # WebSocket upgrade support (Streamlit also uses WS on the root path)
        proxy_set_header Upgrade    $http_upgrade;
        proxy_set_header Connection "upgrade";

        proxy_set_header Host              $http_host;
        proxy_set_header X-Real-IP         $remote_addr;
        proxy_set_header X-Forwarded-For   $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;

        proxy_connect_timeout  60s;
        proxy_send_timeout     60s;
        proxy_read_timeout     60s;
    }
}
```

Save: `Ctrl+O`, then `Enter`. Close: `Ctrl+X`.

**Important:** Replace `<YOUR_ELASTIC_IP>` on the `server_name` line with your actual Elastic IP address (e.g., `server_name 13.233.45.67;`).

### 8.3 Enable the Site and Disable the Default

```
[EC2 SSH]
# Enable the auditflow site
sudo ln -s /etc/nginx/sites-available/auditflow /etc/nginx/sites-enabled/auditflow

# Remove the default Nginx welcome page — it would conflict on port 80
sudo rm -f /etc/nginx/sites-enabled/default
```

### 8.4 Test the Configuration

```
[EC2 SSH]
sudo nginx -t
```

Expected output:
```
nginx: the configuration file /etc/nginx/nginx.conf syntax is ok
nginx: configuration file /etc/nginx/nginx.conf test is successful
```

> **Why always test before restarting:** A syntax error in the Nginx config prevents Nginx from starting, immediately taking the site offline. Always validate first.

If errors are reported, the message includes the exact line number — fix that line and re-run `sudo nginx -t`.

### 8.5 Restart Nginx and Enable Auto-Start

```
[EC2 SSH]
sudo systemctl restart nginx

# Register Nginx to start automatically whenever the server reboots
sudo systemctl enable nginx

# Confirm it is running
sudo systemctl status nginx
```

The status output should show `active (running)` in green.

---

## 9. Process Management with systemd

### Why systemd Over PM2

**systemd** is chosen over PM2 because:
- It is built into Ubuntu — no additional installation required.
- It natively manages any process type (Python, Go, Java, etc.). PM2 is optimized for Node.js.
- It integrates with OS logging via `journalctl`.
- It handles service start ordering (backend before frontend).
- It automatically restarts crashed processes using configurable retry policies.

### 9.1 Create the FastAPI Backend Service

```
[EC2 SSH]
sudo nano /etc/systemd/system/auditflow-backend.service
```

Paste:

```ini
[Unit]
Description=AuditFlow FastAPI Backend (Uvicorn)
After=network.target
Before=auditflow-frontend.service

[Service]
Type=exec
User=auditflow
Group=auditflow
WorkingDirectory=/opt/auditflow

# Load all variables from .env into the process environment
EnvironmentFile=/opt/auditflow/.env

ExecStart=/opt/auditflow/venv/bin/python -m uvicorn backend.main:app \
    --host 127.0.0.1 \
    --port 8000 \
    --log-level info

# Route all stdout/stderr to systemd journal (view with: journalctl -u auditflow-backend)
StandardOutput=journal
StandardError=journal
SyslogIdentifier=auditflow-backend

# Restart on crash but NOT on a clean manual stop
Restart=on-failure
RestartSec=5s

# Security hardening
NoNewPrivileges=true
PrivateTmp=true

[Install]
WantedBy=multi-user.target
```

Save: `Ctrl+O`, `Enter`, `Ctrl+X`.

### 9.2 Create the Streamlit Frontend Service

```
[EC2 SSH]
sudo nano /etc/systemd/system/auditflow-frontend.service
```

Paste:

```ini
[Unit]
Description=AuditFlow Streamlit Frontend
After=network.target auditflow-backend.service
Requires=auditflow-backend.service

[Service]
Type=exec
User=auditflow
Group=auditflow
WorkingDirectory=/opt/auditflow

EnvironmentFile=/opt/auditflow/.env

# --server.headless=true       prevents Streamlit opening a browser on the server
# --browser.gatherUsageStats=false  disables Streamlit's anonymous telemetry
ExecStart=/opt/auditflow/venv/bin/python -m streamlit run frontend/app.py \
    --server.port=8501 \
    --server.address=127.0.0.1 \
    --logger.level=info \
    --server.headless=true \
    --browser.gatherUsageStats=false

StandardOutput=journal
StandardError=journal
SyslogIdentifier=auditflow-frontend

Restart=on-failure
RestartSec=5s

NoNewPrivileges=true
PrivateTmp=true

[Install]
WantedBy=multi-user.target
```

Save: `Ctrl+O`, `Enter`, `Ctrl+X`.

### 9.3 Enable and Start Both Services

```
[EC2 SSH]
# Reload systemd so it reads the new service files
sudo systemctl daemon-reload

# Register both services to start automatically on every reboot
sudo systemctl enable auditflow-backend
sudo systemctl enable auditflow-frontend

# Start the backend first
sudo systemctl start auditflow-backend

# Wait for the backend to bind to port 8000 before starting the frontend
sleep 5

# Start the frontend
sudo systemctl start auditflow-frontend
```

### 9.4 Verify Both Services Started Successfully

```
[EC2 SSH]
sudo systemctl status auditflow-backend
```

Look for `Active: active (running)`. The log lines at the bottom should show Uvicorn started on port 8000.

```
[EC2 SSH]
sudo systemctl status auditflow-frontend
```

Look for `Active: active (running)`. Streamlit's logs will confirm it started on port 8501.

If either service shows `failed` or `inactive (dead)`, check the logs immediately:

```
[EC2 SSH]
sudo journalctl -u auditflow-backend -n 30
sudo journalctl -u auditflow-frontend -n 30
```

---

## 10. Verify the Full Stack

Run these tests in order to confirm every layer is working correctly before setting up CI/CD.

### 10.1 Test FastAPI Directly (Internal)

```
[EC2 SSH]
curl http://127.0.0.1:8000/health
```

Expected: JSON response such as `{"status":"healthy"}`.

### 10.2 Test FastAPI Through Nginx

```
[EC2 SSH]
curl http://127.0.0.1/api/health
```

Expected: The same JSON response. This confirms Nginx is correctly stripping the `/api` prefix and proxying to FastAPI.

### 10.3 Test the Streamlit Frontend (Internal)

```
[EC2 SSH]
curl -I http://127.0.0.1:8501
```

Expected: `HTTP/1.1 200 OK` in the response headers.

### 10.4 Test From Your Browser

```
[LOCAL - VS Code Terminal]
# Open your browser and navigate to:
# http://<YOUR_ELASTIC_IP>
```

You should see the AuditFlow Streamlit UI load completely. Enter a public GitHub repository URL and confirm the analysis workflow starts without errors.

---

## 11. CI/CD Pipeline with GitHub Actions

### Overview

Every push to the `main` branch triggers a workflow that:
1. SSH-es into EC2 using a dedicated deploy key.
2. Pulls the latest code from `main`.
3. Installs any new Python dependencies.
4. Restarts both systemd services.
5. Verifies the health endpoint responds successfully.

### 11.1 Generate a Dedicated Deploy SSH Key

> **Why a separate key:** Never reuse your personal `auditflow-key.pem` for CI/CD. A dedicated deploy key can be revoked independently if a GitHub Secret is ever compromised, without affecting your personal access.

```
[LOCAL - VS Code Terminal]
ssh-keygen -t ed25519 -C "auditflow-github-actions-deploy" -f "$env:USERPROFILE\.ssh\auditflow_deploy_key" -N '""'
```

Two files are created in `C:\Users\<YOUR_USERNAME>\.ssh\`:
- `auditflow_deploy_key` — **private key** (will be stored in GitHub Secrets)
- `auditflow_deploy_key.pub` — **public key** (will be installed on EC2)

### 11.2 Print the Public Key

```
[LOCAL - VS Code Terminal]
Get-Content "$env:USERPROFILE\.ssh\auditflow_deploy_key.pub"
```

Copy the entire output line (it starts with `ssh-ed25519 AAAA...`).

### 11.3 Install the Public Key on EC2

```
[EC2 SSH]
sudo -u auditflow -i

mkdir -p /home/auditflow/.ssh
chmod 700 /home/auditflow/.ssh

# Paste the full public key line here (replace the placeholder)
echo "ssh-ed25519 AAAA<YOUR_DEPLOY_PUBLIC_KEY> auditflow-github-actions-deploy" >> /home/auditflow/.ssh/authorized_keys

chmod 600 /home/auditflow/.ssh/authorized_keys

# Verify the key was written
cat /home/auditflow/.ssh/authorized_keys

exit
```

### 11.4 Print the Private Key

```
[LOCAL - VS Code Terminal]
Get-Content "$env:USERPROFILE\.ssh\auditflow_deploy_key"
```

Copy the **entire output** including the `-----BEGIN OPENSSH PRIVATE KEY-----` and `-----END OPENSSH PRIVATE KEY-----` lines.

### 11.5 Add Secrets to GitHub

`[GitHub]`

Go to your repository → **Settings** → **Secrets and variables** → **Actions** → **New repository secret**.

Add each of the following secrets:

| Secret Name | Value | Description |
|-------------|-------|-------------|
| `EC2_SSH_PRIVATE_KEY` | Full contents of `auditflow_deploy_key` (including BEGIN/END lines) | Deploy key for SSH |
| `EC2_HOST` | Your Elastic IP address | Server address for SSH |
| `EC2_USER` | `auditflow` | Linux user for the SSH session |
| `GROQ_API_KEY` | Your Groq API key | Available if the `.env` file ever needs to be regenerated |

### 11.6 Allow the auditflow User to Restart Services Without a Password

The deploy script runs `sudo systemctl restart` non-interactively. Grant passwordless sudo for **only** these specific commands:

```
[EC2 SSH]
sudo visudo -f /etc/sudoers.d/auditflow
```

Add this single line exactly as written:

```
auditflow ALL=(ALL) NOPASSWD: /bin/systemctl restart auditflow-backend, /bin/systemctl restart auditflow-frontend, /bin/systemctl status auditflow-backend, /bin/systemctl status auditflow-frontend
```

Save: `Ctrl+X`, then `Y`, then `Enter`.

Validate there are no syntax errors:

```
[EC2 SSH]
sudo visudo -c -f /etc/sudoers.d/auditflow
```

Expected: `/etc/sudoers.d/auditflow: parsed OK`

> **Why this exact syntax:** Passwordless sudo is granted **only** for these four specific `systemctl` commands. The `auditflow` user cannot run any other sudo command, severely limiting the impact of a compromised deploy key.

### 11.7 Create the GitHub Actions Workflow File

```
[LOCAL - VS Code Terminal]
# Run from the project root directory
mkdir -p .github\workflows
notepad .github\workflows\deploy.yml
```

Paste the following content:

```yaml
# .github/workflows/deploy.yml
# Deploys AuditFlow to AWS EC2 on every push to the main branch.

name: Deploy to EC2

on:
  push:
    branches:
      - main          # Trigger on pushes to main only
  workflow_dispatch:  # Also allow manual runs from the GitHub Actions tab

jobs:
  deploy:
    name: Deploy to Production (EC2)
    runs-on: ubuntu-latest

    steps:

      # ── Step 1: Check out the repository ───────────────────────────────
      - name: Checkout repository
        uses: actions/checkout@v4

      # ── Step 2: Configure SSH ───────────────────────────────────────────
      - name: Configure SSH access
        run: |
          mkdir -p ~/.ssh
          # Write the private deploy key from GitHub Secrets to a file
          echo "${{ secrets.EC2_SSH_PRIVATE_KEY }}" > ~/.ssh/deploy_key
          chmod 600 ~/.ssh/deploy_key
          # Pre-populate known_hosts to avoid the interactive "are you sure?"
          # prompt that would hang the CI/CD job indefinitely
          ssh-keyscan -H ${{ secrets.EC2_HOST }} >> ~/.ssh/known_hosts

      # ── Step 3: Deploy to EC2 ────────────────────────────────────────────
      - name: Deploy application to EC2
        run: |
          ssh -i ~/.ssh/deploy_key \
              -o StrictHostKeyChecking=no \
              ${{ secrets.EC2_USER }}@${{ secrets.EC2_HOST }} \
              'bash -s' << 'DEPLOY_SCRIPT'

            # Exit immediately if any command returns a non-zero exit code
            set -e

            echo "=== [1/5] Navigating to application directory ==="
            cd /opt/auditflow

            echo "=== [2/5] Pulling latest code from main branch ==="
            git fetch origin main
            # Hard reset ensures the server exactly matches the remote,
            # even if files were manually edited on the server
            git reset --hard origin/main

            echo "=== [3/5] Installing/updating Python dependencies ==="
            source venv/bin/activate
            pip install --quiet -r requirements.txt

            echo "=== [4/5] Restarting services ==="
            sudo systemctl restart auditflow-backend
            sleep 4
            sudo systemctl restart auditflow-frontend

            echo "=== [5/5] Verifying deployment health ==="
            sleep 6
            curl --fail --silent --max-time 10 http://127.0.0.1:8000/health > /dev/null \
              && echo "Backend health check:  PASSED" \
              || { echo "Backend health check:  FAILED"; exit 1; }
            curl --fail --silent --max-time 15 http://127.0.0.1:8501 > /dev/null \
              && echo "Frontend health check: PASSED" \
              || { echo "Frontend health check: FAILED"; exit 1; }

            echo ""
            echo "=== Deployment complete ==="

          DEPLOY_SCRIPT

      # ── Step 4: Post-failure diagnostic hints ────────────────────────────
      - name: Show debug hints on failure
        if: failure()
        run: |
          echo ""
          echo "Deployment failed. SSH into the server and run:"
          echo "  Backend logs:   sudo journalctl -u auditflow-backend -n 50"
          echo "  Frontend logs:  sudo journalctl -u auditflow-frontend -n 50"
          echo "  Nginx errors:   sudo tail -30 /var/log/nginx/error.log"
          echo "  Service status: sudo systemctl status auditflow-backend"
```

Save: `Ctrl+S`. Close Notepad.

### 11.8 Commit and Push the Workflow

```
[LOCAL - VS Code Terminal]
cd d:\Nitin\auditflow
git add .github/workflows/deploy.yml
git commit -m "Add GitHub Actions CI/CD deployment pipeline"
git push origin main
```

### 11.9 Verify the Workflow Runs

`[GitHub]`

1. Go to your repository → **Actions** tab.
2. Click the `Deploy to EC2` workflow run triggered by your push.
3. All five deploy steps should show green checkmarks.
4. The final log output should include `=== Deployment complete ===`.

---

## 12. Security Best Practices

### 12.1 Harden SSH on the Server

```
[EC2 SSH]
sudo nano /etc/ssh/sshd_config
```

Find and update these settings (add them if they do not exist):

```
# Disable password login — only SSH key authentication is permitted
PasswordAuthentication no

# Disable direct root login
PermitRootLogin no

# Reduce the brute-force attack window
MaxAuthTries 3

# Only allow these specific users to connect via SSH
AllowUsers ubuntu auditflow
```

> **Critical:** Before restarting `sshd`, open a **second VS Code Terminal tab** and verify your SSH key still connects successfully:
> ```
> [LOCAL - VS Code Terminal]
> ssh -i "$env:USERPROFILE\.ssh\auditflow-key.pem" ubuntu@<YOUR_ELASTIC_IP>
> ```
> Only restart `sshd` if the second session connects. This protects against locking yourself out.

```
[EC2 SSH]
sudo systemctl restart sshd
```

### 12.2 Confirm Security Group Has Minimal Open Ports

`[AWS Console]`

Go to **EC2** → **Security Groups** → `auditflow-sg` → **Inbound rules**. Verify exactly:

| Port | Source | Required |
|------|--------|----------|
| 22 | Your IP only | Yes |
| 80 | 0.0.0.0/0 | Yes |
| 443 | 0.0.0.0/0 | Yes |
| 8000 | — | Must NOT exist |
| 8501 | — | Must NOT exist |

### 12.3 Enable Automatic OS Security Updates

```
[EC2 SSH]
sudo apt install -y unattended-upgrades
sudo dpkg-reconfigure -plow unattended-upgrades
```

Select **Yes** when prompted to enable automatic security updates.

> **Why:** Critical OS patches (kernel, OpenSSL, SSH server) are applied automatically without requiring manual SSH access.

### 12.4 Secrets Management Rules

- **Never commit `.env` to Git.** Confirm `.gitignore` contains `.env`.
- All secrets used by GitHub Actions are stored as **GitHub Actions Secrets** — encrypted at rest and automatically masked (`***`) in all log output.
- Rotate `GROQ_API_KEY` every 90 days: update it in `/opt/auditflow/.env` on the server and in GitHub Secrets.
- The `.env` file on the server uses `chmod 600` — readable only by the `auditflow` user.

---

## 13. Monitoring & Logging

### 13.1 View Application Logs

```
[EC2 SSH]
# Stream backend logs live (Ctrl+C to stop)
sudo journalctl -u auditflow-backend -f

# Stream frontend logs live
sudo journalctl -u auditflow-frontend -f

# View the last 50 lines of backend logs
sudo journalctl -u auditflow-backend -n 50

# View logs from the last hour
sudo journalctl -u auditflow-backend --since "1 hour ago"
```

### 13.2 View Nginx Logs

```
[EC2 SSH]
# Every HTTP request reaching the server
sudo tail -f /var/log/nginx/access.log

# Nginx errors (proxy failures, config issues)
sudo tail -f /var/log/nginx/error.log
```

### 13.3 Check System Resources

```
[EC2 SSH]
# Memory usage — critical to monitor on 1 GB t2.micro
free -h

# Disk usage
df -h /

# CPU and memory by process (press q to exit)
top
```

### 13.4 Create a Health Check Script

```
[EC2 SSH]
sudo nano /usr/local/bin/auditflow-health
```

Paste:

```bash
#!/bin/bash
echo "=== AuditFlow Health Check — $(date) ==="
echo ""
echo "--- System Resources ---"
free -h
echo ""
df -h /
echo ""
echo "--- Service Status ---"
systemctl is-active auditflow-backend  && echo "Backend:  RUNNING" || echo "Backend:  STOPPED"
systemctl is-active auditflow-frontend && echo "Frontend: RUNNING" || echo "Frontend: STOPPED"
systemctl is-active nginx              && echo "Nginx:    RUNNING" || echo "Nginx:    STOPPED"
echo ""
echo "--- HTTP Health Checks ---"
curl -sf http://127.0.0.1:8000/health > /dev/null && echo "Backend  API: OK" || echo "Backend  API: FAILED"
curl -sf -o /dev/null http://127.0.0.1:8501       && echo "Frontend UI: OK" || echo "Frontend UI: FAILED"
curl -sf -o /dev/null http://127.0.0.1/api/health && echo "Nginx Proxy: OK" || echo "Nginx Proxy: FAILED"
```

```
[EC2 SSH]
sudo chmod +x /usr/local/bin/auditflow-health

# Run the health check at any time with:
auditflow-health
```

### 13.5 Automate Health Checks via Cron

```
[EC2 SSH]
sudo crontab -e
```

When prompted to select an editor, type `1` for nano. Add this line at the bottom:

```
*/5 * * * * /usr/local/bin/auditflow-health >> /var/log/auditflow-health.log 2>&1
```

Save: `Ctrl+O`, `Enter`. Close: `Ctrl+X`.

View health check history:

```
[EC2 SSH]
sudo tail -100 /var/log/auditflow-health.log
```

### 13.6 Monitor SQLite Database Size

LangGraph checkpoints accumulate over time. Check the database size periodically:

```
[EC2 SSH]
du -sh /opt/auditflow/data/checkpoints.db
```

If it grows excessively large (over 500 MB), clear it during a maintenance window:

```
[EC2 SSH]
sudo systemctl stop auditflow-backend
sudo -u auditflow rm /opt/auditflow/data/checkpoints.db
sudo systemctl start auditflow-backend
```

> **Warning:** This deletes all in-progress analysis threads. Only perform during a maintenance window.

---

## 14. Scaling Considerations

### 14.1 Free Tier Resource Limits

| Resource | Free Tier Limit | AuditFlow Usage | Risk Level |
|----------|----------------|-----------------|------------|
| Compute | 750 hours/month t2.micro | ~720 hours if always on | Low |
| RAM | 1 GB | FastAPI + Streamlit + OS ≈ 700–850 MB | Medium — monitor with `free -h` |
| Network egress | 100 GB/month | Low (Groq API calls are small JSON payloads) | Low |
| EBS storage | 30 GB | ~3–5 GB used | Low |
| Elastic IP | Free while associated with a running instance | In use | Free |

**Add a swap file as a safety net against OOM on the 1 GB instance:**

```
[EC2 SSH]
sudo fallocate -l 1G /swapfile
sudo chmod 600 /swapfile
sudo mkswap /swapfile
sudo swapon /swapfile
# Make swap persistent across reboots
echo '/swapfile none swap sw 0 0' | sudo tee -a /etc/fstab
```

### 14.2 Upgrade Path When Free Tier Ends

| Scenario | Action | Estimated Cost |
|----------|--------|---------------|
| Need more RAM | Stop instance → change type to `t3.small` (2 GB RAM) | ~$15/month |
| Higher traffic | Add Application Load Balancer + scale to `t3.medium` | ~$40/month |
| Separate services | Run backend and frontend on separate instances, update `BACKEND_URL` | ~$30/month |
| Containerize | Use existing `docker/Dockerfile` with AWS ECS Fargate | Pay-per-use |
| Managed database | Replace SQLite with Amazon RDS PostgreSQL, update `DB_PATH` | ~$15/month |

### 14.3 Production Hardening Beyond Free Tier

- Replace open SSH port (22) with **AWS Systems Manager Session Manager** — eliminates the need for port 22 entirely.
- Add an **Application Load Balancer** with **SSL/TLS termination** — offloads HTTPS from Nginx and enables auto-scaling.
- Use **AWS Secrets Manager** instead of `.env` files for API key management.
- Add **Amazon CloudWatch** for metrics dashboards and automated alerting on health check failures.

---

## 15. Troubleshooting Guide

### 15.1 Cannot SSH into the Instance

**Symptoms:** `Connection refused`, `Connection timed out`, or `Permission denied (publickey)`

```
[LOCAL - VS Code Terminal]
# Add -v for verbose output showing exactly where the failure occurs
ssh -i "$env:USERPROFILE\.ssh\auditflow-key.pem" ubuntu@<YOUR_ELASTIC_IP> -v
```

```
[LOCAL - PowerShell]
# Verify key file permissions
(Get-Acl "$env:USERPROFILE\.ssh\auditflow-key.pem").Access

# Fix permissions if incorrect
icacls "$env:USERPROFILE\.ssh\auditflow-key.pem" /inheritance:r /grant:r "${env:USERNAME}:(R)"
```

`[AWS Console]` — also check:
- EC2 → Security Groups → `auditflow-sg` → Inbound rules: SSH port 22 must list your current IP.
- EC2 → Elastic IPs: Elastic IP must show **Associated** with `auditflow-production`.

---

### 15.2 Nginx Returns 502 Bad Gateway

**Cause:** Nginx is running but the upstream service it proxies to is down.

```
[EC2 SSH]
# Identify which service is failing
sudo systemctl status auditflow-backend
sudo systemctl status auditflow-frontend

# Read the logs for the startup error
sudo journalctl -u auditflow-backend -n 40
sudo journalctl -u auditflow-frontend -n 40

# Restart the failing service
sudo systemctl restart auditflow-backend
```

| Error in logs | Cause | Fix |
|--------------|-------|-----|
| `ModuleNotFoundError` | Package not installed in venv | `sudo -u auditflow /opt/auditflow/venv/bin/pip install -r /opt/auditflow/requirements.txt` |
| `ValidationError` for `GROQ_API_KEY` | Missing or wrong env var | Check and fix `/opt/auditflow/.env`, then restart backend |
| `Address already in use` | Port 8000 or 8501 occupied by another process | `sudo lsof -i :8000` then `sudo kill <PID>` |
| `No such file or directory` (Python path) | Wrong venv path in service file | Verify `/opt/auditflow/venv/bin/python` exists |

---

### 15.3 Streamlit Loads Blank or Shows "Please wait..." Forever

**Cause:** Streamlit loaded but its WebSocket connection (`/_stcore/stream`) is not being proxied correctly.

```
[EC2 SSH]
# Confirm the WebSocket location block exists in Nginx config
grep -n "_stcore" /etc/nginx/sites-available/auditflow

# Check for WebSocket upgrade errors
sudo tail -20 /var/log/nginx/error.log

# Reload Nginx config (no downtime)
sudo nginx -t && sudo systemctl reload nginx
```

---

### 15.4 GitHub Actions Deploy Job Fails

**"Permission denied (publickey)" error:**

```
[EC2 SSH]
# Verify the deploy public key is present in authorized_keys
sudo -u auditflow cat /home/auditflow/.ssh/authorized_keys

# Verify directory and file permissions
sudo -u auditflow ls -la /home/auditflow/.ssh/
# .ssh directory must be:    drwx------ (700)
# authorized_keys must be:   -rw------- (600)
```

**"sudo: a password is required" error:**

```
[EC2 SSH]
sudo cat /etc/sudoers.d/auditflow
# Must contain the NOPASSWD line for the two systemctl restart commands

sudo visudo -c -f /etc/sudoers.d/auditflow
# Must return: parsed OK
```

**"git reset --hard failed" or ownership errors:**

```
[EC2 SSH]
sudo chown -R auditflow:auditflow /opt/auditflow/
```

---

### 15.5 Application Runs Out of Memory

```
[EC2 SSH]
# Check current memory usage
free -h

# Find the top memory consumers
ps aux --sort=-%mem | head -15

# Check for kernel OOM events
sudo journalctl -k | grep -i "out of memory\|oom"
```

**Fix:** Add a 1 GB swap file (see [Section 14.1](#141-free-tier-resource-limits) above).

---

### 15.6 Groq API Errors in Logs

```
[EC2 SSH]
sudo journalctl -u auditflow-backend -f | grep -iE "groq|401|429|timeout|exception"
```

| Error | Cause | Fix |
|-------|-------|-----|
| `401 Unauthorized` | Invalid `GROQ_API_KEY` | Update key in `/opt/auditflow/.env`, then `sudo systemctl restart auditflow-backend` |
| `429 Too Many Requests` | Groq free tier rate limit hit (30 req/min) | Built-in `tenacity` retry logic handles this; reduce concurrent analyses |
| `Connection timeout` | Outbound traffic to `api.groq.com` blocked | Verify EC2 Security Group outbound rules allow all outbound (default) |

---

### 15.7 Elastic IP Unreachable After Instance Restart

`[AWS Console]`

1. Go to **EC2** → **Elastic IPs**.
2. If the Elastic IP shows **Unassociated**: select it → **Actions** → **Associate Elastic IP address** → select `auditflow-production`.

> **Why this can happen:** If the instance was **terminated** (not just stopped) and a new one was launched, the Elastic IP must be re-associated with the new instance ID.

---

*AuditFlow Deployment Guide — AWS EC2 Free Tier + Elastic IP + Nginx + systemd + GitHub Actions CI/CD*
*Stack: FastAPI (LangGraph + Groq LLM) + Streamlit · Python 3.11 · Ubuntu 22.04 LTS*
