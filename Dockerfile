FROM python:3.12-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    NOVEL_DB_PATH=/data/novel_atlas.db \
    NOVEL_MAX_UPLOAD_MB=30

WORKDIR /app

COPY pyproject.toml ./
COPY app ./app
COPY static ./static
COPY prompts ./prompts
COPY evals ./evals
COPY launcher.py ./launcher.py

RUN python -m pip install --no-cache-dir . \
    && groupadd --gid 10001 novelatlas \
    && useradd --uid 10001 --gid novelatlas --no-create-home --shell /usr/sbin/nologin novelatlas \
    && install -d -o novelatlas -g novelatlas -m 0700 /data

USER 10001:10001
EXPOSE 8080

HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
  CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8080/readyz', timeout=3).read()"]

CMD ["python", "-m", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8080", "--no-access-log"]
