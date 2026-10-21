# WiFi Dashboard Migration Quickstart

This project is organized into three folders:

- `dockerup-essential`: runtime files to move and run
- `dockerup-docs`: guides and deployment scripts
- `noneedfile`: old or optional files

## 1) Copy to target machine

Copy `dockerup-essential` to the target host. Example Ubuntu path:

`/mnt/md127/WIFI_YFP_DashBoard/dockerup-essential`

## 2) Prepare `.env`

In `dockerup-essential`, create `.env` (or copy from `.env.production`):

```bash
cd /mnt/md127/WIFI_YFP_DashBoard/dockerup-essential
cp .env.production .env
```

## 3) Prepare persistent directories

```bash
mkdir -p docker-data/pgdata docker-data/grafana-data docker-data/app-logs
sudo chown -R 70:70 docker-data/pgdata   # postgres:16-alpine runs as uid 70
sudo chown -R 472:472 docker-data/grafana-data
sudo chown -R sit:sit /mnt/md127/WIFI_YFP_DashBoard
```

## 3.1) Allow user `sit` to operate Docker

```bash
sudo usermod -aG docker sit
newgrp docker
```

If you use the md127 mount service, it will also apply `sit:sit` ownership to `/mnt/md127`
and `/mnt/md127/WIFI_YFP_DashBoard` after mount.

## 3.2) One-shot permission repair

If permissions become inconsistent after migration or reboot, run:

```bash
cd /mnt/md127/WIFI_YFP_DashBoard/dockerup-docs/02_Maintenance/scripts
chmod +x fix_project_permissions.sh
sudo ./fix_project_permissions.sh
```

## 4) Start database first

```bash
docker compose up -d postgres
```

## 5) Restore DB (if migrating existing data)

If you have backup SQL:

```bash
# If source file was generated on Windows PowerShell with UTF-16
iconv -f UTF-16LE -t UTF-8 backup_wifitest.sql > backup_wifitest.utf8.sql
sed -i 's/\r$//' backup_wifitest.utf8.sql

# Optional: reset schema before import to avoid "already exists"
docker exec -i wifitest-db psql -v ON_ERROR_STOP=1 -U qc -d wifitest -c "DROP SCHEMA public CASCADE; CREATE SCHEMA public AUTHORIZATION qc;"

# Import
docker exec -i wifitest-db psql -v ON_ERROR_STOP=1 -U qc -d wifitest < backup_wifitest.utf8.sql
```

## 6) Start all services

```bash
docker compose up -d
docker compose ps
```

## 7) Validate

```bash
curl http://localhost:8000/health
docker exec wifitest-db psql -U qc -d wifitest -c "SELECT COUNT(*) FROM test_record;"
```

Open pages:

- API Dashboard: `http://<host-ip>:8000/`
- Grafana: `http://<host-ip>:3000/`

## Notes

- Do not run `docker compose down -v` in production.
- For local Windows testing, use `.env` with `STORAGE_PATH=.`.
