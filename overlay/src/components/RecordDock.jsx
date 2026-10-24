import React from "react";

export class RecordDock extends React.Component {
  constructor(props) {
    super(props);
    this.fileInput = React.createRef();
  }

  openFiles = () => {
    if (this.fileInput.current) {
      this.fileInput.current.click();
    }
  };

  onFiles = (event) => {
    const list = event.target.files;
    if (list && list.length && this.props.onFiles) {
      this.props.onFiles(Array.from(list));
    }
    event.target.value = "";
  };

  onDrop = (event) => {
    event.preventDefault();
    const list = event.dataTransfer && event.dataTransfer.files;
    if (list && list.length && this.props.onFiles) {
      this.props.onFiles(Array.from(list));
    }
  };

  render() {
    const {
      intent,
      recording,
      compiling,
      trying,
      dealing,
      eventCount,
      factCount,
      attachments,
      busy,
      onIntent,
      onStart,
      onStop,
      onTry,
      onDeal,
      onRemoveFile,
    } = this.props;
    const locked = recording || compiling || trying || dealing || busy;
    let hint = "A prompt, a file, or a recording";
    if (compiling) {
      hint = "Compiling a skill…";
    } else if (dealing) {
      hint = "Reading the prompt…";
    } else if (trying) {
      hint = "Reading this page…";
    } else if (recording) {
      hint = eventCount + " steps";
    } else if (!factCount) {
      hint = "I don't remember you yet — I'll ask as I go";
    }
    return (
      <section className="px-4 py-4">
        <label className="block text-sm text-mute" htmlFor="intent">
          What are we doing?
        </label>
        <textarea
          id="intent"
          className="mt-1 w-full resize-none border-b border-line bg-transparent py-2 text-ink"
          placeholder="Upload this onto YouTube with a suitable caption"
          rows={2}
          value={intent}
          onChange={onIntent}
          disabled={locked}
        />
        <div
          className="mt-3 border border-dashed border-line px-3 py-2"
          onDragOver={(event) => event.preventDefault()}
          onDrop={this.onDrop}
        >
          <input
            ref={this.fileInput}
            type="file"
            multiple
            className="hidden"
            onChange={this.onFiles}
          />
          {(attachments || []).length ? (
            <ul className="space-y-1">
              {attachments.map((item) => (
                <li key={item.name + item.size} className="flex items-center justify-between gap-2 text-sm">
                  <span>
                    {item.name}
                    <span className="text-mute">
                      {" "}
                      {item.data ? "" : "· I'll ask you to pick this"}
                    </span>
                  </span>
                  <button className="text-mute" type="button" onClick={() => onRemoveFile(item)} disabled={locked}>
                    Remove
                  </button>
                </li>
              ))}
            </ul>
          ) : (
            <button className="text-sm text-mute" type="button" onClick={this.openFiles} disabled={locked}>
              Drop a file, or choose one
            </button>
          )}
          {(attachments || []).length ? (
            <button className="mt-2 text-sm text-wipe" type="button" onClick={this.openFiles} disabled={locked}>
              Add another
            </button>
          ) : null}
        </div>
        <div className="mt-4 flex flex-wrap items-center gap-3">
          {recording ? (
            <button className="bg-record px-4 py-2 font-semibold text-ink" onClick={onStop}>
              That's the task
            </button>
          ) : (
            <>
              <button
                className="bg-wipe px-4 py-2 font-semibold text-glass disabled:opacity-40"
                onClick={onDeal}
                disabled={locked}
              >
                Deal with this
              </button>
              <button
                className="border border-wipe px-4 py-2 font-semibold text-wipe disabled:opacity-40"
                onClick={onStart}
                disabled={locked}
              >
                Record this
              </button>
              <button
                className="px-2 py-2 text-sm text-mute disabled:opacity-40"
                onClick={onTry}
                disabled={locked}
              >
                Try this page
              </button>
            </>
          )}
          <span className="text-sm text-mute">{hint}</span>
        </div>
      </section>
    );
  }
}
