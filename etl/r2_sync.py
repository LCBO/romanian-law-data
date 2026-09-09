import logging
import os
from pathlib import Path
import boto3
from botocore.config import Config

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

DATA_DIR = Path(os.getenv("DATA_DIR", "data"))


def get_r2_client():
    endpoint_url = os.getenv("R2_ENDPOINT") or os.getenv("R2_ENDPOINT_URL")
    account_id = os.getenv("R2_ACCOUNT_ID")
    access_key = os.getenv("R2_ACCESS_KEY_ID") or os.getenv("AWS_ACCESS_KEY_ID")
    secret_key = os.getenv("R2_SECRET_ACCESS_KEY") or os.getenv("AWS_SECRET_ACCESS_KEY")

    if not endpoint_url:
        if account_id:
            endpoint_url = f"https://{account_id}.r2.cloudflarestorage.com"
        else:
            raise ValueError(
                "Missing R2 endpoint: either R2_ENDPOINT (e.g. https://<account-id>.r2.cloudflarestorage.com) "
                "or R2_ACCOUNT_ID must be set."
            )

    if not access_key or not secret_key:
        raise ValueError("Missing R2 credentials: R2_ACCESS_KEY_ID and R2_SECRET_ACCESS_KEY must be set.")

    return boto3.client(
        "s3",
        endpoint_url=endpoint_url,
        aws_access_key_id=access_key,
        aws_secret_access_key=secret_key,
        config=Config(signature_version="s3v4"),
        region_name="auto",
    )


def sync_to_r2(bucket_name: str | None = None, prefix: str = "", files: list[str] | None = None):
    bucket = bucket_name or os.getenv("R2_BUCKET") or os.getenv("R2_BUCKET_NAME") or "lawchat-documents"

    try:
        client = get_r2_client()
    except Exception as e:
        logger.error(f"Cannot initialize R2 client: {e}")
        return False

    logger.info(f"Connecting to Cloudflare R2 bucket: {bucket}")

    if files:
        files_to_upload = [Path(f) for f in files]
    else:
        known_patterns = [
            "*.parquet",
            "*.sql",
            "*.sha256",
            "fts.duckdb",
            "fts.duckdb.zst*",
        ]
        files_to_upload = []
        for pat in known_patterns:
            files_to_upload.extend(DATA_DIR.glob(pat))

    if not files_to_upload:
        logger.warning("No files found to upload to R2.")
        return True

    success_count = 0
    for local_path in files_to_upload:
        if not local_path.exists() or local_path.is_dir():
            continue

        filename = local_path.name
        remote_key = f"{prefix}/{filename}".lstrip("/") if prefix else filename
        logger.info(f"Uploading {local_path} -> s3://{bucket}/{remote_key} ({local_path.stat().st_size / (1024*1024):.2f} MB)...")
        
        content_type = "application/octet-stream"
        if filename.endswith(".parquet"):
            content_type = "application/vnd.apache.parquet"
        elif filename.endswith(".sql"):
            content_type = "text/plain"
        elif filename.endswith(".duckdb"):
            content_type = "application/x-duckdb"

        try:
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
            success_count += 1
        except Exception as e:
            logger.error(f"Failed to upload {filename}: {e}")

    logger.info(f"R2 Sync finished: {success_count}/{len(files_to_upload)} files uploaded.")
    return True


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Sync Parquet & FTS data to Cloudflare R2")
    parser.add_argument("--bucket", type=str, default=None, help="R2 Bucket name (default: lawchat-documents)")
    parser.add_argument("--prefix", type=str, default="", help="Optional S3 key prefix")
    parser.add_argument("files", nargs="*", default=None, help="Specific file paths to upload")
    args = parser.parse_args()

    sync_to_r2(bucket_name=args.bucket, prefix=args.prefix, files=args.files or None)
