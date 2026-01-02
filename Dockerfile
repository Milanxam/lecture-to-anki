FROM python:3.11-slim

# Install FFmpeg
RUN apt-get update && \
    apt-get install -y --no-install-recommends ffmpeg && \
    rm -rf /var/lib/apt/lists/*

# Set working directory
WORKDIR /app

# Copy requirements first for better caching
COPY requirements.txt .

# Install Python dependencies (use headless opencv for Docker)
RUN pip install --no-cache-dir -r requirements.txt && \
    pip uninstall -y opencv-python && \
    pip install --no-cache-dir opencv-python-headless

# Copy application code
COPY lecture_to_anki.py .
COPY prompts/ ./prompts/

# Create directories for volumes
RUN mkdir -p /app/lectures /app/output /app/temp

# Default environment
ENV PYTHONUNBUFFERED=1

# Entrypoint
ENTRYPOINT ["python", "lecture_to_anki.py"]
CMD ["--help"]

