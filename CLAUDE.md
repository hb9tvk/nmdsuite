# Repo guide for AI agents

This file is read automatically by Claude Code when working in this repo. Keep it terse.

## Project shape

- Django 5 + SQLite. Single shared DB; all domain rows carry a `Contest` FK.
- Apps: `core` (models, audit log, seed command), `registration`, `portal` (auth lives here),
  `scoring`, `admin_module`. Each app is mounted under a distinct URL prefix.
- Template lookup: project-level `templates/` first (base.html, partials), then per-app dirs.
- Auth: Django built-in `auth.User`. `username = callsign without /P`. Argon2 hasher.
- i18n: DE/FR/IT only. No English UI strings beyond fallback `msgid`s.

## Hard constraints (do not violate)

- The `.nmd / .csv` upload format is fixed at `UTC;CALL;RSTS;TXTS;RSTR;TXTR`. **Do not** add a
  mode column. Mode is derived at parse time from RST length: 2 digits = SSB, 3 digits = CW.
- Dupe handling for NMD↔NMD QSOs (per peer, mode, contest half): keep the **best-quality**
  match (`full_match` > `text_mismatch` > `unmatched`), not the chronologically earliest.
- Out of scope (do not propose, even tangentially): Cabrillo/ADIF *import*, captcha,
  rules-engine versioning, health/metrics endpoints. ADIF *export* is in scope.
- The `reference/` folder is read-only legacy material. Never edit files there.

## Conventions

- Audit-worthy actions go through `core.audit.audit(...)`. Don't write `AuditLog.objects.create`
  directly from views.
- Money-equivalent identifiers: callsigns are stored uppercase, no `/P` in the `User.username`,
  but `/P` is preserved in `Participant.callsign` if the operator uses it on air.
- Coordinates: store CH1903+ (LV95) E/N and WGS84 lat/lon in canonical columns; keep the
  user's original input in the `*_input_*` columns for display.
- All datetimes are UTC. The contest start/end times in `Contest` are timezone-aware UTC.

## Commands

```bash
pytest                            # run tests
python manage.py runserver
python manage.py makemigrations
python manage.py migrate
python manage.py seed_contest --year <YYYY>
python manage.py makemessages -l de -l fr -l it
python manage.py compilemessages
```
