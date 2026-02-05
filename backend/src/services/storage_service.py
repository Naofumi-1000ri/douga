import asyncio
import shutil
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import BinaryIO

from src.config import get_settings

settings = get_settings()


class LocalStorageService:
    """Local file storage for development without GCS."""

    def __init__(self) -> None:
        self.base_path = Path(settings.local_storage_path)
        self.base_path.mkdir(parents=True, exist_ok=True)

    def _get_full_path(self, storage_key: str) -> Path:
        full_path = self.base_path / storage_key
        full_path.parent.mkdir(parents=True, exist_ok=True)
        return full_path

    def generate_upload_url(
        self,
        project_id: str,
        filename: str,
        content_type: str,
        expires_minutes: int = 60,
    ) -> tuple[str, str, datetime]:
        """Generate upload URL - returns local API endpoint."""
        ext = filename.rsplit(".", 1)[-1] if "." in filename else ""
        storage_key = f"projects/{project_id}/assets/{uuid.uuid4()}.{ext}"
        expires_at = datetime.now(timezone.utc) + timedelta(minutes=expires_minutes)

        # Return local upload endpoint
        upload_url = f"http://127.0.0.1:8000/api/storage/upload/{storage_key}"
        return upload_url, storage_key, expires_at

    def get_public_url(self, storage_key: str) -> str:
        """Get URL for accessing the file."""
        return f"http://127.0.0.1:8000/api/storage/files/{storage_key}"

    def generate_download_url(self, storage_key: str, expires_minutes: int = 60) -> str:
        """Generate download URL."""
        return self.get_public_url(storage_key)

    def upload_file_from_bytes(self, storage_key: str, data: bytes, content_type: str = "application/octet-stream") -> str:
        """Upload file from bytes."""
        full_path = self._get_full_path(storage_key)
        full_path.write_bytes(data)
        return self.get_public_url(storage_key)

    def upload_file(self, storage_key: str, file_obj: BinaryIO, content_type: str) -> str:
        """Upload file from file object."""
        full_path = self._get_full_path(storage_key)
        full_path.write_bytes(file_obj.read())
        return self.get_public_url(storage_key)

    async def download_file(self, storage_key: str, local_path: str) -> str:
        """Copy file to local path."""
        full_path = self._get_full_path(storage_key)
        await asyncio.to_thread(shutil.copy, str(full_path), local_path)
        return local_path

    async def upload_file(self, local_path: str, storage_key: str, content_type: str | None = None) -> str:
        """Upload from local path."""
        full_path = self._get_full_path(storage_key)
        await asyncio.to_thread(shutil.copy, local_path, str(full_path))
        return self.get_public_url(storage_key)

    async def get_signed_url(self, storage_key: str, expiration_minutes: int = 60) -> str:
        """Get download URL."""
        return self.generate_download_url(storage_key, expiration_minutes)

    def delete_file(self, storage_key: str) -> bool:
        """Delete file."""
        full_path = self._get_full_path(storage_key)
        if full_path.exists():
            full_path.unlink()
            return True
        return False

    def file_exists(self, storage_key: str) -> bool:
        """Check if file exists."""
        return self._get_full_path(storage_key).exists()

    def get_file_path(self, storage_key: str) -> Path:
        """Get the actual file path for serving."""
        return self._get_full_path(storage_key)

    def copy_file(self, source_key: str, dest_key: str) -> bool:
        """Copy file from source to destination."""
        source_path = self._get_full_path(source_key)
        dest_path = self._get_full_path(dest_key)
        if source_path.exists():
            shutil.copy(str(source_path), str(dest_path))
            return True
        return False

    def list_files(self, prefix: str) -> list[str]:
        """List all files with the given prefix."""
        prefix_path = self.base_path / prefix
        if not prefix_path.exists():
            return []
        # Return relative paths from base_path
        files = []
        for file_path in prefix_path.rglob("*"):
            if file_path.is_file():
                files.append(str(file_path.relative_to(self.base_path)))
        return files


class GCSStorageService:
    """Google Cloud Storage service for production."""

    def __init__(self) -> None:
        from google.auth import compute_engine, default
        from google.auth.transport import requests as auth_requests
        from google.cloud import storage

        self._storage = storage
        self._client: storage.Client | None = None
        self._bucket: storage.Bucket | None = None

        # Get default credentials
        self._credentials, self._project = default()
        self._service_account_email: str | None = None
        self._auth_request = auth_requests.Request()

        # For Compute Engine/Cloud Run, we need to get the service account email
        if isinstance(self._credentials, compute_engine.Credentials):
            # Refresh to get the service account email
            self._credentials.refresh(self._auth_request)
            self._service_account_email = self._credentials.service_account_email
        elif hasattr(self._credentials, 'service_account_email'):
            # Service account credentials
            self._service_account_email = self._credentials.service_account_email

    @property
    def client(self):
        if self._client is None:
            if settings.gcs_project_id:
                self._client = self._storage.Client(project=settings.gcs_project_id)
            else:
                self._client = self._storage.Client()
        return self._client

    @property
    def bucket(self):
        if self._bucket is None:
            self._bucket = self.client.bucket(settings.gcs_bucket_name)
        return self._bucket

    def _generate_signed_url(
        self,
        storage_key: str,
        method: str,
        content_type: str | None = None,
        expires_minutes: int = 60,
    ) -> str:
        """Generate a signed URL using the GCS library with IAM credentials."""
        blob = self.bucket.blob(storage_key)

        # Refresh credentials if needed
        if not self._credentials.valid:
            self._credentials.refresh(self._auth_request)

        # Use the GCS library's built-in signed URL generation
        # On Cloud Run, this automatically uses IAM signBlob API
        kwargs = {
            "version": "v4",
            "expiration": timedelta(minutes=expires_minutes),
            "method": method,
        }

        # For Cloud Run/Compute Engine, we need to provide the service account email
        if self._service_account_email:
            kwargs["service_account_email"] = self._service_account_email
            kwargs["access_token"] = self._credentials.token

        if content_type:
            kwargs["content_type"] = content_type

        return blob.generate_signed_url(**kwargs)

    def generate_upload_url(
        self,
        project_id: str,
        filename: str,
        content_type: str,
        expires_minutes: int = 60,
    ) -> tuple[str, str, datetime]:
        """Generate a signed URL for uploading a file directly to GCS."""
        ext = filename.rsplit(".", 1)[-1] if "." in filename else ""
        storage_key = f"projects/{project_id}/assets/{uuid.uuid4()}.{ext}"
        expires_at = datetime.now(timezone.utc) + timedelta(minutes=expires_minutes)

        upload_url = self._generate_signed_url(
            storage_key=storage_key,
            method="PUT",
            content_type=content_type,
            expires_minutes=expires_minutes,
        )

        return upload_url, storage_key, expires_at

    def get_public_url(self, storage_key: str) -> str:
        """Get the public URL for a stored file."""
        return f"https://storage.googleapis.com/{settings.gcs_bucket_name}/{storage_key}"

    def generate_download_url(
        self,
        storage_key: str,
        expires_minutes: int = 60,
    ) -> str:
        """Generate a signed URL for downloading a file."""
        return self._generate_signed_url(
            storage_key=storage_key,
            method="GET",
            expires_minutes=expires_minutes,
        )

    def upload_file_from_bytes(self, storage_key: str, data: bytes, content_type: str = "application/octet-stream") -> str:
        """Upload file from bytes directly to GCS."""
        blob = self.bucket.blob(storage_key)
        blob.upload_from_string(data, content_type=content_type)
        return self.get_public_url(storage_key)

    def upload_file_from_fileobj(
        self,
        storage_key: str,
        file_obj: BinaryIO,
        content_type: str,
    ) -> str:
        """Upload a file from file object directly to GCS."""
        blob = self.bucket.blob(storage_key)
        blob.upload_from_file(file_obj, content_type=content_type)
        return self.get_public_url(storage_key)

    async def download_file(self, storage_key: str, local_path: str) -> str:
        """Download a file from GCS to local path."""
        blob = self.bucket.blob(storage_key)
        await asyncio.to_thread(blob.download_to_filename, local_path)
        return local_path

    async def upload_file(self, local_path: str, storage_key: str, content_type: str | None = None) -> str:
        """Upload a local file to GCS."""
        blob = self.bucket.blob(storage_key)
        if content_type:
            await asyncio.to_thread(
                blob.upload_from_filename,
                local_path,
                content_type=content_type,
            )
        else:
            await asyncio.to_thread(blob.upload_from_filename, local_path)
        return self.get_public_url(storage_key)

    async def get_signed_url(self, storage_key: str, expiration_minutes: int = 60) -> str:
        """Generate a signed download URL (async wrapper for generate_download_url)."""
        return self.generate_download_url(storage_key, expiration_minutes)

    def delete_file(self, storage_key: str) -> bool:
        """Delete a file from GCS."""
        blob = self.bucket.blob(storage_key)
        if blob.exists():
            blob.delete()
            return True
        return False

    def file_exists(self, storage_key: str) -> bool:
        """Check if a file exists in GCS."""
        blob = self.bucket.blob(storage_key)
        return blob.exists()

    def copy_file(self, source_key: str, dest_key: str) -> bool:
        """Copy file from source to destination in GCS."""
        source_blob = self.bucket.blob(source_key)
        if source_blob.exists():
            self.bucket.copy_blob(source_blob, self.bucket, dest_key)
            return True
        return False

    def list_files(self, prefix: str) -> list[str]:
        """List all files with the given prefix."""
        blobs = self.bucket.list_blobs(prefix=prefix)
        return [blob.name for blob in blobs]

    def download_file_content(self, storage_key: str) -> bytes | None:
        """Download file content as bytes from GCS."""
        blob = self.bucket.blob(storage_key)
        if blob.exists():
            return blob.download_as_bytes()
        return None

    def upload_file_content(
        self, content: bytes, storage_key: str, content_type: str = "application/octet-stream"
    ) -> str:
        """Upload content bytes to GCS."""
        blob = self.bucket.blob(storage_key)
        blob.upload_from_string(content, content_type=content_type)
        return self.get_public_url(storage_key)


# Use LocalStorageService or GCSStorageService based on config
StorageService = LocalStorageService if settings.use_local_storage else GCSStorageService


# Singleton instance
storage_service = StorageService()


def get_storage_service() -> StorageService:
    return storage_service
