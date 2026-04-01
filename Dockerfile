FROM python:3.12-slim

WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    && rm -rf /var/lib/apt/lists/*

# Install Python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY src/ src/
COPY scripts/ scripts/

# Cloud Run sets PORT env var. Uvicorn starts on that port.
CMD ["python", "-c", "import uvicorn; from src.main import app; import os; uvicorn.run(app, host='0.0.0.0', port=int(os.environ.get('PORT', 8080)))"]
