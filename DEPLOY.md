# Deployment

Employee data is sensitive. Always serve this over HTTPS and keep backups of the `data` folder.

## Before any deployment
1. `python web/build_web.py` (creates `static/index.html`).
2. Choose an invite code so strangers cannot create accounts on your server, and set it as `HR_SIGNUP_CODE`.
3. Generate an encryption key and keep it safe (losing it makes stored employee data unreadable):
   `python -c "from cryptography.fernet import Fernet;print(Fernet.generate_key().decode())"` -> `HR_SECRET_KEY`

## Option A: a small Linux server with Caddy (simplest HTTPS)
```
sudo apt install -y python3-venv caddy
sudo useradd -r -m hr && sudo -u hr -i
git clone <your repo> app && cd app          # or copy the folder
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
.venv/bin/python web/build_web.py
```
Create `/etc/systemd/system/hr.service`:
```
[Service]
User=hr
WorkingDirectory=/home/hr/app
Environment=HR_PORT=8000 HR_DATA_DIR=/home/hr/data HR_TRUST_PROXY=1 HR_COOKIE_SECURE=1
Environment=HR_SECRET_KEY=<your key> HR_SIGNUP_CODE=<your invite code>
ExecStart=/home/hr/app/.venv/bin/python server.py
Restart=always
[Install]
WantedBy=multi-user.target
```
`sudo systemctl enable --now hr`, then put this in `/etc/caddy/Caddyfile` and `sudo systemctl reload caddy`:
```
hr.yourdomain.com {
    reverse_proxy 127.0.0.1:8000
}
```
Point the domain's DNS A record at the server. Caddy gets the HTTPS certificate automatically.

## Option B: Docker on any host (Render, Railway, Fly.io, a VPS)
```
python web/build_web.py
docker build -t hr-dashboard .
docker run -d -p 8000:8000 -v hrdata:/data \
  -e HR_SECRET_KEY=<your key> -e HR_SIGNUP_CODE=<invite code> -e HR_COOKIE_SECURE=1 hr-dashboard
```
On a platform host, create a service from the Dockerfile, attach a persistent volume mounted at `/data`, set the
environment variables above, and use the HTTPS address the platform gives you. Without a persistent volume, accounts
and data are lost on every restart.

## After deployment
- Create the first company account at your address, save its recovery code, upload a CSV.
- Test sign-out and sign-in again, and from your phone.
- Back up the `data` folder (database and, unless you use `HR_SECRET_KEY`, `secret.key`) regularly.
- This runs as a single process with a SQLite database, which suits dozens of companies. For heavy use, move to a
  hosted database and a production web server.
