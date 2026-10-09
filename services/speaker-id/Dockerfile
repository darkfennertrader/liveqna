FROM nvidia/cuda:12.9.1-cudnn-runtime-ubuntu24.04

# Matches the host's driver/runtime stack (CUDA 12.9, cuDNN 9.13), same
# base image used by the stt service.

RUN apt-get update && apt-get install -y --no-install-recommends \
        python3 python3-pip python3-venv gcc build-essential python3-dev \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir --break-system-packages -r requirements.txt

COPY app ./app

RUN mkdir -p /cache/huggingface
ENV HF_HOME=/cache/huggingface

EXPOSE 8083
CMD ["python3", "-m", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8083"]
