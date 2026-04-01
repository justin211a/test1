FROM python:3.12-slim

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY src/ src/
COPY scripts/ scripts/

EXPOSE 8080

# Use shell form so $PORT env var is expanded at runtime
CMD python -c "import uvicorn, os; from src.main import app; uvicorn.run(app, host='0.0.0.0', port=int(os.environ.get('PORT', 8080)))"
