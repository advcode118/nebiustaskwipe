import React from "react";
import { TaskwipeAPI } from "./api.js";
import { ExtensionBridge } from "./chrome.js";
import { RecordDock } from "./components/RecordDock.jsx";
import { SkillList } from "./components/SkillList.jsx";
import { MemoryBook } from "./components/MemoryBook.jsx";
import { RunRibbon } from "./components/RunRibbon.jsx";
import { ReviewPane } from "./components/ReviewPane.jsx";
import { Vault } from "./components/Vault.jsx";
import { Activity } from "./components/Activity.jsx";

export class App extends React.Component {
  constructor(props) {
    super(props);
    this.bridge = new ExtensionBridge();
    this.poll = null;
    this.tabPoll = null;
    this.cancelled = false;
    this.paused = false;
    this.runTabId = null;
    this.state = {
      apiBase: "http://127.0.0.1:8001",
      health: null,
      intent: "",
      recording: false,
      eventCount: 0,
      skills: [],
      facts: [],
      examples: [],
      documents: [],
      runs: [],
      exporting: false,
      vaultIds: [],
      uploadingVault: false,
      attachments: [],
      matches: [],
      run: null,
      runInputs: {},
      selectedSkillId: null,
      confirm: null,
      confirmResolver: null,
      compiling: false,
      committing: false,
      trying: false,
      dealing: false,
      error: null,
      pane: "skills",
      review: null,
    };
    this.api = new TaskwipeAPI(this.state.apiBase);
  }

  componentDidMount() {
    this.bootstrap();
    this.tabPoll = setInterval(() => {
      this.refreshMatches();
    }, 2500);
  }

  componentWillUnmount() {
    this.cancelled = true;
    if (this.poll) {
      clearInterval(this.poll);
    }
    if (this.tabPoll) {
      clearInterval(this.tabPoll);
    }
  }

  async bootstrap() {
    try {
      if (this.bridge.available()) {
        const stored = await this.bridge.send({ type: "getApiBase" });
        if (stored && stored.apiBase) {
          this.api = new TaskwipeAPI(stored.apiBase);
          this.setState({ apiBase: stored.apiBase });
        }
      }
      await this.refresh();
      await this.refreshMatches();
    } catch (error) {
      this.setState({ health: { ok: false }, error: error.message });
    }
  }

  async refreshMatches() {
    if (!this.bridge.available() || this.cancelled) {
      return;
    }
    try {
      const tab = await this.bridge.send({ type: "getTabInfo" });
      if (!tab || tab.error) {
        return;
      }
      const matched = await this.api.matchSkills(tab.url || "", tab.title || "");
      if (!this.cancelled) {
        this.setState({ matches: matched.skills || [] });
      }
    } catch (error) {
      // Tab matching is best-effort; the skills list still works.
    }
  }

  async refresh() {
    try {
      const [health, skills, memory, examples, documents, activity] = await Promise.all([
        this.api.health(),
        this.api.skills(),
        this.api.memory(),
        this.api.examples().catch(() => ({ examples: [] })),
        this.api.documents().catch(() => ({ documents: [] })),
        this.api.activity().catch(() => ({ runs: [] })),
      ]);
      this.setState({
        health,
        skills: skills.skills || [],
        facts: memory.facts || [],
        examples: examples.examples || [],
        documents: documents.documents || [],
        runs: activity.runs || [],
        error: health.has_api_key ? null : "Add NEBIUS_API_KEY to backend/.env",
      });
    } catch (error) {
      this.setState({
        health: { ok: false },
        error: "Cannot reach Django at " + this.api.base + ". Start the backend.",
      });
    }
  }

  setIntent = (event) => {
    this.setState({ intent: event.target.value });
  };

  readAttachment(file) {
    const max = 8 * 1024 * 1024;
    if (file.size > max) {
      return Promise.resolve({
        name: file.name,
        type: file.type,
        size: file.size,
        data: null,
      });
    }
    return new Promise((resolve, reject) => {
      const reader = new FileReader();
      reader.onload = () => {
        const raw = String(reader.result || "");
        resolve({
          name: file.name,
          type: file.type,
          size: file.size,
          data: raw.includes(",") ? raw.split(",")[1] : raw,
        });
      };
      reader.onerror = () => reject(new Error("Could not read " + file.name));
      reader.readAsDataURL(file);
    });
  }

  addFiles = async (files) => {
    try {
      const added = [];
      for (const file of files) {
        added.push(await this.readAttachment(file));
      }
      this.setState((prev) => {
        const names = new Set(prev.attachments.map((item) => item.name + item.size));
        const next = prev.attachments.slice();
        added.forEach((item) => {
          if (!names.has(item.name + item.size)) {
            next.push(item);
          }
        });
        return { attachments: next };
      });
    } catch (error) {
      this.setState({ error: error.message });
    }
  };

  removeFile = (item) => {
    this.setState((prev) => ({
      attachments: prev.attachments.filter((file) => file.name !== item.name || file.size !== item.size),
    }));
  };

  startRecording = async () => {
    this.setState({ error: null, recording: true, eventCount: 1 });
    try {
      const result = await this.bridge.send({ type: "startRecording", intent: this.state.intent });
      if (result && result.error) {
        throw new Error(result.error);
      }
      this.poll = setInterval(async () => {
        const buffer = await this.bridge.send({ type: "getRecording" });
        if (buffer) {
          this.setState({ eventCount: (buffer.events || []).length, recording: buffer.active });
        }
      }, 700);
    } catch (error) {
      this.setState({ recording: false, error: error.message });
    }
  };

  reviewFromSkill(skill, split) {
    const yours = (split && split.yours) || skill.split_yours || [];
    const thisRun = (split && split.this_run) || skill.split_this_run || [];
    const many = (skill.stages || []).length > 1;
    const stages = (skill.stages || []).map((stage) => ({
      id: stage.id,
      name: stage.name || stage.page_kind || "Page",
      url_pattern: stage.url_pattern || "",
      page_kind: stage.page_kind || "",
      playbook: stage.playbook || [],
      yours: many || (stage.split_yours && stage.split_yours.length) ? stage.split_yours || [] : yours,
      this_run: many || (stage.split_this_run && stage.split_this_run.length) ? stage.split_this_run || [] : thisRun,
      demonstration: stage.demonstration || [],
      memory_keys: stage.memory_keys || [],
      vault_file_ids: stage.vault_file_ids || [],
      exit: stage.exit || "",
    }));
    if (!stages.length) {
      stages.push({
        name: skill.page_kind || skill.name || "Page 1",
        url_pattern: skill.start_url || "",
        page_kind: skill.page_kind || "",
        playbook: skill.playbook || [],
        yours,
        this_run: thisRun,
        demonstration: skill.demonstration || [],
        memory_keys: skill.memory_keys || [],
        vault_file_ids: [],
        exit: "",
      });
    }
    return {
      id: skill.id,
      name: skill.name,
      goal: skill.goal,
      playbook: skill.playbook || [],
      yours,
      this_run: thisRun,
      start_url: skill.start_url || "",
      site_host: skill.site_host || "",
      page_kind: skill.page_kind || "",
      stages,
      stageIndex: 0,
      overwrite_keys: [],
    };
  }

  stopRecording = async () => {
    if (this.poll) {
      clearInterval(this.poll);
      this.poll = null;
    }
    this.setState({ compiling: true, recording: false, error: null });
    try {
      const buffer = await this.bridge.send({ type: "stopRecording" });
      const compiled = await this.api.compileRecording({
        intent: this.state.intent || buffer.intent || "",
        events: buffer.events || [],
        screenshots: buffer.screenshots || [],
      });
      await this.refresh();
      this.setState({
        compiling: false,
        selectedSkillId: compiled.skill && compiled.skill.id,
        review: this.reviewFromSkill(compiled.skill, compiled.split),
        pane: "review",
      });
    } catch (error) {
      this.setState({ compiling: false, error: error.message });
    }
  };

  changeReview = (review) => {
    this.setState({ review });
  };

  commitReview = async () => {
    const review = this.state.review;
    if (!review) {
      return;
    }
    this.setState({ committing: true, error: null });
    try {
      await this.api.commitSkill(review.id, {
        name: review.name,
        goal: review.goal,
        playbook: review.playbook,
        yours: review.yours,
        this_run: review.this_run,
        start_url: review.start_url,
        page_kind: review.page_kind,
        overwrite_keys: review.overwrite_keys,
        stages: (review.stages || []).map((stage) => ({
          name: stage.name,
          url_pattern: stage.url_pattern,
          page_kind: stage.page_kind,
          playbook: stage.playbook,
          yours: stage.yours,
          this_run: stage.this_run,
          demonstration: stage.demonstration,
          memory_keys: stage.memory_keys,
          vault_file_ids: stage.vault_file_ids,
          exit: stage.exit,
        })),
      });
      await this.refresh();
      const runInputs = {};
      (review.this_run || []).forEach((item) => {
        runInputs[item.name] = item.value_from_demo || "";
      });
      this.setState({
        committing: false,
        review: null,
        pane: "skills",
        selectedSkillId: review.id,
        runInputs,
      });
    } catch (error) {
      this.setState({ committing: false, error: error.message });
    }
  };

  discardReview = async () => {
    const review = this.state.review;
    if (review && review.id) {
      try {
        await this.api.deleteSkill(review.id);
      } catch (error) {
        // Keep going; the draft can be forgotten later.
      }
    }
    await this.refresh();
    this.setState({ review: null, pane: "skills" });
  };

  openReview = (skill) => {
    this.setState({
      selectedSkillId: skill.id,
      review: this.reviewFromSkill(skill),
      pane: "review",
    });
  };

  selectSkill = (skill) => {
    const runInputs = {};
    (skill.inputs_schema || []).forEach((item) => {
      runInputs[item.name] = this.state.runInputs[item.name] || item.value_from_demo || "";
    });
    this.setState({ selectedSkillId: skill.id, runInputs, pane: "skills" });
  };

  changeInput = (name, value) => {
    this.setState((prev) => ({ runInputs: { ...prev.runInputs, [name]: value } }));
  };

  deleteSkill = async (skill) => {
    try {
      await this.api.deleteSkill(skill.id);
      await this.refresh();
    } catch (error) {
      this.setState({ error: error.message });
    }
  };

  patchFact = async (fact, payload) => {
    try {
      await this.api.patchMemory(fact.id, payload);
      await this.refresh();
    } catch (error) {
      this.setState({ error: error.message });
    }
  };

  deleteFact = async (fact) => {
    try {
      await this.api.deleteMemory(fact.id);
      await this.refresh();
    } catch (error) {
      this.setState({ error: error.message });
    }
  };

  addFact = async (payload) => {
    try {
      await this.api.saveMemory(payload);
      await this.refresh();
      this.setState({ error: null });
    } catch (error) {
      this.setState({ error: error.message });
      throw error;
    }
  };

  selectedSkill() {
    return this.state.skills.find((skill) => skill.id === this.state.selectedSkillId) || null;
  }

  askLocalConfirm(summary) {
    return new Promise((resolve) => {
      this.setState({ confirm: { summary, local: true }, confirmResolver: resolve });
    });
  }

  finishLocalConfirm = (approved) => {
    const resolver = this.state.confirmResolver;
    this.setState({ confirm: null, confirmResolver: null });
    if (resolver) {
      resolver(approved);
    }
  };

  toolResult(action, executed) {
    if (executed && executed.error) {
      return {
        tool_use_id: action.tool_use_id,
        toolset_name: action.toolset_name,
        is_error: true,
        content: executed.error,
      };
    }
    const result = (executed && executed.result) || {};
    const item = {
      tool_use_id: action.tool_use_id,
      toolset_name: action.toolset_name,
      is_error: false,
      content: result.text || "OK",
    };
    if (result.kind === "image") {
      const raw = result.data || "";
      item.content = [
        {
          type: "image",
          source: {
            type: "base64",
            media_type: result.media_type || "image/jpeg",
            data: raw.includes(",") ? raw.split(",")[1] : raw,
          },
        },
      ];
    }
    if (executed && executed.tab && action.name === "navigate") {
      item.browser_state = {
        tabs: [
          {
            tab_id: executed.tab.id,
            title: executed.tab.title || "",
            url: executed.tab.url || "",
            active: true,
          },
        ],
      };
    }
    return item;
  }

  async executeAction(action) {
    if (this.paused || this.cancelled) {
      return { error: "Paused by the human." };
    }
    if (action.name === "wait") {
      const ms = Math.min(30000, Math.max(0, Number(action.input && action.input.duration) || 1) * 1000);
      await new Promise((resolve) => setTimeout(resolve, ms));
    }
    if (this.paused || this.cancelled) {
      return { error: "Paused by the human." };
    }
    if (action.name === "attach_from_vault") {
      const documentId = action.input && action.input.document_id;
      if (!documentId) {
        return { error: "attach_from_vault needs a document id." };
      }
      try {
        const file = await this.api.documentFile(documentId);
        action = {
          ...action,
          name: "attach_file",
          input: {
            ...(action.input || {}),
            attachment_name: file.name,
            file,
          },
        };
      } catch (error) {
        return { error: error.message || "Could not attach that vault file." };
      }
    }
    if (action.name === "attach_file" && !(action.input && action.input.file && action.input.file.data)) {
      const name = (action.input && (action.input.attachment_name || (action.input.file && action.input.file.name))) || "";
      const attachment = this.findAttachment(name);
      if (!attachment) {
        return { error: "No attachment named " + (name || "(none)") + " on this job." };
      }
      if (!attachment.data) {
        const picked = await this.askFilePick(attachment.name);
        return {
          result: {
            kind: "text",
            text: picked ? "The human chose " + attachment.name + " in the OS picker." : "The human skipped the file.",
          },
        };
      }
      action = {
        ...action,
        input: {
          ...(action.input || {}),
          attachment_name: attachment.name,
          file: { name: attachment.name, type: attachment.type, data: attachment.data },
        },
      };
    }
    let executed = await this.bridge.send({
      type: "execute",
      action,
      allowDangerous: false,
      tabId: this.runTabId || undefined,
    });
    if (executed && executed.result && executed.result.needs_pick) {
      const filename = executed.result.filename || (action.input && action.input.attachment_name) || "the file";
      const picked = await this.askFilePick(filename);
      return {
        result: {
          kind: "text",
          text: picked ? "The human chose " + filename + " in the OS picker." : "The human skipped the file.",
        },
      };
    }
    if (executed && (executed.blocked || (executed.result && executed.result.blocked))) {
      const summary = executed.dangerousSummary || (executed.result && executed.result.dangerousSummary) || "This looks irreversible.";
      const approved = await this.askLocalConfirm(summary);
      if (!approved) {
        return { error: "The human denied this click." };
      }
      executed = await this.bridge.send({
        type: "execute",
        action,
        allowDangerous: true,
        tabId: this.runTabId || undefined,
      });
    }
    return executed;
  }

  waitingStatuses() {
    return ["awaiting_confirm", "awaiting_plan", "awaiting_value", "awaiting_job", "awaiting_file"];
  }

  findAttachment(name) {
    const wanted = String(name || "").toLowerCase();
    return (
      this.state.attachments.find((item) => item.name.toLowerCase() === wanted) ||
      this.state.attachments.find((item) => wanted && item.name.toLowerCase().includes(wanted)) ||
      this.state.attachments[0] ||
      null
    );
  }

  askFilePick(filename) {
    return new Promise((resolve) => {
      this.setState({
        confirm: {
          summary: "Choose " + filename + " in the file picker, then continue.",
          local: true,
          kind: "file",
          filename,
        },
        confirmResolver: resolve,
      });
    });
  }

  driveRun = async (initial) => {
    let run = initial;
    let hops = 0;
    let emptyTurns = 0;
    this.paused = false;
    this.setState({ run, error: null, trying: false });
    try {
      while (run && (run.status === "running" || this.waitingStatuses().includes(run.status)) && hops < 200) {
        hops += 1;
        if (this.waitingStatuses().includes(run.status)) {
          this.setState({
            run,
            confirm: run.status === "awaiting_confirm" ? { summary: run.confirm_summary, local: false } : null,
          });
          return;
        }
        const actions = run.pending_actions || [];
        if (!actions.length) {
          if (run.status === "running" && emptyTurns < 2) {
            emptyTurns += 1;
            const tab = await this.pinnedTab();
            const next = await this.api.turn(run.id, {
              tool_results: [],
              current_url: tab.url,
              tab_title: tab.title,
            });
            run = next.run;
            this.setState({ run });
            continue;
          }
          break;
        }
        emptyTurns = 0;
        const toolResults = [];
        for (const action of actions) {
          if (this.paused || this.cancelled) {
            toolResults.push({
              tool_use_id: action.tool_use_id,
              toolset_name: action.toolset_name,
              is_error: true,
              content: "Paused by the human.",
            });
            continue;
          }
          this.setState({ run: { ...run, last_status_text: action.name } });
          const executed = await this.executeAction(action);
          toolResults.push(this.toolResult(action, executed));
        }
        const tab = await this.pinnedTab();
        if (this.paused) {
          await this.api.turn(run.id, { tool_results: toolResults, current_url: tab.url, tab_title: tab.title });
          return;
        }
        const next = await this.api.turn(run.id, {
          tool_results: toolResults,
          current_url: tab.url,
          tab_title: tab.title,
        });
        run = next.run;
        this.setState({ run });
      }
      await this.refresh();
    } catch (error) {
      this.setState({ error: error.message, trying: false });
    }
  };

  async tabInfo() {
    const tab = await this.bridge.send({ type: "getTabInfo" });
    if (!tab || tab.error) {
      return { url: "", title: "" };
    }
    return { url: tab.url || "", title: tab.title || "" };
  }

  async pinnedTab() {
    const message = { type: "getTabInfo" };
    if (this.runTabId) {
      message.tabId = this.runTabId;
    }
    const tab = await this.bridge.send(message);
    if (!tab || tab.error) {
      return { url: "", title: "", id: "" };
    }
    if (tab.id) {
      this.runTabId = tab.id;
    }
    return { url: tab.url || "", title: tab.title || "", id: tab.id || "" };
  }

  async focusSkillTab(url) {
    if (!url || !this.bridge.available()) {
      return null;
    }
    const opened = await this.bridge.send({ type: "focusUrl", url });
    if (!opened || opened.error || !opened.id) {
      return null;
    }
    this.runTabId = opened.id;
    return { url: opened.url || "", title: opened.title || "", id: opened.id };
  }

  startRun = async () => {
    const skill = this.selectedSkill();
    if (!skill) {
      this.setState({ error: "Choose a skill first." });
      return;
    }
    if (!skill.ready) {
      this.openReview(skill);
      return;
    }
    this.setState({ error: null });
    this.runTabId = null;
    try {
      let tab = skill.start_url ? await this.focusSkillTab(skill.start_url) : null;
      if (!tab) {
        tab = await this.tabInfo();
      }
      let host = "";
      try {
        host = tab.url ? new URL(tab.url).host : "";
      } catch (error) {
        host = "";
      }
      let force = false;
      const hostKey = (value) => String(value || "").toLowerCase().replace(/^www\./, "");
      if (skill.site_host && host && hostKey(skill.site_host) !== hostKey(host)) {
        const approved = await this.askLocalConfirm(
          "This skill was recorded on " + skill.site_host + ". This tab is " + host + ". Run anyway?"
        );
        if (!approved) {
          return;
        }
        force = true;
      }
      const created = await this.api.startRun({
        skill_id: skill.id,
        inputs: this.state.runInputs,
        current_url: tab.url,
        tab_title: tab.title,
        force,
      });
      await this.driveRun(created.run);
    } catch (error) {
      this.setState({ error: error.message });
    }
  };

  startJob = async () => {
    const prompt = (this.state.intent || "").trim();
    if (!prompt && !this.state.attachments.length && !this.state.vaultIds.length) {
      this.setState({ error: "Write what you want done, or drop a file." });
      return;
    }
    this.setState({ error: null, dealing: true, pane: "skills" });
    this.runTabId = null;
    try {
      const tab = await this.tabInfo();
      const vaultIds = this.state.vaultIds.slice();
      for (const item of this.state.attachments) {
        const name = String(item.name || "").toLowerCase();
        const type = String(item.type || "").toLowerCase();
        const image = type.startsWith("image/") || /\.(jpe?g|png|webp|gif)$/.test(name);
        const parseable =
          image ||
          type.includes("pdf") ||
          type.includes("word") ||
          type.startsWith("text/") ||
          /\.(pdf|docx|txt)$/.test(name);
        if (!parseable || !item.data) {
          continue;
        }
        const binary = atob(item.data);
        const bytes = new Uint8Array(binary.length);
        for (let index = 0; index < binary.length; index += 1) {
          bytes[index] = binary.charCodeAt(index);
        }
        const file = new File([bytes], item.name, { type: item.type || "application/octet-stream" });
        const uploaded = await this.api.uploadDocument(file, item.name);
        if (uploaded.document && uploaded.document.id) {
          vaultIds.push(uploaded.document.id);
        }
      }
      const created = await this.api.startJob({
        prompt,
        current_url: tab.url,
        tab_title: tab.title,
        vault_ids: vaultIds,
        attachments: this.state.attachments.map((item) => {
          let text;
          try {
            if (item.data && item.type && item.type.startsWith("text/") && item.size < 20000) {
              text = atob(item.data);
            }
          } catch (error) {
            text = undefined;
          }
          return {
            name: item.name,
            media_type: item.type,
            size: item.size,
            injectable: Boolean(item.data),
            text,
          };
        }),
      });
      this.setState({ dealing: false, run: created.run });
    } catch (error) {
      this.setState({ dealing: false, error: error.message });
    }
  };

  goJob = async (payload) => {
    const run = this.state.run;
    if (!run) {
      return;
    }
    this.setState({ error: null });
    try {
      const job = run.job || {};
      const startUrl = job.start_url || "";
      if (startUrl && this.bridge.available()) {
        const opened = await this.focusSkillTab(startUrl);
        if (!opened) {
          await this.bridge.send({
            type: "execute",
            action: { name: "navigate", input: { url: startUrl } },
            tabId: this.runTabId || undefined,
          });
        }
      }
      const tab = await this.pinnedTab();
      const created = await this.api.goJob(run.id, {
        ...payload,
        current_url: tab.url,
      });
      await this.driveRun(created.run);
    } catch (error) {
      this.setState({ error: error.message });
    }
  };

  pickFile = async (done) => {
    const run = this.state.run;
    if (!run) {
      return;
    }
    try {
      const next = await this.api.pickedFile(run.id, done);
      await this.driveRun(next.run);
    } catch (error) {
      this.setState({ error: error.message });
    }
  };

  keepCraft = async () => {
    const run = this.state.run;
    const items = (run && run.fill_report && run.fill_report.keep_craft) || [];
    if (!items.length) {
      return;
    }
    try {
      for (const item of items) {
        if (item.text) {
          await this.api.saveExample({
            kind: item.kind || "caption",
            text: item.text,
            title: item.title || "",
            notes: item.notes || "",
          });
        }
      }
      await this.refresh();
      this.setState({ error: null, run: { ...run, fill_report: { ...run.fill_report, keep_craft: [] } } });
    } catch (error) {
      this.setState({ error: error.message });
    }
  };

  uploadVault = async (file) => {
    this.setState({ uploadingVault: true, error: null, pane: "vault" });
    try {
      await this.api.uploadDocument(file, file.name);
      await this.refresh();
      this.setState({ uploadingVault: false });
    } catch (error) {
      this.setState({ uploadingVault: false, error: error.message });
    }
  };

  openVaultDoc = async (doc) => {
    try {
      const payload = await this.api.document(doc.id);
      this.setState((prev) => ({
        documents: prev.documents.map((item) =>
          item.id === doc.id ? { ...item, ...payload.document } : item
        ),
      }));
    } catch (error) {
      this.setState({ error: error.message });
    }
  };

  attachVault = (doc) => {
    this.setState((prev) => {
      const has = prev.vaultIds.includes(doc.id);
      return {
        vaultIds: has ? prev.vaultIds.filter((id) => id !== doc.id) : prev.vaultIds.concat([doc.id]),
      };
    });
  };

  acceptVaultFact = async (doc, fact) => {
    try {
      await this.api.acceptDocument(doc.id, { fact_ids: [fact.id] });
      await this.refresh();
      await this.openVaultDoc(doc);
    } catch (error) {
      this.setState({ error: error.message });
    }
  };

  rejectVaultFact = async (doc, fact) => {
    try {
      await this.api.rejectDocument(doc.id, { fact_ids: [fact.id] });
      await this.refresh();
      await this.openVaultDoc(doc);
    } catch (error) {
      this.setState({ error: error.message });
    }
  };

  deleteVaultDoc = async (doc) => {
    try {
      await this.api.deleteDocument(doc.id);
      this.setState((prev) => ({
        vaultIds: prev.vaultIds.filter((id) => id !== doc.id),
      }));
      await this.refresh();
    } catch (error) {
      this.setState({ error: error.message });
    }
  };

  deleteExample = async (example) => {
    try {
      await this.api.deleteExample(example.id);
      await this.refresh();
    } catch (error) {
      this.setState({ error: error.message });
    }
  };

  startAttempt = async () => {
    this.setState({ error: null, trying: true, pane: "skills" });
    try {
      const tab = await this.tabInfo();
      const created = await this.api.startRun({
        intent: this.state.intent || "Fill this page with my details, stopping before Submit/Pay.",
        current_url: tab.url,
        tab_title: tab.title,
      });
      await this.driveRun(created.run);
    } catch (error) {
      this.setState({ trying: false, error: error.message });
    }
  };

  confirmRun = async (approved) => {
    const run = this.state.run;
    if (this.state.confirm && this.state.confirm.local) {
      this.finishLocalConfirm(approved);
      return;
    }
    if (!run) {
      return;
    }
    try {
      const next = await this.api.confirm(run.id, approved);
      this.setState({ confirm: null });
      await this.driveRun(next.run);
    } catch (error) {
      this.setState({ error: error.message, confirm: null });
    }
  };

  approvePlan = async (approved) => {
    const run = this.state.run;
    if (!run) {
      return;
    }
    try {
      const next = await this.api.approvePlan(run.id, approved);
      await this.driveRun(next.run);
    } catch (error) {
      this.setState({ error: error.message });
    }
  };

  answerRun = async (payload) => {
    const run = this.state.run;
    if (!run) {
      return;
    }
    try {
      const next = await this.api.answerRun(run.id, payload);
      await this.refresh();
      await this.driveRun(next.run);
    } catch (error) {
      this.setState({ error: error.message });
    }
  };

  async focusRunTab(run) {
    const job = (run && run.job) || {};
    const url = job.start_url || (run && run.current_url) || "";
    if (!/^https?:\/\//i.test(url)) {
      return;
    }
    await this.focusSkillTab(url);
  }

  resumeRun = async (payload) => {
    const run = this.state.run;
    if (!run) {
      return;
    }
    try {
      const next = await this.api.resume(run.id, payload || {});
      await this.focusRunTab(next.run);
      await this.driveRun(next.run);
    } catch (error) {
      this.setState({ error: error.message });
    }
  };

  resumeActivity = async (item) => {
    if (!item || !item.id) {
      return;
    }
    try {
      const next = await this.api.resume(item.id, {});
      this.setState({ pane: "skills" });
      await this.focusRunTab(next.run);
      await this.driveRun(next.run);
      await this.refresh();
    } catch (error) {
      this.setState({ error: error.message });
    }
  };

  exportProfile = async () => {
    this.setState({ exporting: true, error: null });
    try {
      const blob = await this.api.exportArchive();
      const url = URL.createObjectURL(blob);
      const link = document.createElement("a");
      link.href = url;
      link.download = "taskwipe-profile.zip";
      link.click();
      URL.revokeObjectURL(url);
      this.setState({ exporting: false });
    } catch (error) {
      this.setState({ exporting: false, error: error.message });
    }
  };

  keepAttempt = async () => {
    const run = this.state.run;
    if (!run) {
      return;
    }
    this.setState({ committing: true, error: null });
    try {
      const kept = await this.api.keepRun(run.id);
      await this.refresh();
      this.setState({
        committing: false,
        selectedSkillId: kept.skill && kept.skill.id,
        review: this.reviewFromSkill(kept.skill, kept.split),
        pane: "review",
      });
    } catch (error) {
      this.setState({ committing: false, error: error.message });
    }
  };

  pauseRun = async () => {
    if (!this.state.run) {
      return;
    }
    this.paused = true;
    await this.api.pause(this.state.run.id);
    this.setState({ run: { ...this.state.run, status: "paused" }, confirm: null });
  };

  render() {
    const skill = this.selectedSkill();
    return (
      <div className="min-h-full bg-glass font-sans text-ink">
        <header className="border-b border-line px-4 py-4">
          <div className="flex items-end justify-between gap-3">
            <div>
              <div className="text-2xl font-extrabold tracking-tight">taskwipe</div>
              <div className="text-sm text-mute">Show once. Wipe it off the list.</div>
            </div>
            <span
              className={
                "h-2 w-2 rounded-full " +
                (this.state.health && this.state.health.ok ? "bg-wipe" : "bg-record")
              }
            />
          </div>
          {this.state.recording ? (
            <div className="mt-3 h-1 bg-record" style={{ width: Math.min(100, 12 + this.state.eventCount * 6) + "%" }} />
          ) : (
            <div className="mt-3 h-1 bg-wipe/40" />
          )}
        </header>

        <RecordDock
          intent={this.state.intent}
          recording={this.state.recording}
          compiling={this.state.compiling}
          trying={this.state.trying}
          dealing={this.state.dealing}
          eventCount={this.state.eventCount}
          factCount={(this.state.facts || []).length}
          attachments={this.state.attachments}
          busy={Boolean(
            this.state.run &&
              ["running", "awaiting_job", "awaiting_plan", "awaiting_value", "awaiting_file", "awaiting_confirm"].includes(
                this.state.run.status
              )
          )}
          onIntent={this.setIntent}
          onStart={this.startRecording}
          onStop={this.stopRecording}
          onTry={this.startAttempt}
          onDeal={this.startJob}
          onFiles={this.addFiles}
          onRemoveFile={this.removeFile}
        />

        {this.state.error ? (
          <div className="mx-4 mb-3 border border-record/40 bg-record/10 px-3 py-2 text-sm">{this.state.error}</div>
        ) : null}

        <RunRibbon
          run={this.state.run}
          confirm={this.state.confirm}
          onConfirm={this.confirmRun}
          onPlan={this.approvePlan}
          onAnswer={this.answerRun}
          onResume={this.resumeRun}
          onKeep={this.keepAttempt}
          onKeepCraft={this.keepCraft}
          onGoJob={this.goJob}
          onFile={this.pickFile}
          onPause={this.pauseRun}
        />

        <nav className="mx-4 mb-2 flex gap-4 text-sm">
          <button
            className={this.state.pane === "skills" ? "text-ink" : "text-mute"}
            onClick={() => this.setState({ pane: "skills" })}
          >
            Skills
          </button>
          <button
            className={this.state.pane === "memory" ? "text-ink" : "text-mute"}
            onClick={() => this.setState({ pane: "memory" })}
          >
            Profile
          </button>
          <button
            className={this.state.pane === "vault" ? "text-ink" : "text-mute"}
            onClick={() => this.setState({ pane: "vault" })}
          >
            Vault
          </button>
          <button
            className={this.state.pane === "activity" ? "text-ink" : "text-mute"}
            onClick={() => this.setState({ pane: "activity" })}
          >
            Activity
          </button>
          {this.state.review ? (
            <button
              className={this.state.pane === "review" ? "text-ink" : "text-mute"}
              onClick={() => this.setState({ pane: "review" })}
            >
              Review
            </button>
          ) : null}
        </nav>

        <div className="px-4 pb-2">
          <label className="block text-xs text-mute" htmlFor="api-base">
            Django API
          </label>
          <input
            id="api-base"
            className="w-full border-b border-line bg-transparent py-1 text-sm"
            value={this.state.apiBase}
            onChange={(event) => {
              const apiBase = event.target.value;
              this.api = new TaskwipeAPI(apiBase);
              this.setState({ apiBase });
            }}
            onBlur={async () => {
              if (this.bridge.available()) {
                await this.bridge.send({ type: "setApiBase", apiBase: this.state.apiBase });
              }
              this.refresh();
            }}
          />
        </div>

        {this.state.pane === "review" ? (
          <ReviewPane
            review={this.state.review}
            onChange={this.changeReview}
            onCommit={this.commitReview}
            onDiscard={this.discardReview}
            committing={this.state.committing}
          />
        ) : this.state.pane === "skills" ? (
          <SkillList
            skills={this.state.skills}
            matches={this.state.matches}
            selected={skill}
            runInputs={this.state.runInputs}
            onSelect={this.selectSkill}
            onDelete={this.deleteSkill}
            onInput={this.changeInput}
            onRun={this.startRun}
            onReview={this.openReview}
            running={this.state.run && this.state.run.status === "running"}
          />
        ) : this.state.pane === "vault" ? (
          <Vault
            documents={this.state.documents}
            vaultIds={this.state.vaultIds}
            uploading={this.state.uploadingVault}
            onUpload={this.uploadVault}
            onOpen={this.openVaultDoc}
            onAttach={this.attachVault}
            onAccept={this.acceptVaultFact}
            onReject={this.rejectVaultFact}
            onDelete={this.deleteVaultDoc}
          />
        ) : this.state.pane === "activity" ? (
          <Activity
            runs={this.state.runs}
            exporting={this.state.exporting}
            onExport={this.exportProfile}
            onResume={this.resumeActivity}
          />
        ) : (
          <MemoryBook
            facts={this.state.facts}
            examples={this.state.examples}
            onPatch={this.patchFact}
            onDelete={this.deleteFact}
            onAdd={this.addFact}
            onDeleteExample={this.deleteExample}
          />
        )}
      </div>
    );
  }
}
