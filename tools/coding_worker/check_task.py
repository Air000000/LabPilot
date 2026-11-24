"""Runnable offline task CLI, budget, replay and independent-validator regression."""

from __future__ import annotations

import argparse
import json
import os
import signal
import socket
import subprocess
import sys
import time
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
from unittest.mock import patch

from baseline import git, prepare
from budget import BudgetModel
from coding_task import MAX_CHANGED_FILES, MAX_DIFF_BYTES, SYSTEM_TEMPLATE, execute, worker_limits
from coding_task import main as worker_main
from environment import BoundaryError, CodingEnvironment


def endpoint_check():
    for endpoint in (None, "unix:///private/custom-docker.sock"):
        with patch.dict(os.environ, {"DOCKER_CONTEXT": "unselected-context"}):
            if endpoint is None:
                os.environ.pop("DOCKER_HOST", None)
            else:
                os.environ["DOCKER_HOST"] = endpoint
            with (
                patch.object(sys, "argv", ["coding_task.py", "inspect", "--task", "/unused"]),
                patch("coding_task.read", return_value={"status": "prepared"}),
                redirect_stdout(StringIO()),
            ):
                assert worker_main() == 0
            assert os.environ["DOCKER_HOST"] == (endpoint or "unix:///run/labpilot-docker.sock")
            assert "DOCKER_CONTEXT" not in os.environ
    return {"explicit_endpoint_preserved": True, "default_endpoint_preserved": True}


def budget_check():
    class Inner:
        def query(self, messages, **kwargs):
            self.kwargs = kwargs
            return {
                "extra": {
                    "actions": [{"command": "true"}],
                    "response": {
                        "usage": {"prompt_tokens": 7, "completion_tokens": 3, "total_tokens": 10},
                        "choices": [{"finish_reason": "stop"}],
                    },
                }
            }

    events = []
    inner = Inner()
    model = BudgetModel(inner, calls=1, tokens=20, output_cap=16, record=events.append)
    model.query([{"role": "user", "content": "synthetic"}])
    assert inner.kwargs["max_tokens"] == 16 and model.tokens == 10
    assert events[0]["status"] == "reserved" and events[1]["status"] == "response_received"
    try:
        model.query([])
    except BoundaryError as exc:
        assert str(exc) == "model_budget_exhausted"
    else:
        raise AssertionError("call budget not enforced")

    class Unknown:
        def query(self, messages, **kwargs):
            return {"extra": {"response": {}}}

    model = BudgetModel(Unknown(), calls=2, tokens=20, output_cap=16, record=events.append)
    try:
        model.query([])
    except BoundaryError as exc:
        assert str(exc) == "model_usage_unknown"
    else:
        raise AssertionError("unknown usage accepted")
    try:
        model.query([])
    except BoundaryError as exc:
        assert str(exc) == "model_previous_outcome_unknown"
    else:
        raise AssertionError("unknown outcome retried")

    class Failed:
        def query(self, messages, **kwargs):
            raise ValueError("PRIVATE_SENTINEL")

    model = BudgetModel(Failed(), calls=2, tokens=20, output_cap=16, record=events.append)
    try:
        model.query([])
    except BoundaryError as exc:
        assert str(exc) == "model_call_outcome_unknown"
        assert "PRIVATE_SENTINEL" not in json.dumps(events)
    else:
        raise AssertionError("transport failure not closed")
    # Keep only SDK status/class; never messages, headers, body or arbitrary class names.
    import httpx
    from litellm.exceptions import APIConnectionError, BadRequestError

    class PRIVATE_SENTINEL_Error(Exception):
        pass

    failures = [
        (
            BadRequestError(
                "PRIVATE_SENTINEL",
                "synthetic",
                "deepseek",
                response=httpx.Response(
                    400,
                    request=httpx.Request(
                        "POST",
                        "https://example.invalid",
                        headers={"Authorization": "PRIVATE_SENTINEL"},
                    ),
                    json={"error": "PRIVATE_SENTINEL"},
                ),
            ),
            "BadRequestError",
            400,
        ),
        (
            APIConnectionError("PRIVATE_SENTINEL", "deepseek", "synthetic"),
            "APIConnectionError",
            None,
        ),
        (PRIVATE_SENTINEL_Error("PRIVATE_SENTINEL"), "unknown", None),
    ]
    for error, label, status in failures:
        events = []
        inner = Inner()
        with patch.object(inner, "query", side_effect=error) as transport:
            model = BudgetModel(inner, calls=2, tokens=20, output_cap=16, record=events.append)
            for _ in range(2):
                try:
                    model.query([])
                except BoundaryError:
                    pass
                else:
                    raise AssertionError("unknown outcome retried")
            assert transport.call_count == 1
        assert events[-1] == {
            "ordinal": 1,
            "status": "outcome_unknown",
            "error_type": label,
            "http_status": status,
        }
        assert "PRIVATE_SENTINEL" not in json.dumps(events)
        assert model.tokens == 0  # No known usage; not a claim of zero billed tokens.

    # Failures retain only closed-vocabulary completion metadata before they stop.
    for finish, reason in (
        ("length", "model_output_truncated"),
        ("content_filter", "model_output_filtered"),
        ("PRIVATE_SENTINEL", "model_completion_unknown"),
        (None, "model_completion_unknown"),
    ):
        inner = Inner()
        message = inner.query([])
        message["extra"]["response"]["choices"][0]["finish_reason"] = finish
        events = []
        with patch.object(inner, "query", return_value=message):
            model = BudgetModel(inner, calls=2, tokens=20, output_cap=16, record=events.append)
            try:
                model.query([])
            except BoundaryError as exc:
                assert str(exc) == reason
            else:
                raise AssertionError("incomplete response accepted")
            assert model.tokens == 10 and events[-1]["status"] == "response_received"
            assert events[-1]["finish_reason"] == (
                finish if finish in {"length", "content_filter"} else "unknown"
            )
            assert events[-1]["choice_count"] == 1
            assert "PRIVATE_SENTINEL" not in json.dumps(events)
            try:
                model.query([])
            except BoundaryError:
                pass
            else:
                raise AssertionError("closed response retried")
            assert inner.query.call_count == 1
    return {
        "sdk_error_metadata_redacted_and_terminal": True,
        "completion_metadata_before_rejection": True,
        "truncation_distinguished": True,
        "unknown_finish_reason_redacted": True,
        "reserved_before_query": True,
        "known_usage_accounted": True,
        "exhausted_budget_rejected": True,
        "unknown_usage_rejected": True,
    }


def sdk_response(commands, *, finish="tool_calls"):
    from litellm import ModelResponse

    return ModelResponse(
        model="deepseek-flash",
        choices=[
            {
                "index": 0,
                "finish_reason": finish,
                "message": {
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [
                        {
                            "id": "mock-" + str(i),
                            "type": "function",
                            "function": {
                                "name": "bash",
                                "arguments": json.dumps({"command": command}),
                            },
                        }
                        for i, command in enumerate(commands)
                    ],
                },
            }
        ],
        usage={"prompt_tokens": 7, "completion_tokens": 3, "total_tokens": 10},
    )


def native_boundary_check():
    from minisweagent.exceptions import FormatError
    from minisweagent.models.litellm_model import LitellmModel

    for commands, error in (
        (["true"], None),
        ([], FormatError),
        (["true", "false"], BoundaryError),
        ([None], BoundaryError),
        (["  "], BoundaryError),
    ):
        inner = LitellmModel(model_name="deepseek/deepseek-v4-flash", cost_tracking="ignore_errors")
        events = []
        model = BudgetModel(inner, calls=1, tokens=20, output_cap=16, record=events.append)
        with (
            patch("litellm.completion", return_value=sdk_response(commands)) as transport,
            patch("litellm.cost_calculator.completion_cost", return_value=0.01),
        ):
            try:
                result = model.query([{"role": "user", "content": "synthetic task"}])
            except (FormatError, BoundaryError) as exc:
                assert error is not None and isinstance(exc, error)
            else:
                assert error is None and result["extra"]["actions"][0]["command"] == "true"
            assert (
                transport.call_count == 1
                and transport.call_args.kwargs["tools"][0]["function"]["name"] == "bash"
            )
            received = next(e for e in events if e["status"] == "response_received")
            assert received["finish_reason"] == "tool_calls" and model.tokens == 10
    return {"upstream_native_parser": True, "single_nonempty_command_enforced": True}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--image", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    os.environ.pop("DOCKER_CONTEXT", None)
    os.environ["DOCKER_HOST"] = "unix:///run/labpilot-docker.sock"
    os.environ["MSWEA_MODEL_RETRY_STOP_AFTER_ATTEMPT"] = "1"
    os.environ["LITELLM_LOCAL_MODEL_COST_MAP"] = "True"
    worker_limits()
    assert "do not chmod or chown" in SYSTEM_TEMPLATE
    assert str(MAX_CHANGED_FILES) in SYSTEM_TEMPLATE and str(MAX_DIFF_BYTES) in SYSTEM_TEMPLATE
    os.umask(0o077)
    args.output.mkdir(mode=0o700, parents=True)
    root = args.output.resolve()
    original = root / "fixture-origin"
    original.mkdir()
    (original / "src").mkdir()
    (original / "tests").mkdir()
    (original / "src/calc.py").write_text("def add(a,b):\n    return a-b\n")
    (original / "tests/test_calc.py").write_text(
        "import sys\nsys.path.insert(0,'/workspace/src')\nfrom calc imp"
        "ort add\ndef test_add():\n    assert add(3,2)==5\n"
    )
    git(original, "init", "-q", "-b", "main")
    git(original, "add", "--all")
    git(
        original,
        "-c",
        "user.name=LabPilot Local",
        "-c",
        "user.email=local@example.invalid",
        "-c",
        "core.hooksPath=/dev/null",
        "commit",
        "-qm",
        "Offline regression fixture",
    )
    baseline = root / "baseline"
    prepare(original, baseline)
    profile = {
        "image": args.image,
        "editable_paths": ["src/calc.py"],
        "ignored_paths": [".pytest_cache/*"],
        "validators": [
            {
                "image": args.image,
                "argv": [
                    "python",
                    "-c",
                    (
                        "import sys;sys.path.insert(0,'/workspace/src');from calc imp"
                        "ort add;assert add(3,2)==5;assert add(-2,7)==5;assert add(.5"
                        ",.25)==.75"
                    ),
                ],
            }
        ],
    }
    (root / "profile.json").write_text(json.dumps(profile))
    (root / "goal.md").write_text("Fix addition; only src/calc.py may change.")
    actions = [
        (
            "cat src/calc.py && python -c \"print('HEAD' + 'x'*12000 + "
            "'PRIVATE_MIDDLE' + 'y'*12000 + 'TAIL')\""
        ),
        "python -m pytest -q -p no:cacheprovider tests",
        "sed -i 's/return a-b/return a+b/' src/calc.py",
        "python -m pytest -q -p no:cacheprovider tests",
        "echo COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT",
    ]
    (root / "actions.json").write_text(json.dumps(actions))
    cli = Path(__file__).with_name("coding_task.py").resolve()

    def call(*arguments, expected=0):
        result = subprocess.run(
            [sys.executable, str(cli), *map(str, arguments)],
            capture_output=True,
            text=True,
            timeout=120,
        )
        assert result.returncode == expected, (result.returncode, result.stdout, result.stderr)
        return result.stdout

    task = root / "task"
    call(
        "prepare",
        "--baseline",
        baseline,
        "--goal",
        root / "goal.md",
        "--profile",
        root / "profile.json",
        "--task",
        task,
        "--calls",
        "6",
        "--wall-seconds",
        "60",
    )
    # A default run refuses model use before provisioning any external task.
    denied = call("run", "--task", task, expected=1)
    assert "live_model_not_enabled" in denied
    assert json.loads((task / "status.json").read_text())["status"] == "prepared"
    result = json.loads(call("run", "--task", task, "--actions", root / "actions.json"))
    assert result["status"] == "candidate_ready" and result["provider_calls"] == 0
    assert result["scripted_steps"] == 5
    trajectory = json.loads((task / "trajectory.json").read_text())
    system_message = next(m for m in trajectory["messages"] if m["role"] == "system")
    assert "do not chmod or chown" in system_message["content"]
    patch_text = (task / "candidate.diff").read_text()
    assert "-    return a-b" in patch_text and "+    return a+b" in patch_text
    assert json.loads((task / "validation.json").read_text())["passed"]
    assert "task_not_prepared" in call(
        "run", "--task", task, "--actions", root / "actions.json", expected=1
    )
    call("validate", "--task", task)
    state = json.loads((task / "status.json").read_text())
    report = json.loads((task / "validation.json").read_text())
    assert state["validation_identity"] == report["identity"]
    assert "initial_validation_identity" in state
    original_bytes = (original / "src/calc.py").read_bytes()
    assert original_bytes == b"def add(a,b):\n    return a-b\n"
    # Candidate byte tampering must not be accepted as a new validated artifact.
    (task / "candidate/src/calc.py").write_text("def add(a,b):\n    return 0\n")
    assert "candidate_content_changed" in call("validate", "--task", task, expected=1)
    assert "candidate_content_changed" in call("inspect", "--task", task, expected=1)

    # Infrastructure failures stay separate from scientific or model acceptance.
    def new_task(name, *extra):
        target = root / name
        call(
            "prepare",
            "--baseline",
            baseline,
            "--goal",
            root / "goal.md",
            "--profile",
            root / "profile.json",
            "--task",
            target,
            "--calls",
            "6",
            "--wall-seconds",
            "60",
            *extra,
        )
        return target

    native_task = root / "native-task"
    call(
        "prepare",
        "--baseline",
        baseline,
        "--goal",
        root / "goal.md",
        "--profile",
        root / "profile.json",
        "--task",
        native_task,
        "--calls",
        "6",
        "--wall-seconds",
        "60",
        "--model",
        "deepseek/deepseek-v4-flash",
        "--output-cap",
        "8192",
    )
    assert json.loads((native_task / "task.json").read_text())["output_cap"] == 8192
    import httpx

    requests = []

    def http_send(client, request, **kwargs):
        assert request.url.host == "api.deepseek.com"
        assert request.url.path in {"/chat/completions", "/v1/chat/completions"}
        body = json.loads(request.content)
        assert body["model"] == "deepseek-v4-flash" and body["max_tokens"] == 8192
        assert len(body["tools"]) == 1 and body["tools"][0]["function"]["name"] == "bash"
        assert body["tools"][0]["type"] == "function" and "_skip_mcp_handler" not in body
        # One eligible SDK format correction also consumes a model call.
        response = sdk_response([] if not requests else [actions[len(requests) - 1]]).model_dump()
        response["choices"][0]["message"]["reasoning_content"] = "synthetic reasoning"
        if requests:
            response["choices"][0]["message"]["tool_calls"][0]["id"] = "mock-http-" + str(
                len(requests)
            )
        requests.append(body)
        return httpx.Response(200, request=request, json=response)

    # Intercept HTTP, not litellm.completion: exercise real SDK imports, mapping and parsing.
    with (
        patch.dict(os.environ, {"DEEPSEEK_API_KEY": "synthetic-not-a-secret"}),
        patch.object(
            socket.socket, "connect", side_effect=AssertionError("network forbidden")
        ) as tcp,
        patch("socket.getaddrinfo", side_effect=AssertionError("DNS forbidden")) as dns,
        patch.object(httpx.Client, "send", http_send),
    ):
        import litellm

        litellm.telemetry = False
        execute(native_task, None, True)
        tcp.assert_not_called()
        dns.assert_not_called()
        assert len(requests) == 6
        system = requests[0]["messages"][0]["content"]
        spec = json.loads((native_task / "task.json").read_text())
        assert "6 model calls (including format corrections and submission)" in system
        assert str(spec["tokens"]) + " cumulative response-accounted total_tokens" in system
        assert "including cached input" in system and "output cap 8192" in system
        assert "60 seconds wall time" in system and "Budget exhaustion is failure" in system
        assert "No tool calls found" in requests[1]["messages"][-1]["content"]
        assert requests[2]["messages"][-1]["role"] == "tool"
        assert requests[2]["messages"][-1]["tool_call_id"] == "mock-http-1"
        assert requests[2]["messages"][-2]["reasoning_content"] == "synthetic reasoning"
        for ordinal, request in enumerate(requests[2:], start=2):
            progress = request["messages"][-1]["content"]
            assert f"calls used={ordinal}/6, remaining={6 - ordinal}" in progress
            assert "elapsed=" in progress and "/60 seconds" in progress
        feedback = requests[2]["messages"][-1]["content"]
        assert len(feedback) < 11000 and "elided_chars" in feedback
        assert "HEAD" in feedback and "TAIL" in feedback and "PRIVATE_MIDDLE" not in feedback
        assert "mcp" not in sys.modules
    native_state = json.loads((native_task / "status.json").read_text())
    native_trace = json.loads((native_task / "trajectory.json").read_text())
    first_output = next(m for m in native_trace["messages"] if m.get("role") == "tool")
    assert len(first_output["extra"]["raw_output"]) > 24000
    assert "PRIVATE_MIDDLE" in first_output["extra"]["raw_output"]
    assert "PRIVATE_MIDDLE" not in first_output["content"]
    assert native_state["status"] == "candidate_ready" and native_state["total_tokens"] == 60
    assert json.loads((native_task / "validation.json").read_text())["passed"]
    assert (
        native_task / "candidate/src/calc.py"
    ).read_bytes() == b"def add(a,b):\n    return a+b\n"
    assert all(
        e["finish_reason"] == "tool_calls"
        for e in native_state["calls"]
        if e["status"] == "response_received"
    )
    assert "task_budget_invalid" in call(
        "prepare",
        "--baseline",
        baseline,
        "--goal",
        root / "goal.md",
        "--profile",
        root / "profile.json",
        "--task",
        root / "invalid-cap",
        "--output-cap",
        "0",
        expected=1,
    )
    assert not (root / "invalid-cap").exists()

    limited = new_task("call-limit", "--model", "deepseek/deepseek-v4-flash", "--calls", "1")
    with (
        patch("litellm.completion", return_value=sdk_response([actions[2]])) as transport,
        patch("litellm.cost_calculator.completion_cost", return_value=0.01),
    ):
        try:
            execute(limited, None, True)
        except BoundaryError as exc:
            assert str(exc) == "agent_not_submitted"
        else:
            raise AssertionError("call exhaustion treated as submission")
        assert transport.call_count == 1
    limited_trace = json.loads((limited / "trajectory.json").read_text())
    assert limited_trace["info"]["exit_status"] == "LimitsExceeded"
    assert "calls used=1/1, remaining=0" in limited_trace["messages"][-2]["content"]
    assert json.loads((limited / "export.json").read_text())["status"] == "unchecked"
    assert not (limited / "candidate.json").exists() and not (limited / "validation.json").exists()

    draft = new_task("budget-draft", "--model", "deepseek/deepseek-v4-flash", "--tokens", "10")
    with (
        patch("litellm.completion", return_value=sdk_response([actions[2]])) as transport,
        patch("litellm.cost_calculator.completion_cost", return_value=0.01),
    ):
        try:
            execute(draft, None, True)
        except BoundaryError as exc:
            assert str(exc) == "model_budget_exhausted"
        else:
            raise AssertionError("exhausted draft accepted")
        assert transport.call_count == 1
    draft_state = json.loads((draft / "status.json").read_text())
    draft_export = json.loads((draft / "export.json").read_text())
    assert (
        draft_state["status"] == "interrupted" and draft_state["detail"] == "model_budget_exhausted"
    )
    assert (
        draft_export["status"] == "unchecked"
        and draft_export["identity"] == draft_state["export_identity"]
    )
    assert (draft / "exported/src/calc.py").read_bytes() == b"def add(a,b):\n    return a+b\n"
    assert (draft / "exported/src/calc.py").stat().st_mode & 0o777 == 0o600
    assert not (draft / "candidate.json").exists() and not (draft / "validation.json").exists()

    truncated = new_task("truncated", "--model", "deepseek/deepseek-v4-flash")
    with (
        patch(
            "litellm.completion",
            return_value=sdk_response(["touch SHOULD_NOT_RUN"], finish="length"),
        ),
        patch("litellm.cost_calculator.completion_cost", return_value=0.01),
        patch.object(CodingEnvironment, "execute") as dispatch,
    ):
        try:
            execute(truncated, None, True)
        except BoundaryError as exc:
            assert str(exc) == "model_output_truncated"
        else:
            raise AssertionError("truncated command accepted")
        dispatch.assert_not_called()
    truncated_state = json.loads((truncated / "status.json").read_text())
    assert truncated_state["detail"] == "model_output_truncated"
    assert truncated_state["calls"][-1]["finish_reason"] == "length"
    assert truncated_state["calls"][-1]["usage"]["total_tokens"] == 10
    assert not (truncated / "candidate.json").exists()

    for name, commands, status, detail in (
        ("mode", ["chmod +x src/calc.py", actions[-1]], "interrupted", "candidate_mode_changed"),
        (
            "scope",
            ["echo changed >> tests/test_calc.py", actions[-1]],
            "interrupted",
            "candidate_outside_scope",
        ),
        (
            "invalid",
            ["sed -i 's/return a-b/return a*b/' src/calc.py", actions[-1]],
            "validation_failed",
            None,
        ),
    ):
        target = new_task(name)
        action_path = root / (name + "-actions.json")
        action_path.write_text(json.dumps(commands))
        state = json.loads(call("run", "--task", target, "--actions", action_path, expected=1))
        assert state["status"] == status
        if detail:
            assert state["detail"] == detail
            export = json.loads((target / "export.json").read_text())
            assert (
                export["status"] == "unchecked" and export["identity"] == state["export_identity"]
            )
            assert not (target / "candidate.json").exists()
            if name == "mode":
                entry = next(e for e in export["entries"] if e["path"] == "src/calc.py")
                assert entry["mode"] == 0o755
                assert (target / "exported/src/calc.py").read_bytes() == original_bytes
                assert (target / "exported/src/calc.py").stat().st_mode & 0o777 == 0o600
        else:
            assert not json.loads((target / "validation.json").read_text())["passed"]

    target = new_task("cancel")
    slow = root / "slow-actions.json"
    slow.write_text(json.dumps(["sleep 50"]))
    process = subprocess.Popen(
        [sys.executable, str(cli), "run", "--task", str(target), "--actions", str(slow)],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        state = json.loads((target / "status.json").read_text())
        if state.get("container_id"):
            break
        assert process.poll() is None
        time.sleep(0.1)
    else:
        process.kill()
        raise AssertionError("worker never started")
    assert "active_task_use_ctrl_c_to_cancel" in call("cleanup", "--task", target, expected=1)
    process.send_signal(signal.SIGTERM)
    output, errors = process.communicate(timeout=30)
    assert json.loads(output)["detail"] == "watchdog_or_cancel", (output, errors)
    call("cleanup", "--task", target)
    env = {**os.environ, "DOCKER_HOST": "unix:///run/labpilot-docker.sock"}
    observed = subprocess.run(
        [
            "/usr/bin/docker",
            "ps",
            "-aq",
            "--filter",
            "label=labpilot.coding.task=" + state["task_id"],
        ],
        capture_output=True,
        check=True,
        env=env,
    )
    assert not observed.stdout.strip()
    result = {
        "status": "passed",
        "provider_calls": 0,
        "fixture_steps": 5,
        "replayed_diff": True,
        "independent_validation": True,
        "original_unchanged": True,
        "no_implicit_live_call": True,
        "terminal_run_not_restarted": True,
        "candidate_tamper_rejected": True,
        "scope_rejected": True,
        "mode_rejection_retains_unchecked_export": True,
        "mode_policy_disclosed": True,
        "independent_failure_is_terminal": True,
        "cancel_cleanup_verified": True,
        "docker_endpoint": endpoint_check(),
        "budget": budget_check(),
        "native_sdk_boundary": native_boundary_check(),
        "mock_http_transport_docker_replay_and_validation": True,
        "sdk_http_path_without_mcp_dependency": True,
        "upstream_tool_observation_truncated_raw_audit_retained": True,
        "budget_failure_retains_unsubmitted_unchecked_draft": True,
        "frozen_budgets_disclosed": True,
        "sdk_progress_counts_format_correction": True,
        "last_allowed_call_can_submit_and_validate": True,
        "call_limit_without_submit_stays_failure": True,
        "real_network_forbidden": True,
        "truncated_sdk_command_not_dispatched": True,
    }
    (root / "result.json").write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps(result))


if __name__ == "__main__":
    main()
