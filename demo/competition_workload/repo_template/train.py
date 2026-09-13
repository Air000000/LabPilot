from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import os
import random
from pathlib import Path

FEATURES = ("x1", "x2", "x3")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Train a tiny deterministic logistic-regression classifier."
    )
    parser.add_argument("--learning-rate", type=float, required=True)
    parser.add_argument("--epochs", type=int, required=True)
    parser.add_argument("--seed", type=int, required=True)
    parser.add_argument("--dataset", required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    if not math.isfinite(args.learning_rate) or not 0 < args.learning_rate <= 1:
        parser.error("learning-rate must be finite and in (0, 1]")
    if not 1 <= args.epochs <= 5000:
        parser.error("epochs must be in [1, 5000]")
    if not 0 <= args.seed <= 2**31 - 1:
        parser.error("seed must be a non-negative 32-bit integer")
    return args


def resolve_dataset(value: str) -> Path:
    prefix = "repo://"
    if value.startswith(prefix):
        relative = value[len(prefix) :]
        if not relative or relative.startswith("/") or ".." in Path(relative).parts:
            raise ValueError("invalid repo dataset URI")
        return (Path(__file__).resolve().parent / relative).resolve(strict=True)
    return Path(value).expanduser().resolve(strict=True)


def load_dataset(
    path: Path,
) -> tuple[
    list[tuple[tuple[float, ...], int]],
    list[tuple[tuple[float, ...], int]],
]:
    training: list[tuple[tuple[float, ...], int]] = []
    validation: list[tuple[tuple[float, ...], int]] = []
    with path.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        expected = ["split", *FEATURES, "label"]
        if reader.fieldnames != expected:
            raise ValueError(f"dataset columns must be {expected!r}")
        for row in reader:
            features = tuple(float(row[name]) for name in FEATURES)
            if not all(math.isfinite(value) for value in features):
                raise ValueError("dataset contains non-finite feature")
            label = int(row["label"])
            if label not in {0, 1}:
                raise ValueError("dataset labels must be 0 or 1")
            item = (features, label)
            if row["split"] == "train":
                training.append(item)
            elif row["split"] == "validation":
                validation.append(item)
            else:
                raise ValueError("dataset split must be train or validation")
    if not training or not validation:
        raise ValueError("dataset must contain train and validation rows")
    return training, validation


def standardize(
    training: list[tuple[tuple[float, ...], int]],
    validation: list[tuple[tuple[float, ...], int]],
) -> tuple[
    list[tuple[tuple[float, ...], int]],
    list[tuple[tuple[float, ...], int]],
    tuple[float, ...],
    tuple[float, ...],
]:
    count = len(training)
    means = tuple(
        sum(features[index] for features, _ in training) / count
        for index in range(len(FEATURES))
    )
    stddevs_list: list[float] = []
    for index, mean in enumerate(means):
        variance = (
            sum((features[index] - mean) ** 2 for features, _ in training) / count
        )
        stddev = math.sqrt(variance)
        if stddev == 0:
            raise ValueError("dataset contains a zero-variance feature")
        stddevs_list.append(stddev)
    stddevs = tuple(stddevs_list)

    def transform(
        rows: list[tuple[tuple[float, ...], int]],
    ) -> list[tuple[tuple[float, ...], int]]:
        return [
            (
                tuple(
                    (features[index] - means[index]) / stddevs[index]
                    for index in range(len(FEATURES))
                ),
                label,
            )
            for features, label in rows
        ]

    return transform(training), transform(validation), means, stddevs


def sigmoid(value: float) -> float:
    bounded = max(min(value, 35.0), -35.0)
    return 1.0 / (1.0 + math.exp(-bounded))


def train_model(
    training: list[tuple[tuple[float, ...], int]],
    *,
    learning_rate: float,
    epochs: int,
    seed: int,
) -> tuple[tuple[float, ...], float]:
    rng = random.Random(seed)
    weights = [rng.uniform(-0.05, 0.05) for _ in FEATURES]
    bias = rng.uniform(-0.05, 0.05)
    inverse_count = 1.0 / len(training)

    for _ in range(epochs):
        gradient = [0.0 for _ in FEATURES]
        bias_gradient = 0.0
        for features, label in training:
            score = (
                sum(
                    weight * feature
                    for weight, feature in zip(weights, features, strict=True)
                )
                + bias
            )
            residual = sigmoid(score) - label
            for index, feature in enumerate(features):
                gradient[index] += residual * feature
            bias_gradient += residual
        for index in range(len(weights)):
            weights[index] -= learning_rate * gradient[index] * inverse_count
        bias -= learning_rate * bias_gradient * inverse_count

    return tuple(weights), bias


def accuracy(
    rows: list[tuple[tuple[float, ...], int]],
    weights: tuple[float, ...],
    bias: float,
) -> float:
    correct = 0
    for features, label in rows:
        score = (
            sum(
                weight * feature
                for weight, feature in zip(weights, features, strict=True)
            )
            + bias
        )
        prediction = 1 if score >= 0.0 else 0
        correct += int(prediction == label)
    return correct / len(rows)


def canonical_json_bytes(value: object) -> bytes:
    return (
        json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
        + "\n"
    ).encode("utf-8")


def atomic_write(path: Path, content: bytes) -> None:
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    try:
        with temporary.open("wb") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def main() -> int:
    args = parse_args()
    dataset_path = resolve_dataset(args.dataset)
    training, validation = load_dataset(dataset_path)
    training, validation, means, stddevs = standardize(training, validation)
    weights, bias = train_model(
        training,
        learning_rate=args.learning_rate,
        epochs=args.epochs,
        seed=args.seed,
    )
    validation_accuracy = accuracy(validation, weights, bias)

    output_dir = args.output_dir.resolve(strict=True)
    if not output_dir.is_dir() or output_dir.is_symlink():
        raise ValueError("output-dir must be an existing directory")

    result = {
        "schema_version": "v1",
        "metrics": {"accuracy": validation_accuracy},
    }
    model = {
        "schema_version": "v1",
        "algorithm": "logistic_regression_batch_gradient_descent",
        "features": list(FEATURES),
        "weights": list(weights),
        "bias": bias,
        "feature_means": list(means),
        "feature_stddevs": list(stddevs),
        "learning_rate": args.learning_rate,
        "epochs": args.epochs,
        "seed": args.seed,
        "dataset_sha256": hashlib.sha256(dataset_path.read_bytes()).hexdigest(),
        "validation_rows": len(validation),
    }
    atomic_write(output_dir / "result.json", canonical_json_bytes(result))
    atomic_write(output_dir / "model.json", canonical_json_bytes(model))
    print(f"validation_accuracy={validation_accuracy:.6f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
