import React from "react";

export class Vault extends React.Component {
  constructor(props) {
    super(props);
    this.fileInput = React.createRef();
    this.state = { openId: null };
  }

  openPicker = () => {
    if (this.fileInput.current) {
      this.fileInput.current.click();
    }
  };

  onFiles = (event) => {
    const list = event.target.files;
    if (list && list.length && this.props.onUpload) {
      Array.from(list).forEach((file) => this.props.onUpload(file));
    }
    event.target.value = "";
  };

  review = (document) => {
    if (this.state.openId === document.id) {
      this.setState({ openId: null });
      return;
    }
    this.setState({ openId: document.id });
    if (this.props.onOpen) {
      this.props.onOpen(document);
    }
  };

  render() {
    const { documents, vaultIds, onAttach, onAccept, onReject, onDelete, uploading } = this.props;
    const attached = new Set(vaultIds || []);
    return (
      <section className="px-4 pb-8">
        <p className="mb-4 text-sm text-mute">
          Files stay on this machine. I'll read them once and suggest facts — nothing is remembered until you keep it.
        </p>
        <input
          ref={this.fileInput}
          className="hidden"
          type="file"
          accept=".pdf,.docx,.txt,application/pdf,application/vnd.openxmlformats-officedocument.wordprocessingml.document,text/plain"
          multiple
          onChange={this.onFiles}
        />
        <button className="bg-pane px-3 py-1.5 text-sm" onClick={this.openPicker} disabled={uploading}>
          {uploading ? "Reading…" : "Add a file"}
        </button>
        {(documents || []).length ? (
          <ul className="mt-4 divide-y divide-line">
            {documents.map((doc) => {
              const open = this.state.openId === doc.id;
              const detail = doc.proposals ? doc : null;
              return (
                <li key={doc.id} className="py-3">
                  <div className="flex items-baseline justify-between gap-2">
                    <button className="text-left font-semibold" onClick={() => this.review(doc)}>
                      {doc.title}
                    </button>
                    <span className="text-xs text-mute">{doc.status}</span>
                  </div>
                  <div className="text-xs text-mute">
                    {doc.filename}
                    {doc.proposal_count ? " · " + doc.proposal_count + " to review" : ""}
                  </div>
                  <div className="mt-2 flex flex-wrap gap-3 text-sm">
                    <label className="flex items-center gap-2 text-mute">
                      <input
                        type="checkbox"
                        checked={attached.has(doc.id)}
                        onChange={() => onAttach(doc)}
                      />
                      Use on the next Deal with this
                    </label>
                    <button className="text-mute" onClick={() => this.review(doc)}>
                      {open ? "Hide" : "Review"}
                    </button>
                    <button className="text-mute" onClick={() => onDelete(doc)}>
                      Delete
                    </button>
                  </div>
                  {open && detail && (detail.proposals || []).length ? (
                    <ul className="mt-3 space-y-2 border border-warn/40 p-3">
                      {detail.proposals.map((fact) => (
                        <li key={fact.id} className="text-sm">
                          <div>
                            {fact.label}: {fact.value}
                          </div>
                          {fact.source_quote ? (
                            <div className="text-xs text-mute">“{fact.source_quote}”</div>
                          ) : null}
                          <div className="mt-1 flex gap-2">
                            <button className="text-wipe" onClick={() => onAccept(doc, fact)}>
                              Keep
                            </button>
                            <button className="text-mute" onClick={() => onReject(doc, fact)}>
                              Skip
                            </button>
                          </div>
                        </li>
                      ))}
                    </ul>
                  ) : null}
                  {open && detail && (detail.skipped || []).length ? (
                    <p className="mt-2 text-xs text-mute">
                      Skipped secrets: {(detail.skipped || []).length}
                    </p>
                  ) : null}
                  {open && detail && !(detail.proposals || []).length ? (
                    <p className="mt-2 text-sm text-mute">Nothing waiting on this file.</p>
                  ) : null}
                </li>
              );
            })}
          </ul>
        ) : (
          <p className="mt-4 text-sm text-mute">No files yet. Add a CV or a transcript.</p>
        )}
      </section>
    );
  }
}
