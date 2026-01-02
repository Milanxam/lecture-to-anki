FROM python:3.11-slim

# Install FFmpeg and other dependencies
# Note: libgl1 replaces deprecated libgl1-mesa-glx in newer Debian
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
        ffmpeg \
        libgl1 \
        libglib2.0-0 \
        libsm6 \
        libxext6 \
        libxrender1 \
    && rm -rf /var/lib/apt/lists/*

# Set working directory
WORKDIR /app

# Copy requirements first for better caching
COPY requirements.txt .

# Install Python dependencies (use headless opencv for Docker)
RUN pip install --no-cache-dir -r requirements.txt && \
    pip uninstall -y opencv-python || true && \
    pip install --no-cache-dir opencv-python-headless

# Copy application code
COPY lecture_to_anki.py .
COPY prompts/ ./prompts/

# Create directories for volumes
RUN mkdir -p /app/lectures /app/output /app/temp

# Default environment
ENV PYTHONUNBUFFERED=1

# Health check (for container orchestration)
HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
    CMD python -c "import lecture_to_anki; print('OK')" || exit 1

# Entrypoint
ENTRYPOINT ["python", "lecture_to_anki.py"]
CMD ["--help"]

