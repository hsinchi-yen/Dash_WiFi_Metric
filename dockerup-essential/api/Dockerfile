# Build context is dockerup-essential/ (see docker-compose.yml): the API package and the shared
# log_parser.py are baked into the image (the web front end is served by nginx).
FROM python:3.10-slim

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends libpq-dev gcc && rm -rf /var/lib/apt/lists/*

COPY api/requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt

COPY api/ /app/
# After api/: the shared parser must win over anything in api/ (docker-compose.dev.yml leaves an empty
# mount point with this name there).
COPY log_parser.py /app/

CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "2"]
