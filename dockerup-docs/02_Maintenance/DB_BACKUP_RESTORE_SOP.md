# DB BACKUP/RESTORE SOP

Version: 1.0  
Scope: WiFi Dashboard PostgreSQL backup and restore operations  
Default path: /mnt/md127/WIFI_YFP_DashBoard

---

## 1. Purpose

This SOP defines standard operations to:
- Create DB backups safely
- Restore DB from backups reliably
- Verify data integrity after backup/restore

Scripts used:
- dockerup-docs/scripts/backup_db_oneclick.sh
- dockerup-docs/scripts/restore_db_oneclick.sh

---

## 2. Prerequisites

Required conditions:
1. Docker service is running
2. PostgreSQL container name is wifitest-db (default)
3. Project root exists at /mnt/md127/WIFI_YFP_DashBoard
4. Compose file exists at /mnt/md127/WIFI_YFP_DashBoard/dockerup-essential/docker-compose.yml
5. User has sudo permission

Quick checks:
1. sudo docker ps
2. ls -lah /mnt/md127/WIFI_YFP_DashBoard
3. ls -lah /mnt/md127/WIFI_YFP_DashBoard/dockerup-docs/scripts

---

## 3. Backup SOP

### 3.1 Recommended backup mode: Logical backup

Use when:
- Migrating to another machine
- Long-term retention
- Need highest portability

Run:
1. cd /mnt/md127/WIFI_YFP_DashBoard/dockerup-docs/scripts
2. chmod +x backup_db_oneclick.sh
3. sudo ./backup_db_oneclick.sh

Output:
- /mnt/md127/WIFI_YFP_DashBoard/backups/db_backup_YYYYMMDD_HHMMSS.tar.gz

Contains:
- wifitest_YYYYMMDD_HHMMSS.dump
- globals_YYYYMMDD_HHMMSS.sql

### 3.2 Optional backup mode: Cold backup

Use when:
- Need filesystem-level pgdata snapshot
- Source and target PostgreSQL major versions are guaranteed compatible

Run:
1. cd /mnt/md127/WIFI_YFP_DashBoard/dockerup-docs/scripts
2. sudo MODE=cold ./backup_db_oneclick.sh

Output:
- /mnt/md127/WIFI_YFP_DashBoard/backups/pgdata_YYYYMMDD_HHMMSS.tar.gz

Note:
- This mode temporarily stops PostgreSQL service.

---

## 4. Restore SOP

### 4.1 Recommended restore mode: Logical restore

Input archive type:
- db_backup_YYYYMMDD_HHMMSS.tar.gz

Run:
1. cd /mnt/md127/WIFI_YFP_DashBoard/dockerup-docs/scripts
2. chmod +x restore_db_oneclick.sh
3. sudo ARCHIVE=/mnt/md127/WIFI_YFP_DashBoard/backups/db_backup_YYYYMMDD_HHMMSS.tar.gz ./restore_db_oneclick.sh

What script does:
1. Extract archive to restore temp folder
2. Start postgres service and wait ready
3. Restore globals (if present)
4. Run pg_restore with clean and if-exists
5. Start full stack
6. Validate row count in test_record

### 4.2 Optional restore mode: Cold restore

Input archive type:
- pgdata_YYYYMMDD_HHMMSS.tar.gz

Run:
1. cd /mnt/md127/WIFI_YFP_DashBoard/dockerup-docs/scripts
2. sudo MODE=cold ARCHIVE=/mnt/md127/WIFI_YFP_DashBoard/backups/pgdata_YYYYMMDD_HHMMSS.tar.gz ./restore_db_oneclick.sh

What script does:
1. Stop full stack
2. Clear docker-data/pgdata
3. Extract pgdata archive
4. Fix ownership to 999:999
5. Start full stack
6. Validate row count in test_record

---

## 5. Verification SOP

After backup:
1. ls -lh /mnt/md127/WIFI_YFP_DashBoard/backups
2. Confirm latest archive exists and size is reasonable

After restore:
1. sudo docker ps
2. curl http://localhost:8000/health
3. sudo docker exec -i wifitest-db psql -U qc -d wifitest -c "SELECT COUNT(*) AS total_records FROM test_record;"

Optional deeper check:
1. sudo docker exec -i wifitest-db psql -U qc -d wifitest -c "SELECT work_order, COUNT(*) FROM test_record GROUP BY work_order ORDER BY 2 DESC LIMIT 10;"

---

## 6. Rollback and Recovery

If restore fails:
1. Keep failed archive and logs unchanged
2. Re-run restore using previous known-good archive
3. Prefer logical restore for cross-host recovery

If cold restore fails to start postgres:
1. Check ownership: sudo chown -R 999:999 /mnt/md127/WIFI_YFP_DashBoard/docker-data/pgdata
2. Check container logs: sudo docker logs wifitest-db
3. Retry restore with logical archive

---

## 7. Operational Recommendations

1. Run logical backup daily or before any system changes
2. Keep at least 7 to 14 historical archives
3. Test restore on a non-production host periodically
4. Do not rely on only one backup mode
5. Do not store backups only on the same disk; copy to external/NAS/offsite

---

## 8. Quick Command Summary

Logical backup:
- sudo ./backup_db_oneclick.sh

Cold backup:
- sudo MODE=cold ./backup_db_oneclick.sh

Logical restore:
- sudo ARCHIVE=/mnt/md127/WIFI_YFP_DashBoard/backups/db_backup_YYYYMMDD_HHMMSS.tar.gz ./restore_db_oneclick.sh

Cold restore:
- sudo MODE=cold ARCHIVE=/mnt/md127/WIFI_YFP_DashBoard/backups/pgdata_YYYYMMDD_HHMMSS.tar.gz ./restore_db_oneclick.sh

---

## 9. Related Files

- dockerup-docs/scripts/backup_db_oneclick.sh
- dockerup-docs/scripts/restore_db_oneclick.sh
- dockerup-essential/docker-compose.yml
- dockerup-docs/README_MIGRATION_QUICKSTART.md
