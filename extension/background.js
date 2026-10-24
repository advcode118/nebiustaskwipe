const DEFAULT_API = "http://127.0.0.1:8001";

const emptyBuffer = () => ({
  active: false,
  intent: "",
  events: [],
  screenshots: [],
  startedAt: 0,
});

async function getBuffer() {
  const stored = await chrome.storage.session.get(["recording"]);
  return stored.recording || emptyBuffer();
}

async function setBuffer(recording) {
  await chrome.storage.session.set({ recording });
}

async function getApiBase() {
  const stored = await chrome.storage.local.get(["apiBase"]);
  return stored.apiBase || DEFAULT_API;
}

async function activeTab() {
  const tabs = await chrome.tabs.query({ active: true, currentWindow: true });
  return tabs[0];
}

function pageKey(url) {
  try {
    const parsed = new URL(url);
    return parsed.host.replace(/^www\./, "") + parsed.pathname.replace(/\/$/, "");
  } catch (error) {
    return "";
  }
}

async function tabById(tabId) {
  const id = Number(tabId);
  if (!id) {
    return null;
  }
  try {
    return await chrome.tabs.get(id);
  } catch (error) {
    return null;
  }
}

function waitForTab(tabId, timeoutMs) {
  return new Promise((resolve) => {
    let settled = false;
    const finish = (tab) => {
      if (settled) {
        return;
      }
      settled = true;
      chrome.tabs.onUpdated.removeListener(onUpdated);
      resolve(tab);
    };
    const timer = setTimeout(async () => {
      try {
        finish(await chrome.tabs.get(tabId));
      } catch (error) {
        finish({ id: tabId, url: "", title: "" });
      }
    }, timeoutMs);
    function onUpdated(id, info, tab) {
      if (id === tabId && info.status === "complete") {
        clearTimeout(timer);
        finish(tab);
      }
    }
    chrome.tabs.onUpdated.addListener(onUpdated);
    chrome.tabs
      .get(tabId)
      .then((tab) => {
        if (tab.status === "complete") {
          clearTimeout(timer);
          finish(tab);
        }
      })
      .catch(() => {
        clearTimeout(timer);
        finish({ id: tabId, url: "", title: "" });
      });
  });
}

async function focusUrl(url) {
  let target = String(url || "").trim();
  if (!target) {
    throw new Error("focusUrl needs a url");
  }
  if (!/^https?:\/\//i.test(target)) {
    target = "https://" + target;
  }
  const wanted = pageKey(target);
  const tabs = await chrome.tabs.query({});
  let tab = tabs.find((item) => item.url && pageKey(item.url) === wanted);
  if (tab) {
    await chrome.windows.update(tab.windowId, { focused: true });
    await chrome.tabs.update(tab.id, { active: true });
  } else {
    let windowId;
    try {
      const win = await chrome.windows.getLastFocused({ windowTypes: ["normal"] });
      windowId = win && win.id;
    } catch (error) {
      windowId = undefined;
    }
    tab = await chrome.tabs.create({ url: target, active: true, windowId });
  }
  const fresh = await waitForTab(tab.id, 8000);
  return {
    id: fresh.id,
    url: fresh.url || "",
    title: fresh.title || "",
    windowId: fresh.windowId,
  };
}

async function framesFor(tabId) {
  try {
    const frames = await chrome.webNavigation.getAllFrames({ tabId });
    if (frames && frames.length) {
      return frames;
    }
  } catch (error) {
    // Permission or unsupported page.
  }
  return [{ frameId: 0 }];
}

async function sendToFrame(tabId, frameId, message) {
  return chrome.tabs.sendMessage(tabId, message, { frameId });
}

async function sendToAllFrames(tabId, message) {
  const frames = await framesFor(tabId);
  const results = [];
  for (const frame of frames) {
    try {
      results.push({
        frameId: frame.frameId,
        url: frame.url,
        payload: await sendToFrame(tabId, frame.frameId, message),
      });
    } catch (error) {
      results.push({ frameId: frame.frameId, url: frame.url, error: error.message });
    }
  }
  return results;
}

async function captureShot() {
  try {
    const dataUrl = await chrome.tabs.captureVisibleTab({ format: "jpeg", quality: 55 });
    return { media_type: "image/jpeg", data: dataUrl };
  } catch (error) {
    return null;
  }
}

chrome.runtime.onInstalled.addListener(() => {
  chrome.sidePanel.setPanelBehavior({ openPanelOnActionClick: true });
});

chrome.runtime.onMessage.addListener((message, sender, sendResponse) => {
  handleMessage(message, sender)
    .then(sendResponse)
    .catch((error) => sendResponse({ error: error.message || String(error) }));
  return true;
});

async function handleMessage(message, sender) {
  switch (message.type) {
    case "ping":
      return { ok: true, apiBase: await getApiBase() };
    case "setApiBase":
      await chrome.storage.local.set({ apiBase: message.apiBase });
      return { ok: true };
    case "getApiBase":
      return { apiBase: await getApiBase() };
    case "getTabInfo": {
      const tab = (await tabById(message.tabId)) || (await activeTab());
      return tab ? { url: tab.url, title: tab.title, id: tab.id } : { error: "No active tab" };
    }
    case "focusUrl":
      return focusUrl(message.url);
    case "getRecording":
      return getBuffer();
    case "startRecording":
      return startRecording(message.intent || "");
    case "stopRecording":
      return stopRecording();
    case "recordedEvent":
      return appendEvent(message.event, sender);
    case "execute":
      return executeOnTab(message.action, Boolean(message.allowDangerous), message.tabId);
    case "screenshot":
      return captureShot();
    default:
      return { error: "Unknown message" };
  }
}

async function startRecording(intent) {
  const tab = await activeTab();
  if (!tab || !tab.id) {
    throw new Error("No active tab to record.");
  }
  const shot = await captureShot();
  await setBuffer({
    active: true,
    intent,
    events: [
      {
        t: 0,
        type: "navigate",
        url: tab.url,
        title: tab.title,
      },
    ],
    screenshots: shot ? [shot] : [],
    startedAt: Date.now(),
  });
  await sendToAllFrames(tab.id, { type: "startRecording" });
  return { ok: true };
}

async function appendEvent(event, sender) {
  const recording = await getBuffer();
  if (!recording.active) {
    return { ok: false };
  }
  if (sender && sender.frameId) {
    event.frameId = sender.frameId;
  }
  recording.events.push(event);
  const isKeyframe = event.type === "navigate" || event.type === "submit" || recording.events.length % 8 === 0;
  if (isKeyframe) {
    const shot = await captureShot();
    if (shot) {
      recording.screenshots.push(shot);
      recording.screenshots = recording.screenshots.slice(-4);
    }
  }
  await setBuffer(recording);
  return { ok: true, count: recording.events.length };
}

async function stopRecording() {
  const recording = await getBuffer();
  const tab = await activeTab();
  if (tab && tab.id) {
    await sendToAllFrames(tab.id, { type: "stopRecording" });
  }
  const shot = await captureShot();
  if (shot) {
    recording.screenshots.push(shot);
    recording.screenshots = recording.screenshots.slice(-4);
  }
  recording.active = false;
  await setBuffer(recording);
  return recording;
}

function rewriteRefs(text, frameId) {
  if (!text) {
    return text;
  }
  return String(text).replace(/\[ref_(\d+)\]/g, "[f" + frameId + "::ref_$1]");
}

function parseRef(ref) {
  if (!ref || typeof ref !== "string") {
    return null;
  }
  const match = ref.match(/^f(\d+)::(ref_\d+)$/);
  if (match) {
    return { frameId: Number(match[1]), localRef: match[2] };
  }
  return null;
}

async function executeOnTab(action, allowDangerous, tabId) {
  const tab = (await tabById(tabId)) || (await activeTab());
  if (!tab || !tab.id) {
    throw new Error("No active tab.");
  }
  if (action.name === "screenshot") {
    const shot = await captureShot();
    if (!shot) {
      throw new Error("Could not capture this tab.");
    }
    return {
      ok: true,
      result: { kind: "image", media_type: shot.media_type, data: shot.data },
      tab: { id: String(tab.id), url: tab.url, title: tab.title },
    };
  }
  if (action.name === "navigate") {
    const url = action.input && action.input.url;
    if (!url) {
      throw new Error("navigate needs a url");
    }
    if (url === "back") {
      await chrome.tabs.goBack(tab.id);
    } else if (url === "forward") {
      await chrome.tabs.goForward(tab.id);
    } else if (url === "reload") {
      await chrome.tabs.reload(tab.id);
    } else {
      let target = url;
      if (!/^https?:\/\//i.test(target)) {
        target = "https://" + target;
      }
      if (!/^https?:\/\//i.test(target)) {
        throw new Error("Refusing non-http URL");
      }
      await chrome.tabs.update(tab.id, { url: target });
    }
    await delay(800);
    const fresh = await chrome.tabs.get(tab.id);
    return {
      ok: true,
      result: { kind: "text", text: "Navigated to " + (fresh.url || "") },
      tab: { id: String(fresh.id), url: fresh.url, title: fresh.title },
      navigated: true,
    };
  }

  if (action.name === "read_page" || action.name === "find" || action.name === "get_page_text") {
    const dumps = await sendToAllFrames(tab.id, { type: "execute", action, allowDangerous });
    const parts = [];
    for (const dump of dumps) {
      const frameUrl = dump.url || "";
      if (/recaptcha|hcaptcha|captcha|challenges\.cloudflare|about:blank/i.test(frameUrl)) {
        continue;
      }
      if (!dump.payload || dump.payload.error || !dump.payload.result) {
        continue;
      }
      const text = dump.payload.result.text || "";
      parts.push("frame f" + dump.frameId + " " + frameUrl + "\n" + rewriteRefs(text, dump.frameId));
    }
    const fresh = await chrome.tabs.get(tab.id);
    return {
      ok: true,
      result: { kind: "text", text: parts.join("\n\n") || "No frames readable." },
      tab: { id: String(fresh.id), url: fresh.url, title: fresh.title },
    };
  }

  const target = action.input && (action.input.target || action.input.ref);
  const ref = typeof target === "string" ? target : target && target.ref;
  const parsed = parseRef(ref);
  const frameId = parsed ? parsed.frameId : 0;
  const localAction = JSON.parse(JSON.stringify(action));
  if (parsed && localAction.input && localAction.input.target) {
    if (typeof localAction.input.target === "object") {
      localAction.input.target.ref = parsed.localRef;
    }
  }
  let result;
  try {
    result = await sendToFrame(tab.id, frameId, {
      type: "execute",
      action: localAction,
      allowDangerous,
    });
  } catch (error) {
    result = await sendToFrame(tab.id, 0, { type: "execute", action, allowDangerous });
  }
  if (result && result.error) {
    throw new Error(result.error);
  }
  const fresh = await chrome.tabs.get(tab.id);
  return {
    ok: true,
    result: result && result.result,
    tab: { id: String(fresh.id), url: fresh.url, title: fresh.title },
    blocked: Boolean(result && result.blocked),
    dangerous: Boolean(result && (result.dangerous || result.blocked)),
    dangerousSummary: (result && (result.dangerousSummary || (result.result && result.result.dangerousSummary))) || "",
  };
}

function delay(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}
