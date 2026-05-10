# Convenience targets for local development.
# Windows users: `make` works under Git Bash / WSL; from PowerShell run the commands directly.

PY ?= python

.PHONY: install dev migrate seed test lint format messages compile-messages run docker-build docker-up docker-down

install:
	$(PY) -m pip install -e .[dev]

migrate:
	$(PY) manage.py migrate

seed:
	$(PY) manage.py seed_contest --year 2026

dev: migrate seed
	$(PY) manage.py runserver 0.0.0.0:8000

run: dev

test:
	$(PY) -m pytest

lint:
	$(PY) -m ruff check .
	$(PY) -m black --check .

format:
	$(PY) -m ruff check --fix .
	$(PY) -m black .

messages:
	$(PY) manage.py makemessages -l de -l fr -l it --ignore=reference/* --ignore=.venv/*

compile-messages:
	$(PY) manage.py compilemessages

docker-build:
	docker compose build

docker-up:
	docker compose up -d

docker-down:
	docker compose down
