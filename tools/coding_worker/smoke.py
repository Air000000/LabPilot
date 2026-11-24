"""Zero-provider-call checks against real Docker and the frozen NOTEARS working tree."""

from __future__ import annotations

import argparse
import io
import json
import os
import tarfile
from pathlib import Path
from unittest.mock import patch

from environment import BoundaryError, CodingEnvironment, archive_files, bounded
from minisweagent.agents.default import DefaultAgent
from minisweagent.models.test_models import DeterministicModel, make_output


def docker(*args: str) -> bytes:
    code, result = bounded(["/usr/bin/docker", *args], timeout=20)
    if code:
        raise RuntimeError("docker_inspection_failed")
    return result


def check_archive_rejections() -> int:
    cases = [
        ("../escape", tarfile.REGTYPE, 0o644),
        ("/escape", tarfile.REGTYPE, 0o644),
        ("link", tarfile.SYMTYPE, 0o644),
        ("hard", tarfile.LNKTYPE, 0o644),
        ("device", tarfile.CHRTYPE, 0o644),
        ("setuid", tarfile.REGTYPE, 0o4644),
    ]
    for name, kind, mode in cases:
        stream = io.BytesIO()
        with tarfile.open(fileobj=stream, mode="w") as archive:
            info = tarfile.TarInfo(name)
            info.type, info.mode, info.linkname = kind, mode, "../outside"
            archive.addfile(info)
        try:
            archive_files(stream.getvalue())
        except BoundaryError:
            continue
        raise AssertionError("unsafe archive accepted")
    stream = io.BytesIO()
    with tarfile.open(fileobj=stream, mode="w") as archive:
        archive.addfile(tarfile.TarInfo("duplicate"))
        archive.addfile(tarfile.TarInfo("duplicate"))
    try:
        archive_files(stream.getvalue())
    except BoundaryError:
        return len(cases) + 1
    raise AssertionError("duplicate accepted")


def scripted_loop(image: str, output: Path) -> dict:
    files = {
        "src/calc.py": b"def add(a, b):\n    return a - b\n",
        "tests/test_calc.py": (
            b"import sys\nsys.path.insert(0, '/workspace/src')\nfrom calc im"
            b"port add\ndef test_add():\n    assert add(3, 2) == 5\n"
        ),
    }
    commands = [
        "cat src/calc.py",
        "python -m pytest -q -p no:cacheprovider tests",
        (
            "python -c \"from pathlib import Path; p=Path('src/calc.py'); "
            "p.write_text(p.read_text().replace('a - b', 'a + b'))\""
        ),
        "python -m pytest -q -p no:cacheprovider tests",
        "echo COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT",
    ]
    env = CodingEnvironment(image)
    try:
        env.load(files)
        model = DeterministicModel(
            outputs=[
                make_output("Offline fixture action", [{"command": cmd}], cost=0.0)
                for cmd in commands
            ],
            cost_per_call=0.0,
        )
        agent = DefaultAgent(
            model,
            env,
            system_template="Offline boundary check",
            instance_template="{{task}}",
            step_limit=6,
            cost_limit=1,
            wall_time_limit_seconds=90,
            output_path=output / "offline-trajectory.json",
        )
        result = agent.run("Exercise read/edit/test and finish; scripted model, no provider.")
        assert result["exit_status"] == "Submitted", result
        observations = [m["content"] for m in agent.messages if m.get("role") == "user"]
        assert any("1 failed" in m for m in observations)
        assert any("1 passed" in m for m in observations)
        tree = env.export()
        assert tree["src/calc.py"][1] == b"def add(a, b):\n    return a + b\n"
        assert tree["tests/test_calc.py"][1] == files["tests/test_calc.py"]
        assert agent.n_calls == 5 and agent.cost == 0
        return {
            "status": "passed",
            "scripted_steps": agent.n_calls,
            "provider_calls": 0,
            "test_failed_before_edit": True,
            "test_passed_after_edit": True,
            "protected_test_unchanged": True,
        }
    finally:
        env.cleanup()


def lifecycle(image: str) -> dict:
    env = CodingEnvironment(image)
    try:
        os.environ["LABPILOT_SMOKE_SECRET_SENTINEL"] = "not-a-real-key"
        check = env.execute(
            {
                "command": (
                    'python -c "import os,socket; from pathlib import Path; asser'
                    "t os.getuid()==1000; assert 'LABPILOT_SMOKE_SECRET_SENTINEL'"
                    " not in os.environ; assert not Path('/run/docker.sock').exis"
                    "ts(); assert not Path('/run/labpilot-runner/control.sock').e"
                    "xists(); s=socket.socket();s.settimeout(.2);assert s.connect"
                    "_ex(('1.1.1.1',443)) != 0;print('isolation_ok')\""
                )
            }
        )
        assert check["returncode"] == 0, check
        assert env.execute({"command": "touch /rootfs-write-check"})["returncode"] != 0
        env.execute(
            {
                "command": (
                    "(while :; do date +%s%N > /workspace/marker; sleep .01; done"
                    ") >/dev/null 2>&1 &"
                )
            }
        )
        env.execute({"command": "sleep .1"})
        first = env.export()
        assert "marker" in first
        assert (
            docker("inspect", "--format", "{{.State.Paused}}", env.container_id).strip() == b"true"
        )
        again = archive_files(docker("cp", f"{env.container_id}:/workspace/.", "-"))
        assert first == again
    finally:
        env.cleanup()
        os.environ.pop("LABPILOT_SMOKE_SECRET_SENTINEL", None)
    for command, reason in [
        ("sleep 30", "command_timeout"),
        ("python -c \"print('x' * (2 * 1024 * 1024))\"", "command_output_limit"),
    ]:
        env = CodingEnvironment(image)
        try:
            env.execute({"command": command}, timeout=1)
        except BoundaryError as exc:
            assert str(exc) == reason, str(exc)
            assert env.container_id is None
        else:
            raise AssertionError("expected limit rejection")
        finally:
            env.cleanup()
    return {
        "status": "passed",
        "nonroot": True,
        "no_network": True,
        "readonly_root": True,
        "no_secret_or_socket": True,
        "paused_export_stable": True,
        "timeout_cleanup": True,
        "output_limit_cleanup": True,
    }


def notears(cpu: str, plot_image: str, baseline: Path, output: Path) -> dict:
    manifest = json.loads((baseline / "baseline.json").read_text())
    files = {
        e["path"]: (baseline / "source" / e["path"]).read_bytes()
        for e in manifest["entries"]
        if not e.get("deleted")
    }
    modes = {
        e["path"]: int(e["git_mode"], 8) & 0o777
        for e in manifest["entries"]
        if not e.get("deleted")
    }
    failures = {}
    for label, image, expected in [
        ("cpu", cpu, "ModuleNotFoundError"),
        ("plot", plot_image, "NameError"),
    ]:
        env = CodingEnvironment(image)
        try:
            env.load(files, modes)
            result = env.execute({"command": "python -c 'import notears.linear'"})
            assert result["returncode"] != 0 and expected in result["output"], result
            (output / (label + "-original-import.txt")).write_text(result["output"])
            failures[label] = {"returncode": result["returncode"], "error_type": expected}
        finally:
            env.cleanup()
    reference = {}
    with tarfile.open(baseline / "reference.tar") as archive:
        for entry in archive:
            if entry.isfile():
                stream = archive.extractfile(entry)
                assert stream is not None
                reference[entry.name] = stream.read()
    env = CodingEnvironment(cpu)
    try:
        env.load(reference)
        program = """import json, numpy as np
from notears.linear import notears_linear
rng=np.random.default_rng(723)
a=rng.normal(size=128)
l2=np.column_stack([a, 1.2*a + .2*rng.normal(size=128), -.7*a + .3*rng.normal(size=128)])
a=rng.binomial(1,.5,size=128)
b=rng.binomial(1,1/(1+np.exp(-(3*a-1.5))))
c=rng.binomial(1,1/(1+np.exp(-(2*b-1))))
logistic=np.column_stack([a,b,c]).astype(float)
a=rng.poisson(1.,size=128)
b=rng.poisson(np.exp(.5*a))
c=rng.poisson(np.exp(.3*a))
values={'l2':l2, 'logistic':logistic, 'poisson':np.column_stack([a,b,c]).astype(float)}
results={}
for name,X in values.items():
 W=notears_linear(X,lambda1=.05,loss_type=name,max_iter=30,w_threshold=.1)
 assert W.shape==(3,3) and np.isfinite(W).all() and np.all(np.diag(W)==0)
 assert np.count_nonzero(W)>0, 'reference must detect a zero-output fake'
 results[name]={'matrix': W.tolist(), 'input': X.tolist()}
print(json.dumps(results))
"""
        env.load({"reference_check.py": program.encode()})
        result = env.execute({"command": "python reference_check.py"})
        assert result["returncode"] == 0, result
        result_data = json.loads(result["output"])
        (output / "reference-results.json").write_text(json.dumps(result_data, indent=2))
    finally:
        env.cleanup()
    return {
        "baseline_identity": manifest["identity"],
        "files": len(files),
        "original_import": failures,
        "independent_original_head_solvers": list(result_data),
        "real_agent_task": "NOT RUN",
        "candidate_modified_by_human": False,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cpu-image", required=True)
    parser.add_argument("--plot-image", required=True)
    parser.add_argument("--baseline", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--docker-host", default="unix:///run/labpilot-docker.sock")
    args = parser.parse_args()
    os.environ["DOCKER_HOST"] = args.docker_host
    args.output.mkdir(mode=0o700, parents=True)
    os.umask(0o077)
    original = docker("ps", "--no-trunc", "--format", "{{.ID}} {{.Names}}")
    try:
        with patch(
            "socket.socket.connect", side_effect=AssertionError("offline Python network denied")
        ):
            results = {
                "archive_rejections": check_archive_rejections(),
                "loop": scripted_loop(args.cpu_image, args.output),
                "lifecycle": lifecycle(args.cpu_image),
                "notears": notears(args.cpu_image, args.plot_image, args.baseline, args.output),
            }
        results["existing_containers_unchanged"] = original == docker(
            "ps", "--no-trunc", "--format", "{{.ID}} {{.Names}}"
        )
        assert results["existing_containers_unchanged"]
        (args.output / "result.json").write_text(json.dumps(results, indent=2) + "\n")
        print(json.dumps(results))
    finally:
        assert not docker("ps", "-aq", "--filter", "label=labpilot.coding.task").strip(), (
            "task container left behind"
        )


if __name__ == "__main__":
    main()
