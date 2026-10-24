def _compact_facts(items):
    compact = []
    seen = set()
    for item in items or []:
        key = str(item.get("key") or "")
        label = str(item.get("label") or key)
        value = item.get("value")
        if value is None:
            continue
        fingerprint = (key, str(value))
        if fingerprint in seen:
            continue
        seen.add(fingerprint)
        compact.append({"key": key, "label": label, "value": str(value)})
        if len(compact) >= 12:
            break
    return compact


def stamp_activity(run):
    from core.services.memory import memory_for_run

    facts = (run.job or {}).get("memory") or []
    if not facts:
        facts = memory_for_run(
            run.skill if run.skill_id else None,
            current_url=run.current_url,
            tab_title=run.tab_title,
            goal=run.intent,
        )
    checkpoint = dict(run.checkpoint or {})
    checkpoint["facts_used"] = _compact_facts(facts)
    run.checkpoint = checkpoint
    return run


def activity_dict(run):
    procedure = ""
    job = run.job or {}
    procedure_info = job.get("procedure") or {}
    if run.skill_id:
        procedure = run.skill.name
    elif procedure_info.get("skill_name"):
        procedure = procedure_info["skill_name"]
    elif job.get("start_label"):
        procedure = job["start_label"]
    else:
        procedure = run.intent or "this tab"
    stage_reached = ""
    if run.skill_id:
        index = run.stage_index or 0
        stage = next((item for item in run.skill.stages.all() if item.index == index), None)
        if stage:
            stage_reached = stage.name or stage.page_kind or ("Page " + str(stage.index + 1))
    report = run.fill_report if isinstance(run.fill_report, dict) else {}
    facts = _compact_facts(report.get("filled") or [])
    if not facts:
        facts = _compact_facts((run.checkpoint or {}).get("facts_used") or job.get("memory") or [])
    lessons = report.get("lessons") or []
    lesson = ""
    if lessons:
        lesson = str(lessons[-1] or "")
    elif run.outcome:
        lesson = run.outcome
    return {
        "id": str(run.id),
        "status": run.status,
        "kind": run.kind,
        "procedure": procedure,
        "stage_reached": stage_reached,
        "facts_used": facts,
        "lesson": lesson,
        "resumable": run.status in {"paused", "blocked"},
        "updated_at": run.updated_at.isoformat(),
        "intent": run.intent,
    }
