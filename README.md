# BirdWatch — Docker / Coolify Deployment

## Vereisten
- Docker & Docker Compose
- Intel i3 of nieuwer (AVX ondersteuning vereist voor BirdNET)
- USB microfoon aangesloten op de host machine

---

## Lokaal draaien met Docker Compose

```bash
# Clone/download de bestanden
cd birdwatch-docker

# Bouwen en starten
docker compose up -d

# Logs bekijken
docker compose logs -f

# Dashboard
http://localhost:5000
```

---

## Coolify Deployment

### Stap 1 — Push naar Git repository
```bash
git init
git add .
git commit -m "BirdWatch initial"
git remote add origin https://github.com/JOUW_NAAM/birdwatch.git
git push -u origin main
```

### Stap 2 — Coolify instellen
1. Ga naar Coolify dashboard
2. **New Resource** → **Docker Compose**
3. Koppel je Git repository
4. Coolify detecteert automatisch de `docker-compose.yml`

### Stap 3 — USB microfoon doorgeven
In Coolify onder **Advanced** → **Volumes & Devices**:
```
Device: /dev/snd:/dev/snd
```
Of voeg toe aan de docker-compose.yml:
```yaml
devices:
  - /dev/snd:/dev/snd
privileged: true
```

### Stap 4 — Environment variables in Coolify
```
PORT=5000
DB_PATH=/app/data/birdwatch.db
```

### Stap 5 — Domain instellen
In Coolify onder **Domains**:
```
birdwatch.jouwdomein.nl
```
Coolify regelt automatisch SSL via Let's Encrypt.

---

## Persistent storage in Coolify
Coolify beheert volumes automatisch. De volgende data blijft bewaard:
- `/app/recordings` — audio opnames
- `/app/logs` — logbestanden  
- `/app/data/birdwatch.db` — database met alle detecties

---

## USB microfoon op Coolify server
De USB mic moet aangesloten zijn op de server waar Coolify draait.
Check of hij herkend wordt:
```bash
# Op de Coolify server
arecord -l
# Moet tonen: card X: KT USB Audio
```

Pas dan in `recorder.py` de device aan:
```python
# Verander hw:0,0 naar het juiste device
cmd = ["arecord", "-D", "hw:X,0", ...]
```

---

## Troubleshooting

**BirdNET model download bij eerste start**
Bij eerste start download de container het BirdNET model (~100 MB).
Dit kan 2-3 minuten duren. Check logs:
```bash
docker compose logs -f birdwatch
```

**Microfoon niet gevonden in container**
```bash
docker compose exec birdwatch arecord -l
```

**Port conflict**
Verander in docker-compose.yml:
```yaml
ports:
  - "8080:5000"  # gebruik poort 8080 in plaats van 5000
```
