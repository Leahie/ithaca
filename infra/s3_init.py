"""Create the object-store bucket if it is missing. Safe to rerun.

    python infra/s3_init.py
"""

from __future__ import annotations

import os

import boto3
from botocore.client import Config
from botocore.exceptions import ClientError


def main() -> None:
    bucket = os.environ.get("S3_BUCKET", "ithaca")
    s3 = boto3.client(
        "s3",
        endpoint_url=os.environ["S3_ENDPOINT_URL"],
        aws_access_key_id=os.environ["S3_ACCESS_KEY"],
        aws_secret_access_key=os.environ["S3_SECRET_KEY"],
        region_name="us-east-1",
        config=Config(s3={"addressing_style": "path"}),
    )
    try:
        s3.head_bucket(Bucket=bucket)
        print(f"bucket {bucket}: exists")
    except ClientError:
        s3.create_bucket(Bucket=bucket)
        print(f"bucket {bucket}: created")


if __name__ == "__main__":
    main()
