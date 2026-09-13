# LabPilot Competition Demo Workload

This repository is a deliberately small, real CPU machine-learning workload for LabPilot's competition Golden Path. It trains a binary logistic-regression classifier with batch gradient descent using only the Python 3.12 standard library.

## Experiment objective

Find a bounded hyperparameter configuration whose validation accuracy is at least **0.92** while running no more than four Trials.

Use a `parameter_sweep` experiment with the exact candidate matrix below:

```text
learning_rate: [0.0005, 0.005]
epochs: [100, 1000]
seeds: [7]
```

The Cartesian product produces exactly four Trials.

## Training entrypoint

- `train_entrypoint`: `train.py`
- `eval_entrypoint`: `null`
- `workdir`: `.`
- Python profile: `python-3.12`
- framework requirements: none
- device requirement: none / CPU
- CUDA requirement: none
- recommended timeout: 30 seconds
- recommended max retries: 1

The training CLI is:

```text
python train.py \
  --learning-rate <learning_rate> \
  --epochs <epochs> \
  --seed <seed> \
  --dataset <dataset> \
  --output-dir <output_dir>
```

For LabPilot, use this exact `train_argv_template`:

```json
[
  "--learning-rate", "{parameter:learning_rate}",
  "--epochs", "{parameter:epochs}",
  "--seed", "{seed}",
  "--dataset", "{dataset}",
  "--output-dir", "{output_dir}"
]
```

## Dataset contract

The dataset is committed with this repository at `data/classification.csv`.

Use the following DatasetReference:

```text
reference_type: uri
path_or_uri: repo://data/classification.csv
stable_identity: sha256:1a5ea8912a402d121f54210dabb020adef9bb573471918b8aba2aca444b56e31
reproducibility_level: strict
```

`repo://` is intentionally resolved by `train.py` relative to the materialized repository root. This keeps the demo dataset bound to the approved source snapshot without requiring a separate host dataset mount.

The CSV has a fixed `train` / `validation` split and columns:

```text
split,x1,x2,x3,label
```

## Result Plane contract

The training process writes two files under the supplied output directory.

### `result.json`

Declare this artifact as:

```text
name: result
relative_path: result.json
required: true
```

It uses LabPilot's existing `result-json-v1` contract:

```json
{"schema_version":"v1","metrics":{"accuracy":0.941}}
```

Declare the metric as:

```text
name: accuracy
source_artifact: result
split: validation
```

### `model.json`

Declare this artifact as:

```text
name: model
relative_path: model.json
required: true
```

It records the fitted weights, preprocessing statistics, hyperparameters, seed, and dataset SHA-256 for presentation/provenance.

## Deterministic validation

Use these validation rules:

```text
artifact_exists(result)
artifact_exists(model)
metric_finite(accuracy)
metric_gte(accuracy, 0.92)
```

The measured reference outcomes on the development machine are:

| learning_rate | epochs | seed | validation accuracy | Expected verdict |
|---:|---:|---:|---:|---|
| 0.0005 | 100 | 7 | 0.650 | INVALID |
| 0.0005 | 1000 | 7 | 0.895 | INVALID |
| 0.005 | 100 | 7 | 0.895 | INVALID |
| 0.005 | 1000 | 7 | 0.941 | VALID |

These measurements are demo-workload characterization, not a claimed LabPilot productivity benchmark. The formal verdict during a LabPilot run must come from the persisted Result Plane validation, not from this table.

## Expected Proposal summary

A valid Agent Proposal should therefore contain:

```text
experiment_type = parameter_sweep
train_entrypoint = train.py
workdir = .
parameter_matrix = {
  learning_rate: [0.0005, 0.005],
  epochs: [100, 1000]
}
seeds = [7]
dataset = repo://data/classification.csv
python_profile = python-3.12
framework_requirements = []
timeout_seconds = 30
max_retries = 1
```

The Agent should derive this contract from repository evidence and return a structured Proposal. It must not directly launch the training process; LabPilot verification and human approval remain authoritative before execution.
