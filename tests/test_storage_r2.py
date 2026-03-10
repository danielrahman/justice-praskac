from __future__ import annotations

import justice.storage_r2 as storage


class FakeClientError(Exception):
    pass


class FakeR2Client:
    def __init__(self):
        self.objects: dict[str, bytes] = {}
        self.put_calls = 0

    def head_object(self, Bucket: str, Key: str):
        if Key not in self.objects:
            raise FakeClientError(Key)
        return {"Bucket": Bucket, "Key": Key}

    def put_object(self, Bucket: str, Key: str, Body: bytes, ContentType: str):
        self.put_calls += 1
        self.objects[Key] = Body
        return {"Bucket": Bucket, "Key": Key, "ContentType": ContentType}

    def get_object(self, Bucket: str, Key: str):
        return {"Body": storage.bytes_to_fileobj(self.objects[Key])}


def test_upload_bytes_if_missing_dedupes(monkeypatch):
    client = FakeR2Client()
    monkeypatch.setattr(storage, "OBJECT_STORAGE_BACKEND", "r2")
    monkeypatch.setattr(storage, "S3_BUCKET", "test-bucket")
    monkeypatch.setattr(storage, "ClientError", FakeClientError)
    monkeypatch.setattr(storage, "_r2_client", lambda: client)

    key = storage.upload_bytes_if_missing("companies/1/documents/abc.pdf", b"pdf-bytes", "application/pdf")
    key2 = storage.upload_bytes_if_missing("companies/1/documents/abc.pdf", b"pdf-bytes", "application/pdf")

    assert key == key2
    assert client.put_calls == 1
    assert storage.download_bytes(key) == b"pdf-bytes"
