"""Versioned multi-file handoff; synthetic receipts are not live-model acceptance."""

import asyncio
import json
import os
import shutil
import subprocess
import sys
from hashlib import sha256
from pathlib import Path
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest
import pytest_asyncio
from tests.integration.context.test_repository_snapshot import _seed_capture_service
from tests.integration.context.test_repository_source import _git
from tests.integration.context.test_source_adoption import CONTEXT, capture, commit
from tests.integration.context.test_source_candidates import counts
from tests.integration.experiments.test_proposal_service import _payload

from labpilot.api.source_adoption_evidence import read_adoption_evidence
from labpilot.context.content_store import ContentAddressedStore
from labpilot.context.git_adapter import GitAdapter
from labpilot.context.project_source_candidates import ProjectSourceCandidateService, _identity
from labpilot.context.proposal_repository_adapter import ProposalRepositoryFactAdapter
from labpilot.context.repository_source import RepositorySnapshotSourceService
from labpilot.context.source_adoption import AdoptSource, SourceAdoptionService
from labpilot.context.source_candidates import CandidateError
from labpilot.experiments.proposal_models import ExperimentRow
from labpilot.experiments.proposal_service import ProposalApplicationService
from labpilot.experiments.proposal_verifier import ProposalVerifier

pytestmark = pytest.mark.integration


def write(path, value, *, signed=False):
    if signed:
        value.pop("identity", None)
        value["identity"] = _identity(value)
    path.write_text(json.dumps(value))
    return value


def seal(root):
    task = write(root / "task.json", json.loads((root / "task.json").read_text()), signed=True)
    manifest = json.loads((root / "candidate.json").read_text())
    manifest["task_identity"] = task["identity"]
    manifest = write(root / "candidate.json", manifest, signed=True)
    validation = json.loads((root / "validation.json").read_text())
    validation.update(task_identity=task["identity"], candidate_identity=manifest["identity"])
    validation = write(root / "validation.json", validation, signed=True)
    write(
        root / "status.json",
        {
            "status": "candidate_ready",
            "task_identity": task["identity"],
            "validation_identity": validation["identity"],
        },
    )


async def synthetic_receipt(system):
    root = system.tmp / "task"
    root.mkdir()
    baseline = system.tmp / "baseline"
    baseline.mkdir()
    entries = [
        {
            "path": p.relative_to(system.repository).as_posix(),
            "git_mode": "100644",
            "sha256": sha256(p.read_bytes()).hexdigest(),
        }
        for p in sorted(system.repository.rglob("*"))
        if p.is_file() and ".git" not in p.relative_to(system.repository).parts
    ]
    baseline_record = write(baseline / "baseline.json", {"entries": entries}, signed=True)
    candidate = root / "candidate"
    shutil.copytree(system.repository, candidate, ignore=shutil.ignore_patterns(".git"))
    # Generate an actual Git diff without touching the registered base repository.
    await _git(candidate, "init", "-q", "-b", "main")
    await commit(candidate)
    (candidate / "src/solver.py").write_text("def add(a, b):\n    return a + b\n")
    (candidate / "obsolete.py").unlink()
    (candidate / "tests").mkdir()
    (candidate / "tests/test_add.py").write_text("def test_contract():\n    assert 2 + 3 == 5\n")
    await _git(candidate, "add", "--all")
    patch = await asyncio.to_thread(
        subprocess.check_output, ["/usr/bin/git", "diff", "--cached", "--binary"], cwd=candidate
    )
    (root / "candidate.diff").write_bytes(patch)
    shutil.rmtree(candidate / ".git")  # Only this fresh test repository's metadata.
    manifest = {
        "base_identity": baseline_record["identity"],
        "changed_paths": ["obsolete.py", "src/solver.py", "tests/test_add.py"],
        "entries": [
            {
                "path": p.relative_to(candidate).as_posix(),
                "mode": 0o644,
                "sha256": sha256(p.read_bytes()).hexdigest(),
            }
            for p in sorted(candidate.rglob("*"))
            if p.is_file()
        ],
    }
    validator = {"image": "sha256:" + "1" * 64, "argv": ["python", "-c", "assert 2+3==5"]}
    write(
        root / "task.json",
        {
            "schema_version": "labpilot.coding_task.v1",
            "goal": "Fix addition and remove obsolete code",
            "baseline": str(baseline),
            "baseline_identity": baseline_record["identity"],
            "snapshot_binding": {
                "project_id": str(system.project_id),
                "snapshot_id": str(system.snapshot_id),
                "snapshot_identity": system.snapshot_identity,
            },
            "profile": {
                "editable_paths": ["src/*.py", "tests/*.py", "obsolete.py"],
                "validators": [validator],
            },
        },
    )
    write(root / "candidate.json", manifest)
    output = "validation-" + "a" * 32 + "-0.log"
    (root / output).write_text("synthetic receipt fixture; no model or Docker invoked\n")
    write(
        root / "validation.json",
        {
            "passed": True,
            "checks": [
                {
                    **validator,
                    "exit_code": 0,
                    "passed": True,
                    "source_unchanged": True,
                    "output_file": output,
                    "output_sha256": sha256((root / output).read_bytes()).hexdigest(),
                }
            ],
        },
    )
    seal(root)
    return root


@pytest_asyncio.fixture
async def system(isolated_database_url, tmp_path):
    repository = tmp_path / "repository"
    (repository / "src").mkdir(parents=True)
    (repository / "src/solver.py").write_text("def add(a, b):\n    return a - b\n")
    (repository / "obsolete.py").write_text("# remove me\n")
    (repository / "protected").mkdir()
    for index in range(130):
        (repository / "protected" / str(index)).write_text("protected\n")
    await _git(repository, "init", "-q", "-b", "main")
    await commit(repository)
    database, app, registration = await _seed_capture_service(
        isolated_database_url, tmp_path, repository
    )
    source = RepositorySnapshotSourceService(
        database.session,
        git_adapter=GitAdapter(git_executable=Path("/usr/bin/git"), timeout_seconds=5),
        content_store=ContentAddressedStore(tmp_path / "content"),
    )
    system = SimpleNamespace(
        database=database,
        database_url=isolated_database_url,
        capture=app,
        registration_id=registration,
        source=source,
        repository=repository,
        tmp=tmp_path,
    )
    try:
        system.snapshot_id = await capture(system, "base")
        descriptor = await source.describe(system.snapshot_id)
        system.snapshot_identity = descriptor.repository_snapshot_identity
        system.project_id = descriptor.project_id
        system.candidates = ProjectSourceCandidateService(source, tmp_path / "candidates")
        system.task = await synthetic_receipt(system)
        yield system
    finally:
        await database.dispose()


@pytest.mark.parametrize("setgid_parent", [False, True], ids=["private", "setgid"])
async def test_project_candidate_multi_file_adoption_and_existing_verifier(system, setgid_parent):
    if setgid_parent:
        system.candidates.root.chmod(0o2700)
    before = await counts(system.database)
    package = await system.candidates.import_task(system.task, actor_id="human")
    assert await system.candidates.import_task(system.task, actor_id="human") == package
    assert await counts(system.database) == before  # Import creates no business object.
    view = await system.candidates.inspect(package)
    assert view["package"]["schema_version"] == "labpilot.candidate_package.v2"
    assert view["report"]["runtime_checks"] == "passed_operator_receipt"
    assert view["report"]["scientific_validation"] == "not_run"
    assert len(view["report"]["output_refs"]) == 1
    assert not (system.repository / "train.py").exists()
    evidence = await read_adoption_evidence(
        system.candidates.store,
        package_ref=package,
        project_id=str(system.project_id),
        base_id=str(system.snapshot_id),
        base_identity=system.snapshot_identity,
    )
    assert evidence.static_report["schema_version"] == "labpilot.source_check_report.v2"
    assert evidence.rationale == "Fix addition and remove obsolete code"
    patch = (system.task / "candidate.diff").read_bytes()
    process = await asyncio.create_subprocess_exec(
        "/usr/bin/git", "apply", "-", cwd=system.repository, stdin=asyncio.subprocess.PIPE
    )
    await process.communicate(patch)
    assert process.returncode == 0
    await commit(system.repository)
    target = await capture(system, "target")
    identity = (await system.source.describe(target)).repository_snapshot_identity
    cmd = AdoptSource(
        project_id=system.project_id,
        package_ref=package,
        target_snapshot_id=target,
        target_snapshot_identity=identity,
    )
    service = SourceAdoptionService(system.database.session, system.candidates)
    outcome = await service.adopt(context=CONTEXT, idempotency_key="human-adopt", command=cmd)
    assert (
        await service.adopt(context=CONTEXT, idempotency_key="human-adopt", command=cmd)
    ).replayed
    assert UUID(outcome.envelope.primary_result_id)
    experiment = uuid4()
    async with system.database.session() as session:
        session.add(ExperimentRow(experiment_id=experiment, project_id=system.project_id))
        await session.commit()
    facts = ProposalRepositoryFactAdapter(system.source)
    proposals = ProposalApplicationService(
        system.database.session, repository_facts=facts, verifier=ProposalVerifier(facts)
    )
    revision = await proposals.create_manual_revision(
        context=CONTEXT,
        experiment_id=experiment,
        payload=_payload(
            system,
            repository_snapshot_id=target,
            repository_snapshot_identity=identity,
            train_entrypoint="src/solver.py",
            eval_entrypoint="src/solver.py",
        ),
    )
    result = await proposals.verify_revision(
        context=CONTEXT, proposal_revision_id=revision.proposal_revision_id
    )
    assert result.result.passed, result.result.findings
    after = await counts(system.database)
    for table in ("experiment_versions", "batches", "trials", "attempts"):
        assert after[table] == before[table]


@pytest.mark.parametrize(
    "case,reason",
    [
        ("failed", "coding_receipt_binding_mismatch"),
        ("binding", "base_binding_mismatch"),
        ("baseline", "coding_base_mismatch"),
        ("output", "coding_validator_mismatch"),
        ("candidate", "candidate_integrity_error"),
    ],
)
async def test_project_receipt_rejects_mismatch_without_domain_writes(system, case, reason):
    root = system.task
    if case == "candidate":
        (root / "candidate/src/solver.py").write_text("tampered\n")
    elif case == "output":
        next(root.glob("*.log")).write_text("tampered\n")
    elif case == "failed":
        value = json.loads((root / "validation.json").read_text())
        value["passed"] = False
        write(root / "validation.json", value)
        seal(root)
    elif case == "binding":
        value = json.loads((root / "task.json").read_text())
        value["snapshot_binding"]["snapshot_identity"] = "f" * 64
        write(root / "task.json", value)
        seal(root)
    elif case == "baseline":
        task = json.loads((root / "task.json").read_text())
        path = Path(task["baseline"]) / "baseline.json"
        base = json.loads(path.read_text())
        base["entries"][0]["sha256"] = "f" * 64
        base = write(path, base, signed=True)
        task["baseline_identity"] = base["identity"]
        write(root / "task.json", task)
        value = json.loads((root / "candidate.json").read_text())
        value["base_identity"] = base["identity"]
        write(root / "candidate.json", value)
        seal(root)
    before = await counts(system.database)
    with pytest.raises(CandidateError, match=reason):
        await system.candidates.import_task(root, actor_id="human")
    assert await counts(system.database) == before
    assert not list(system.candidates.root.glob("completed-*"))


async def test_real_worker_handoff_when_explicitly_enabled(system):
    worker = os.environ.get("LABPILOT_CODING_WORKER_PYTHON")
    image = os.environ.get("LABPILOT_CODING_IMAGE")
    if not worker or not image:
        pytest.skip("optional host Docker/worker acceptance; synthetic CI receipt is separate")
    repo = Path(__file__).resolve().parents[3]
    baseline = system.tmp / "actual-baseline"
    task = system.tmp / "actual-task"
    profile = system.tmp / "profile.json"
    goal = system.tmp / "goal.md"
    actions = system.tmp / "actions.json"
    write(
        profile,
        {
            "image": image,
            "editable_paths": ["src/*.py", "tests/*.py", "obsolete.py"],
            "ignored_paths": [],
            "validators": [
                {
                    "image": image,
                    "argv": [
                        "python",
                        "-c",
                        "import sys;sys.path.insert(0,'/workspace/src');"
                        "from solver import add;assert add(2,3)==5",
                    ],
                }
            ],
        },
    )
    goal.write_text("Fix addition, remove obsolete.py and add a development test.")
    actions.write_text(
        json.dumps(
            [
                "cat src/solver.py",
                "sed -i 's/return a - b/return a + b/' src/solver.py; "
                "rm obsolete.py; mkdir tests; "
                "printf 'def test_contract():\\n    assert 2+3==5\\n' > tests/test_add.py",
                "echo COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT",
            ]
        )
    )

    async def call(*args):
        process = await asyncio.create_subprocess_exec(
            worker,
            *map(str, args),
            cwd=repo,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        output, error = await asyncio.wait_for(process.communicate(), 180)
        assert process.returncode == 0, (output.decode(), error.decode())

    cli = repo / "tools/coding_worker/coding_task.py"
    await call(
        repo / "tools/coding_worker/baseline.py",
        "--repository",
        system.repository,
        "--output",
        baseline,
    )
    await call(
        cli,
        "prepare",
        "--baseline",
        baseline,
        "--goal",
        goal,
        "--profile",
        profile,
        "--task",
        task,
        "--project-id",
        system.project_id,
        "--snapshot-id",
        system.snapshot_id,
        "--snapshot-identity",
        system.snapshot_identity,
        "--calls",
        "5",
    )
    await call(cli, "run", "--task", task, "--actions", actions)
    assert json.loads((task / "status.json").read_text())["provider_calls"] == 0
    package = await system.candidates.import_task(task, actor_id="human")
    assert (await system.candidates.inspect(package))["status"] == "project_candidate_validated"


async def test_explicit_cli_onboarding_reuses_domain_authority(system, monkeypatch, capsys):
    from scripts import source_candidate as cli

    help_text = await asyncio.to_thread(
        subprocess.check_output, [sys.executable, str(Path(cli.__file__)), "--help"], text=True
    )
    assert all(
        name in help_text for name in ("create-project", "register-repository", "capture-snapshot")
    )

    monkeypatch.setattr(
        cli,
        "Settings",
        lambda: SimpleNamespace(
            database_url=system.database_url,
            repository_allowed_roots=(system.tmp,),
            repository_content_store_root=system.tmp / "content",
            local_actor_id="human",
            installation_identity="test",
        ),
    )
    # The fixture has a primary registration. Use a new project for real CLI registration.
    common = dict(profile="project-python-v1", idempotency_key="cli-create")
    await cli.run(
        SimpleNamespace(
            command="create-project",
            name="Coding user project",
            artifact_root_ref="coding-artifacts",
            **common,
        )
    )
    project = json.loads(capsys.readouterr().out)
    common["idempotency_key"] = "cli-register"
    await cli.run(
        SimpleNamespace(
            command="register-repository",
            project_id=UUID(project["primary_result_id"]),
            repository=system.repository,
            expected_project_row_version=1,
            **common,
        )
    )
    registered = json.loads(capsys.readouterr().out)
    common["idempotency_key"] = "cli-capture"
    arguments = SimpleNamespace(
        command="capture-snapshot", registration_id=UUID(registered["primary_result_id"]), **common
    )
    await cli.run(arguments)
    snapshot = json.loads(capsys.readouterr().out)
    assert snapshot["is_clean"] is True
    await cli.run(arguments)
    replay = json.loads(capsys.readouterr().out)
    assert (
        replay["replayed"] is True and replay["primary_result_id"] == snapshot["primary_result_id"]
    )


@pytest.mark.parametrize(
    "directory,mode",
    [(False, 0o4644), (False, 0o2644), (False, 0o1644), (True, 0o4700), (True, 0o1700)],
)
async def test_project_tree_rejects_special_permissions(system, directory, mode):
    root = system.tmp / "unsafe-tree"
    child = root / "subdir"
    child.mkdir(parents=True)
    target = child / "file.py"
    target.write_text("pass\n")
    (child if directory else target).chmod(mode)
    with pytest.raises(CandidateError, match="tree_type_unsupported"):
        await system.candidates._tree(root)


async def test_project_tree_rejects_symlink(system):
    root = system.tmp / "symlink-tree"
    root.mkdir()
    (root / "target.py").write_text("pass\n")
    (root / "link.py").symlink_to("target.py")
    with pytest.raises(CandidateError, match="tree_type_unsupported"):
        await system.candidates._tree(root)
