"""Turn a user goal into an ordered tool plan via the LLM, with a safe fallback."""

import json
import re

from ..providers import router as provider_router
from .tools import validate_plan

PLAN_SYSTEM = (
    "أنت مخطط مهام في MAHDI AI. حوّل هدف المستخدم إلى خطة خطوات JSON فقط، بدون أي نص آخر.\n"
    "الأدوات المتاحة:\n"
    '- answer: إجابة نصية مباشرة. args: {"instruction": "..."}\n'
    '- analyze: تحليل نص. args: {"instruction": "..."}\n'
    '- search: بحث ويب (يحتاج موافقة). args: {"query": "..."}\n'
    '- code_run: تشغيل كود بايثون (يحتاج موافقة). args: {"code": "..."}\n'
    '- file_write: إنشاء ملف (يحتاج موافقة). args: {"name": "...", "content": "..."}\n'
    '- project_gen: توليد مشروع flask_site (يحتاج موافقة). args: {"name": "...", "kind": "flask_site"}\n'
    'أرجع قائمة JSON: [{"tool": "...", "args": {...}, "title": "وصف قصير بالعربي"}]\n'
    "أقل عدد ممكن من الخطوات، وآخر خطوة لازم تكون answer تلخّص النتيجة للمستخدم."
)


async def make_plan(goal: str) -> list[dict]:
    fallback = [{"tool": "answer", "args": {"instruction": goal}, "title": "إجابة مباشرة"}]
    try:
        text, _ = await provider_router.complete(
            [{"role": "user", "content": f"هدف المستخدم: {goal}"}], PLAN_SYSTEM
        )
    except Exception:  # noqa: BLE001
        return fallback
    m = re.search(r"\[.*\]", text, re.DOTALL)
    if not m:
        return fallback
    try:
        steps = validate_plan(json.loads(m.group(0)))
    except ValueError:
        return fallback
    return steps or fallback
