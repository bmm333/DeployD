# Stage 1: builder — install dependencies
FROM python:3.10-slim AS builder

WORKDIR /build

COPY pyproject.toml .
COPY deployd/ ./deployd/

RUN pip install --no-cache-dir --prefix=/install .

# Stage 2: runtime
FROM python:3.10-slim AS runtime

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

WORKDIR /app

COPY --from=builder /install /usr/local

COPY deployd/ ./deployd/
COPY demo/ ./demo/
COPY data/runbooks/ ./data/runbooks/
COPY scripts/ ./scripts/

RUN mkdir -p data/investigations data/chroma

# Streamlit
EXPOSE 8501

CMD ["streamlit", "run", "demo/app.py", \
     "--server.address=0.0.0.0", \
     "--server.headless=true"]
