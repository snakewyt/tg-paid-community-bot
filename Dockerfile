FROM python:3.12-slim

# Create non-root user
RUN useradd --create-home --shell /bin/bash botuser

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY alembic.ini .
COPY alembic/ ./alembic/
COPY app/ ./app/

# Create writable data directory for SQLite
RUN mkdir -p /app/data && chown botuser:botuser /app/data

# Drop privileges
USER botuser

CMD ["python", "-m", "app.main"]
