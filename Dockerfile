FROM python:3.11-slim

RUN apt-get update && apt-get install -y \
    portaudio19-dev \
    libportaudio2 \
    ffmpeg \
    libsndfile1 \
    alsa-utils \
    gcc \
    curl \
    unzip \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Download BirdNET-Pi vertalingen (alle talen als JSON)
RUN curl -sL "https://github.com/Nachtzuster/BirdNET-Pi/archive/refs/heads/main.zip" -o /tmp/birdnetpi.zip && \
    unzip -q /tmp/birdnetpi.zip "BirdNET-Pi-main/model/l18n/*" -d /tmp/ && \
    mkdir -p /app/labels && \
    cp /tmp/BirdNET-Pi-main/model/l18n/*.json /app/labels/ && \
    rm -rf /tmp/birdnetpi.zip /tmp/BirdNET-Pi-main

COPY . .

RUN mkdir -p recordings logs data

EXPOSE 5000
ENV PYTHONUNBUFFERED=1
ENV PORT=5000
ENV LABELS_DIR=/app/labels

CMD ["python", "app.py"]
