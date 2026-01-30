FROM python:3.9-slim

# Set working directory
WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    libheif-dev \
    libmagickwand-dev \
    gcc \
    g++ \
    build-essential \
    python3 \
    python3-pip \
    python3-venv \
    libopenjp2-7 \
    libsdl2-2.0-0 \
    python3-pil \
    fbi \
    python3-smbus \
    i2c-tools \
    imagemagick \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

# Create and activate virtual environment
RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

# Create necessary directories (including /app/data for volume mount)
RUN mkdir -p /app/uploads/thumbnails \
    /app/logs \
    /app/credentials \
    /app/integrations/overlays \
    /app/config \
    /app/data/uploads/thumbnails \
    /app/data/logs \
    /app/data/db_backups

# Copy requirements first for better caching
COPY requirements.txt .

# Install Python dependencies (needs ~10 GB free disk on build host for PyTorch/ML deps).
# Install wheel first so more packages use binary wheels, reducing build-time disk use.
RUN pip install --no-cache-dir --upgrade pip wheel \
    && pip install --no-cache-dir -r requirements.txt

# Copy the rest of the application
COPY . .

# Note: Running as root because container uses privileged mode for hardware access
# and needs to create directories in mounted volumes

# Expose the port the app runs on
EXPOSE 5000

# Set environment variables
ENV FLASK_APP=server.py
ENV FLASK_ENV=production
ENV PYTHONUNBUFFERED=1
ENV PYTHONPATH=/app

# Make the entrypoint script executable
RUN chmod +x ./docker/docker-entrypoint.sh

# Use the entrypoint script
ENTRYPOINT ["./docker/docker-entrypoint.sh"]