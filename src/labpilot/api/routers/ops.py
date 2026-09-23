"""Small read-only index for finding persisted interview evidence."""

from html import escape
from typing import Annotated
from urllib.parse import urlencode
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse
from sqlalchemy import select

from labpilot.agent_runtime.models import AgentRunRow, AgentStepRow
from labpilot.api.dependencies import ApplicationServices, get_application_services
from labpilot.api.errors import ServiceUnavailableError
from labpilot.execution.models import BatchRow, TrialRow
from labpilot.experiments.proposal_models import (
    ExperimentVersionRow,
    ProposalVerificationRow,
)
from labpilot.results_validation.models import BatchSummaryRevisionRow
from labpilot.tools_policy.models import ToolCallRow

router = APIRouter()


def _link(path: str, label: str) -> str:
    return f'<a href="{escape(path, quote=True)}">{escape(label)}</a>'


@router.get("/ops", include_in_schema=False, response_class=HTMLResponse)
async def ops(
    services: Annotated[ApplicationServices, Depends(get_application_services)],
) -> HTMLResponse:
    if services.ops_sessions is None:
        raise ServiceUnavailableError("operator audit is not configured")
    cards: list[str] = []
    async with services.ops_sessions() as session:
        runs = (
            await session.scalars(
                select(AgentRunRow).order_by(AgentRunRow.created_at.desc()).limit(12)
            )
        ).all()
        for run in runs:
            links = [_link(f"/agent-runs/{run.agent_run_id}/trace", "Agent 步骤")]
            operator_params = {"run": str(run.agent_run_id)}
            phase = {"failed": "Agent 失败", "completed": "Agent 已结束 / 未生成方案"}.get(
                run.status, "Agent 运行中"
            )
            tool_names = (
                await session.scalars(
                    select(ToolCallRow.tool_name)
                    .join(AgentStepRow, AgentStepRow.tool_call_id == ToolCallRow.tool_call_id)
                    .where(AgentStepRow.agent_run_id == run.agent_run_id)
                    .order_by(AgentStepRow.ordinal)
                )
            ).all()
            details = [
                f"状态: {escape(run.status)}",
                f"模型: {escape(run.provider_name)} / {escape(run.model_name)}",
                f"模型调用: {run.total_model_calls}",
            ]
            if tool_names:
                details.append("仓库工具: " + " → ".join(map(escape, tool_names)))
            revision_id = run.produced_proposal_revision_id
            if revision_id is not None:
                phase = "方案已生成 / 待验证"
                links.append(_link(f"/proposal-revisions/{revision_id}", "Proposal"))
                verification = await session.scalar(
                    select(ProposalVerificationRow)
                    .where(ProposalVerificationRow.proposal_revision_id == revision_id)
                    .order_by(ProposalVerificationRow.created_at.desc())
                    .limit(1)
                )
                if verification is not None:
                    operator_params["verification"] = str(verification.proposal_verification_id)
                    phase = "验证通过 / 待批准" if verification.passed else "验证未通过"
                    links.append(
                        _link(
                            f"/proposal-verifications/{verification.proposal_verification_id}",
                            "确定性验证",
                        )
                    )
                version = await session.scalar(
                    select(ExperimentVersionRow)
                    .where(ExperimentVersionRow.proposal_revision_id == revision_id)
                    .order_by(ExperimentVersionRow.created_at.desc())
                    .limit(1)
                )
                if version is not None:
                    operator_params["version"] = str(version.experiment_version_id)
                    phase = "已批准 / 待执行"
                    links.append(
                        _link(
                            f"/experiment-versions/{version.experiment_version_id}",
                            "批准后的版本",
                        )
                    )
                    batch = await session.scalar(
                        select(BatchRow)
                        .where(BatchRow.experiment_version_id == version.experiment_version_id)
                        .order_by(BatchRow.created_at.desc())
                        .limit(1)
                    )
                    if batch is not None:
                        operator_params["batch"] = str(batch.batch_id)
                        phase = "执行中"
                        links.append(_link(f"/batches/{batch.batch_id}", "最近 Batch"))
                        trials = (
                            await session.scalars(
                                select(TrialRow)
                                .where(TrialRow.batch_id == batch.batch_id)
                                .order_by(TrialRow.trial_ordinal)
                            )
                        ).all()
                        links.extend(
                            _link(
                                f"/trials/{trial.trial_id}/result",
                                f"Trial {trial.trial_ordinal} 结果",
                            )
                            for trial in trials
                        )
                        summary = await session.scalar(
                            select(BatchSummaryRevisionRow)
                            .where(BatchSummaryRevisionRow.batch_id == batch.batch_id)
                            .order_by(BatchSummaryRevisionRow.revision_number.desc())
                            .limit(1)
                        )
                        if summary is not None:
                            phase = "完整链路已完成"
                            links.append(_link(f"/batches/{batch.batch_id}/summary", "结果汇总"))
                            for name, value in summary.aggregates_json.items():
                                if isinstance(value, dict) and "mean" in value:
                                    details.append(f"{escape(name)}: {escape(str(value['mean']))}")
            if run.source_batch_summary_revision_id is not None:
                source = await session.get(
                    BatchSummaryRevisionRow, run.source_batch_summary_revision_id
                )
                if source is not None:
                    details.append(f"接续上一轮 Summary 修订 {source.revision_number}")
                    links.insert(
                        0,
                        _link(
                            f"/ops/summary-revisions/{source.batch_summary_revision_id}",
                            "绑定的结果修订",
                        ),
                    )
            links.append(_link("/operator?" + urlencode(operator_params), "在操作台查看 / 继续"))
            details.insert(0, f"阶段: {phase}")
            local_time = run.created_at.astimezone().strftime("%Y-%m-%d %H:%M %Z")
            cards.append(
                "<article>"
                f"<small>{escape(local_time)}</small>"
                f"<h2>{escape(run.goal)}</h2>"
                f"<p>{' · '.join(details)}</p>"
                f"<nav>{' '.join(links)}</nav>"
                "</article>"
            )
    body = "".join(cards) or "<p>还没有 Agent 运行记录。</p>"
    return HTMLResponse(
        "<!doctype html><html lang='zh-CN'><meta charset='utf-8'>"
        "<meta name='viewport' content='width=device-width,initial-scale=1'>"
        "<title>LabPilot · 运行记录</title>"
        "<style>body{font:16px/1.6 system-ui,sans-serif;max-width:950px;"
        "margin:40px auto;padding:0 20px;color:#172b40}"
        "a{color:#075985}article{border:1px solid #d9e4eb;border-radius:12px;"
        "padding:20px;margin:18px 0}"
        "article h2{font-size:1.2rem;margin:.3em 0;overflow-wrap:anywhere}small{color:#567}"
        "nav{display:flex;flex-wrap:wrap;gap:8px}nav a{padding:5px 10px;"
        "background:#edf6fb;border-radius:6px}"
        "</style><main><h1>运行记录</h1><p>最近 12 次 Agent 运行 · 只读审计入口 · "
        + _link("/operator", "返回实验操作台")
        + "</p>"
        + body
        + "</main></html>"
    )


@router.get("/ops/summary-revisions/{revision_id}", include_in_schema=False)
async def ops_summary_revision(
    revision_id: UUID,
    services: Annotated[ApplicationServices, Depends(get_application_services)],
) -> JSONResponse:
    if services.ops_sessions is None:
        raise ServiceUnavailableError("operator audit is not configured")
    async with services.ops_sessions() as session:
        summary = await session.get(BatchSummaryRevisionRow, revision_id)
    if summary is None:
        raise HTTPException(status_code=404)
    return JSONResponse(
        {
            "revision_id": str(summary.batch_summary_revision_id),
            "batch_id": str(summary.batch_id),
            "revision_number": summary.revision_number,
            "summary_protocol_version": summary.summary_protocol_version,
            "included": summary.included_result_refs_json,
            "excluded": summary.excluded_reasons_json,
            "warnings": summary.warning_json,
            "aggregates": summary.aggregates_json,
            "created_at": summary.created_at.isoformat(),
        }
    )
