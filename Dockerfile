FROM python:3.11-slim

# System dependencies
RUN apt-get update && apt-get install -y \
    portaudio19-dev \
    libportaudio2 \
    ffmpeg \
    libsndfile1 \
    alsa-utils \
    gcc \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application
COPY . .

# Create directories
RUN mkdir -p recordings logs

# Expose port
EXPOSE 5000

# Environment
ENV PYTHONUNBUFFERED=1
ENV PORT=5000

CMD ["python", "app.py"]
