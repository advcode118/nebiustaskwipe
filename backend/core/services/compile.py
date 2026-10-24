import base64
import json
import re
from pathlib import Path

from django.conf import settings

from core.models import Skill
from core.services.stages import write_stages_from_events
from core.services.split import (
    build_demonstration,
    fields_from_events,
    merge_model_split,
    start_from_events,
)

COMPILE_PROMPT = """You compile a Taskwipe skill from one demonstration.

Split EVERY filled value into exactly one of two piles. This split is the product.

1. YOURS — stable facts about the person: name, email, phone, university, course, home/shipping address, delivery notes they always use.
2. THIS RUN — values that will be different the next time they do the task: subject lines, titles, "why I want this", message bodies, dates, tracking ids, search queries.

Do not put a university email in THIS RUN. Do not put a subject line or motivation essay in YOURS.
Never save passwords, card numbers, CVV, tokens.

Also emit a goal-level playbook (what to accomplish, not CSS selectors) and say what kind of page this is.

Return JSON only, no markdown:
{
  "skill": {
    "name": "short human name",
    "goal": "one paragraph of success, stopping before Submit/Pay unless already submitted in the demo",
    "playbook": ["full list of goal-level steps", "..."],
    "page_kind": "e.g. university internship application form with identity fields",
    "start_url": "the url where the demonstration began"
  },
  "yours": [
    {"key": "email", "label": "University email", "value": "...", "category": "identity", "reason": "stable identity"}
  ],
  "this_run": [
    {"name": "why", "type": "string", "label": "Why this internship", "value_from_demo": "...", "reason": "written for this application"}
  ]
}

category must be one of: identity, shipping, work, other.
"""


def _client():
    if not settings.NEBIUS_API_KEY and not settings.ANTHROPIC_API_KEY:
        raise RuntimeError("NEBIUS_API_KEY is missing. Put it in backend/.env")

    class _Messages:
        @staticmethod
        def create(**kwargs):
            from core.services.llm import complete

            return complete(kwargs)

    class _Facade:
        messages = _Messages()

    return _Facade()


def _strip_secrets(events):
    cleaned = []
    for event in events or []:
        item = dict(event)
        if item.get("is_secret"):
            item["value"] = None
        cleaned.append(item)
    return cleaned


def _parse_json(text):
    blob = text.strip()
    fenced = re.search(r"```(?:json)?\s*([\s\S]*?)```", blob)
    if fenced:
        blob = fenced.group(1).strip()
    return json.loads(blob)


def _save_screenshots(skill, screenshots):
    paths = []
    if not screenshots:
        skill.screenshot_paths = []
        return
    folder = Path(settings.MEDIA_ROOT) / "skills" / str(skill.id)
    folder.mkdir(parents=True, exist_ok=True)
    for index, shot in enumerate(screenshots[:4]):
        data = shot.get("data") or ""
        if "," in data:
            data = data.split(",", 1)[1]
        if not data:
            continue
        try:
            raw = base64.b64decode(data)
        except Exception:
            continue
        name = f"{index}.jpg"
        (folder / name).write_bytes(raw)
        paths.append(f"skills/{skill.id}/{name}")
    skill.screenshot_paths = paths


def _load_screenshot_blocks(skill, limit=2):
    blocks = []
    for relative in (skill.screenshot_paths or [])[:limit]:
        path = Path(settings.MEDIA_ROOT) / relative
        if not path.exists():
            continue
        blocks.append(
            {
                "type": "image",
                "source": {
                    "type": "base64",
                    "media_type": "image/jpeg",
                    "data": base64.b64encode(path.read_bytes()).decode("ascii"),
                },
            }
        )
    return blocks


def compile_recording(recording, screenshots=None):
    events = _strip_secrets(recording.events)
    start_url, site_host = start_from_events(events)
    heuristic_yours, heuristic_run = fields_from_events(events)

    content = [
        {
            "type": "text",
            "text": COMPILE_PROMPT
            + "\n\nUser intent note:\n"
            + (recording.intent or "(none)")
            + "\n\nStart URL: "
            + (start_url or "(unknown)")
            + "\n\nDemonstration events JSON:\n"
            + json.dumps(events)[:60000],
        }
    ]
    for shot in (screenshots or [])[:4]:
        data = shot.get("data") or ""
        if "," in data:
            data = data.split(",", 1)[1]
        if not data:
            continue
        content.append(
            {
                "type": "image",
                "source": {
                    "type": "base64",
                    "media_type": shot.get("media_type") or "image/png",
                    "data": data,
                },
            }
        )

    client = _client()
    response = client.messages.create(
        model=settings.COMPILE_MODEL,
        max_tokens=4096,
        messages=[{"role": "user", "content": content}],
    )
    text = "".join(block.text for block in response.content if getattr(block, "type", "") == "text")
    parsed = _parse_json(text)
    skill_data = parsed.get("skill") or {}
    name = (skill_data.get("name") or "Untitled skill").strip()
    goal = (skill_data.get("goal") or recording.intent or "Repeat the demonstrated task.").strip()
    playbook = skill_data.get("playbook") or []
    if not isinstance(playbook, list):
        playbook = [str(playbook)]
    playbook = [
        "Stop before Submit or Pay and wait for the human"
        if re.search(r"\b(submit|place order|pay)\b", str(step), re.I)
        else step
        for step in playbook
    ]
    page_kind = (skill_data.get("page_kind") or "").strip()
    model_start = (skill_data.get("start_url") or "").strip()
    if model_start:
        start_url = model_start
        from urllib.parse import urlparse

        site_host = urlparse(start_url).netloc or site_host

    yours, this_run = merge_model_split(
        heuristic_yours,
        heuristic_run,
        parsed.get("yours") or [],
        parsed.get("this_run") or parsed.get("inputs") or [],
    )
    demonstration = build_demonstration(events, yours, this_run)

    skill = Skill.objects.create(
        name=name[:200],
        goal=goal,
        playbook=playbook,
        inputs_schema=this_run,
        memory_keys=[item["key"] for item in yours],
        source_events=events,
        demonstration=demonstration,
        start_url=start_url,
        site_host=site_host,
        page_kind=page_kind,
        split_yours=yours,
        split_this_run=this_run,
        ready=False,
    )
    _save_screenshots(skill, screenshots)
    skill.save()
    write_stages_from_events(skill, events, yours, this_run, playbook=playbook)
    recording.skill = skill
    recording.status = "compiled"
    recording.error = ""
    recording.save(update_fields=["skill", "status", "error"])
    return {
        "skill": skill.to_dict(),
        "split": {"yours": yours, "this_run": this_run, "playbook": playbook},
    }
