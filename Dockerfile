# Stage 1: builder - install dependencies
FROM python:3.10-slim AS builder

WORKDIR /build

COPY pyproject.toml requirements.lock ./
COPY deployd/ ./deployd/

# Exact, tested versions from the lock (CPU torch), then the project itself without re-resolving.
RUN pip install --no-cache-dir --prefix=/install \
        --extra-index-url https://download.pytorch.org/whl/cpu -r requirements.lock \
    && pip install --no-cache-dir --prefix=/install --no-deps .

# Stage 2: runtime
FROM python:3.10-slim AS runtime

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

WORKDIR /app

COPY --from=builder /install /usr/local

COPY pyproject.toml ./
COPY deployd/ ./deployd/
COPY data/ ./data/
COPY prompts/ ./prompts/
COPY demo/ ./demo/

EXPOSE 8000 8501

CMD ["uvicorn", "deployd.entrypoints.api:app", "--host", "0.0.0.0", "--port", "8000"]
