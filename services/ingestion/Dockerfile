FROM python:3.13-slim

WORKDIR /app
ENV PYTHONPATH=/app

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        fonts-dejavu-core \
        fonts-liberation \
        libreoffice-calc \
        libreoffice-impress \
        libreoffice-writer \
        poppler-utils \
        python3-uno \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app
COPY migrations ./migrations
COPY scripts ./scripts

RUN mkdir -p /data/documents /models /tmp/ingestion-home \
    && chown -R 1000:1000 /data/documents /models /tmp/ingestion-home

EXPOSE 8084
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8084"]
