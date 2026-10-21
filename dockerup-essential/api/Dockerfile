# Build context is dockerup-essential/ (see docker-compose.yml): the API package, the shared
# log_parser.py and the single-file dashboard are all baked into the image.
FROM python:3.10-slim

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends libpq-dev gcc && rm -rf /var/lib/apt/lists/*

COPY api/requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt

COPY api/ /app/
# After api/: the shared parser and the single-file dashboard must win over anything in api/
# (docker-compose.dev.yml leaves empty mount points with these names there).
COPY log_parser.py wifi_dashboard.html /app/

CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "2"]
