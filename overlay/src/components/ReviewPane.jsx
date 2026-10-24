import React from "react";

export class ReviewPane extends React.Component {
  currentStage() {
    const { review } = this.props;
    const stages = review.stages || [];
    if (!stages.length) {
      return null;
    }
    const index = Math.min(review.stageIndex || 0, stages.length - 1);
    return { stage: stages[index], index, stages };
  }

  changeReview(patch) {
    this.props.onChange({ ...this.props.review, ...patch });
  }

  changeCurrentStage(patch) {
    const current = this.currentStage();
    if (!current) {
      this.changeReview(patch);
      return;
    }
    const stages = current.stages.slice();
    stages[current.index] = { ...current.stage, ...patch };
    this.changeReview({ stages });
  }

  moveYoursToRun = (index) => {
    const { review } = this.props;
    const current = this.currentStage();
    const yoursSource = current ? current.stage.yours : review.yours;
    const runSource = current ? current.stage.this_run : review.this_run;
    const yours = (yoursSource || []).slice();
    const item = yours.splice(index, 1)[0];
    const thisRun = (runSource || []).concat([
      {
        name: item.key,
        type: "string",
        label: item.label,
        value_from_demo: item.value,
        reason: "moved: type this next time",
      },
    ]);
    if (current) {
      this.changeCurrentStage({ yours, this_run: thisRun });
    } else {
      this.changeReview({ yours, this_run: thisRun });
    }
  };

  moveRunToYours = (index) => {
    const { review } = this.props;
    const current = this.currentStage();
    const yoursSource = current ? current.stage.yours : review.yours;
    const runSource = current ? current.stage.this_run : review.this_run;
    const thisRun = (runSource || []).slice();
    const item = thisRun.splice(index, 1)[0];
    const yours = (yoursSource || []).concat([
      {
        key: item.name,
        label: item.label,
        value: item.value_from_demo || "",
        category: "other",
        reason: "moved: this is you",
      },
    ]);
    if (current) {
      this.changeCurrentStage({ yours, this_run: thisRun });
    } else {
      this.changeReview({ yours, this_run: thisRun });
    }
  };

  changeField = (field) => (event) => {
    this.changeReview({ [field]: event.target.value });
  };

  changePlaybook = (event) => {
    const playbook = event.target.value.split("\n").map((line) => line.trim()).filter(Boolean);
    const current = this.currentStage();
    if (current) {
      this.changeCurrentStage({ playbook });
    } else {
      this.changeReview({ playbook });
    }
  };

  toggleOverwrite = (key) => {
    const { review } = this.props;
    const overwrite_keys = new Set(review.overwrite_keys || []);
    if (overwrite_keys.has(key)) {
      overwrite_keys.delete(key);
    } else {
      overwrite_keys.add(key);
    }
    this.changeReview({ overwrite_keys: Array.from(overwrite_keys) });
  };

  selectStage = (index) => {
    this.changeReview({ stageIndex: index });
  };

  renameStage = (event) => {
    this.changeCurrentStage({ name: event.target.value });
  };

  deleteStage = () => {
    const current = this.currentStage();
    if (!current || current.stages.length < 2) {
      return;
    }
    const stages = current.stages.slice();
    stages.splice(current.index, 1);
    this.changeReview({
      stages,
      stageIndex: Math.min(current.index, stages.length - 1),
    });
  };

  mergeStage = () => {
    const current = this.currentStage();
    if (!current || current.index < 1) {
      return;
    }
    const stages = current.stages.slice();
    const prev = stages[current.index - 1];
    const cur = current.stage;
    stages[current.index - 1] = {
      ...prev,
      playbook: (prev.playbook || []).concat(cur.playbook || []),
      yours: (prev.yours || []).concat(cur.yours || []),
      this_run: (prev.this_run || []).concat(cur.this_run || []),
      demonstration: (prev.demonstration || []).concat(cur.demonstration || []),
      memory_keys: Array.from(new Set((prev.memory_keys || []).concat(cur.memory_keys || []))),
      exit: cur.exit || prev.exit,
    };
    stages.splice(current.index, 1);
    this.changeReview({ stages, stageIndex: current.index - 1 });
  };

  renderPiles(yours, thisRun) {
    const overwrite = new Set(this.props.review.overwrite_keys || []);
    return (
      <>
        <div className="mb-3 font-semibold">This is you</div>
        {(yours || []).length ? (
          (yours || []).map((item, index) => (
            <div key={item.key + index} className="mb-3 border-b border-line pb-2">
              <div className="text-sm">{item.label}</div>
              <div className="text-sm text-wipe">{item.value}</div>
              {item.conflict ? (
                <label className="mt-1 block text-sm text-warn">
                  <input
                    type="checkbox"
                    className="mr-2"
                    checked={overwrite.has(item.key)}
                    onChange={() => this.toggleOverwrite(item.key)}
                  />
                  Already remember “{item.existing}”. Overwrite with this recording.
                </label>
              ) : null}
              <button className="text-sm text-mute" onClick={() => this.moveYoursToRun(index)}>
                I'll type this next time
              </button>
            </div>
          ))
        ) : (
          <p className="mb-3 text-sm text-mute">Nothing lasting in this recording.</p>
        )}

        <div className="mb-3 font-semibold">Type this next time</div>
        {(thisRun || []).length ? (
          (thisRun || []).map((item, index) => (
            <div key={item.name + index} className="mb-3 border-b border-line pb-2">
              <div className="text-sm">{item.label}</div>
              <div className="text-sm text-mute">{item.value_from_demo || item.value || "(empty)"}</div>
              <button className="text-sm text-wipe" onClick={() => this.moveRunToYours(index)}>
                This is me
              </button>
            </div>
          ))
        ) : (
          <p className="mb-3 text-sm text-mute">No per-run inputs. That's fine.</p>
        )}
      </>
    );
  }

  render() {
    const { review, onCommit, onDiscard, committing } = this.props;
    if (!review) {
      return null;
    }
    const current = this.currentStage();
    const stage = current ? current.stage : null;
    const playbook = stage ? stage.playbook : review.playbook;
    const yours = stage ? stage.yours : review.yours;
    const thisRun = stage ? stage.this_run : review.this_run;
    return (
      <section className="px-4 pb-8">
        <h2 className="mb-1 text-lg font-semibold">Check the split before you run</h2>
        <p className="mb-4 text-sm text-mute">
          One recording, three piles: the playbook, facts that are you, values you will type next time.
          The demonstration stays attached.
        </p>

        <label className="block text-sm text-mute">Skill name</label>
        <input className="mb-3 w-full border-b border-line bg-transparent py-1" value={review.name} onChange={this.changeField("name")} />

        <label className="block text-sm text-mute">Goal</label>
        <textarea className="mb-3 w-full border border-line bg-pane px-2 py-1" rows={3} value={review.goal} onChange={this.changeField("goal")} />

        <label className="block text-sm text-mute">This skill is for</label>
        <input className="mb-1 w-full border-b border-line bg-transparent py-1" value={review.start_url} onChange={this.changeField("start_url")} />
        <input className="mb-3 w-full border-b border-line bg-transparent py-1" value={review.page_kind} onChange={this.changeField("page_kind")} placeholder="What kind of page" />

        {current ? (
          <div className="mb-4">
            <div className="mb-2 text-sm text-mute">Pages in this recording</div>
            <div className="mb-3 flex gap-2 overflow-x-auto border-b border-line pb-2">
              {current.stages.map((item, index) => (
                <button
                  key={(item.id || item.url_pattern || "page") + index}
                  className={
                    index === current.index
                      ? "bg-wipe px-2 py-1 text-sm font-semibold text-glass"
                      : "border border-line px-2 py-1 text-sm text-mute"
                  }
                  onClick={() => this.selectStage(index)}
                >
                  {item.name || "Page " + (index + 1)}
                </button>
              ))}
            </div>
            <label className="block text-sm text-mute">Page name</label>
            <input
              className="mb-2 w-full border-b border-line bg-transparent py-1"
              value={stage.name || ""}
              onChange={this.renameStage}
            />
            <div className="mb-3 flex gap-3">
              {current.index > 0 ? (
                <button className="text-sm text-wipe" onClick={this.mergeStage}>
                  Merge with previous
                </button>
              ) : null}
              {current.stages.length > 1 ? (
                <button className="text-sm text-mute" onClick={this.deleteStage}>
                  Delete this page
                </button>
              ) : null}
            </div>
          </div>
        ) : null}

        <label className="block text-sm text-mute">Playbook</label>
        <textarea
          className="mb-4 w-full border border-line bg-pane px-2 py-1 text-sm"
          rows={Math.max(4, (playbook || []).length + 1)}
          value={(playbook || []).join("\n")}
          onChange={this.changePlaybook}
        />

        {this.renderPiles(yours, thisRun)}

        <div className="mt-4 flex gap-2">
          <button className="bg-wipe px-3 py-2 font-semibold text-glass disabled:opacity-40" onClick={onCommit} disabled={committing}>
            Keep this skill
          </button>
          <button className="px-3 py-2 text-sm text-mute" onClick={onDiscard}>
            Discard
          </button>
        </div>
      </section>
    );
  }
}
