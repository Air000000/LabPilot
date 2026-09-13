from __future__ import annotations

import argparse
import json
import urllib.error
import urllib.request
from typing import Any
from uuid import UUID

_DEFAULT_GOAL = (
    "Inspect this repository and propose a small parameter-sweep experiment that can "
    "improve validation accuracy to at least 0.92. Use at most four Trials, keep "
    "execution CPU-only, and define deterministic validation so only results meeting "
    "the target are eligible for selection."
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Exercise the real competition Agent -> Proposal -> Verify path."
    )
    parser.add_argument("--project-id", type=UUID, required=True)
    parser.add_argument("--snapshot-id", type=UUID, required=True)
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--goal", default=_DEFAULT_GOAL)
    return parser.parse_args()


def request_json(method: str, url: str, body: dict[str, Any] | None = None) -> dict[str, Any]:
    data = None
    headers: dict[str, str] = {}
    if body is not None:
        data = json.dumps(body, separators=(",", ":")).encode("utf-8")
        headers["Content-Type"] = "application/json"
    request = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(request, timeout=180) as response:
            payload = response.read()
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise SystemExit(f"HTTP {exc.code} {url}: {detail}") from exc
    except urllib.error.URLError as exc:
        raise SystemExit(f"request failed for {url}: {exc.reason}") from exc
    decoded = json.loads(payload)
    if type(decoded) is not dict:
        raise SystemExit(f"unexpected JSON response from {url}")
    return decoded


def main() -> int:
    args = parse_args()
    base_url = args.base_url.rstrip("/")

    created = request_json(
        "POST",
        f"{base_url}/projects/{args.project_id}/agent-runs",
        {
            "repository_snapshot_id": str(args.snapshot_id),
            "goal": args.goal,
            "budget": {
                "max_steps": 10,
                "max_model_calls": 10,
                "max_wall_clock_seconds": 120,
                "max_tokens": 20_000,
            },
        },
    )
    agent_run_id = created.get("agent_run_id")
    row_version = created.get("row_version")
    if type(agent_run_id) is not str or type(row_version) is not int:
        raise SystemExit("AgentRun create response is missing identity/version")

    advanced = request_json(
        "POST",
        f"{base_url}/agent-runs/{agent_run_id}/advance",
        {"expected_row_version": row_version},
    )
    proposal_revision_id = advanced.get("produced_proposal_revision_id")
    if advanced.get("status") != "completed" or type(proposal_revision_id) is not str:
        print(json.dumps(advanced, indent=2, sort_keys=True))
        raise SystemExit("Agent did not complete with a ProposalRevision")

    verification = request_json(
        "POST",
        f"{base_url}/proposal-revisions/{proposal_revision_id}/verify",
    )
    result = verification.get("result")
    if type(result) is not dict or result.get("passed") is not True:
        print(json.dumps(verification, indent=2, sort_keys=True))
        raise SystemExit("Agent Proposal failed deterministic verification")

    print(f"COMPETITION_AGENT_RUN_ID={agent_run_id}")
    print(f"COMPETITION_PROPOSAL_REVISION_ID={proposal_revision_id}")
    print(
        "COMPETITION_PROPOSAL_VERIFICATION_ID="
        f"{verification['proposal_verification_id']}"
    )
    print(f"COMPETITION_AGENT_TERMINATION={advanced.get('termination_reason')}")
    print("COMPETITION_PROPOSAL_VERIFICATION=PASS")
    print("COMPETITION_AGENT_PROPOSAL=PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
