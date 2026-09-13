from __future__ import annotations

import csv
import hashlib
import random
from pathlib import Path

TRAIN_ROWS = 4000
VALIDATION_ROWS = 1000
GENERATOR_SEED = 20260913
NOISE_STDDEV = 0.35
EXPECTED_SHA256 = "1a5ea8912a402d121f54210dabb020adef9bb573471918b8aba2aca444b56e31"


def main() -> int:
    target = Path(__file__).resolve().parent / "data" / "classification.csv"
    target.parent.mkdir(parents=True, exist_ok=True)
    rng = random.Random(GENERATOR_SEED)
    with target.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle, lineterminator="\n")
        writer.writerow(["split", "x1", "x2", "x3", "label"])
        for index in range(TRAIN_ROWS + VALIDATION_ROWS):
            x1 = rng.gauss(0.0, 1.0)
            x2 = rng.gauss(0.0, 1.0)
            x3 = rng.gauss(0.0, 1.0)
            noise = rng.gauss(0.0, NOISE_STDDEV)
            score = 1.4 * x1 - 1.1 * x2 + 0.8 * x3 + noise - 0.05
            label = 1 if score > 0.0 else 0
            writer.writerow(
                [
                    "train" if index < TRAIN_ROWS else "validation",
                    f"{x1:.8f}",
                    f"{x2:.8f}",
                    f"{x3:.8f}",
                    label,
                ]
            )
    actual = hashlib.sha256(target.read_bytes()).hexdigest()
    if actual != EXPECTED_SHA256:
        raise RuntimeError(f"dataset hash mismatch: {actual}")
    print(f"generated={target}")
    print(f"sha256={actual}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
