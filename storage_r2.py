"""
Cloudflare R2 storage — S3-compatible, so we use boto3.

We use ONE bucket for two things:
  - garment photos, stored under photos/<uuid>.<ext>
  - the closet's metadata, stored as a single JSON object at closet.json

Storing the metadata as a JSON blob (instead of a real database) is a
deliberate simplification for a single-user hobby app — it means one
storage provider, one set of credentials, and no database migrations.
If this ever needs concurrent multi-user writes, swap this module for a
real database; nothing else in the app needs to change since
closet_store.py exposes the same load_closet/save_item/update_item/
delete_item functions either way.
"""

import os
import json
import threading

import boto3
from botocore.client import Config

CLOSET_KEY = "closet.json"
PHOTO_PREFIX = "photos/"

_lock = threading.Lock()
_client = None


def _get_client():
    global _client
    if _client is not None:
        return _client

    account_id = os.environ.get("R2_ACCOUNT_ID")
    access_key = os.environ.get("R2_ACCESS_KEY_ID")
    secret_key = os.environ.get("R2_SECRET_ACCESS_KEY")

    if not all([account_id, access_key, secret_key]):
        raise RuntimeError(
            "R2 credentials missing. Set R2_ACCOUNT_ID, R2_ACCESS_KEY_ID, "
            "and R2_SECRET_ACCESS_KEY in your .env file."
        )

    _client = boto3.client(
        "s3",
        endpoint_url=f"https://{account_id}.r2.cloudflarestorage.com",
        aws_access_key_id=access_key,
        aws_secret_access_key=secret_key,
        config=Config(signature_version="s3v4"),
        region_name="auto",
    )
    return _client


def _bucket_name() -> str:
    bucket = os.environ.get("R2_BUCKET_NAME")
    if not bucket:
        raise RuntimeError("R2_BUCKET_NAME is not set in your .env file.")
    return bucket


def public_url_for(object_key: str) -> str:
    """
    Builds the public URL for an object. Requires either:
    - R2_PUBLIC_URL set to your bucket's public r2.dev URL or custom domain
      (e.g. https://pub-xxxxx.r2.dev or https://cdn.yourdomain.com)
    """
    base = os.environ.get("R2_PUBLIC_URL")
    if not base:
        raise RuntimeError(
            "R2_PUBLIC_URL is not set. Enable public access on your R2 "
            "bucket and put its URL in .env."
        )
    return f"{base.rstrip('/')}/{object_key}"


# ---- Photos ----

def upload_photo(local_path: str, object_key: str, content_type: str) -> None:
    client = _get_client()
    client.upload_file(
        local_path,
        _bucket_name(),
        object_key,
        ExtraArgs={"ContentType": content_type},
    )


def delete_photo(object_key: str) -> None:
    client = _get_client()
    try:
        client.delete_object(Bucket=_bucket_name(), Key=object_key)
    except Exception:
        pass  # best-effort; don't block item deletion on a storage hiccup


# ---- Closet metadata (JSON blob) ----

def load_closet_json() -> list:
    client = _get_client()
    try:
        obj = client.get_object(Bucket=_bucket_name(), Key=CLOSET_KEY)
        return json.loads(obj["Body"].read())
    except client.exceptions.NoSuchKey:
        return []
    except Exception as e:
        # Botocore raises a generic ClientError for a missing key on some
        # setups instead of NoSuchKey specifically — treat "not found" as
        # an empty closet rather than a hard failure.
        if "NoSuchKey" in str(e) or "404" in str(e):
            return []
        raise


def save_closet_json(closet: list) -> None:
    client = _get_client()
    with _lock:
        client.put_object(
            Bucket=_bucket_name(),
            Key=CLOSET_KEY,
            Body=json.dumps(closet, indent=2).encode("utf-8"),
            ContentType="application/json",
        )
