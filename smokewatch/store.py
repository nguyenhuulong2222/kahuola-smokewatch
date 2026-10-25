"""Storage: S3 in AWS, a local folder for dev/eval. Same interface."""
from __future__ import annotations

import json
import os


class LocalStore:
    def __init__(self, root: str):
        self.root = root

    def _p(self, key):
        p = os.path.join(self.root, key)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        return p

    def put_bytes(self, key, data: bytes, content_type="application/octet-stream"):
        with open(self._p(key), "wb") as f:
            f.write(data)

    def get_bytes(self, key):
        try:
            with open(os.path.join(self.root, key), "rb") as f:
                return f.read()
        except FileNotFoundError:
            return None

    def list(self, prefix):
        base = os.path.join(self.root, prefix)
        if not os.path.isdir(base):
            return []
        return sorted(os.path.join(prefix, f) for f in os.listdir(base))

    def delete(self, key):
        try:
            os.remove(os.path.join(self.root, key))
        except FileNotFoundError:
            pass

    def url(self, key):
        return "file://" + os.path.abspath(os.path.join(self.root, key))

    def put_json(self, key, obj):
        self.put_bytes(key, json.dumps(obj, indent=2, default=str).encode(), "application/json")

    def get_json(self, key, default=None):
        b = self.get_bytes(key)
        return json.loads(b) if b else default


class S3Store(LocalStore):
    def __init__(self, bucket: str):
        import boto3
        self.bucket = bucket
        self.s3 = boto3.client("s3")

    def put_bytes(self, key, data: bytes, content_type="application/octet-stream"):
        self.s3.put_object(Bucket=self.bucket, Key=key, Body=data, ContentType=content_type)

    def get_bytes(self, key):
        try:
            return self.s3.get_object(Bucket=self.bucket, Key=key)["Body"].read()
        except self.s3.exceptions.NoSuchKey:
            return None

    def list(self, prefix):
        out = []
        for page in self.s3.get_paginator("list_objects_v2").paginate(Bucket=self.bucket, Prefix=prefix):
            out += [o["Key"] for o in page.get("Contents", [])]
        return sorted(out)

    def delete(self, key):
        self.s3.delete_object(Bucket=self.bucket, Key=key)

    def url(self, key, expires=3600):
        return self.s3.generate_presigned_url("get_object", Params={"Bucket": self.bucket, "Key": key},
                                              ExpiresIn=expires)


def default_store():
    b = os.environ.get("BUCKET")
    return S3Store(b) if b else LocalStore(os.environ.get("LOCAL_STORE", "./out"))
