import os
import time
import subprocess
import schedule
from datetime import datetime, timedelta
from pathlib import Path
import logging
from google.oauth2.service_account import Credentials
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

class BackupService:
    def __init__(self):
        self.backup_dir = Path("/backups")
        self.backup_dir.mkdir(exist_ok=True)
        
        self.postgres_url = os.environ.get("DATABASE_URL")
        self.redis_url = os.environ.get("REDIS_URL")
        self.retention_days = int(os.environ.get("BACKUP_RETENTION_DAYS", "30"))
        
        # Google Drive setup
        self.setup_google_drive()
        
    def setup_google_drive(self):
        """Setup Google Drive API client"""
        try:
            credentials_path = "/secrets/gdrive-service-account.json"
            if os.path.exists(credentials_path):
                credentials = Credentials.from_service_account_file(
                    credentials_path,
                    scopes=['https://www.googleapis.com/auth/drive.file']
                )
                self.drive_service = build('drive', 'v3', credentials=credentials)
                logger.info("Google Drive backup enabled")
            else:
                self.drive_service = None
                logger.warning("Google Drive backup disabled - no credentials")
        except Exception as e:
            logger.error(f"Failed to setup Google Drive: {e}")
            self.drive_service = None

    def backup_postgres(self):
        """Backup PostgreSQL database"""
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        backup_file = self.backup_dir / f"postgres_{timestamp}.sql"
        
        try:
            cmd = f"pg_dump {self.postgres_url} > {backup_file}"
            subprocess.run(cmd, shell=True, check=True)
            
            # Compress backup
            subprocess.run(f"gzip {backup_file}", shell=True, check=True)
            compressed_file = f"{backup_file}.gz"
            
            logger.info(f"PostgreSQL backup created: {compressed_file}")
            return compressed_file
        except subprocess.CalledProcessError as e:
            logger.error(f"PostgreSQL backup failed: {e}")
            return None

    def run_backup(self):
        """Run complete backup process"""
        logger.info("Starting backup process...")
        
        backup_files = []
        
        # Backup PostgreSQL
        postgres_backup = self.backup_postgres()
        if postgres_backup:
            backup_files.append(postgres_backup)
            
        # Upload to Google Drive
        for backup_file in backup_files:
            self.upload_to_google_drive(backup_file)
        
        # Cleanup old backups
        self.cleanup_old_backups()
        
        logger.info("Backup process completed")

    def upload_to_google_drive(self, file_path):
        """Upload backup file to Google Drive"""
        if not self.drive_service:
            return None
            
        try:
            folder_id = os.environ.get("GOOGLE_DRIVE_FOLDER_ID")
            
            file_metadata = {
                'name': os.path.basename(file_path),
                'parents': [folder_id] if folder_id else []
            }
            
            media = MediaFileUpload(file_path, resumable=True)
            file = self.drive_service.files().create(
                body=file_metadata,
                media_body=media,
                fields='id'
            ).execute()
            
            logger.info(f"Uploaded to Google Drive: {file_path}")
            return file.get('id')
        except Exception as e:
            logger.error(f"Google Drive upload failed: {e}")
            return None

    def cleanup_old_backups(self):
        """Remove backups older than retention period"""
        cutoff_date = datetime.now() - timedelta(days=self.retention_days)
        
        for backup_file in self.backup_dir.glob("*"):
            if backup_file.is_file():
                file_date = datetime.fromtimestamp(backup_file.stat().st_mtime)
                if file_date < cutoff_date:
                    backup_file.unlink()
                    logger.info(f"Removed old backup: {backup_file}")

    def run_scheduler(self):
        """Run backup scheduler"""
        backup_schedule = os.environ.get("BACKUP_SCHEDULE", "0 2 * * *")
        
        # Schedule daily backup at 2 AM
        schedule.every().day.at("02:00").do(self.run_backup)
        
        logger.info(f"Backup scheduler started with schedule: {backup_schedule}")
        
        # Run initial backup
        self.run_backup()
        
        # Keep scheduler running
        while True:
            schedule.run_pending()
            time.sleep(60)

if __name__ == "__main__":
    backup_service = BackupService()
    backup_service.run_scheduler()