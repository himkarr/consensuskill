# Runtime image for both backend services (gateway + round engine).
# compose overrides the command per service; Render/free-tier runs the
# gateway with EMBEDDED_ENGINE=1 and its own $PORT.
FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

COPY requirements.txt ./
RUN pip install -r requirements.txt

COPY shared/ shared/
COPY engine/ engine/
COPY gateway/ gateway/
COPY data/ data/
COPY deploy/ deploy/

RUN useradd --create-home --uid 10001 app
USER app

EXPOSE 8000
CMD ["sh", "-c", "exec uvicorn gateway.app.main:app --host 0.0.0.0 --port ${PORT:-8000} --log-level warning"]
