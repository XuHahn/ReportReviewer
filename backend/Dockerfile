FROM python:3.12-slim

WORKDIR /app

# Chromium for PDF export
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
        chromium \
    && rm -rf /var/lib/apt/lists/*

ENV CHROME_PATH=/usr/bin/chromium

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

RUN mkdir -p /app/data /app/logs

ENV DB_PATH=/app/data/review.db

EXPOSE 8000

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
