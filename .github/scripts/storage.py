#!/usr/bin/env python3
"""Storage backend abstraction for uploading build artifacts.

Provides a factory function to get the appropriate storage backend
based on provider name (s3, r2).
"""

import os
import subprocess
import sys
from abc import ABC, abstractmethod


class StorageBackend(ABC):
    """Base class for storage backends."""

    @abstractmethod
    def upload_directory(self, local_dir, remote_path):
        """Upload a local directory to remote storage.

        Args:
            local_dir: Path to local directory to upload.
            remote_path: Remote path (e.g., "genome/recipe/").

        Returns:
            True if upload succeeded, False otherwise.
        """
        pass

    @abstractmethod
    def get_public_url(self, remote_path):
        """Get the public URL for a remote path.

        Args:
            remote_path: Remote path (e.g., "genome/recipe/file.txt").

        Returns:
            Public URL string.
        """
        pass


class S3Backend(StorageBackend):
    """AWS S3 storage backend."""

    def __init__(self, bucket, endpoint=None, region=None):
        self.bucket = bucket
        self.endpoint = endpoint
        self.region = region or os.environ.get("AWS_DEFAULT_REGION", "us-east-1")

    def _aws_cmd(self, *args):
        """Build an aws CLI command with optional endpoint."""
        cmd = ["aws"]
        if self.endpoint:
            cmd.extend(["--endpoint-url", self.endpoint])
        cmd.extend(args)
        return cmd

    def upload_directory(self, local_dir, remote_path):
        """Upload directory to S3."""
        s3_uri = f"s3://{self.bucket}/{remote_path.lstrip('/')}"
        cmd = self._aws_cmd("s3", "cp", "--recursive", local_dir, s3_uri)
        print(f"Uploading {local_dir} -> {s3_uri}")
        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode != 0:
            print(f"Upload failed: {result.stderr}", file=sys.stderr)
            return False
        print(result.stdout)
        return True

    def get_public_url(self, remote_path):
        """Get S3 public URL."""
        return f"https://{self.bucket}.s3.amazonaws.com/{remote_path.lstrip('/')}"


class R2Backend(StorageBackend):
    """Cloudflare R2 storage backend (S3-compatible)."""

    def __init__(self, bucket, endpoint, public_url=None):
        self.bucket = bucket
        self.endpoint = endpoint
        self.public_url = public_url

    def _aws_cmd(self, *args):
        """Build an aws CLI command with R2 endpoint."""
        cmd = ["aws", "--endpoint-url", self.endpoint]
        cmd.extend(args)
        return cmd

    def upload_directory(self, local_dir, remote_path):
        """Upload directory to R2."""
        s3_uri = f"s3://{self.bucket}/{remote_path.lstrip('/')}"
        cmd = self._aws_cmd("s3", "cp", "--recursive", local_dir, s3_uri)
        print(f"Uploading {local_dir} -> {s3_uri}")
        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode != 0:
            print(f"Upload failed: {result.stderr}", file=sys.stderr)
            return False
        print(result.stdout)
        return True

    def get_public_url(self, remote_path):
        """Get R2 public URL."""
        if self.public_url:
            return f"{self.public_url.rstrip('/')}/{remote_path.lstrip('/')}"
        return f"{self.endpoint.rstrip('/')}/{self.bucket}/{remote_path.lstrip('/')}"


def get_backend(provider, bucket, endpoint=None, **kwargs):
    """Factory function to get a storage backend.

    Args:
        provider: Storage provider name ("s3" or "r2").
        bucket: Bucket name.
        endpoint: Optional endpoint URL (required for R2).
        **kwargs: Additional provider-specific arguments.

    Returns:
        StorageBackend instance.

    Raises:
        ValueError: If provider is unknown.
    """
    provider = provider.lower()
    if provider == "s3":
        return S3Backend(
            bucket=bucket,
            endpoint=endpoint,
            region=kwargs.get("region"),
        )
    elif provider == "r2":
        if not endpoint:
            raise ValueError("R2 backend requires an endpoint URL")
        return R2Backend(
            bucket=bucket,
            endpoint=endpoint,
            public_url=kwargs.get("public_url"),
        )
    else:
        raise ValueError(f"Unknown storage provider: {provider}")


if __name__ == "__main__":
    # Simple test / usage example
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("--provider", required=True)
    parser.add_argument("--bucket", required=True)
    parser.add_argument("--endpoint", default="")
    parser.add_argument("--local-dir", required=True)
    parser.add_argument("--remote-path", required=True)
    args = parser.parse_args()

    backend = get_backend(args.provider, args.bucket, args.endpoint or None)
    success = backend.upload_directory(args.local_dir, args.remote_path)
    if success:
        url = backend.get_public_url(args.remote_path)
        print(f"Public URL: {url}")
    else:
        sys.exit(1)
