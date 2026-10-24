import React from "react";

export class SkillList extends React.Component {
  renderSkill(skill, active) {
    const { runInputs, onSelect, onDelete, onInput, onRun, onReview, running } = this.props;
    return (
      <li key={skill.id} className="py-3">
        <button className="w-full text-left" onClick={() => onSelect(skill)}>
          <div className={"font-semibold " + (active ? "text-wipe" : "text-ink")}>{skill.name}</div>
          <div className="text-sm text-mute">{skill.page_kind || skill.goal}</div>
          <div className="text-xs text-mute">{skill.match_reason || skill.site_host || skill.start_url}</div>
        </button>
        {active ? (
          <div className="mt-3 space-y-2">
            {(skill.playbook || []).map((step, index) => (
              <div key={index} className="text-sm text-mute">
                {step}
              </div>
            ))}
            {(skill.inputs_schema || []).map((item) => (
              <label key={item.name} className="block text-sm">
                <span className="text-mute">{item.label || item.name}</span>
                <input
                  className="mt-1 w-full border border-line bg-pane px-2 py-1"
                  value={runInputs[item.name] || ""}
                  placeholder={item.value_from_demo || ""}
                  onChange={(event) => onInput(item.name, event.target.value)}
                />
              </label>
            ))}
            {(skill.lessons || []).length ? (
              <div className="text-xs text-mute">From last time: {skill.lessons[skill.lessons.length - 1]}</div>
            ) : null}
            <div className="flex gap-2 pt-1">
              <button
                className="bg-wipe px-3 py-1.5 font-semibold text-glass disabled:opacity-40"
                onClick={onRun}
                disabled={running}
              >
                Run
              </button>
              <button className="px-3 py-1.5 text-sm text-mute" onClick={() => onReview(skill)}>
                Edit split
              </button>
              <button className="px-3 py-1.5 text-sm text-mute" onClick={() => onDelete(skill)}>
                Forget skill
              </button>
            </div>
          </div>
        ) : null}
      </li>
    );
  }

  render() {
    const { skills, selected, matches } = this.props;
    const ready = skills.filter((skill) => skill.ready);
    const selectedId = selected && selected.id;
    const matchIds = new Set((matches || []).map((skill) => skill.id));
    const rest = ready.filter((skill) => !matchIds.has(skill.id));
    if (!ready.length) {
      return (
        <section className="px-4 py-6 text-sm text-mute">
          No ready skills. Try this page with what I remember, or record a task.
        </section>
      );
    }
    return (
      <section className="px-4 pb-8">
        {(matches || []).length ? (
          <div className="mb-4">
            <div className="mb-1 text-xs text-mute">This tab looks like</div>
            <ul className="divide-y divide-line border border-line">
              {matches.map((skill) => this.renderSkill(skill, selectedId === skill.id))}
            </ul>
          </div>
        ) : null}
        {rest.length ? (
          <ul className="divide-y divide-line">
            {rest.map((skill) => this.renderSkill(skill, selectedId === skill.id))}
          </ul>
        ) : !matches.length ? (
          <ul className="divide-y divide-line">
            {ready.map((skill) => this.renderSkill(skill, selectedId === skill.id))}
          </ul>
        ) : null}
      </section>
    );
  }
}
