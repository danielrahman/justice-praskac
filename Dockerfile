FROM python:3.13-slim

# System dependencies for PDF processing and OCR
RUN apt-get update && apt-get install -y --no-install-recommends \
    poppler-utils \
    tesseract-ocr \
    tesseract-ocr-ces \
    tesseract-ocr-eng \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Railway sets PORT env var
CMD ["sh", "-c", "exec uvicorn justice.app:app --host 0.0.0.0 --port ${PORT:-8000}"]
