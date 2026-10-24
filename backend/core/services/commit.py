from core.services.memory import commit_yours
from core.services.stages import ensure_one_stage, replace_stages, rollup_from_stages
from core.services.split import build_demonstration


def commit_skill(skill, payload):
    name = (payload.get("name") or skill.name).strip()
    goal = (payload.get("goal") or skill.goal).strip()
    stages_payload = payload.get("stages")
    used_stages = isinstance(stages_payload, list) and bool(stages_payload)
    if used_stages:
        replace_stages(skill, stages_payload)
        rollup_from_stages(skill)
        yours = skill.split_yours
        this_run = skill.split_this_run
        playbook = skill.playbook
    else:
        playbook = payload.get("playbook")
        if not isinstance(playbook, list) or not playbook:
            playbook = skill.playbook
        yours = payload.get("yours")
        if yours is None:
            yours = skill.split_yours
        this_run = payload.get("this_run")
        if this_run is None:
            this_run = skill.split_this_run
    skill.name = name[:200]
    skill.goal = goal
    skill.playbook = playbook
    skill.split_yours = yours
    skill.split_this_run = this_run
    skill.inputs_schema = this_run
    skill.memory_keys = [item.get("key") for item in yours if item.get("key")]
    skill.start_url = payload.get("start_url", skill.start_url) or skill.start_url
    skill.site_host = payload.get("site_host", skill.site_host) or skill.site_host
    skill.page_kind = payload.get("page_kind", skill.page_kind) or skill.page_kind
    if not used_stages:
        skill.demonstration = build_demonstration(skill.source_events, yours, this_run)
    memory = commit_yours(yours, skill, overwrite_keys=payload.get("overwrite_keys") or [])
    skill.ready = True
    skill.save()
    if not used_stages:
        ensure_one_stage(skill)
    return {"skill": skill.to_dict(), "memory": memory}
