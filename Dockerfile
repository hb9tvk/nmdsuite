FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
       build-essential gettext libproj-dev proj-data proj-bin \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY . .
RUN pip install -e .

RUN mkdir -p /data /app/staticfiles \
    && python manage.py collectstatic --noinput \
    && python manage.py compilemessages || true

EXPOSE 5005
VOLUME ["/data"]
CMD ["gunicorn", "nmdsuite.wsgi:application", "--bind", "0.0.0.0:5005", "--workers", "3", "--timeout", "120"]