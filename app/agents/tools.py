"""Execution engine tools. Gated tools never run without an approved Approval row."""

import json
import re
import urllib.parse
import urllib.request

from sqlalchemy.orm import Session

from ..db import Artifact, Device
from ..providers import router as provider_router

# Tools with external reach or side effects: each call needs his explicit approval first.
GATED_TOOLS = {"search", "code_run", "file_write", "project_gen"}
# Pure in-model reasoning, no external effect: run automatically inside a run.
INTERNAL_TOOLS = {"answer", "analyze"}
ALL_TOOLS = GATED_TOOLS | INTERNAL_TOOLS

SEARCH_TIMEOUT = 15
SEARCH_MAX_RESULTS = 5
MAX_ARTIFACT_BYTES = 200_000


def _duckduckgo(query: str) -> list[dict]:
    url = "https://duckduckgo.com/html/?" + urllib.parse.urlencode({"q": query})
    req = urllib.request.Request(url, headers={"User-Agent": "MAHDI-AI/1.0"})
    with urllib.request.urlopen(req, timeout=SEARCH_TIMEOUT) as resp:
        html = resp.read().decode("utf-8", "replace")
    results = []
    for m in re.finditer(
        r'class="result__a"[^>]*href="([^"]+)"[^>]*>(.*?)</a>', html, re.DOTALL
    ):
        link = m.group(1)
        title = re.sub(r"<[^>]+>", "", m.group(2)).strip()
        uddg = re.search(r"uddg=([^&]+)", link)
        if uddg:
            link = urllib.parse.unquote(uddg.group(1))
        if title:
            results.append({"title": title, "url": link})
        if len(results) >= SEARCH_MAX_RESULTS:
            break
    return results


FLASK_TEMPLATE = {
    "app.py": 'from flask import Flask, render_template\n\napp = Flask(__name__)\n\n\n@app.get("/")\ndef home():\n    return render_template("index.html", name="{name}")\n\n\nif __name__ == "__main__":\n    app.run(debug=True)\n',
    "requirements.txt": "flask>=3.0\n",
    "templates/index.html": "<!doctype html>\n<html lang=\"ar\" dir=\"rtl\">\n<head><meta charset=\"utf-8\"><title>{name}</title></head>\n<body><h1>{name}</h1><p>موقعك جاهز للتعديل.</p></body>\n</html>\n",
    "README.md": "# {name}\n\nتشغيل:\n\n    pip install -r requirements.txt\n    python app.py\n",
}


async def execute(tool: str, args: dict, device: Device, db: Session, goal: str) -> dict:
    """Run one tool. Gated tools must only reach here after approval."""
    if tool in ("answer", "analyze"):
        instruction = str(args.get("instruction") or goal)
        system = (
            "أنت MAHDI AI. نفّذ الخطوة التالية ضمن هدف المستخدم، وجاوب بالعربي الواضح.\n"
            f"هدف المستخدم: {goal}"
        )
        text, provider = await provider_router.complete(
            [{"role": "user", "content": instruction}], system
        )
        return {"text": text, "provider": provider}

    if tool == "search":
        query = str(args.get("query") or goal)
        try:
            results = _duckduckgo(query)
        except Exception as e:  # noqa: BLE001
            return {"error": f"search failed: {e}", "results": []}
        return {"query": query, "results": results}

    if tool == "code_run":
        from ..routers.code import CodeRunIn, run_code

        out = run_code(CodeRunIn(code=str(args.get("code") or "")), device)
        return out.model_dump()

    if tool == "file_write":
        name = str(args.get("name") or "file.txt")[:200]
        content = str(args.get("content") or "")
        if len(content.encode("utf-8")) > MAX_ARTIFACT_BYTES:
            return {"error": "content too large"}
        artifact = Artifact(
            device_id=device.id, kind="file", name=name, content=content
        )
        db.add(artifact)
        db.flush()
        return {"artifact_id": artifact.id, "name": name}

    if tool == "project_gen":
        kind = str(args.get("kind") or "flask_site")
        name = re.sub(r"[^A-Za-z0-9_-]", "", str(args.get("name") or "myproject"))[:60] or "myproject"
        if kind != "flask_site":
            return {"error": f"unsupported project kind: {kind} (available: flask_site)"}
        ids = []
        for path, tpl in FLASK_TEMPLATE.items():
            artifact = Artifact(
                device_id=device.id,
                kind="project",
                name=f"{name}/{path}",
                content=tpl.format(name=name),
            )
            db.add(artifact)
            db.flush()
            ids.append(artifact.id)
        return {"project": name, "kind": kind, "artifact_ids": ids}

    return {"error": f"unknown tool: {tool}"}


def validate_plan(raw) -> list[dict]:
    """Keep only well-formed steps with known tools."""
    steps = []
    if not isinstance(raw, list):
        return steps
    for item in raw[:10]:
        if not isinstance(item, dict):
            continue
        tool = item.get("tool")
        if tool not in ALL_TOOLS:
            continue
        args = item.get("args")
        steps.append(
            {
                "tool": tool,
                "args": args if isinstance(args, dict) else {},
                "title": str(item.get("title") or tool)[:200],
            }
        )
    return steps


def dumps(obj) -> str:
    return json.dumps(obj, ensure_ascii=False)


def loads(text: str, default):
    try:
        return json.loads(text) if text else default
    except (TypeError, ValueError):
        return default
