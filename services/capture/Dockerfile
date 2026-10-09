FROM python:3.13-slim

# pipewire-bin provides pw-dump / pw-record, which this service shells out
# to for enumeration and capture.
RUN apt-get update && apt-get install -y --no-install-recommends \
        pipewire-bin \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app

EXPOSE 8081
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8081"]
