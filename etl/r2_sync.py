import logging
import os
from pathlib import Path
import boto3
from botocore.config import Config

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

DATA_DIR = Path("data")


def get_r2_client():
    account_id = os.getenv("R2_ACCOUNT_ID")
    access_key = os.getenv("R2_ACCESS_KEY_ID")
    secret_key = os.getenv("R2_SECRET_ACCESS_KEY")

    if not all([account_id, access_key, secret_key]):
        raise ValueError("Missing R2 credentials: R2_ACCOUNT_ID, R2_ACCESS_KEY_ID, R2_SECRET_ACCESS_KEY must be set.")

    endpoint_url = f"https://{account_id}.r2.cloudflarestorage.com"
    
    return boto3.client(
        "s3",
        endpoint_url=endpoint_url,
        aws_access_key_id=access_key,
        aws_secret_access_key=secret_key,
        config=Config(signature_version="s3v4"),
        region_name="auto",
    )


def sync_to_r2(bucket_name: str | None = None, prefix: str = ""):
    bucket = bucket_name or os.getenv("R2_BUCKET_NAME")
    if not bucket:
        raise ValueError("Bucket name must be provided or set via R2_BUCKET_NAME.")

    client = get_r2_client()
    logger.info(f"Connecting to Cloudflare R2 bucket: {bucket}")

    files_to_upload = [
        "decisions.parquet",
        "decision_paragraphs.parquet",
        "relationships.parquet",
        "create_views.sql",
        "decisions.sha256",
    ]

    # Add FTS split archives if present
    for fts_part in DATA_DIR.glob("fts.duckdb.zst.part-*"):
        files_to_upload.append(fts_part.name)

    for filename in files_to_upload:
        local_path = DATA_DIR / filename if (DATA_DIR / filename).exists() else Path(filename)
        if not local_path.exists():
            logger.warning(f"File not found, skipping: {local_path}")
            continue

        remote_key = f"{prefix}/{filename}".lstrip("/") if prefix else filename
        logger.info(f"Uploading {local_path} -> s3://{bucket}/{remote_key}...")
        
        content_type = "application/octet-stream"
        if filename.endswith(".parquet"):
            content_type = "application/vnd.apache.parquet"
        elif filename.endswith(".sql"):
            content_type = "text/plain"

        client.upload_file(
            str(local_path),
            bucket,
            remote_key,
            ExtraArgs={
                "ContentType": content_type,
                "CacheControl": "public, max-age=86400",
            },
        )
        logger.info(f"Uploaded {filename} successfully.")


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Sync Parquet & FTS data to Cloudflare R2")
    parser.add_argument("--bucket", type=str, default=None, help="R2 Bucket name")
    parser.add_argument("--prefix", type=str, default="", help="Optional S3 key prefix")
    args = parser.parse_args()

    sync_to_r2(bucket_name=args.bucket, prefix=args.prefix)
