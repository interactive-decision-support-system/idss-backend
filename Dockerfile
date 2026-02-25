FROM python:3.11-slim

WORKDIR /app

# System deps
RUN apt-get update && apt-get install -y build-essential && rm -rf /var/lib/apt/lists/*

# Install only runtime dependencies (no sentence-transformers/faiss/PyTorch — saves ~500MB and avoids timeout)
RUN pip install --no-cache-dir \
    fastapi==0.128.0 \
    "uvicorn[standard]==0.40.0" \
    pydantic==2.12.5 \
    sqlalchemy==2.0.46 \
    psycopg2-binary==2.9.11 \
    redis==7.1.0 \
    openai==2.16.0 \
    python-dotenv==1.2.1 \
    httpx==0.28.1

# Copy application code
COPY mcp-server/ ./mcp-server/
COPY agent/ ./agent/

# PYTHONPATH so `from agent import ...` resolves inside mcp-server
ENV PYTHONPATH=/app
ENV PYTHONUNBUFFERED=1
ENV PYTHONDONTWRITEBYTECODE=1

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--app-dir", "/app/mcp-server", "--host", "0.0.0.0", "--port", "8000"]
