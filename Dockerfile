FROM python:3.11-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

RUN useradd --create-home --uid 10001 app \
    && mkdir -p /app/data \
    && chown app:app /app/data

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY --chown=app:app . .

USER app
EXPOSE 5001

CMD ["sh", "-c", "test -n \"$TRAB_ADMIN_PIN\" || { echo 'Set TRAB_ADMIN_PIN in the container environment.' >&2; exit 1; }; exec python server.py"]