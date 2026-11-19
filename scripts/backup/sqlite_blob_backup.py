"""Back up a live SQLite database to a private Azure Blob using VM identity."""

import datetime
import email.utils
import hashlib
import http.client
import json
import sqlite3
import tempfile
import urllib.parse
import urllib.request
import uuid
from pathlib import Path

ACCOUNT = "stsmartbudgetbkc43d1885"
CONTAINER = "sqlite-backups"
DATABASE = Path("/home/azureuser/smartbudget-server/data/smartbudget.sqlite3")
CHUNK_SIZE = 1024 * 1024


def digest_file(path: Path) -> str:
    """Hash a file incrementally so backup size does not determine peak memory."""
    result = hashlib.sha256()
    with path.open("rb") as data:
        while chunk := data.read(CHUNK_SIZE):
            result.update(chunk)
    return result.hexdigest()


def make_backup(source: Path, destination: Path) -> None:
    """Take a consistent SQLite online backup and check its integrity."""
    if not source.is_file():
        raise FileNotFoundError("Source database does not exist")
    with sqlite3.connect(source.as_uri() + "?mode=ro", uri=True, timeout=30) as src:
        with sqlite3.connect(destination) as dst:
            src.backup(dst)
            if dst.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                raise RuntimeError("Backup integrity check failed")


def managed_identity_token() -> str:
    """Obtain a short-lived storage token from the VM metadata endpoint."""
    query = urllib.parse.urlencode(
        {
            "api-version": "2018-02-01",
            "resource": "https://storage.azure.com/",
        }
    )
    request = urllib.request.Request(
        "http://169.254.169.254/metadata/identity/oauth2/token?" + query,
        headers={"Metadata": "true"},
    )
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with opener.open(request, timeout=20) as response:
        token = json.load(response).get("access_token")
    if not isinstance(token, str) or not token:
        raise RuntimeError("VM identity did not return an access token")
    return token


def transfer_backup(path: Path, blob_name: str, token: str) -> None:
    """Upload a block blob and verify its bytes by reading it back."""
    digest = digest_file(path)
    host = f"{ACCOUNT}.blob.core.windows.net"
    endpoint = f"/{CONTAINER}/{urllib.parse.quote(blob_name, safe='/')}"
    headers = {
        "Authorization": "Bearer " + token,
        "x-ms-version": "2023-11-03",
        "x-ms-date": email.utils.format_datetime(
            datetime.datetime.now(datetime.timezone.utc), usegmt=True
        ),
    }
    connection = http.client.HTTPSConnection(host, timeout=120)
    try:
        connection.putrequest("PUT", endpoint)
        for key, value in {
            **headers,
            "Content-Length": str(path.stat().st_size),
            "Content-Type": "application/octet-stream",
            "x-ms-blob-type": "BlockBlob",
            "x-ms-meta-sha256": digest,
            "If-None-Match": "*",
        }.items():
            connection.putheader(key, value)
        connection.endheaders()
        with path.open("rb") as file:
            while chunk := file.read(CHUNK_SIZE):
                connection.send(chunk)
        response = connection.getresponse()
        response.read()
        if response.status != 201:
            raise RuntimeError(f"Blob upload failed with HTTP {response.status}")
        connection.request("GET", endpoint, headers=headers)
        response = connection.getresponse()
        if response.status != 200:
            response.read()
            raise RuntimeError(
                f"Backup verification failed with HTTP {response.status}"
            )
        received = hashlib.sha256()
        while chunk := response.read(CHUNK_SIZE):
            received.update(chunk)
        if received.hexdigest() != digest:
            raise RuntimeError("Stored backup differs from the local backup")
    finally:
        connection.close()


def run_backup(source: Path = DATABASE) -> str:
    """Back up one database into the separate daily retention prefix."""
    with tempfile.TemporaryDirectory(prefix="smartbudget-db-backup-") as directory:
        backup = Path(directory) / "backup.sqlite3"
        make_backup(source, backup)
        timestamp = datetime.datetime.now(datetime.timezone.utc).strftime(
            "%Y%m%dT%H%M%SZ"
        )
        blob_name = f"daily/smartbudget-{timestamp}-{uuid.uuid4().hex[:12]}.sqlite3"
        transfer_backup(backup, blob_name, managed_identity_token())
        return blob_name


if __name__ == "__main__":
    print("Verified backup: " + run_backup())
