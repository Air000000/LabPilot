import difflib
import os
import re
from pathlib import Path

import pytest

from labpilot.context.source_candidates import CandidateError
from labpilot.context.source_patch_normalization import normalize_patch

BASE = (
    b'raise RuntimeError("must never execute candidate")\n\nimport math\n\n'
    b"def train_model(training):\n    gradient = sum(training)\n"
    + b"".join(f"    value_{i} = {i}\n".encode() for i in range(20))
    + b"    return gradient\n\ndef accuracy():\n    return 0.92\n"
)
TARGET = BASE.replace(b"gradient = sum(training)", b"gradient = math.fsum(training)")


def patch(after, before=BASE):
    return (
        b"diff --git a/train.py b/train.py\n"
        + "".join(
            difflib.unified_diff(
                before.decode().splitlines(keepends=True),
                after.decode().splitlines(keepends=True),
                fromfile="a/train.py",
                tofile="b/train.py",
            )
        ).encode()
    )


def counts(value, delta):
    return re.sub(
        rb"@@ -(\d+),(\d+) \+(\d+),(\d+) @@",
        lambda m: b"@@ -"
        + m[1]
        + b","
        + str(max(1, int(m[2]) + delta)).encode()
        + b" +"
        + m[3]
        + b","
        + str(max(1, int(m[4]) + delta)).encode()
        + b" @@",
        value,
    )


@pytest.mark.parametrize("delta", [0, 1, 7, -1])
def test_counts_are_derived_and_receipt_is_reproducible(delta):
    raw = counts(patch(TARGET), delta)
    result, receipt = normalize_patch(BASE, raw)
    assert result == patch(TARGET)
    assert normalize_patch(BASE, raw) == (result, receipt)
    from hashlib import sha256

    assert receipt["raw_patch_sha256"] == sha256(raw).hexdigest()
    assert receipt["canonical_patch_sha256"] == sha256(result).hexdigest()
    assert receipt["base_sha256"] == sha256(BASE).hexdigest()
    assert receipt["profile"] == "git-recount-unified-diff-v1"


@pytest.mark.parametrize(
    "after",
    [
        BASE.replace(b"    return gradient", b"    gradient += 1\n    return gradient"),
        BASE.replace(b"    value_10 = 10\n", b""),
        TARGET.replace(b"return gradient", b"return gradient * 2"),
    ],
)
def test_insert_delete_and_multiple_hunks(after):
    canonical, _ = normalize_patch(BASE, counts(patch(after), 3))
    assert canonical == patch(after)


@pytest.mark.parametrize(
    "raw",
    [
        patch(TARGET).replace(b"train.py", b"other.py"),
        patch(TARGET).replace(b"a/train.py", b"a/../train.py"),
        patch(TARGET).replace(b"a/train.py", b"/train.py"),
        patch(TARGET).replace(
            b"--- a/train.py", b"old mode 100644\nnew mode 100755\n--- a/train.py"
        ),
        patch(TARGET).replace(b"--- a/train.py", b"GIT binary patch\n--- a/train.py"),
        patch(TARGET) + b"diff --git a/other.py b/other.py\n",
        patch(TARGET).replace(b"-    gradient = sum(training)", b"-    gradient = sum(missing)"),
        patch(BASE.replace(b"return 0.92", b"return 1.0")),
        patch(BASE.replace(b"sum(training)", b"(")),
        patch(TARGET).replace(
            b"+    gradient = math.fsum(training)", b"+    gradient = sum(training)"
        ),
        patch(TARGET).replace(b"@@", b"@x@"),
        b"\0",
        b"\xff",
        b"",
        b"x" * 32769,
    ],
)
def test_invalid_patch_never_produces_canonical_output(raw):
    with pytest.raises(CandidateError):
        normalize_patch(BASE, raw)


@pytest.mark.parametrize(
    "base", [BASE.rstrip(b"\n"), BASE + b"#" * 32769, BASE + b"\n" * 501, b"\xff\n"]
)
def test_unsupported_baseline_is_rejected(base):
    with pytest.raises(CandidateError):
        normalize_patch(base, patch(TARGET))


def test_fixed_tools_private_temporary_directory_and_cleanup(monkeypatch):
    from labpilot.context import source_candidates

    real = source_candidates._run
    directories = []
    arguments = []

    def spy(argv, *, cwd, data=b""):
        assert cwd.stat().st_uid == os.geteuid()
        assert cwd.stat().st_mode & 0o777 == 0o700
        directories.append(cwd)
        arguments.append(argv)
        return real(argv, cwd=cwd, data=data)

    monkeypatch.setattr(source_candidates, "_run", spy)
    normalize_patch(BASE, counts(patch(TARGET), 1))
    assert all(not d.exists() for d in directories)
    assert ["/usr/bin/git", "apply", "--check", "--recount", "-"] in arguments
    assert ["/usr/bin/git", "apply", "--check", "-"] in arguments
    assert all(
        a[0] == "/usr/bin/git" or a[1:] == ["-I", str(Path(source_candidates._CHECKER))]
        for a in arguments
    )


def test_tool_failure_is_fixed_and_temporary_files_are_cleaned(monkeypatch):
    from labpilot.context import source_candidates

    directories = []

    def fail(argv, *, cwd, data=b""):
        directories.append(cwd)
        raise OSError("secret input must not be exposed")

    monkeypatch.setattr(source_candidates, "_run", fail)
    with pytest.raises(CandidateError, match="^patch_normalization_failed$"):
        normalize_patch(BASE, patch(TARGET))
    assert directories and all(not d.exists() for d in directories)
