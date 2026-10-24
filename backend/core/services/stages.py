import re
from urllib.parse import urlparse

from django.db.models import Max

from core.models import Stage, url_pattern_from_start
from core.services.split import build_demonstration, fields_from_events


def _url_key(url):
    parsed = urlparse(url or "")
    path = parsed.path or "/"
    return (parsed.netloc, path)


def norm_path(url_or_path):
    raw = url_or_path or ""
    if "://" in raw:
        raw = urlparse(raw).path or "/"
    return (raw or "/").rstrip("/") or "/"


def url_matches_pattern(url, pattern):
    if not pattern:
        return False
    return norm_path(url) == norm_path(pattern)


def registrable_host(url_or_host):
    raw = url_or_host or ""
    if "://" in raw:
        raw = urlparse(raw).hostname or ""
    host = raw.lower().split(":")[0]
    if host.startswith("www."):
        host = host[4:]
    return host


def same_site(url, other):
    """www.ebay.co.uk and ebay.co.uk are the same site. ebay.co.uk and vinted.co.uk are not."""
    left = registrable_host(url)
    right = registrable_host(other)
    return bool(left) and left == right


def stages_for(skill):
    if not skill:
        return []
    return list(skill.stages.order_by("index"))


def current_stage(run):
    stages = stages_for(run.skill if run.skill_id else None)
    if not stages:
        return None
    index = min(max(int(run.stage_index or 0), 0), len(stages) - 1)
    return stages[index]


def advance_stage(run, url=None):
    stages = stages_for(run.skill if run.skill_id else None)
    if not stages:
        return False
    url = url or run.current_url
    current = min(max(int(run.stage_index or 0), 0), len(stages) - 1)
    matched = None
    for stage in stages:
        if url_matches_pattern(url, stage.url_pattern):
            matched = stage
            break
    if matched is None:
        stage = stages[current]
        if url_matches_pattern(url, stage.exit) and current + 1 < len(stages):
            matched = stages[current + 1]
    if matched is None or matched.index < current:
        return False
    if matched.index == current:
        return False
    run.stage_index = matched.index
    checkpoint = dict(run.checkpoint or {})
    finished = list(checkpoint.get("finished") or [])
    if current not in finished:
        finished.append(current)
    checkpoint["finished"] = finished
    checkpoint["stage_index"] = matched.index
    checkpoint["url"] = url
    run.checkpoint = checkpoint
    return True


def split_event_slices(events):
    slices = []
    for event in events or []:
        url = str(event.get("url") or "").strip()
        if event.get("type") == "navigate" and url:
            key = _url_key(url)
            if not slices or _url_key(slices[-1]["url"]) != key:
                slices.append({"url": url, "events": [event]})
            else:
                slices[-1]["events"].append(event)
            continue
        if not slices:
            slices.append({"url": url, "events": [event]})
        else:
            slices[-1]["events"].append(event)
    if not slices:
        return [{"url": "", "events": []}]
    return slices


def playbook_from_events(events):
    steps = []
    seen = set()
    for event in events or []:
        if event.get("is_secret"):
            continue
        if event.get("type") in {"input", "change"}:
            name = event.get("name") or event.get("innerText") or "the field"
            value = str(event.get("value") or "").strip()
            if not value:
                continue
            marker = "fill:" + name
            if marker in seen:
                continue
            seen.add(marker)
            steps.append("Fill " + name)
            continue
        if event.get("type") == "click":
            name = event.get("name") or event.get("innerText") or "the control"
            if re.search(r"\b(submit|place order|pay|publish|send)\b", name, re.I):
                marker = "stop"
                if marker not in seen:
                    seen.add(marker)
                    steps.append("Stop before Submit or Pay and wait for the human")
                continue
            marker = "click:" + name
            if marker in seen:
                continue
            seen.add(marker)
            if re.search(r"\b(continue|next)\b", name, re.I):
                steps.append("Continue to the next page")
            else:
                steps.append("Click " + name)
    return steps or ["Complete this page"]


def page_kind_from_url(url, fallback=""):
    path = (urlparse(url or "").path or "").lower()
    if "education" in path:
        return "education history"
    if "upload" in path:
        return "transcript upload"
    if "review" in path:
        return "application review"
    if path.rstrip("/").endswith("form-a"):
        return "personal details"
    if fallback:
        return fallback
    parts = [part for part in path.split("/") if part]
    return parts[-1].replace("-", " ") if parts else "page"


def stage_name_from_url(url, index):
    kind = page_kind_from_url(url)
    return (kind[:1].upper() + kind[1:]) if kind else "Page " + str(index + 1)


def stage_fields_from_skill(skill):
    return {
        "name": skill.page_kind or skill.name or "Page 1",
        "url_pattern": url_pattern_from_start(skill.start_url),
        "page_kind": skill.page_kind or "",
        "playbook": skill.playbook or [],
        "demonstration": skill.demonstration or [],
        "memory_keys": skill.memory_keys or [],
        "vault_file_ids": [],
        "split_yours": skill.split_yours or [],
        "split_this_run": skill.split_this_run or [],
        "exit": "",
    }


def ensure_one_stage(skill):
    stages = list(skill.stages.order_by("index"))
    fields = stage_fields_from_skill(skill)
    if not stages:
        return Stage.objects.create(skill=skill, index=0, **fields)
    if len(stages) == 1:
        stage = stages[0]
        stage.name = fields["name"] or stage.name
        stage.url_pattern = fields["url_pattern"] or stage.url_pattern
        stage.page_kind = fields["page_kind"]
        stage.playbook = fields["playbook"]
        stage.demonstration = fields["demonstration"]
        stage.memory_keys = fields["memory_keys"]
        stage.split_yours = fields["split_yours"]
        stage.split_this_run = fields["split_this_run"]
        stage.save()
        return stage
    return stages[0]


def next_stage_index(skill):
    last = skill.stages.aggregate(Max("index"))["index__max"]
    return 0 if last is None else last + 1


def sync_skill_from_stage(stage):
    if stage.skill.stages.count() != 1:
        return
    skill = stage.skill
    skill.playbook = stage.playbook or []
    skill.demonstration = stage.demonstration or []
    skill.page_kind = stage.page_kind or ""
    skill.memory_keys = stage.memory_keys or []
    skill.split_yours = stage.split_yours or skill.split_yours
    skill.split_this_run = stage.split_this_run or skill.split_this_run
    skill.save(
        update_fields=[
            "playbook",
            "demonstration",
            "page_kind",
            "memory_keys",
            "split_yours",
            "split_this_run",
            "updated_at",
        ]
    )


def _stage_yours(slice_events, yours):
    local, _this_run = fields_from_events(slice_events)
    keys = {item["key"] for item in local}
    return [item for item in yours if item.get("key") in keys]


def _as_this_run(item):
    if item.get("name"):
        return item
    return {
        "name": item.get("key"),
        "type": item.get("type") or "string",
        "label": item.get("label") or item.get("key"),
        "value_from_demo": item.get("value_from_demo") or item.get("value") or "",
        "reason": item.get("reason") or "",
    }


def _stage_this_run(slice_events, this_run):
    _yours, local = fields_from_events(slice_events)
    names = {item["key"] for item in local}
    return [
        _as_this_run(item)
        for item in this_run
        if item.get("name") in names or item.get("key") in names
    ]


def write_stages_from_events(skill, events, yours, this_run, playbook=None):
    slices = split_event_slices(events)
    specs = []
    for index, item in enumerate(slices):
        url = item["url"] or skill.start_url
        slice_yours = _stage_yours(item["events"], yours)
        slice_run = _stage_this_run(item["events"], this_run)
        if len(slices) == 1:
            steps = playbook or playbook_from_events(item["events"])
            kind = skill.page_kind or page_kind_from_url(url)
            name = skill.page_kind or skill.name or stage_name_from_url(url, index)
        else:
            steps = playbook_from_events(item["events"])
            kind = page_kind_from_url(url, skill.page_kind)
            name = stage_name_from_url(url, index)
        next_url = slices[index + 1]["url"] if index + 1 < len(slices) else ""
        specs.append(
            {
                "name": name,
                "url_pattern": url_pattern_from_start(url),
                "page_kind": kind,
                "playbook": steps,
                "demonstration": build_demonstration(item["events"], yours, this_run),
                "memory_keys": [row.get("key") for row in slice_yours if row.get("key")],
                "vault_file_ids": [],
                "split_yours": slice_yours,
                "split_this_run": slice_run,
                "exit": url_pattern_from_start(next_url),
            }
        )
    replace_stages(skill, specs)
    if len(specs) > 1:
        skill.playbook = [step for spec in specs for step in (spec.get("playbook") or [])]
        skill.save(update_fields=["playbook", "updated_at"])
    return list(skill.stages.order_by("index"))


def replace_stages(skill, payload):
    skill.stages.all().delete()
    created = []
    for index, raw in enumerate(payload or []):
        playbook = raw.get("playbook") or []
        if not isinstance(playbook, list):
            playbook = [str(playbook)]
        yours = raw.get("split_yours")
        if yours is None:
            yours = raw.get("yours") or []
        this_run = raw.get("split_this_run")
        if this_run is None:
            this_run = raw.get("this_run") or []
        created.append(
            Stage.objects.create(
                skill=skill,
                index=index,
                name=(raw.get("name") or stage_name_from_url(raw.get("url_pattern") or "", index))[:200],
                url_pattern=raw.get("url_pattern") or "",
                page_kind=raw.get("page_kind") or "",
                playbook=playbook,
                demonstration=raw.get("demonstration") or [],
                memory_keys=raw.get("memory_keys")
                or [item.get("key") for item in yours if item.get("key")],
                vault_file_ids=raw.get("vault_file_ids") or [],
                split_yours=yours,
                split_this_run=this_run,
                exit=raw.get("exit") or "",
            )
        )
    return created


def rollup_from_stages(skill):
    stages = list(skill.stages.order_by("index"))
    if not stages:
        return skill
    yours = []
    this_run = []
    playbook = []
    demonstration = []
    memory_keys = []
    seen_yours = set()
    seen_run = set()
    for stage in stages:
        playbook.extend(stage.playbook or [])
        demonstration.extend(stage.demonstration or [])
        for key in stage.memory_keys or []:
            if key not in memory_keys:
                memory_keys.append(key)
        for item in stage.split_yours or []:
            key = item.get("key")
            if key and key not in seen_yours:
                seen_yours.add(key)
                yours.append(item)
        for item in stage.split_this_run or []:
            name = item.get("name")
            if name and name not in seen_run:
                seen_run.add(name)
                this_run.append(item)
    first = stages[0]
    skill.playbook = playbook
    skill.demonstration = demonstration
    skill.memory_keys = memory_keys
    skill.split_yours = yours
    skill.split_this_run = this_run
    skill.inputs_schema = this_run
    skill.page_kind = first.page_kind or skill.page_kind
    if first.url_pattern and not skill.start_url:
        skill.start_url = first.url_pattern
    skill.save()
    return skill
