"""Admin summary + minimal dashboard. Disabled unless ADMIN_TOKEN is set.
Every admin route requires the valid token in the X-Admin-Token header;
the token never appears in URLs, the app, or the repo."""

from fastapi import APIRouter, Header, HTTPException
from fastapi.responses import HTMLResponse
from sqlalchemy import func
from sqlalchemy.orm import Session
from fastapi import Depends

from .. import config
from ..db import AgentRun, Approval, Artifact, Device, Message, get_db

router = APIRouter(prefix="/v1/admin", tags=["admin"])


def _check(x_admin_token: str) -> None:
    if not config.ADMIN_TOKEN or x_admin_token != config.ADMIN_TOKEN:
        raise HTTPException(status_code=404, detail="not found")


DASHBOARD_HTML = """<!doctype html>
<html lang="ar" dir="rtl"><head><meta charset="utf-8"><title>MAHDI Admin</title>
<style>
body{font-family:sans-serif;background:#0e0e10;color:#eee;max-width:720px;margin:2rem auto;padding:0 1rem}
.card{background:#1c1c22;border-radius:12px;padding:1rem;margin:.5rem 0}
.n{font-size:2rem;color:#d4af37}
input,button{padding:.5rem;border-radius:8px;border:1px solid #444;background:#141418;color:#eee}
.bad{color:#e06060}.ok{color:#6fcf6f}
</style></head><body>
<h2>MAHDI AI — لوحة الإدارة</h2>
<div class="card"><input id="t" type="password" placeholder="Admin token"> <button onclick="load()">عرض</button></div>
<div id="out"></div>
<script>
async function load(){
  const r = await fetch('/v1/admin/summary', {headers: {'X-Admin-Token': document.getElementById('t').value}});
  const out = document.getElementById('out');
  if(!r.ok){out.innerHTML='<div class="card bad">رفض الوصول</div>';return;}
  const d = await r.json();
  out.innerHTML = `<div class="card"><span class="n">${d.devices}</span> جهاز ·
    <span class="n">${d.messages}</span> رسالة · <span class="n">${d.runs}</span> مهمة وكلاء ·
    <span class="n">${d.approvals_pending}</span> موافقة معلقة · <span class="n">${d.artifacts}</span> ملف مولّد</div>`
    + '<h3>أحدث المهام</h3>' + d.recent_runs.map(r=>
      `<div class="card">#${r.id} — ${r.goal} <b class="${r.status=='done'?'ok':'bad'}">${r.status}</b></div>`).join('');
}
</script></body></html>"""


@router.get("/dashboard", response_class=HTMLResponse)
def dashboard(x_admin_token: str = Header(default="")):
    _check(x_admin_token)
    return DASHBOARD_HTML


@router.get("/summary")
def summary(x_admin_token: str = Header(default=""), db: Session = Depends(get_db)):
    _check(x_admin_token)
    recent_runs = (
        db.query(AgentRun).order_by(AgentRun.id.desc()).limit(10).all()
    )
    return {
        "devices": db.query(func.count(Device.id)).scalar(),
        "messages": db.query(func.count(Message.id)).scalar(),
        "runs": db.query(func.count(AgentRun.id)).scalar(),
        "approvals_pending": db.query(func.count(Approval.id))
        .filter(Approval.status == "pending")
        .scalar(),
        "artifacts": db.query(func.count(Artifact.id)).scalar(),
        "recent_runs": [
            {"id": r.id, "goal": r.goal[:80], "status": r.status, "created_at": r.created_at}
            for r in recent_runs
        ],
    }
