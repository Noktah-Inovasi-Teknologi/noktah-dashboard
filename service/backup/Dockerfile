FROM python:3.12-alpine

WORKDIR /app

# Install system dependencies
RUN apk add --no-cache \
    postgresql-client \
    redis \
    gzip \
    tar

# Copy requirements and install Python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy backup scripts
COPY backup.py .

# Create backup directory
RUN mkdir -p /backups

CMD ["python", "backup.py"]