# Build context is dockerup-essential/ (see docker-compose.yml): the API package, the shared
# log_parser.py and the single-file dashboard are all baked into the image.
FROM python:3.10-slim

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends libpq-dev gcc && rm -rf /var/lib/apt/lists/*

COPY api/requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt

COPY log_parser.py wifi_dashboard.html /app/
COPY api/ /app/

CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "2"]
