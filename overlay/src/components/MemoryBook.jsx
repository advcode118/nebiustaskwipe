import React from "react";

const CATEGORIES = [
  "identity",
  "shipping",
  "study",
  "work",
  "sites",
  "people",
  "preferences",
  "other",
];

const SOURCE_LABEL = {
  typed: "typed",
  recording: "from a recording",
  file: "from a file",
  run: "answered on a run",
};

export class MemoryBook extends React.Component {
  constructor(props) {
    super(props);
    this.state = {
      key: "email",
      label: "Email",
      value: "",
      category: "identity",
      group_key: "",
      add_another: false,
      overwrite: false,
    };
  }

  change = (field) => (event) => {
    const value = event.target.type === "checkbox" ? event.target.checked : event.target.value;
    this.setState({ [field]: value });
  };

  submit = async (event) => {
    event.preventDefault();
    try {
      await this.props.onAdd({
        key: this.state.key,
        label: this.state.label,
        value: this.state.value,
        category: this.state.category,
        group_key: this.state.group_key,
        confirmed: true,
        add_another: this.state.add_another,
        overwrite: this.state.add_another ? false : this.state.overwrite,
      });
      this.setState({ value: "", overwrite: false, add_another: false });
    } catch (error) {
      this.setState({ overwrite: true });
    }
  };

  grouped(facts) {
    return CATEGORIES.map((category) => ({
      category,
      facts: facts.filter((fact) => fact.category === category),
    })).filter((group) => group.facts.length);
  }

  renderFact(fact) {
    const { onPatch, onDelete } = this.props;
    const source = SOURCE_LABEL[fact.source] || fact.source;
    return (
      <li key={fact.id} className="py-2">
        <div className="flex items-baseline justify-between gap-2">
          <div className="font-semibold">{fact.label}</div>
          {fact.is_default ? (
            <span className="text-xs text-wipe">default</span>
          ) : (
            <button className="text-xs text-wipe" onClick={() => onPatch(fact, { is_default: true })}>
              Use this one
            </button>
          )}
        </div>
        {fact.group_key ? <div className="text-xs text-mute">{fact.group_key}</div> : null}
        <input
          className="mt-1 w-full border-b border-line bg-transparent py-1"
          defaultValue={fact.value}
          onBlur={(event) => {
            if (event.target.value !== fact.value) {
              onPatch(fact, { value: event.target.value });
            }
          }}
        />
        <div className="mt-1 text-xs text-mute">
          {source}
          {fact.source_quote ? " — “" + fact.source_quote + "”" : ""}
        </div>
        <button className="mt-1 text-sm text-mute" onClick={() => onDelete(fact)}>
          Delete
        </button>
      </li>
    );
  }

  render() {
    const { facts, examples, onPatch, onDelete, onDeleteExample } = this.props;
    const pending = facts.filter((fact) => !fact.confirmed && !fact.rejected);
    const kept = facts.filter((fact) => fact.confirmed && !fact.rejected);
    const groups = this.grouped(kept);
    return (
      <section className="px-4 pb-8">
        <p className="mb-4 text-sm text-mute">
          Lasting facts for later skills — name, email, parcel address. Captions you keep live here too.
        </p>
        {(examples || []).length ? (
          <div className="mb-6">
            <div className="mb-2 text-sm">How I write</div>
            <ul className="divide-y divide-line">
              {examples.map((item) => (
                <li key={item.id} className="py-2">
                  <div className="text-xs text-mute">{item.kind}</div>
                  <div className="whitespace-pre-wrap text-sm">{item.text}</div>
                  {item.notes ? <div className="text-xs text-mute">{item.notes}</div> : null}
                  <button className="mt-1 text-sm text-mute" onClick={() => onDeleteExample(item)}>
                    Forget this
                  </button>
                </li>
              ))}
            </ul>
          </div>
        ) : null}
        {pending.length ? (
          <div className="mb-4 border border-warn/40 p-3">
            <div className="mb-2 text-sm">Save these for later?</div>
            {pending.map((fact) => (
              <div key={fact.id} className="mb-2 flex items-center justify-between gap-2 text-sm">
                <span>
                  {fact.label}: {fact.value}
                </span>
                <span className="flex gap-2">
                  <button className="text-wipe" onClick={() => onPatch(fact, { confirmed: true })}>
                    Keep
                  </button>
                  <button className="text-mute" onClick={() => onDelete(fact)}>
                    Skip
                  </button>
                </span>
              </div>
            ))}
          </div>
        ) : null}
        {groups.length ? (
          groups.map((group) => (
            <div key={group.category} className="mb-5">
              <div className="text-sm text-mute">{group.category}</div>
              <ul className="divide-y divide-line">{group.facts.map((fact) => this.renderFact(fact))}</ul>
            </div>
          ))
        ) : (
          <p className="text-sm text-mute">Nothing stored yet. Record a form or add a fact below.</p>
        )}
        <form className="mt-6 space-y-2" onSubmit={this.submit}>
          <div className="text-sm">Add a fact</div>
          <input className="w-full border border-line bg-pane px-2 py-1" value={this.state.key} onChange={this.change("key")} />
          <input className="w-full border border-line bg-pane px-2 py-1" value={this.state.label} onChange={this.change("label")} />
          <input
            className="w-full border border-line bg-pane px-2 py-1"
            value={this.state.value}
            onChange={this.change("value")}
            placeholder="value"
          />
          <select className="w-full border border-line bg-pane px-2 py-1" value={this.state.category} onChange={this.change("category")}>
            {CATEGORIES.map((category) => (
              <option key={category} value={category}>
                {category}
              </option>
            ))}
          </select>
          {this.state.category === "shipping" ? (
            <input
              className="w-full border border-line bg-pane px-2 py-1"
              value={this.state.group_key}
              onChange={this.change("group_key")}
              placeholder="home or term"
            />
          ) : null}
          <label className="flex items-center gap-2 text-sm text-mute">
            <input type="checkbox" checked={this.state.add_another} onChange={this.change("add_another")} />
            Keep the one I already have — this is another
          </label>
          {this.state.overwrite && !this.state.add_another ? (
            <label className="flex items-center gap-2 text-sm text-warn">
              <input type="checkbox" checked={this.state.overwrite} onChange={this.change("overwrite")} />
              Replace the value I already remember for this key
            </label>
          ) : null}
          <button className="bg-pane px-3 py-1.5 text-sm" type="submit">
            Remember
          </button>
        </form>
      </section>
    );
  }
}
