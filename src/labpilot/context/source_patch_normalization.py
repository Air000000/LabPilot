"""Explicit static derivation of a canonical patch; no candidate publication."""

import difflib
import json
import platform
import sys
import tempfile
from hashlib import sha256
from pathlib import Path

from labpilot.context import source_candidate_check, source_candidates
from labpilot.context.source_candidates import CandidateError


def normalize_patch(base: bytes, patch: bytes) -> tuple[bytes, dict[str, str]]:
    try:
        if len(base) > 32 * 1024 or len(base.splitlines()) > 500:
            raise CandidateError("base_profile_limit")
        if len(patch) > 32 * 1024:
            raise CandidateError("patch_limit_or_empty")
        source_candidates._patch_scope(patch)
        base.decode("utf-8", "strict")
        patch.decode("utf-8", "strict")
        if not base.endswith(b"\n"):
            raise CandidateError("normalization_text_invalid")
        with tempfile.TemporaryDirectory(prefix="labpilot-normalize-") as directory:
            root = Path(directory)
            file = root / "train.py"
            file.write_bytes(base)
            source_candidates._run(
                ["/usr/bin/git", "apply", "--check", "--recount", "-"], cwd=root, data=patch
            )
            source_candidates._run(
                ["/usr/bin/git", "apply", "--recount", "-"], cwd=root, data=patch
            )
            after = file.read_bytes()
            if len(after) > 128 * 1024:
                raise CandidateError("source_limit")
            after.decode("utf-8", "strict")
            if not after.endswith(b"\n"):
                raise CandidateError("normalization_text_invalid")
            if after == base:
                raise CandidateError("no_op_patch")
            # Reuse the bounded trusted worker; it compiles but never executes candidate code.
            source_candidates._run(
                [sys.executable, "-I", str(Path(source_candidate_check.__file__))],
                cwd=root,
                data=json.dumps({"before": base.hex(), "after": after.hex()}).encode(),
            )
            canonical = b"diff --git a/train.py b/train.py\n" + "".join(
                difflib.unified_diff(
                    base.decode().splitlines(keepends=True),
                    after.decode().splitlines(keepends=True),
                    fromfile="a/train.py",
                    tofile="b/train.py",
                )
            ).encode("utf-8")
            if len(canonical) > 64 * 1024:
                raise CandidateError("diff_limit")
            file.write_bytes(base)
            source_candidates._run(
                ["/usr/bin/git", "apply", "--check", "-"], cwd=root, data=canonical
            )
            source_candidates._run(
                ["/usr/bin/git", "apply", "-"], cwd=root, data=canonical
            )
            if file.read_bytes() != after:
                raise CandidateError("patch_normalization_failed")
            sources = (
                Path(__file__),
                Path(source_candidates.__file__),
                Path(source_candidate_check.__file__),
            )
            receipt = {
                "profile": "git-recount-unified-diff-v1",
                "normalizer_sha256": sha256(
                    b"".join(sha256(p.read_bytes()).digest() for p in sources)
                ).hexdigest(),
                "base_sha256": sha256(base).hexdigest(),
                "raw_patch_sha256": sha256(patch).hexdigest(),
                "canonical_patch_sha256": sha256(canonical).hexdigest(),
                "git_version": source_candidates._run(
                    ["/usr/bin/git", "--version"], cwd=root
                ).decode().strip(),
                "python_version": platform.python_version(),
            }
            return canonical, receipt
    except CandidateError:
        raise
    except Exception:
        raise CandidateError("patch_normalization_failed") from None
