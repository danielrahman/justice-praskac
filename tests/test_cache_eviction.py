import os
import time
from pathlib import Path

from justice.utils import evict_cache_dir


def test_evict_cache_dir_removes_oldest(tmp_path):
    """When total size exceeds max, oldest files are removed."""
    for i in range(5):
        f = tmp_path / f"file{i}.json"
        f.write_text("x" * 1000)
        os.utime(f, (time.time() - (5 - i) * 100, time.time() - (5 - i) * 100))
    # 5 files x 1000 bytes = 5000 bytes. Evict to max 3000.
    evict_cache_dir(tmp_path, max_bytes=3000)
    remaining = sorted(f.name for f in tmp_path.iterdir())
    assert "file0.json" not in remaining
    assert "file1.json" not in remaining
    assert len(remaining) == 3


def test_evict_cache_dir_noop_under_limit(tmp_path):
    """No files removed when under the limit."""
    f = tmp_path / "small.json"
    f.write_text("x" * 100)
    evict_cache_dir(tmp_path, max_bytes=10000)
    assert f.exists()


def test_evict_cache_dir_empty_dir(tmp_path):
    """Empty directory doesn't crash."""
    evict_cache_dir(tmp_path, max_bytes=1000)


def test_evict_cache_dir_exact_limit(tmp_path):
    """Files at exactly the limit are not evicted."""
    for i in range(3):
        f = tmp_path / f"file{i}.json"
        f.write_text("x" * 1000)
    evict_cache_dir(tmp_path, max_bytes=3000)
    assert len(list(tmp_path.iterdir())) == 3
