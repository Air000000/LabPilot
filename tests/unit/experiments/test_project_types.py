from dataclasses import FrozenInstanceError
from uuid import UUID

import pytest

from labpilot.experiments.project_types import (
    ArchiveProject,
    CreateProject,
    project_identity,
)


def test_create_project_accepts_valid_input() -> None:
    command = CreateProject(
        name="Causal Diffusion",
        description="Counterfactual image experiments",
        artifact_root_ref="artifact-root:projects/causal-diffusion",
    )

    assert command.name == "Causal Diffusion"
    assert command.description == "Counterfactual image experiments"
    assert command.artifact_root_ref == "artifact-root:projects/causal-diffusion"


@pytest.mark.parametrize("name", ["", " \t\n"])
def test_create_project_rejects_blank_name(name: str) -> None:
    with pytest.raises(ValueError, match="name"):
        CreateProject(name=name, description=None, artifact_root_ref="artifact-root:valid")


def test_create_project_rejects_name_over_200_utf8_bytes() -> None:
    with pytest.raises(ValueError, match="200 UTF-8 bytes"):
        CreateProject(
            name="界" * 67,
            description=None,
            artifact_root_ref="artifact-root:valid",
        )


def test_create_project_rejects_description_over_8_kib_utf8_bytes() -> None:
    with pytest.raises(ValueError, match="8192 UTF-8 bytes"):
        CreateProject(
            name="valid",
            description="界" * 2731,
            artifact_root_ref="artifact-root:valid",
        )


@pytest.mark.parametrize("artifact_root_ref", ["", " \t\n"])
def test_create_project_rejects_blank_artifact_root_ref(
    artifact_root_ref: str,
) -> None:
    with pytest.raises(ValueError, match="artifact_root_ref"):
        CreateProject(
            name="valid",
            description=None,
            artifact_root_ref=artifact_root_ref,
        )


def test_create_project_rejects_artifact_root_ref_over_1000_utf8_bytes() -> None:
    with pytest.raises(ValueError, match="1000 UTF-8 bytes"):
        CreateProject(
            name="valid",
            description=None,
            artifact_root_ref="界" * 334,
        )


@pytest.mark.parametrize("expected_row_version", [0, -1])
def test_archive_project_requires_positive_expected_row_version(
    expected_row_version: int,
) -> None:
    with pytest.raises(ValueError, match="expected_row_version"):
        ArchiveProject(
            project_id=UUID("12345678-1234-5678-1234-567812345678"),
            expected_row_version=expected_row_version,
        )


def test_project_commands_are_frozen_and_slotted() -> None:
    create = CreateProject(
        name="valid",
        description=None,
        artifact_root_ref="artifact-root:valid",
    )
    archive = ArchiveProject(
        project_id=UUID("12345678-1234-5678-1234-567812345678"),
        expected_row_version=1,
    )

    with pytest.raises(FrozenInstanceError):
        create.name = "changed"  # type: ignore[misc]
    with pytest.raises(FrozenInstanceError):
        archive.expected_row_version = 2  # type: ignore[misc]
    assert not hasattr(create, "__dict__")
    assert not hasattr(archive, "__dict__")


def test_project_identity_uses_exact_namespace_and_payload() -> None:
    project_id = UUID("12345678-1234-5678-1234-567812345678")

    assert (
        project_identity(project_id)
        == "877334bbd764ca291808a8a481a6d2f2d17653c647c85bc55e2c23937e1e1579"
    )


def test_project_identity_is_stable_and_distinguishes_projects() -> None:
    first = UUID("12345678-1234-5678-1234-567812345678")
    second = UUID("12345678-1234-5678-1234-567812345679")

    assert project_identity(first) == project_identity(first)
    assert project_identity(first) != project_identity(second)
