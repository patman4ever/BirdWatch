FROM python:3.11-slim

RUN apt-get update && apt-get install -y \
    portaudio19-dev \
    libportaudio2 \
    ffmpeg \
    libsndfile1 \
    alsa-utils \
    gcc \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Voeg audio groep toe
RUN groupadd -g 29 audio || true
RUN usermod -aG audio root

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

RUN mkdir -p recordings logs data

EXPOSE 5000

ENV PYTHONUNBUFFERED=1
ENV PORT=5000

CMD ["python", "app.py"]
