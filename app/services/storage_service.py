import boto3
import uuid
import logging
import time
from botocore.exceptions import ClientError, EndpointConnectionError
from fastapi import UploadFile, HTTPException
from fastapi.concurrency import run_in_threadpool # 🌟 ADDED: For Non-Blocking I/O
from botocore.client import Config
from app.core.config import settings

logger = logging.getLogger(__name__)

class StorageService:
    def __init__(self):
        if settings.USE_S3:
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
            self.bucket_name = settings.S3_BUCKET_NAME
            self._ensure_bucket_exists()

    def _ensure_bucket_exists(self):
        """Bucket မရှိသေးပါက အလိုအလျောက် အသစ်ဖန်တီးပေးမည်။ MinIO မတက်သေးလျှင် စောင့်ဆိုင်းပေးမည်။"""
        max_retries = 5
        retry_delay = 3  # seconds
        
        for attempt in range(max_retries):
            try:
                self.s3_client.head_bucket(Bucket=self.bucket_name)
                logger.info(f"Storage bucket '{self.bucket_name}' is ready.")
                return
                
            except EndpointConnectionError:
                if attempt < max_retries - 1:
                    logger.warning(f"MinIO not ready yet. Retrying in {retry_delay} seconds... (Attempt {attempt + 1}/{max_retries})")
                    time.sleep(retry_delay)
                else:
                    logger.error("CRITICAL: Failed to connect to MinIO during startup.")
                    raise
                    
            except ClientError as e:
                error_code = e.response.get("Error", {}).get("Code")
                if error_code == "404":
                    logger.info(f"Creating new PRIVATE bucket: {self.bucket_name}")
                    self.s3_client.create_bucket(Bucket=self.bucket_name)
                    return
                else:
                    logger.error(f"Unexpected ClientError checking bucket: {str(e)}")
                    raise e

    async def upload_file(self, file: UploadFile, folder: str = "evidence") -> str:
        """UploadFile ကို လက်ခံပြီး MinIO သို့ သိမ်းဆည်းမည်။ (Non-Blocking)"""
        if not settings.USE_S3:
            raise HTTPException(status_code=500, detail="S3 storage is not enabled")

        if not file or not file.filename:
             raise HTTPException(status_code=400, detail="No file provided")

        try:
            # Handle files with no extension gracefully
            parts = file.filename.split('.')
            file_extension = parts[-1] if len(parts) > 1 else "bin" 
            
            object_name = f"{folder}/{uuid.uuid4()}.{file_extension}"
            
            # 🌟 PERFORMANCE & BUG FIX: Offload synchronous boto3 call to a separate thread
            # This prevents the FastAPI event loop from being blocked, ensuring large files upload successfully.
            
            # 1. Spool file content into memory (safe since we have a 10MB limit in the router)
            content = await file.read()
            
            # 2. Use run_in_threadpool to execute the blocking put_object call
            await run_in_threadpool(
                self.s3_client.put_object,
                Bucket=self.bucket_name,
                Key=object_name,
                Body=content,
                ContentType=file.content_type
            )
            
            return object_name
            
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
        if not object_name:
            return "" # Handle empty/null object names gracefully

        try:
            response = self.s3_client.generate_presigned_url(
                'get_object',
                Params={
                    'Bucket': self.bucket_name,
                    'Key': object_name
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
        if not settings.USE_S3 or not object_name:
            return
            
        try:
            await run_in_threadpool(
                self.s3_client.delete_object,
                Bucket=self.bucket_name,
                Key=object_name
            )
        except Exception as e:
            logger.error(f"Failed to delete file {object_name}: {str(e)}")
            # Do not raise exception here; let the workflow continue even if deletion fails

storage_service = StorageService()