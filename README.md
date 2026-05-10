# NMD Contest Suite

Web application suite for administering the **USKA National Mountain Day** Swiss
ham radio contest. Four modules over one shared SQLite database:

- **Registration** (`/anmeldung/`) — public registration form with map.
- **Participant Portal** (`/submission/`) — log entry, station data, post-contest scoring view.
- **Scoring** (`/scoring/`) — admin-only QSO matching, ranking lists.
- **Administration** (`/admin/`) — contest lifecycle (open/close, publish, archive), mass email, backup/restore.

The legacy reference applications (TCL scoring app, Python log submission app, contest rules PDF)
live under `reference/` and are not part of the production code.

## Quick start (local dev)

Requirements: Python 3.12+, optionally Docker.

```bash
# 1. Create venv + install
python -m venv .venv
. .venv/Scripts/activate         # PowerShell: .venv\Scripts\Activate.ps1
pip install -e .[dev]

# 2. Configure
cp .env.example .env             # edit at least DJANGO_SECRET_KEY

# 3. Migrate + seed + run
python manage.py migrate
python manage.py seed_contest --year 2026
python manage.py createsuperuser
python manage.py runserver
```

Open <http://localhost:8000/>. The portal redirects to `/submission/`.

## Layout

```
nmdsuite/
├── nmdsuite/          # Django project (settings, urls)
├── core/              # shared models, audit helper, seed command
├── registration/      # M1
├── portal/            # M2 (auth scaffolding lives here today)
├── scoring/           # M3
├── admin_module/      # M4
├── locale/            # de/, fr/, it/ .po files
├── static/            # css/js (collected by Whitenoise)
├── templates/         # cross-app templates (base.html, partials)
├── tests/             # pytest tests
├── reference/         # untouched legacy artefacts (read-only)
└── data/              # SQLite (gitignored, mounted as a volume in Docker)
```

## Translations

```bash
python manage.py makemessages -l de -l fr -l it --ignore=reference/* --ignore=.venv/*
# edit locale/<code>/LC_MESSAGES/django.po
python manage.py compilemessages
```

## Docker

```bash
docker compose build
docker compose up -d
```

The SQLite file lives in the named volume `nmd_data` mounted at `/data` inside
the container. The `.env` file is read at container start; SMTP creds are
optional (anonymous relay works too).

## Tests

```bash
pytest
```

## Roadmap

- **M0 — Foundations** (this release): models, auth, i18n, Docker, audit log.
- **M1 — Registration**: public form + Swisstopo map, account creation, confirmation email.
- **M2 — Portal**: log entry/upload, station data, scoring view post-publication.
- **M3 — Scoring**: matching engine (Levenshtein ≤ 2), dupe handling, ranking export, ADIF export.
- **M4 — Administration**: lifecycle, on-behalf ops, mass email, backup/restore, audit log viewer.
- **M5 — Hardening**: end-to-end tests, security review, deployment runbook.
