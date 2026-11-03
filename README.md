# DevDuo HR Dashboard (company accounts)

A multi-company HR dashboard. A company creates an account, gives its details, uploads its employee file, and
from then on signs in with its password each time it opens the dashboard.

## How it works

1. **Create account.** The company enters its name, industry, size, country, the admin's name, work email and phone,
   the currency its salaries are recorded in, and the currency to show amounts in, then picks a password (10+ characters).
2. **Recovery code.** Shown once at sign-up. It is the only way to reset a forgotten password.
3. **Upload the employee CSV** (Account tab). Columns are matched by name: Name, Date of joining and Annual CTC
   (or Monthly salary) are required; Designation, Department, Location, Date of leaving, Date of birth and Currency are
   optional. A template is available in the app and in `sample/employees_template.csv`.
4. **Sign in with email and password** every time. Sessions end after 30 minutes of inactivity or 8 hours in total.

**Demo mode (for showing clients).** The sign-in screen has a "Skip sign in: view the demo dashboard" button that opens every page with made-up sample data, no account needed. Open `https://your-address/#demo` to land straight in it. Nothing is stored and no real data is touched. Set `HR_DEMO=0` to hide the button on a production server.

Pages: Overview, Departments & people, Salary (monthly, individual), Attendance and Bonus (sample data until real
records are added), Timesheet (CSV upload in the browser), Account.

## Run it

```
pip install -r requirements.txt
python web/build_web.py        # embeds logo and background into static/index.html
python server.py               # http://127.0.0.1:8000
```

Python 3.9 or newer. The only dependency is `cryptography`. Put your own office photo in `assets/office_bg.jpg`
(about 1920x1080) and your logo in `assets/logo.png`, then run `python web/build_web.py` again.

## Preview without a server

`dist/preview.html` is a clickable demo. Accounts are kept in the browser and the data is made up. It is for looking at
the screens only and is not secure.

## Security design

- Passwords and recovery codes are stored only as scrypt hashes.
- Sign-in sessions use a random token in an HttpOnly, SameSite=Strict cookie (Secure when served over HTTPS).
  Only a hash of the token is stored.
- Five wrong passwords lock the account for 15 minutes. Sign-in, sign-up and reset are also rate limited per IP.
- Each company only ever receives its own data. Employee data is encrypted at rest (Fernet) with a key kept outside the
  database (`data/secret.key`, or the `HR_SECRET_KEY` variable).
- Requests must be JSON, which blocks cross-site form posts. Responses carry CSP, no-sniff, no-frame and no-referrer headers.
- Changing a password signs out all other devices. Deleting an account removes its data.
- Employee data is held on your server. You are responsible for HTTPS, backups and access to the machine (see DEPLOY.md).

## Settings (environment variables)

| Variable | Meaning |
|---|---|
| `HR_HOST`, `HR_PORT` | Bind address and port (default 127.0.0.1:8000) |
| `HR_DATA_DIR` | Folder for the database and key (default `./data`) |
| `HR_SECRET_KEY` | Encryption key. Generate with `python -c "from cryptography.fernet import Fernet;print(Fernet.generate_key().decode())"` |
| `HR_SIGNUP_CODE` | If set, new companies must enter this invite code to sign up. Recommended for a private deployment |
| `HR_COOKIE_SECURE` | Set to `1` when served over HTTPS |
| `HR_TRUST_PROXY` | Set to `1` behind nginx, Caddy or a platform proxy |
| `HR_DEMO` | Set to `0` to hide the demo button (default: shown) |

## Tests

```
python tests/test_app.py        # starts a real server on a free port and tests the whole flow
```

## Files

`server.py` server and API · `hrcore.py` CSV cleaning · `web/app.html` the dashboard page · `web/build_web.py` build
step · `web/preview_mock.js` preview stand-in · `assets/` logo and background · `sample/` CSV template · `starter/` the
original Streamlit script · `Dockerfile`.
