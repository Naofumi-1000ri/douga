import os
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
        upload_url = f"http://localhost:8000/api/storage/upload/{storage_key}"
        return upload_url, storage_key, expires_at

    def get_public_url(self, storage_key: str) -> str:
        """Get URL for accessing the file."""
        return f"http://localhost:8000/api/storage/files/{storage_key}"

    def generate_download_url(self, storage_key: str, expires_minutes: int = 60) -> str:
        """Generate download URL."""
        return self.get_public_url(storage_key)

    def upload_file_from_bytes(self, storage_key: str, data: bytes) -> str:
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
        shutil.copy(str(full_path), local_path)
        return local_path

    async def upload_file(self, local_path: str, storage_key: str, content_type: str | None = None) -> str:
        """Upload from local path."""
        full_path = self._get_full_path(storage_key)
        shutil.copy(local_path, str(full_path))
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


class IAMSigner:
    """Signer that uses IAM API to sign bytes."""

    def __init__(self, credentials, service_account_email):
        self._credentials = credentials
        self._service_account_email = service_account_email

    def sign(self, message):
        """Sign bytes using IAM signBlob API."""
        import base64
        import httpx

        # Ensure credentials are fresh
        from google.auth.transport import requests as auth_requests
        if not self._credentials.valid:
            self._credentials.refresh(auth_requests.Request())

        url = f"https://iamcredentials.googleapis.com/v1/projects/-/serviceAccounts/{self._service_account_email}:signBlob"

        response = httpx.post(
            url,
            json={"payload": base64.b64encode(message).decode("utf-8")},
            headers={
                "Authorization": f"Bearer {self._credentials.token}",
                "Content-Type": "application/json",
            },
            timeout=30.0,
        )

        if response.status_code != 200:
            raise RuntimeError(f"IAM signBlob failed: {response.status_code} {response.text}")

        result = response.json()
        return base64.b64decode(result["signedBlob"])


class IAMSigningCredentials:
    """Wrapper credentials that use IAM API for signing."""

    def __init__(self, credentials, service_account_email):
        self._credentials = credentials
        self.service_account_email = service_account_email
        self.token = credentials.token
        self._signer = IAMSigner(credentials, service_account_email)

    def refresh(self, request):
        self._credentials.refresh(request)
        self.token = self._credentials.token

    @property
    def signer(self):
        return self._signer

    def sign_bytes(self, message):
        """Sign bytes using IAM signBlob API."""
        return self._signer.sign(message)

    @property
    def signer_email(self):
        return self.service_account_email


class GCSStorageService:
    """Google Cloud Storage service for production."""

    def __init__(self) -> None:
        from google.cloud import storage
        from google.auth import default, compute_engine
        from google.auth.transport import requests as auth_requests

        self._storage = storage
        self._client: storage.Client | None = None
        self._bucket: storage.Bucket | None = None

        # Get default credentials
        self._credentials, self._project = default()
        self._signing_credentials = None
        self._service_account_email: str | None = None
        self._auth_request = auth_requests.Request()

        # For Compute Engine/Cloud Run, we need to create signing credentials
        if isinstance(self._credentials, compute_engine.Credentials):
            # Refresh to get the service account email
            self._credentials.refresh(self._auth_request)
            self._service_account_email = self._credentials.service_account_email

            # Create IAM-based signing credentials wrapper
            self._signing_credentials = IAMSigningCredentials(
                credentials=self._credentials,
                service_account_email=self._service_account_email,
            )
        elif hasattr(self._credentials, 'service_account_email'):
            # Service account credentials - can sign directly
            self._service_account_email = self._credentials.service_account_email
            self._signing_credentials = self._credentials

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

    def _generate_signed_url_v4(
        self,
        storage_key: str,
        method: str,
        content_type: str | None = None,
        expires_minutes: int = 60,
    ) -> str:
        """Generate a V4 signed URL using IAM signBlob API."""
        import base64
        import hashlib
        import urllib.parse
        import httpx

        # Ensure credentials are fresh
        if not self._credentials.valid:
            self._credentials.refresh(self._auth_request)

        now = datetime.now(timezone.utc)
        credential_scope_date = now.strftime("%Y%m%d")
        request_timestamp = now.strftime("%Y%m%dT%H%M%SZ")
        expiration = int(timedelta(minutes=expires_minutes).total_seconds())

        host = f"{settings.gcs_bucket_name}.storage.googleapis.com"
        canonical_uri = f"/{storage_key}"
        credential_scope = f"{credential_scope_date}/auto/storage/goog4_request"
        credential = f"{self._service_account_email}/{credential_scope}"

        # Query parameters
        query_params = {
            "X-Goog-Algorithm": "GOOG4-RSA-SHA256",
            "X-Goog-Credential": credential,
            "X-Goog-Date": request_timestamp,
            "X-Goog-Expires": str(expiration),
            "X-Goog-SignedHeaders": "host",
        }
        if content_type:
            query_params["X-Goog-SignedHeaders"] = "content-type;host"

        canonical_query_string = "&".join(
            f"{urllib.parse.quote(k, safe='')}={urllib.parse.quote(v, safe='')}"
            for k, v in sorted(query_params.items())
        )

        # Canonical headers
        if content_type:
            canonical_headers = f"content-type:{content_type}\nhost:{host}\n"
            signed_headers = "content-type;host"
        else:
            canonical_headers = f"host:{host}\n"
            signed_headers = "host"

        # Canonical request
        canonical_request = "\n".join([
            method,
            canonical_uri,
            canonical_query_string,
            canonical_headers,
            signed_headers,
            "UNSIGNED-PAYLOAD",
        ])

        # String to sign
        canonical_request_hash = hashlib.sha256(canonical_request.encode()).hexdigest()
        string_to_sign = "\n".join([
            "GOOG4-RSA-SHA256",
            request_timestamp,
            credential_scope,
            canonical_request_hash,
        ])

        # Sign using IAM signBlob API
        sign_url = f"https://iamcredentials.googleapis.com/v1/projects/-/serviceAccounts/{self._service_account_email}:signBlob"
        response = httpx.post(
            sign_url,
            json={"payload": base64.b64encode(string_to_sign.encode()).decode()},
            headers={
                "Authorization": f"Bearer {self._credentials.token}",
                "Content-Type": "application/json",
            },
            timeout=30.0,
        )

        if response.status_code != 200:
            raise RuntimeError(f"IAM signBlob failed: {response.status_code} {response.text}")

        signature = base64.b64decode(response.json()["signedBlob"])
        signature_hex = signature.hex()

        # Build signed URL
        signed_url = f"https://{host}{canonical_uri}?{canonical_query_string}&X-Goog-Signature={signature_hex}"
        return signed_url

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

        upload_url = self._generate_signed_url_v4(
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
        return self._generate_signed_url_v4(
            storage_key=storage_key,
            method="GET",
            expires_minutes=expires_minutes,
        )

    def upload_file(
        self,
        storage_key: str,
        file_obj: BinaryIO,
        content_type: str,
    ) -> str:
        """Upload a file directly to GCS."""
        blob = self.bucket.blob(storage_key)
        blob.upload_from_file(file_obj, content_type=content_type)
        return self.get_public_url(storage_key)

    async def download_file(self, storage_key: str, local_path: str) -> str:
        """Download a file from GCS to local path."""
        blob = self.bucket.blob(storage_key)
        blob.download_to_filename(local_path)
        return local_path

    async def upload_file(self, local_path: str, storage_key: str, content_type: str | None = None) -> str:
        """Upload a local file to GCS."""
        blob = self.bucket.blob(storage_key)
        if content_type:
            blob.upload_from_filename(local_path, content_type=content_type)
        else:
            blob.upload_from_filename(local_path)
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


# Use LocalStorageService or GCSStorageService based on config
StorageService = LocalStorageService if settings.use_local_storage else GCSStorageService


# Singleton instance
storage_service = StorageService()


def get_storage_service() -> StorageService:
    return storage_service
