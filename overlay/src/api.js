export class TaskwipeAPI {
  constructor(base) {
    this.base = (base || "http://127.0.0.1:8001").replace(/\/$/, "");
  }

  async request(method, path, body) {
    const response = await fetch(this.base + path, {
      method,
      headers: body ? { "Content-Type": "application/json" } : undefined,
      body: body ? JSON.stringify(body) : undefined,
    });
    const data = await response.json().catch(() => ({}));
    if (!response.ok) {
      throw new Error(data.error || response.statusText || "Request failed");
    }
    return data;
  }

  health() {
    return this.request("GET", "/api/health/");
  }

  skills() {
    return this.request("GET", "/api/skills/");
  }

  deleteSkill(id) {
    return this.request("DELETE", "/api/skills/" + id + "/");
  }

  memory() {
    return this.request("GET", "/api/memory/");
  }

  saveMemory(payload) {
    return this.request("POST", "/api/memory/", payload);
  }

  patchMemory(id, payload) {
    return this.request("PATCH", "/api/memory/" + id + "/", payload);
  }

  deleteMemory(id) {
    return this.request("DELETE", "/api/memory/" + id + "/");
  }

  compileRecording(payload) {
    return this.request("POST", "/api/recordings/", payload);
  }

  commitSkill(id, payload) {
    return this.request("POST", "/api/skills/" + id + "/commit/", payload);
  }

  startRun(payload) {
    return this.request("POST", "/api/runs/", payload);
  }

  turn(runId, payload) {
    return this.request("POST", "/api/runs/" + runId + "/turn/", payload);
  }

  confirm(runId, approved) {
    return this.request("POST", "/api/runs/" + runId + "/confirm/", { approved });
  }

  approvePlan(runId, approved) {
    return this.request("POST", "/api/runs/" + runId + "/plan/", { approved });
  }

  answerRun(runId, payload) {
    return this.request("POST", "/api/runs/" + runId + "/answer/", payload);
  }

  resume(runId, payload) {
    return this.request("POST", "/api/runs/" + runId + "/resume/", payload);
  }

  keepRun(runId) {
    return this.request("POST", "/api/runs/" + runId + "/keep/");
  }

  pause(runId) {
    return this.request("POST", "/api/runs/" + runId + "/pause/");
  }

  examples() {
    return this.request("GET", "/api/examples/");
  }

  saveExample(payload) {
    return this.request("POST", "/api/examples/", payload);
  }

  deleteExample(id) {
    return this.request("DELETE", "/api/examples/" + id + "/");
  }

  startJob(payload) {
    return this.request("POST", "/api/jobs/", payload);
  }

  goJob(runId, payload) {
    return this.request("POST", "/api/jobs/" + runId + "/go/", payload);
  }

  pickedFile(runId, done) {
    return this.request("POST", "/api/runs/" + runId + "/file/", { done });
  }

  documents() {
    return this.request("GET", "/api/documents/");
  }

  document(id) {
    return this.request("GET", "/api/documents/" + id + "/");
  }

  async documentFile(id) {
    const response = await fetch(this.base + "/api/documents/" + id + "/file/");
    if (!response.ok) {
      throw new Error("Could not download that vault file");
    }
    const blob = await response.blob();
    const data = await new Promise((resolve, reject) => {
      const reader = new FileReader();
      reader.onload = () => {
        const raw = String(reader.result || "");
        resolve(raw.includes(",") ? raw.split(",")[1] : raw);
      };
      reader.onerror = () => reject(new Error("Could not read that vault file"));
      reader.readAsDataURL(blob);
    });
    const header = response.headers.get("Content-Disposition") || "";
    const match = header.match(/filename="?([^"]+)"?/i);
    const name = (match && match[1]) || "vault-file";
    return { name, type: blob.type || "", data };
  }

  async uploadDocument(file, title) {
    const body = new FormData();
    body.append("file", file);
    if (title) {
      body.append("title", title);
    }
    const response = await fetch(this.base + "/api/documents/", { method: "POST", body });
    const data = await response.json().catch(() => ({}));
    if (!response.ok) {
      throw new Error(data.error || response.statusText || "Request failed");
    }
    return data;
  }

  acceptDocument(id, payload) {
    return this.request("POST", "/api/documents/" + id + "/accept/", payload || {});
  }

  rejectDocument(id, payload) {
    return this.request("POST", "/api/documents/" + id + "/reject/", payload || {});
  }

  deleteDocument(id) {
    return this.request("DELETE", "/api/documents/" + id + "/");
  }

  activity() {
    return this.request("GET", "/api/runs/");
  }

  async exportArchive() {
    const response = await fetch(this.base + "/api/export/");
    if (!response.ok) {
      const data = await response.json().catch(() => ({}));
      throw new Error(data.error || response.statusText || "Could not export");
    }
    return response.blob();
  }

  matchSkills(url, title) {
    const query = new URLSearchParams();
    if (url) {
      query.set("url", url);
    }
    if (title) {
      query.set("title", title);
    }
    return this.request("GET", "/api/skills/match/?" + query.toString());
  }
}
