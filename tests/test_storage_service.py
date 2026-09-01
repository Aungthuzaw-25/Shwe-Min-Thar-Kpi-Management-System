import asyncio
from types import SimpleNamespace

import pytest
from botocore.exceptions import EndpointConnectionError
from fastapi import HTTPException

import app.services.storage_service as storage_module


class FakeS3Client:
    def __init__(self):
        self.calls = []

    def head_bucket(self, Bucket):
        self.calls.append(("head_bucket", Bucket))
        raise EndpointConnectionError(endpoint_url="http://minio:9000")


def test_storage_service_falls_back_when_minio_not_available(monkeypatch):
    monkeypatch.setattr(storage_module.settings, "USE_S3", True)
    monkeypatch.setattr(storage_module.settings, "S3_ENDPOINT", "http://minio:9000")
    monkeypatch.setattr(storage_module.settings, "S3_ACCESS_KEY", "kpiadmin")
    monkeypatch.setattr(storage_module.settings, "S3_SECRET_KEY", "kpiadmin123")
    monkeypatch.setattr(storage_module.boto3, "client", lambda *args, **kwargs: FakeS3Client())

    service = storage_module.StorageService()

    assert service.is_available is False


def test_upload_file_rejects_unavailable_storage():
    service = storage_module.StorageService()
    service.s3_client = None
    file_obj = SimpleNamespace(filename="report.pdf", content_type="application/pdf")

    async def _run():
        with pytest.raises(HTTPException) as exc_info:
            await service.upload_file(file_obj, folder="evidence")
        assert exc_info.value.status_code == 503

    asyncio.run(_run())
