import boto3
import uuid
import logging
import time
import os
import re
from botocore.exceptions import ClientError, EndpointConnectionError
from fastapi import UploadFile, HTTPException
from fastapi.concurrency import run_in_threadpool # 🌟 ADDED: For Non-Blocking I/O
from botocore.client import Config
from app.core.config import settings

logger = logging.getLogger(__name__)
MAX_UPLOAD_SIZE = 10 * 1024 * 1024
_SAFE_EXTENSION = re.compile(r"^[a-z0-9]{1,10}$")


def _validate_storage_key(key: str) -> str:
    """Reject path-like keys so object storage cannot be used for traversal."""
    if (
        not key
        or len(key) > 1024
        or "\x00" in key
        or "\\" in key
        or key.startswith("/")
        or any(part in {"", ".", ".."} for part in key.split("/"))
    ):
        raise HTTPException(status_code=400, detail="Invalid storage object path.")
    return key

class StorageService:
    def __init__(self):
        self.s3_client = None
        self.bucket_name = settings.S3_BUCKET_NAME
        self.is_available = False

        if not settings.USE_S3:
            logger.info("S3 storage is disabled via configuration.")
            return

        if not all((settings.S3_ENDPOINT, settings.S3_ACCESS_KEY, settings.S3_SECRET_KEY)):
            logger.warning("S3 storage is enabled but endpoint/credentials are missing; storage will remain unavailable.")
            return

        try:
            # 🌟 PERFORMANCE FIX: Added connection pooling and timeouts to prevent hanging
            boto_config = Config(
                connect_timeout=5,
                read_timeout=60,
                retries={'max_attempts': 3}
            )
            self.s3_client = boto3.client(
                's3',
                endpoint_url=settings.S3_ENDPOINT,
                aws_access_key_id=settings.S3_ACCESS_KEY,
                aws_secret_access_key=settings.S3_SECRET_KEY,
                config=boto_config
            )
            self.is_available = self._ensure_bucket_exists()
        except Exception as exc:
            logger.warning(f"S3 client could not be initialized; storage will stay unavailable: {exc}")
            self.s3_client = None
            self.is_available = False

    def _ensure_bucket_exists(self):
        """Bucket မရှိသေးပါက အလိုအလျောက် အသစ်ဖန်တီးပေးမည်။ MinIO မတက်သေးလျှင် safe mode ဖြင့် သင့်တော်စွာ back off လုပ်မည်။"""
        if not self.s3_client:
            return False

        max_retries = 3
        retry_delay = 1  # Fast-fail for local/dev environments when storage is absent.

        for attempt in range(max_retries):
            try:
                self.s3_client.head_bucket(Bucket=self.bucket_name)
                logger.info(f"Storage bucket '{self.bucket_name}' is ready.")
                return True

            except (EndpointConnectionError, ConnectionError, OSError):
                if attempt < max_retries - 1:
                    logger.warning(f"Storage backend not ready yet. Retrying in {retry_delay} second(s)... (Attempt {attempt + 1}/{max_retries})")
                    time.sleep(retry_delay)
                else:
                    logger.warning("Storage backend is unavailable; continuing without S3-backed uploads.")
                    return False

            except ClientError as e:
                error_code = e.response.get("Error", {}).get("Code")
                if error_code == "404":
                    logger.info(f"Creating new PRIVATE bucket: {self.bucket_name}")
                    self.s3_client.create_bucket(Bucket=self.bucket_name)
                    return True
                else:
                    logger.warning(f"Unexpected S3 bucket error: {str(e)}. Storage will remain unavailable.")
                    return False

        return False

    async def upload_file(self, file: UploadFile, folder: str = "evidence") -> str:
        """UploadFile ကို လက်ခံပြီး MinIO သို့ သိမ်းဆည်းမည်။ (Non-Blocking)"""
        if not settings.USE_S3 or not self.s3_client:
            raise HTTPException(status_code=503, detail="Storage backend is unavailable. Please try again later.")

        if not file or not file.filename:
             raise HTTPException(status_code=400, detail="No file provided")

        try:
            # Handle files with no extension gracefully
            safe_folder = _validate_storage_key(folder)
            file_extension = os.path.splitext(os.path.basename(file.filename))[1].lower().lstrip(".")
            if not _SAFE_EXTENSION.fullmatch(file_extension):
                file_extension = "bin"
            object_name = _validate_storage_key(
                f"{safe_folder}/{uuid.uuid4()}.{file_extension}"
            )

            # 🌟 PERFORMANCE & BUG FIX: Offload synchronous boto3 call to a separate thread
            # This prevents the FastAPI event loop from being blocked, ensuring large files upload successfully.

            # Validate the spooled upload before handing the file object to boto3.
            file_object = file.file
            current_position = file_object.tell()
            file_object.seek(0, os.SEEK_END)
            file_size = file_object.tell()
            file_object.seek(current_position)
            if file_size > MAX_UPLOAD_SIZE:
                raise HTTPException(status_code=413, detail="File exceeds the 10 MB upload limit.")
            file_object.seek(0)

            # Stream the spooled file instead of duplicating the entire payload in memory.
            await run_in_threadpool(
                self.s3_client.upload_fileobj,
                Bucket=self.bucket_name,
                Key=object_name,
                Fileobj=file_object,
                ExtraArgs={"ContentType": file.content_type or "application/octet-stream"},
            )

            return object_name

        except HTTPException:
            raise
        except ClientError as e:
            logger.error(f"AWS ClientError during upload: {str(e)}")
            raise HTTPException(status_code=500, detail="Storage service error during upload")
        except Exception as e:
            logger.error(f"File upload failed: {str(e)}")
            raise HTTPException(status_code=500, detail="Could not upload file to storage")

    def get_presigned_url(self, object_name: str, expiration: int = 3600) -> str:
        """
        Database ထဲရှိ Object Name ကိုယူ၍ ကြည့်ရှုခွင့် Presigned URL ထုတ်ပေးမည်။
        """
        safe_object_name = _validate_storage_key(object_name)
        if not self.s3_client:
            return ""

        try:
            response = self.s3_client.generate_presigned_url(
                'get_object',
                Params={
                    'Bucket': self.bucket_name,
                    'Key': safe_object_name
                },
                ExpiresIn=expiration
            )
            return response
        except ClientError as e:
            logger.error(f"Generate presigned URL failed: {str(e)}")
            raise HTTPException(status_code=500, detail="Could not generate file access link")

    # 🌟 ADDED: Missing delete_file function required by router.py
    async def delete_file(self, object_name: str):
        """MinIO/S3 မှ ဖိုင်ကို ဖျက်ပစ်မည်"""
        if not settings.USE_S3 or not object_name or not self.s3_client:
            return

        try:
            safe_object_name = _validate_storage_key(object_name)
            await run_in_threadpool(
                self.s3_client.delete_object,
                Bucket=self.bucket_name,
                Key=safe_object_name
            )
        except Exception as e:
            logger.error(f"Failed to delete file {object_name}: {str(e)}")
            raise RuntimeError("Storage deletion failed") from e

storage_service = StorageService()