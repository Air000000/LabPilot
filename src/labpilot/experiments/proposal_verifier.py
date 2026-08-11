from dataclasses import dataclass
from math import isfinite, prod
from typing import Literal
from uuid import UUID

from labpilot.common.hashing import namespaced_sha256
from labpilot.experiments.proposal_ports import ProposalRepositoryFactPort
from labpilot.experiments.proposal_types import ProposalPayload

type VerificationSeverity = Literal["blocking", "warning"]

VERIFIER_PROFILE = "slice1-v1"
_RESULT_HASH_NAMESPACE = "labpilot-proposal-verification-result:v1"


@dataclass(frozen=True, slots=True)
class VerificationFinding:
    code: str
    severity: VerificationSeverity
    field: str | None
    message: str


@dataclass(frozen=True, slots=True)
class ProposalVerificationResult:
    profile: str
    passed: bool
    findings: tuple[VerificationFinding, ...]
    result_hash: str


def _is_safe_relative_path(value: str, *, allow_root: bool) -> bool:
    if allow_root and value == ".":
        return True
    return not (
        not value
        or "\x00" in value
        or "\\" in value
        or value.startswith("/")
        or (len(value) >= 2 and value[0].isalpha() and value[1] == ":")
        or any(part in {"", ".", ".."} for part in value.split("/"))
    )


def _valid_parameter_key(value: str) -> bool:
    if not value.strip():
        return False
    try:
        value.encode("utf-8")
    except UnicodeEncodeError:
        return False
    return True


def _finding(
    code: str,
    *,
    field: str | None,
    message: str,
    severity: VerificationSeverity = "blocking",
) -> VerificationFinding:
    return VerificationFinding(code, severity, field, message)


def _finish(findings: list[VerificationFinding]) -> ProposalVerificationResult:
    ordered = tuple(
        sorted(
            findings,
            key=lambda finding: (
                finding.severity,
                finding.code,
                finding.field or "",
                finding.message,
            ),
        )
    )
    passed = not any(finding.severity == "blocking" for finding in ordered)
    result_hash = namespaced_sha256(
        _RESULT_HASH_NAMESPACE,
        {
            "profile": VERIFIER_PROFILE,
            "passed": passed,
            "findings": [
                {
                    "code": finding.code,
                    "severity": finding.severity,
                    "field": finding.field,
                    "message": finding.message,
                }
                for finding in ordered
            ],
        },
    )
    return ProposalVerificationResult(
        profile=VERIFIER_PROFILE,
        passed=passed,
        findings=ordered,
        result_hash=result_hash,
    )


class ProposalVerifier:
    def __init__(self, repository_facts: ProposalRepositoryFactPort) -> None:
        self._repository_facts = repository_facts

    async def verify(
        self,
        *,
        project_id: UUID,
        payload: ProposalPayload,
    ) -> ProposalVerificationResult:
        findings: list[VerificationFinding] = []
        snapshot_available = True
        try:
            snapshot = await self._repository_facts.snapshot_state(
                payload.repository_snapshot_id
            )
        except Exception as exc:
            if not isinstance(exc, LookupError) and exc.args != ("snapshot_not_found",):
                raise

            snapshot_available = False
            findings.append(
                _finding(
                    "snapshot_missing",
                    field="repository_snapshot_id",
                    message="The bound RepositorySnapshot does not exist.",
                )
            )
        else:
            if snapshot.repository_snapshot_id != payload.repository_snapshot_id:
                findings.append(
                    _finding(
                        "snapshot_id_mismatch",
                        field="repository_snapshot_id",
                        message="The returned Snapshot does not match the bound Snapshot id.",
                    )
                )
            if snapshot.project_id != project_id:
                findings.append(
                    _finding(
                        "snapshot_project_mismatch",
                        field="repository_snapshot_id",
                        message="The bound Snapshot belongs to another Project.",
                    )
                )
            if not snapshot.is_clean:
                findings.append(
                    _finding(
                        "snapshot_dirty",
                        field="repository_snapshot_id",
                        message="The bound Snapshot is stored as dirty.",
                    )
                )
            if (
                snapshot.repository_snapshot_identity
                != payload.repository_snapshot_identity
            ):
                findings.append(
                    _finding(
                        "snapshot_identity_mismatch",
                        field="repository_snapshot_identity",
                        message="The bound Snapshot identity does not match the Proposal.",
                    )
                )

        if snapshot_available:
            await self._check_entrypoint(
                payload,
                field="train_entrypoint",
                path=payload.train_entrypoint,
                findings=findings,
            )
            if payload.eval_entrypoint is not None:
                await self._check_entrypoint(
                    payload,
                    field="eval_entrypoint",
                    path=payload.eval_entrypoint,
                    findings=findings,
                )

        workdir_safe = _is_safe_relative_path(payload.workdir, allow_root=True)
        if not workdir_safe:
            findings.append(
                _finding(
                    "workdir_invalid",
                    field="workdir",
                    message="workdir must be a safe repository-relative directory.",
                )
            )
        elif snapshot_available and payload.workdir != "." and not await (
            self._repository_facts.directory_exists(
                payload.repository_snapshot_id,
                payload.workdir,
            )
        ):
            findings.append(
                _finding(
                    "workdir_missing",
                    field="workdir",
                    message="workdir does not exist in the bound Snapshot.",
                )
            )

        self._check_seeds(payload, findings)
        trial_count = self._check_parameter_matrix(payload, findings)
        self._check_experiment_type(payload, trial_count, findings)
        self._check_dataset(payload, findings)
        self._check_artifacts_and_metrics(payload, findings)

        if not 1 <= payload.timeout_seconds <= 86_400:
            findings.append(
                _finding(
                    "timeout_invalid",
                    field="timeout_seconds",
                    message="timeout_seconds must be between 1 and 86400.",
                )
            )
        if not 0 <= payload.max_retries <= 3:
            findings.append(
                _finding(
                    "max_retries_invalid",
                    field="max_retries",
                    message="max_retries must be between 0 and 3.",
                )
            )

        return _finish(findings)

    async def _check_entrypoint(
        self,
        payload: ProposalPayload,
        *,
        field: str,
        path: str,
        findings: list[VerificationFinding],
    ) -> None:
        fact = await self._repository_facts.path_fact(
            payload.repository_snapshot_id,
            path,
        )
        if fact is None or fact.file_type != "regular":
            findings.append(
                _finding(
                    f"{field}_invalid",
                    field=field,
                    message=f"{field} must exist as a regular file in the bound Snapshot.",
                )
            )

    @staticmethod
    def _check_seeds(
        payload: ProposalPayload,
        findings: list[VerificationFinding],
    ) -> None:
        seeds = payload.seeds
        if (
            not 1 <= len(seeds) <= 20
            or len(set(seeds)) != len(seeds)
            or any(type(seed) is not int or seed < 0 for seed in seeds)
        ):
            findings.append(
                _finding(
                    "seeds_invalid",
                    field="seeds",
                    message="seeds must contain 1-20 unique non-negative integers.",
                )
            )

    @staticmethod
    def _check_parameter_matrix(
        payload: ProposalPayload,
        findings: list[VerificationFinding],
    ) -> int:
        matrix = payload.parameter_matrix
        if len(matrix) > 16:
            findings.append(
                _finding(
                    "parameter_key_count",
                    field="parameter_matrix",
                    message="parameter_matrix may contain at most 16 keys.",
                )
            )
        for key, values in matrix.items():
            if not _valid_parameter_key(key):
                findings.append(
                    _finding(
                        "parameter_key_invalid",
                        field="parameter_matrix",
                        message="Parameter keys must be non-blank valid UTF-8.",
                    )
                )
            if not 1 <= len(values) <= 20:
                findings.append(
                    _finding(
                        "parameter_value_count",
                        field="parameter_matrix",
                        message="Each parameter must contain 1-20 values.",
                    )
                )
            if any(
                type(value) not in {str, int, float, bool, type(None)}
                or (type(value) is float and not isfinite(value))
                for value in values
            ):
                findings.append(
                    _finding(
                        "parameter_value_invalid",
                        field="parameter_matrix",
                        message="Parameter values must be finite scalar values.",
                    )
                )
        trial_count = prod(len(values) for values in matrix.values())
        if not 1 <= trial_count <= 20:
            findings.append(
                _finding(
                    "trial_count_invalid",
                    field="parameter_matrix",
                    message="Expanded Trial count must be between 1 and 20.",
                )
            )
        return trial_count

    @staticmethod
    def _check_experiment_type(
        payload: ProposalPayload,
        trial_count: int,
        findings: list[VerificationFinding],
    ) -> None:
        if payload.experiment_type in {"parameter_sweep", "comparison", "ablation"} and (
            trial_count <= 1
        ):
            findings.append(
                _finding(
                    "experiment_trial_requirement",
                    field="experiment_type",
                    message="This experiment type requires multiple planned Trials.",
                )
            )
        if payload.experiment_type == "stability" and len(set(payload.seeds)) < 2:
            findings.append(
                _finding(
                    "stability_seed_requirement",
                    field="seeds",
                    message="stability requires at least two distinct seeds.",
                )
            )

    @staticmethod
    def _check_dataset(
        payload: ProposalPayload,
        findings: list[VerificationFinding],
    ) -> None:
        dataset = payload.dataset_reference
        if dataset.stable_identity:
            return
        if dataset.reproducibility_level == "best_effort":
            findings.append(
                _finding(
                    "dataset_best_effort",
                    severity="warning",
                    field="dataset_reference",
                    message="Dataset has no stable identity and is best effort.",
                )
            )
        else:
            findings.append(
                _finding(
                    "dataset_identity_required",
                    field="dataset_reference",
                    message="strict DatasetReference requires a stable identity.",
                )
            )

    @staticmethod
    def _check_artifacts_and_metrics(
        payload: ProposalPayload,
        findings: list[VerificationFinding],
    ) -> None:
        artifact_names = [artifact.name for artifact in payload.artifacts]
        if len(set(artifact_names)) != len(artifact_names):
            findings.append(
                _finding(
                    "artifact_name_duplicate",
                    field="artifacts",
                    message="Artifact names must be unique.",
                )
            )
        for artifact in payload.artifacts:
            if artifact.required and not _is_safe_relative_path(
                artifact.relative_path,
                allow_root=False,
            ):
                findings.append(
                    _finding(
                        "artifact_path_invalid",
                        field="artifacts",
                        message="Required artifact paths must be safe relative paths.",
                    )
                )

        metric_names = [metric.name for metric in payload.metrics]
        if len(set(metric_names)) != len(metric_names):
            findings.append(
                _finding(
                    "metric_name_duplicate",
                    field="metrics",
                    message="Metric names must be unique.",
                )
            )
        declared_artifacts = set(artifact_names)
        for metric in payload.metrics:
            if metric.source_artifact not in declared_artifacts:
                findings.append(
                    _finding(
                        "metric_artifact_missing",
                        field="metrics",
                        message="Every Metric must reference a declared Artifact.",
                    )
                )

        artifact_coverage = {
            rule.subject
            for rule in payload.validation_rules
            if rule.kind == "artifact_exists"
        }
        for artifact in payload.artifacts:
            if artifact.required and artifact.name not in artifact_coverage:
                findings.append(
                    _finding(
                        "artifact_validation_missing",
                        field="validation_rules",
                        message="Every required Artifact needs artifact_exists coverage.",
                    )
                )

        metric_coverage = {
            rule.subject
            for rule in payload.validation_rules
            if rule.kind == "metric_finite"
        }
        for metric in payload.metrics:
            if metric.name not in metric_coverage:
                findings.append(
                    _finding(
                        "metric_validation_missing",
                        field="validation_rules",
                        message="Every Metric needs metric_finite coverage.",
                    )
                )
