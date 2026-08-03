"""Regression tests for prediction patch sanitization."""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for rel in ("sandboxdev",):
    path = str(ROOT / rel)
    if path not in sys.path:
        sys.path.insert(0, path)

from swesandbox.utils import remove_binary_diffs


def test_remove_binary_diffs_keeps_text_hunks():
    patch = "\n".join(
        [
            "diff --git a/src.py b/src.py",
            "index 1111111..2222222 100644",
            "--- a/src.py",
            "+++ b/src.py",
            "@@ -1 +1 @@",
            "-old",
            "+new",
            "diff --git a/cache.pyc b/cache.pyc",
            "new file mode 100644",
            "index 0000000..3333333",
            "Binary files /dev/null and b/cache.pyc differ",
            "diff --git a/another.py b/another.py",
            "index 4444444..5555555 100644",
            "--- a/another.py",
            "+++ b/another.py",
            "@@ -2 +2 @@",
            "-left",
            "+right",
            "",
        ]
    )

    cleaned = remove_binary_diffs(patch)

    assert "src.py" in cleaned
    assert "another.py" in cleaned
    assert "cache.pyc" not in cleaned
    assert "Binary files" not in cleaned


if __name__ == "__main__":
    test_remove_binary_diffs_keeps_text_hunks()
    print("ok")
