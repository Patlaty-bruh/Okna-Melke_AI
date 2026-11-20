# Один контейнер: FastAPI-интерфейс и агент в одном процессе, frontend раздаёт тот же uvicorn.
FROM python:3.13-slim

ENV PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    HF_HOME=/app/.cache/huggingface

WORKDIR /app

COPY requirements.txt .
# CPU-сборка torch: GPU-зависимости в контейнере не нужны и утяжеляют образ.
RUN pip install --no-cache-dir --index-url https://download.pytorch.org/whl/cpu torch \
    && pip install --no-cache-dir -r requirements.txt

COPY . .

EXPOSE 8000

# Один worker: сессии диалога хранятся в памяти процесса.
CMD ["uvicorn", "api:app", "--host", "0.0.0.0", "--port", "8000"]
