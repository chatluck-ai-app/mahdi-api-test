"""Agent runs + approval gate. No gated tool executes before his explicit approve."""

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from ..agents import planner, tools
from ..auth import current_device
from ..db import AgentRun, Approval, Artifact, Device, get_db

router = APIRouter(prefix="/v1", tags=["agents"])


class RunIn(BaseModel):
    goal: str


class DecideIn(BaseModel):
    decision: str  # "approve" | "reject"


def _run_out(db: Session, run: AgentRun) -> dict:
    approvals = (
        db.query(Approval)
        .filter(Approval.run_id == run.id)
        .order_by(Approval.id)
        .all()
    )
    return {
        "id": run.id,
        "goal": run.goal,
        "status": run.status,
        "plan": tools.loads(run.plan_json, []),
        "log": tools.loads(run.result, []),
        "approvals": [
            {
                "id": a.id,
                "step_index": a.step_index,
                "kind": a.kind,
                "title": a.title,
                "args": tools.loads(a.args_json, {}),
                "status": a.status,
            }
            for a in approvals
        ],
        "created_at": run.created_at,
    }


async def _advance(db: Session, device: Device, run: AgentRun) -> None:
    """Execute steps until the next gated one, then stop at awaiting_approval."""
    steps = tools.loads(run.plan_json, [])
    log = tools.loads(run.result, [])
    pending = (
        db.query(Approval)
        .filter(Approval.run_id == run.id, Approval.status == "pending")
        .count()
    )
    idx = len(log) + pending
    run.status = "running"
    while idx < len(steps):
        step = steps[idx]
        if step["tool"] in tools.GATED_TOOLS:
            db.add(
                Approval(
                    device_id=device.id,
                    run_id=run.id,
                    step_index=idx,
                    kind=step["tool"],
                    title=step["title"],
                    args_json=tools.dumps(step["args"]),
                )
            )
            run.status = "awaiting_approval"
            run.result = tools.dumps(log)
            db.commit()
            return
        try:
            output = await tools.execute(step["tool"], step["args"], device, db, run.goal)
        except Exception as e:  # noqa: BLE001
            run.status = "failed"
            run.result = tools.dumps(log + [{"step": idx, "error": str(e)}])
            db.commit()
            return
        log.append({"step": idx, "tool": step["tool"], "title": step["title"], "output": output})
        idx += 1
    run.status = "done"
    run.result = tools.dumps(log)
    db.commit()


@router.post("/agents/run")
async def start_run(body: RunIn, device: Device = Depends(current_device), db: Session = Depends(get_db)):
    goal = body.goal.strip()
    if not goal:
        raise HTTPException(status_code=422, detail="empty goal")
    plan = await planner.make_plan(goal)
    run = AgentRun(device_id=device.id, goal=goal, status="planning", plan_json=tools.dumps(plan))
    db.add(run)
    db.flush()
    await _advance(db, device, run)
    db.refresh(run)
    return _run_out(db, run)


@router.get("/agents/runs")
def list_runs(device: Device = Depends(current_device), db: Session = Depends(get_db)):
    runs = (
        db.query(AgentRun)
        .filter(AgentRun.device_id == device.id)
        .order_by(AgentRun.id.desc())
        .limit(50)
        .all()
    )
    return {"runs": [_run_out(db, r) for r in runs]}


@router.get("/agents/runs/{run_id}")
def get_run(run_id: int, device: Device = Depends(current_device), db: Session = Depends(get_db)):
    run = db.get(AgentRun, run_id)
    if run is None or run.device_id != device.id:
        raise HTTPException(status_code=404, detail="run not found")
    return _run_out(db, run)


@router.post("/agents/runs/{run_id}/cancel")
def cancel_run(run_id: int, device: Device = Depends(current_device), db: Session = Depends(get_db)):
    run = db.get(AgentRun, run_id)
    if run is None or run.device_id != device.id:
        raise HTTPException(status_code=404, detail="run not found")
    if run.status in ("done", "failed", "canceled"):
        raise HTTPException(status_code=409, detail=f"run already {run.status}")
    run.status = "canceled"
    db.query(Approval).filter(
        Approval.run_id == run.id, Approval.status == "pending"
    ).update({"status": "canceled"}, synchronize_session=False)
    db.commit()
    return {"status": "canceled"}


@router.get("/approvals")
def list_approvals(
    status: str = "pending",
    device: Device = Depends(current_device),
    db: Session = Depends(get_db),
):
    q = db.query(Approval).filter(Approval.device_id == device.id)
    if status != "all":
        q = q.filter(Approval.status == status)
    rows = q.order_by(Approval.id.desc()).limit(50).all()
    return {
        "approvals": [
            {
                "id": a.id,
                "run_id": a.run_id,
                "kind": a.kind,
                "title": a.title,
                "args": tools.loads(a.args_json, {}),
                "status": a.status,
                "created_at": a.created_at,
            }
            for a in rows
        ]
    }


@router.post("/approvals/{approval_id}/decide")
async def decide(
    approval_id: int,
    body: DecideIn,
    device: Device = Depends(current_device),
    db: Session = Depends(get_db),
):
    approval = db.get(Approval, approval_id)
    if approval is None or approval.device_id != device.id:
        raise HTTPException(status_code=404, detail="approval not found")
    if approval.status != "pending":
        raise HTTPException(status_code=409, detail=f"already {approval.status}")
    if body.decision not in ("approve", "reject"):
        raise HTTPException(status_code=422, detail="decision must be approve or reject")

    run = db.get(AgentRun, approval.run_id)
    approval.decided_at = datetime.now(timezone.utc)

    if body.decision == "reject":
        approval.status = "rejected"
        if run is not None:
            run.status = "canceled"
        db.commit()
        return {"status": "rejected", "run_status": "canceled"}

    approval.status = "approved"
    try:
        output = await tools.execute(
            approval.kind, tools.loads(approval.args_json, {}), device, db, run.goal if run else ""
        )
    except Exception as e:  # noqa: BLE001
        approval.result_json = tools.dumps({"error": str(e)})
        if run is not None:
            run.status = "failed"
            run.result = tools.dumps(
                tools.loads(run.result, [])
                + [{"step": approval.step_index, "tool": approval.kind, "error": str(e)}]
            )
        db.commit()
        return {"status": "approved", "run_status": "failed", "output": {"error": str(e)}}

    approval.result_json = tools.dumps(output)
    if run is not None:
        log = tools.loads(run.result, [])
        log.append(
            {"step": approval.step_index, "tool": approval.kind, "title": approval.title, "output": output}
        )
        run.result = tools.dumps(log)
        db.flush()
        await _advance(db, device, run)
    db.commit()
    db.refresh(approval)
    return {
        "status": "approved",
        "run_status": run.status if run else None,
        "output": output,
    }


@router.get("/artifacts")
def list_artifacts(device: Device = Depends(current_device), db: Session = Depends(get_db)):
    rows = (
        db.query(Artifact)
        .filter(Artifact.device_id == device.id)
        .order_by(Artifact.id.desc())
        .limit(100)
        .all()
    )
    return {
        "artifacts": [
            {"id": a.id, "kind": a.kind, "name": a.name, "created_at": a.created_at}
            for a in rows
        ]
    }


@router.get("/artifacts/{artifact_id}")
def get_artifact(
    artifact_id: int, device: Device = Depends(current_device), db: Session = Depends(get_db)
):
    artifact = db.get(Artifact, artifact_id)
    if artifact is None or artifact.device_id != device.id:
        raise HTTPException(status_code=404, detail="artifact not found")
    return {
        "id": artifact.id,
        "kind": artifact.kind,
        "name": artifact.name,
        "content": artifact.content,
        "created_at": artifact.created_at,
    }
