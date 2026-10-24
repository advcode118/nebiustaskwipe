import React from "react";

export class RunRibbon extends React.Component {
  constructor(props) {
    super(props);
    this.state = {
      answer: "",
      remember: true,
      correction: "",
      pickedSkillId: "",
      crafts: null,
      keepCrafts: true,
    };
  }

  componentDidUpdate(prevProps) {
    const run = this.props.run;
    const prev = prevProps.run;
    if (run && run.status === "awaiting_job" && (!prev || prev.id !== run.id)) {
      const job = run.job || {};
      this.setState({
        pickedSkillId: (job.procedure && job.procedure.skill_id) || "",
        crafts: (job.crafts || []).map((item) => ({ ...item })),
        asks: (job.asks || []).map((item) => ({ ...item, value: "" })),
        keepCrafts: true,
      });
    }
  }

  change = (field) => (event) => {
    const value = event.target.type === "checkbox" ? event.target.checked : event.target.value;
    this.setState({ [field]: value });
  };

  submitAnswer = (event) => {
    event.preventDefault();
    this.props.onAnswer({ value: this.state.answer, remember: this.state.remember });
    this.setState({ answer: "" });
  };

  skipAnswer = () => {
    this.props.onAnswer({ value: "", remember: false });
  };

  changeCraft = (index, field) => (event) => {
    const crafts = (this.state.crafts || []).map((item, i) =>
      i === index ? { ...item, [field]: event.target.value } : item
    );
    this.setState({ crafts });
  };

  changeAsk = (index) => (event) => {
    const asks = (this.state.asks || []).map((item, i) =>
      i === index ? { ...item, value: event.target.value } : item
    );
    this.setState({ asks });
  };

  goJob = () => {
    const job = (this.props.run && this.props.run.job) || {};
    const procedure = job.procedure || {};
    const answers = {};
    (this.state.asks || []).forEach((item) => {
      const value = (item.value || "").trim();
      if (item.key && value) {
        answers[item.key] = value;
      }
    });
    this.props.onGoJob({
      skill_id: this.state.pickedSkillId || procedure.skill_id || "",
      crafts: this.state.crafts || job.crafts || [],
      keep_crafts: this.state.keepCrafts,
      answers,
    });
  };

  resume = () => {
    this.props.onResume({
      correction: this.state.correction,
      remember: Boolean(this.state.correction) && this.state.remember,
    });
    this.setState({ correction: "" });
  };

  renderJob(run) {
    const job = run.job || {};
    const procedure = job.procedure || {};
    const crafts = this.state.crafts || job.crafts || [];
    const attachments = job.attachments || [];
    let procedureLine = "I'll learn this on " + (job.start_label || job.start_url || "the page it belongs on");
    if (procedure.status === "known") {
      procedureLine = procedure.skill_name || "a skill I already have";
    } else if (procedure.status === "sequence") {
      const names = (procedure.skills || []).map((skill) => skill.skill_name).filter(Boolean);
      procedureLine = names.join(", then ") || "several skills";
    } else if (procedure.status === "mix") {
      const names = (procedure.skills || []).map((skill) => skill.skill_name).filter(Boolean);
      procedureLine = names.length ? "I'll mix " + names.join(" and ") : "I'll mix the skills that fit";
    } else if (procedure.status === "listing") {
      procedureLine = "Post the photo on " + ((procedure.stages || []).join(", ") || "the listing sites");
    }
    return (
      <div>
        <div className="font-semibold">I'll deal with this</div>
        <p className="mt-1 text-sm text-mute">{job.goal || run.intent}</p>
        {job.summary ? <p className="mt-2 text-sm">{job.summary}</p> : null}
        {procedure.status === "listing" ? (
          <p className="mt-2 text-sm text-mute">
            Title, description, and category come from the photo. Dropdowns are opened and the option is chosen.
            It stops before Upload, List it, or Publish.
          </p>
        ) : null}
        {(this.state.asks || []).map((item, index) => (
          <label key={item.key || index} className="mt-3 block text-sm">
            <span className="text-mute">{item.label}</span>
            <input
              className="mt-1 w-full border border-line bg-glass px-2 py-1"
              value={item.value || ""}
              placeholder={item.placeholder || ""}
              onChange={this.changeAsk(index)}
            />
          </label>
        ))}
        {job.listing ? (
          <p className="mt-2 text-sm text-mute">{job.listing.description}</p>
        ) : null}
        {job.provider || run.provider ? (
          <p className="mt-2 text-xs uppercase tracking-wide text-mute">{job.provider || run.provider}</p>
        ) : null}
        {crafts.map((craft, index) => (
          <label key={craft.kind || index} className="mt-3 block text-sm">
            <span className="text-mute">
              {(craft.kind || "caption") + " — " + (craft.source || "draft")}
            </span>
            {craft.title !== undefined ? (
              <input
                className="mt-1 w-full border border-line bg-glass px-2 py-1"
                value={craft.title || ""}
                placeholder="Title"
                onChange={this.changeCraft(index, "title")}
              />
            ) : null}
            <textarea
              className="mt-1 w-full border border-line bg-glass px-2 py-1"
              rows={4}
              value={craft.text || ""}
              placeholder="Write this, or I'll use what I have"
              onChange={this.changeCraft(index, "text")}
            />
          </label>
        ))}
        {crafts.length ? (
          <label className="mt-2 flex items-center gap-2 text-sm text-mute">
            <input type="checkbox" checked={this.state.keepCrafts} onChange={this.change("keepCrafts")} />
            Keep this writing if it was good
          </label>
        ) : null}
        <div className="mt-3 flex gap-2">
          <button className="bg-wipe px-3 py-1.5 font-semibold text-glass" onClick={this.goJob}>
            Go
          </button>
          <button className="px-3 py-1.5 text-sm" onClick={this.props.onPause}>
            Don't
          </button>
        </div>
        {job.stop_before ? (
          <p className="mt-2 text-sm text-mute">It will stop before {job.stop_before}.</p>
        ) : null}
        <details className="mt-3 text-sm">
          <summary className="cursor-pointer text-mute">Details</summary>
          <div className="mt-2">
            <span className="text-mute">Do: </span>
            {procedureLine}
          </div>
          {procedure.status === "sequence" || procedure.status === "mix" ? (
            <ul className="mt-1 space-y-1 text-mute">
              {(procedure.skills || []).map((skill) => (
                <li key={skill.skill_id}>
                  {skill.skill_name}
                  {(skill.stages || []).length ? <span> — {skill.stages.join(" → ")}</span> : null}
                </li>
              ))}
            </ul>
          ) : (procedure.stages || []).length ? (
            <p className="mt-1 text-mute">{procedure.stages.join(" → ")}</p>
          ) : null}
          {(job.memory || []).length ? (
            <div className="mt-3">
              <div className="text-mute">Stored data it can use</div>
              <ul className="mt-1 space-y-1">
                {job.memory.map((fact) => (
                  <li key={fact.id || fact.key}>
                    {fact.label}
                    <span className="text-mute"> — {fact.value}</span>
                  </li>
                ))}
              </ul>
            </div>
          ) : null}
          {attachments.length ? (
            <p className="mt-2 text-mute">
              This run: {attachments.map((item) => item.title || item.name).join(", ")}
            </p>
          ) : null}
          {(job.sources || []).length ? (
            <div className="mt-3">
              <div className="text-mute">Public pages</div>
              <ul className="mt-1 space-y-1">
                {job.sources.map((source) => (
                  <li key={source.url}>
                    <span>{source.title || source.url}</span>
                    <div className="text-mute">{source.url}</div>
                  </li>
                ))}
              </ul>
            </div>
          ) : null}
          {(job.questions || []).map((question, index) => (
            <p key={index} className="mt-1 text-mute">
              {question}
            </p>
          ))}
        </details>
      </div>
    );
  }

  renderFile(prompt) {
    const yes = () => (prompt.local ? this.props.onConfirm(true) : this.props.onFile(true));
    const no = () => (prompt.local ? this.props.onConfirm(false) : this.props.onFile(false));
    return (
      <div>
        <div className="font-semibold">Choose the file</div>
        <p className="mt-1 text-sm text-mute">{prompt.summary || "Pick " + (prompt.filename || "the file") + " in the dialog."}</p>
        <div className="mt-3 flex gap-2">
          <button className="bg-wipe px-3 py-1.5 font-semibold text-glass" onClick={yes}>
            I picked it
          </button>
          <button className="px-3 py-1.5 text-sm" onClick={no}>
            Skip
          </button>
        </div>
      </div>
    );
  }

  renderPlan(prompt) {
    const fill = prompt.fill || [];
    const skip = prompt.skip || [];
    return (
      <div>
        <div className="font-semibold">I'll have a go</div>
        <p className="mt-1 text-sm">{prompt.summary}</p>
        {fill.length ? (
          <ul className="mt-2 space-y-1 text-sm text-mute">
            {fill.map((item, index) => (
              <li key={index}>
                {item.label}
                {item.value_preview ? <span className="text-wipe"> — {item.value_preview}</span> : null}
              </li>
            ))}
          </ul>
        ) : null}
        {skip.length ? (
          <p className="mt-2 text-sm text-mute">Leave blank: {skip.join(", ")}</p>
        ) : null}
        <div className="mt-3 flex gap-2">
          <button className="bg-wipe px-3 py-1.5 font-semibold text-glass" onClick={() => this.props.onPlan(true)}>
            Go
          </button>
          <button className="px-3 py-1.5 text-sm" onClick={() => this.props.onPlan(false)}>
            Stop
          </button>
        </div>
      </div>
    );
  }

  renderAsk(prompt) {
    return (
      <form onSubmit={this.submitAnswer}>
        <div className="font-semibold">{prompt.label || "I need this"}</div>
        <p className="mt-1 text-sm text-mute">{prompt.question}</p>
        <input
          className="mt-2 w-full border border-line bg-glass px-2 py-1"
          value={this.state.answer}
          onChange={this.change("answer")}
          autoFocus
        />
        <label className="mt-2 flex items-center gap-2 text-sm text-mute">
          <input type="checkbox" checked={this.state.remember} onChange={this.change("remember")} />
          Remember this
        </label>
        <div className="mt-3 flex gap-2">
          <button className="bg-wipe px-3 py-1.5 font-semibold text-glass" type="submit">
            Continue
          </button>
          <button className="px-3 py-1.5 text-sm" type="button" onClick={this.skipAnswer}>
            Skip
          </button>
        </div>
      </form>
    );
  }

  renderConfirm(summary, onConfirm) {
    return (
      <div>
        <div className="font-semibold">This looks irreversible</div>
        <p className="mt-1 text-sm text-mute">{summary}</p>
        <div className="mt-3 flex gap-2">
          <button className="bg-wipe px-3 py-1.5 font-semibold text-glass" onClick={() => onConfirm(true)}>
            Approve
          </button>
          <button className="px-3 py-1.5 text-sm" onClick={() => onConfirm(false)}>
            Stop
          </button>
        </div>
      </div>
    );
  }

  renderReport(run) {
    const report = run.fill_report || {};
    const filled = report.filled || [];
    const skipped = report.skipped || [];
    const guessed = report.guessed || [];
    return (
      <div>
        <div className="font-semibold">{run.status === "completed" ? "Done on this tab" : "Stopped"}</div>
        <p className="mt-1 text-sm text-mute">
          {(run.prompt && (run.prompt.kind === "login" || run.prompt.kind === "unexpected") && run.prompt.summary)
            || run.outcome
            || run.last_status_text}
        </p>
        {filled.length ? (
          <ul className="mt-2 space-y-1 text-sm">
            {filled.map((item, index) => (
              <li key={index}>
                <span className="text-mute">{item.label}</span>
                {item.value ? <span> — {item.value}</span> : null}
                {item.source ? <span className="text-xs text-mute"> ({item.source})</span> : null}
              </li>
            ))}
          </ul>
        ) : null}
        {skipped.length ? <p className="mt-2 text-sm text-mute">Left blank: {skipped.join(", ")}</p> : null}
        {guessed.length ? (
          <p className="mt-1 text-xs text-mute">
            Guessed labels: {guessed.map((item) => item.page_label + " ← " + item.memory_key).join(", ")}
          </p>
        ) : null}
        <div className="mt-3 flex flex-wrap gap-2">
          {run.status === "completed" &&
          (run.kind === "attempt" || (run.kind === "job" && !run.skill_id && (run.job || {}).mode !== "remember")) ? (
            <button className="bg-wipe px-3 py-1.5 font-semibold text-glass" onClick={this.props.onKeep}>
              Keep as skill
            </button>
          ) : null}
          {run.status === "completed" && (report.keep_craft || []).length ? (
            <button className="border border-wipe px-3 py-1.5 font-semibold text-wipe" onClick={this.props.onKeepCraft}>
              Keep this writing
            </button>
          ) : null}
          {run.status === "paused" || run.status === "blocked" ? (
            <>
              <input
                className="min-w-0 flex-1 border border-line bg-glass px-2 py-1 text-sm"
                placeholder="That was wrong — the phone is…"
                value={this.state.correction}
                onChange={this.change("correction")}
              />
              <button className="px-3 py-1.5 text-sm text-wipe" onClick={this.resume}>
                Resume
              </button>
            </>
          ) : null}
        </div>
        {this.state.correction && (run.status === "paused" || run.status === "blocked") ? (
          <label className="mt-2 flex items-center gap-2 text-sm text-mute">
            <input type="checkbox" checked={this.state.remember} onChange={this.change("remember")} />
            Remember this correction
          </label>
        ) : null}
      </div>
    );
  }

  render() {
    const { run, confirm, onConfirm, onPause } = this.props;
    if (!run && !confirm) {
      return null;
    }
    const prompt = (run && run.prompt) || {};
    const waiting =
      (run && ["awaiting_job", "awaiting_plan", "awaiting_value", "awaiting_file", "awaiting_confirm"].includes(run.status)) ||
      Boolean(confirm);
    let body = null;
    if (confirm && confirm.local && confirm.kind === "file") {
      body = this.renderFile({ summary: confirm.summary, filename: confirm.filename, local: true });
    } else if (confirm && confirm.local) {
      body = this.renderConfirm(confirm.summary, onConfirm);
    } else if (run && run.status === "awaiting_job") {
      body = this.renderJob(run);
    } else if (run && run.status === "awaiting_file") {
      body = this.renderFile(prompt);
    } else if (run && run.status === "awaiting_plan") {
      body = this.renderPlan(prompt);
    } else if (run && run.status === "awaiting_value") {
      body = this.renderAsk(prompt);
    } else if (confirm || (run && run.status === "awaiting_confirm")) {
      body = this.renderConfirm((confirm && confirm.summary) || prompt.summary || (run && run.confirm_summary), onConfirm);
    } else if (run && ["completed", "blocked", "paused", "failed"].includes(run.status)) {
      body = this.renderReport(run);
    } else if (run) {
      body = (
        <div className="flex items-start justify-between gap-3">
          <div>
            <div className="text-sm text-mute">
              {run.status}
              {run.stage && run.stage.name ? " — " + run.stage.name : ""}
              {run.provider ? " — " + run.provider : ""}
            </div>
            <div className="text-sm">{run.last_status_text || "Working on this tab"}</div>
          </div>
          {run.status === "running" ? (
            <button className="text-sm text-mute" onClick={onPause}>
              Pause
            </button>
          ) : null}
        </div>
      );
    }
    return (
      <section className={"mx-4 mb-4 border p-3 " + (waiting ? "border-wipe/50 bg-pane" : "border-line bg-pane")}>
        {body}
      </section>
    );
  }
}
