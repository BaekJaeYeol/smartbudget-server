"""Verify that live database backups are isolated and uploaded intact."""

import sqlite3
from pathlib import Path

import pytest

from scripts.backup import sqlite_blob_backup as backup


def test_online_backup_preserves_source_and_includes_wal(tmp_path):
    """Copy committed WAL data without modifying or replacing the live database."""
    source = tmp_path / "live.sqlite3"
    target = tmp_path / "backup.sqlite3"
    with sqlite3.connect(source) as connection:
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("CREATE TABLE item (value TEXT)")
        connection.execute("INSERT INTO item VALUES ('original')")
        connection.commit()
        backup.make_backup(source, target)
        with sqlite3.connect(target) as restored:
            assert restored.execute("SELECT value FROM item").fetchall() == [
                ("original",)
            ]
            restored.execute("INSERT INTO item VALUES ('restore-only')")
        assert connection.execute("SELECT value FROM item").fetchall() == [
            ("original",)
        ]


def test_uploaded_backup_is_read_back_and_verified(tmp_path, monkeypatch):
    """Reject an altered remote blob instead of reporting a successful backup."""
    sample = tmp_path / "data.sqlite3"
    sample.write_bytes(b"database contents")

    class Response:
        """Represent a minimal Azure HTTP response."""

        def __init__(self, status, body=b""):
            """Initialize the status and stream contents."""
            self.status = status
            self.body = body

        def read(self, size=-1):
            """Read and consume bytes from the simulated response."""
            if size < 0:
                size = len(self.body)
            value, self.body = self.body[:size], self.body[size:]
            return value

    remote_data = [sample.read_bytes()]

    class Connection:
        """Record an upload and return its simulated downloaded content."""

        def __init__(self, host, timeout):
            """Initialize per-request state."""
            self.upload = bytearray()
            self.method = ""

        def putrequest(self, method, endpoint):
            """Start the upload."""
            self.method = method

        def putheader(self, key, value):
            """Accept request headers."""

        def endheaders(self):
            """Finish request headers."""

        def send(self, chunk):
            """Collect uploaded bytes."""
            self.upload.extend(chunk)

        def request(self, method, endpoint, headers):
            """Start the read-back request."""
            self.method = method

        def getresponse(self):
            """Return an intact or deliberately corrupted response."""
            return (
                Response(201) if self.method == "PUT" else Response(200, remote_data[0])
            )

        def close(self):
            """Close the simulated connection."""

    monkeypatch.setattr(backup.http.client, "HTTPSConnection", Connection)
    backup.transfer_backup(sample, "daily/test.sqlite3", "test-token")
    remote_data[0] = b"corrupted"
    with pytest.raises(RuntimeError, match="differs"):
        backup.transfer_backup(sample, "daily/test.sqlite3", "test-token")


def test_missing_source_is_not_created(tmp_path: Path):
    """A missing live database must fail without silently creating a new one."""
    source = tmp_path / "missing.sqlite3"
    with pytest.raises(FileNotFoundError):
        backup.make_backup(source, tmp_path / "out.sqlite3")
    assert not source.exists()
