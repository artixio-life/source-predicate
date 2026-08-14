import os
import time
import unicodedata
import boto3
from botocore.client import Config
from botocore.exceptions import ClientError
import logging

logger = logging.getLogger(__name__)


def _is_safe_key_char(ch: str) -> bool:
    """A character that's safe to keep as-is in an S3 key.

    isalnum() covers letters/digits in every script, but some scripts stack
    combining vowel/tone marks onto a base consonant, e.g. Vietnamese in NFD
    form) rely on Unicode "Mark" codepoints (category Mn/Mc) that are NOT
    alphanumeric on their own -- filtering purely on isalnum() shatters those
    words into single consonants separated by underscores. Keeping Mn/Mc
    alongside isalnum() preserves the script as a human reads it while still
    treating punctuation as unsafe.
    """
    if ch in '-._':
        return True
    if ch.isalnum():
        return True
    return unicodedata.category(ch) in ('Mn', 'Mc')


def _safe_title_slug(title: str, max_len: int = 120) -> str:
    """Collapse every run of unsafe characters in `title` to one underscore."""
    out = []
    prev_was_unsafe = False
    for ch in title:
        if _is_safe_key_char(ch):
            out.append(ch)
            prev_was_unsafe = False
        elif not prev_was_unsafe:
            out.append('_')
            prev_was_unsafe = True
    return ''.join(out)[:max_len].strip('._-')


_CONTENT_TYPES = {
    ".pdf": "application/pdf",
    ".doc": "application/msword",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".xls": "application/vnd.ms-excel",
    ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    ".ppt": "application/vnd.ms-powerpoint",
    ".pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    ".zip": "application/zip",
    ".txt": "text/plain; charset=utf-8",
    ".htm": "text/html; charset=utf-8",
    ".html": "text/html; charset=utf-8",
    ".csv": "text/csv; charset=utf-8",
    ".rtf": "application/rtf",
}


def build_document_key(prefix, title: str, ext: str = ".pdf") -> str:
    """
    Build the S3 key for a crawled document: "{prefix}/{ts_ms}_{safe_title}{ext}".

    `prefix` is typically "{country_folder}/{country_id}" so documents are
    grouped per country crawler in the bucket.
    """
    ext = "" if not ext else (ext if ext.startswith(".") else f".{ext}")
    title = (title or "document").strip()
    if ext and title.lower().endswith(ext.lower()):
        title = title[: -len(ext)]
    safe = _safe_title_slug(title) or "document"
    return f"{prefix}/{int(time.time() * 1000)}_{safe}{ext}"


def content_type_for_ext(ext: str) -> str:
    """Map a file extension (with or without leading '.') to a MIME type."""
    if ext and not ext.startswith('.'):
        ext = f".{ext}"
    return _CONTENT_TYPES.get((ext or "").lower(), "application/octet-stream")


def get_s3_client():
    return boto3.client(
        's3',
        endpoint_url=os.getenv('MINIO_ENDPOINT'),
        aws_access_key_id=os.getenv('MINIO_ACCESS_KEY'),
        aws_secret_access_key=os.getenv('MINIO_SECRET_KEY'),
        config=Config(signature_version='s3v4'),
        region_name='us-east-1',
    )


def create_bucket(bucket_name):
    """Create a new S3 bucket."""
    s3 = get_s3_client()
    try:
        s3.create_bucket(Bucket=bucket_name)
        logger.info(f"Bucket created: {bucket_name}")
    except ClientError as e:
        error_code = e.response.get('Error', {}).get('Code', '')
        if error_code in ('BucketAlreadyOwnedByYou', 'BucketAlreadyExists'):
            logger.info(f"Bucket already exists: {bucket_name}")
            return
        logger.error(f"Error creating bucket {bucket_name}: {e}")
        raise
    except Exception as e:
        logger.error(f"Error creating bucket {bucket_name}: {e}")
        raise


def ensure_bucket_exists(bucket_name):
    """Check if the bucket exists on the (shared) MinIO/S3 instance; create it if missing."""
    s3 = get_s3_client()
    try:
        s3.head_bucket(Bucket=bucket_name)
        logger.info(f"Bucket exists: {bucket_name}")
    except ClientError as e:
        error_code = e.response.get('Error', {}).get('Code', '')
        if error_code == '404':
            logger.error(f"Bucket {bucket_name} does not exist. Attempting to create it...")
            create_bucket(bucket_name)
            return
        elif error_code == '403':
            logger.warning(f"Cannot verify bucket {bucket_name} (access denied), assuming it exists")
            return
        else:
            logger.error(f"Error checking bucket {bucket_name}: {e}")
        raise
    except Exception as e:
        logger.warning(f"Could not verify bucket {bucket_name}: {e}. Proceeding anyway...")


def upload_file(file_content, object_name, content_type='application/pdf'):
    """
    Upload a file and return its bare object key (e.g. "united_kingdom/1/172_doc.pdf")
    — NOT a "s3://bucket/key" URI. The bucket name must never be embedded in a
    stored path: doing so ties every stored row to today's bucket name, and a
    later bucket rename/migration then forces a matching Postgres migration
    (this bit us before). Reconstruct the full location at read time from
    MINIO_BUCKET + this key instead.
    """
    s3 = get_s3_client()
    bucket_name = os.getenv('MINIO_BUCKET')
    try:
        ensure_bucket_exists(bucket_name)
        s3.put_object(
            Bucket=bucket_name,
            Key=object_name,
            Body=file_content,
            ContentType=content_type,
        )
        return object_name
    except Exception as e:
        logger.error(f"Failed to upload file to S3: {e}")
        raise
