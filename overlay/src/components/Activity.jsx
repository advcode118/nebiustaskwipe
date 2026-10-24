import React from "react";

export class Activity extends React.Component {
  renderItem(item) {
    const facts = item.facts_used || [];
    return (
      <li key={item.id} className="py-3">
        <div className="flex items-baseline justify-between gap-2">
          <div className="font-semibold">{item.procedure}</div>
          <span className="text-xs text-mute">{item.status}</span>
        </div>
        {item.stage_reached ? (
          <div className="text-sm text-mute">Reached {item.stage_reached}</div>
        ) : null}
        {facts.length ? (
          <ul className="mt-1 space-y-1 text-sm">
            {facts.map((fact) => (
              <li key={fact.key || fact.label}>
                {fact.label}
                {fact.value ? <span className="text-mute"> — {fact.value}</span> : null}
              </li>
            ))}
          </ul>
        ) : null}
        {item.lesson ? <p className="mt-1 text-xs text-mute">{item.lesson}</p> : null}
        {item.resumable ? (
          <button
            className="mt-2 bg-wipe px-3 py-1.5 font-semibold text-glass"
            onClick={() => this.props.onResume(item)}
          >
            Resume
          </button>
        ) : null}
      </li>
    );
  }

  render() {
    const { runs, onExport, exporting } = this.props;
    return (
      <section className="px-4 pb-8">
        <p className="mb-4 text-sm text-mute">
          Past runs on this machine. Resume a paused one, or download the profile and vault.
        </p>
        <button className="bg-pane px-3 py-1.5 text-sm" onClick={onExport} disabled={exporting}>
          {exporting ? "Packing…" : "Download profile and vault"}
        </button>
        {(runs || []).length ? (
          <ul className="mt-4 divide-y divide-line">{runs.map((item) => this.renderItem(item))}</ul>
        ) : (
          <p className="mt-4 text-sm text-mute">No runs yet. Deal with this or run a skill first.</p>
        )}
      </section>
    );
  }
}
