(() => {
  if (window.__taskwipeVersion === 6) {
    return;
  }
  window.__taskwipeVersion = 6;
  window.__taskwipeInjected = true;

  const DANGEROUS = /\b(submit|pay|purchase|buy now|place order|publish|send(?! intake)|delete account|confirm payment)\b/i;
  const SECRET_TYPE = /password|hidden/i;
  const SELECTOR = "a,button,input,select,textarea,summary,[role],[tabindex],[contenteditable='true']";

  let recording = false;
  let startedAt = 0;
  let refMap = new Map();
  let reverseMap = new WeakMap();
  let nextRef = 1;
  let lastTypedAt = 0;

  function resetRefs() {
    refMap = new Map();
    reverseMap = new WeakMap();
    nextRef = 1;
  }

  function ensureRef(el) {
    if (!el) {
      return null;
    }
    if (reverseMap.has(el)) {
      return reverseMap.get(el);
    }
    const ref = "ref_" + nextRef;
    nextRef += 1;
    refMap.set(ref, el);
    reverseMap.set(el, ref);
    return ref;
  }

  function cssPath(el) {
    if (!(el instanceof Element)) {
      return "";
    }
    if (el.id) {
      return "#" + CSS.escape(el.id);
    }
    const parts = [];
    let node = el;
    while (node && node.nodeType === 1 && parts.length < 5) {
      let sel = node.tagName.toLowerCase();
      if (node.getAttribute("name")) {
        sel += '[name="' + node.getAttribute("name") + '"]';
      }
      const parent = node.parentElement;
      if (parent) {
        const siblings = Array.from(parent.children).filter((child) => child.tagName === node.tagName);
        if (siblings.length > 1) {
          sel += ":nth-of-type(" + (siblings.indexOf(node) + 1) + ")";
        }
      }
      parts.unshift(sel);
      node = parent;
    }
    return parts.join(" > ");
  }

  function accessibleName(el) {
    if (!el) {
      return "";
    }
    const labelled = el.getAttribute("aria-label")
      || el.getAttribute("placeholder")
      || el.getAttribute("title")
      || el.getAttribute("alt")
      || "";
    if (labelled) {
      return labelled.trim();
    }
    const wrapped = el.closest && el.closest("label");
    if (wrapped) {
      const wrapText = (wrapped.innerText || "").trim().split("\n")[0];
      if (wrapText) {
        return wrapText.slice(0, 120);
      }
    }
    if (el.labels && el.labels[0]) {
      return (el.labels[0].innerText || "").trim();
    }
    const id = el.getAttribute("id");
    if (id) {
      const root = el.getRootNode && el.getRootNode();
      const scope = root && root.querySelector ? root : document;
      const byFor = scope.querySelector('label[for="' + CSS.escape(id) + '"]');
      if (byFor) {
        return (byFor.innerText || "").trim();
      }
    }
    let node = el.previousElementSibling;
    for (let i = 0; i < 4 && node; i += 1) {
      const text = (node.innerText || node.textContent || "").trim().split("\n")[0];
      if (text && text.length < 140 && !/^(select an option|\+|yes|no|submit)$/i.test(text)) {
        return text.slice(0, 120);
      }
      node = node.previousElementSibling;
    }
    let parent = el.parentElement;
    for (let i = 0; i < 4 && parent; i += 1) {
      const tag = (parent.tagName || "").toLowerCase();
      if (tag === "form" || tag === "body") {
        break;
      }
      const lines = (parent.innerText || "")
        .trim()
        .split("\n")
        .map((line) => line.trim())
        .filter(Boolean);
      const label = lines.find(
        (line) => line.length < 140 && !/^(select an option|\+|yes|no|submit)$/i.test(line)
      );
      if (label) {
        return label.slice(0, 120);
      }
      parent = parent.parentElement;
    }
    const attrName = (el.getAttribute("name") || el.id || "").replace(/[_-]+/g, " ").trim();
    if (attrName) {
      return attrName.slice(0, 120);
    }
    return ((el.innerText || "") + "").trim().slice(0, 120);
  }

  function roleOf(el) {
    if (!el) {
      return "";
    }
    const explicit = el.getAttribute("role");
    if (explicit) {
      return explicit;
    }
    const tag = el.tagName.toLowerCase();
    if (tag === "a") return "link";
    if (tag === "button") return "button";
    if (tag === "select") return "combobox";
    if (tag === "textarea") return "textbox";
    if (tag === "input") {
      const type = (el.type || "text").toLowerCase();
      if (type === "checkbox") return "checkbox";
      if (type === "radio") return "radio";
      if (type === "submit" || type === "button") return "button";
      if (type === "file") return "file";
      return "textbox";
    }
    if (el.isContentEditable) return "textbox";
    return tag;
  }

  function isSecret(el) {
    if (!(el instanceof HTMLInputElement)) {
      return false;
    }
    return SECRET_TYPE.test(el.type) || SECRET_TYPE.test(el.name || "") || SECRET_TYPE.test(el.id || "");
  }

  function notHidden(el) {
    if (!(el instanceof Element) || el.closest("[aria-hidden='true']")) {
      return false;
    }
    const style = window.getComputedStyle(el);
    if (style.display === "none" || style.visibility === "hidden") {
      return false;
    }
    const box = el.getBoundingClientRect();
    return box.width >= 2 && box.height >= 2;
  }

  function inViewport(el) {
    const box = el.getBoundingClientRect();
    return box.width > 0 && box.height > 0 && box.bottom > 0 && box.top < window.innerHeight;
  }

  function isInteractive(el) {
    if (!(el instanceof Element) || !notHidden(el)) {
      return false;
    }
    const tag = el.tagName.toLowerCase();
    if (["a", "button", "input", "select", "textarea", "summary"].includes(tag)) {
      return true;
    }
    const role = el.getAttribute("role");
    if (role && ["button", "link", "textbox", "checkbox", "radio", "combobox", "tab", "menuitem", "switch"].includes(role)) {
      return true;
    }
    if (el.isContentEditable) {
      return true;
    }
    const tabIndex = el.getAttribute("tabindex");
    return tabIndex !== null && tabIndex !== "-1";
  }

  function collectDeep(root, acc, seen) {
    if (!root) {
      return;
    }
    let nodes = [];
    try {
      nodes = Array.from(root.querySelectorAll(SELECTOR));
    } catch (error) {
      nodes = [];
    }
    for (const el of nodes) {
      if (seen.has(el)) {
        continue;
      }
      seen.add(el);
      acc.push(el);
      if (el.shadowRoot) {
        collectDeep(el.shadowRoot, acc, seen);
      }
    }
    const extras = root.querySelectorAll ? root.querySelectorAll("*") : [];
    for (const el of extras) {
      if (el.shadowRoot && !seen.has(el)) {
        collectDeep(el.shadowRoot, acc, seen);
      }
      if (el.tagName === "IFRAME") {
        try {
          const doc = el.contentDocument;
          if (doc && doc.documentElement) {
            collectDeep(doc, acc, seen);
          }
        } catch (error) {
          // Cross-origin; a frame-level content script handles it.
        }
      }
    }
  }

  function eventPayload(type, el, extra) {
    const payload = {
      t: Date.now() - startedAt,
      type,
      url: location.href,
      selector: el ? cssPath(el) : "",
      role: el ? roleOf(el) : "",
      name: el ? accessibleName(el) : "",
      tag: el ? el.tagName.toLowerCase() : "",
      innerText: el ? (el.innerText || "").trim().slice(0, 80) : "",
      frame: location.href,
    };
    if (el && "value" in el) {
      payload.is_secret = isSecret(el);
      payload.value = payload.is_secret ? null : String(el.value || "").slice(0, 500);
      payload.inputType = el.type || "";
    }
    return Object.assign(payload, extra || {});
  }

  function emit(event) {
    chrome.runtime.sendMessage({ type: "recordedEvent", event });
  }

  function onClick(event) {
    if (!recording) return;
    const el = event.target instanceof Element
      ? event.target.closest("a,button,input,select,textarea,[role],[tabindex]") || event.target
      : null;
    emit(eventPayload("click", el));
  }

  function onChange(event) {
    if (!recording) return;
    emit(eventPayload("input", event.target));
  }

  function onSubmit(event) {
    if (!recording) return;
    emit(eventPayload("submit", event.target));
  }

  function onKeyup(event) {
    if (!recording) return;
    const el = event.target;
    if (!(el instanceof HTMLInputElement || el instanceof HTMLTextAreaElement || (el instanceof HTMLElement && el.isContentEditable))) {
      return;
    }
    const now = Date.now();
    if (now - lastTypedAt < 700) return;
    lastTypedAt = now;
    emit(eventPayload("input", el));
  }

  function startRecording() {
    recording = true;
    startedAt = Date.now();
    document.addEventListener("click", onClick, true);
    document.addEventListener("change", onChange, true);
    document.addEventListener("submit", onSubmit, true);
    document.addEventListener("keyup", onKeyup, true);
  }

  function stopRecording() {
    recording = false;
    document.removeEventListener("click", onClick, true);
    document.removeEventListener("change", onChange, true);
    document.removeEventListener("submit", onSubmit, true);
    document.removeEventListener("keyup", onKeyup, true);
  }

  function showGhost(x, y) {
    let ghost = document.getElementById("taskwipe-ghost");
    if (!ghost) {
      ghost = document.createElement("div");
      ghost.id = "taskwipe-ghost";
      ghost.setAttribute("aria-hidden", "true");
      Object.assign(ghost.style, {
        position: "fixed",
        width: "16px",
        height: "16px",
        borderRadius: "50% 50% 50% 0",
        transform: "translate(-2px, -2px) rotate(-28deg)",
        background: "#7EB8A4",
        zIndex: "2147483647",
        pointerEvents: "none",
        transition: "left 120ms ease, top 120ms ease",
      });
      document.documentElement.appendChild(ghost);
    }
    ghost.style.left = x + "px";
    ghost.style.top = y + "px";
  }

  function findFileInput(el) {
    if (el instanceof HTMLInputElement && el.type === "file") {
      return el;
    }
    if (el && el.querySelector) {
      const inner = el.querySelector("input[type=file]");
      if (inner) {
        return inner;
      }
    }
    const all = Array.from(document.querySelectorAll("input[type=file]"));
    const images = all.filter((input) => !input.accept || /image/i.test(input.accept));
    if (images.length) {
      return images[0];
    }
    return all[0] || null;
  }

  function fieldLabel(el) {
    const wrapped = el.closest && el.closest("label");
    if (wrapped) {
      const text = (wrapped.innerText || "").trim().split("\n")[0];
      if (text) {
        return text.slice(0, 80);
      }
    }
    if (el.labels && el.labels[0]) {
      return (el.labels[0].innerText || "").trim().slice(0, 80);
    }
    const id = el.getAttribute("id");
    if (id) {
      const byFor = document.querySelector('label[for="' + CSS.escape(id) + '"]');
      if (byFor) {
        return (byFor.innerText || "").trim().slice(0, 80);
      }
    }
    return (el.getAttribute("aria-label") || "").trim().slice(0, 80);
  }

  function formMap() {
    const fields = [];
    const buttons = [];
    const links = [];
    const nodes = [];
    collectDeep(document, nodes, new Set());
    nodes.forEach((el) => {
      if (!(el instanceof Element)) {
        return;
      }
      const type = (el.getAttribute("type") || "").toLowerCase();
      if (type === "hidden" || type === "password") {
        return;
      }
      const ref = ensureRef(el);
      const tag = el.tagName.toLowerCase();
      if (tag === "input" && type === "file") {
        fields.push({
          ref,
          role: "file",
          label: fieldLabel(el),
          placeholder: "",
          value: el.files && el.files.length ? String(el.files.length) : "",
        });
        return;
      }
      if (tag === "input" || tag === "textarea" || tag === "select" || el.isContentEditable) {
        if (!notHidden(el)) {
          return;
        }
        const value = "value" in el ? el.value : el.innerText || "";
        fields.push({
          ref,
          role: roleOf(el),
          label: fieldLabel(el),
          placeholder: (el.getAttribute("placeholder") || "").slice(0, 80),
          value: String(value || "").trim().slice(0, 80),
        });
        return;
      }
      const name = (el.innerText || accessibleName(el) || "").replace(/\s+/g, " ").trim().slice(0, 80);
      if (!name) {
        return;
      }
      const actionish = /^(upload|save draft|publish|list it|list item|post|post listing|next|continue|save and continue)$/i.test(name);
      if (!actionish && (!notHidden(el) || el.closest("header, nav"))) {
        return;
      }
      const item = { ref, name, role: roleOf(el) };
      if (tag === "a" && !el.getAttribute("role")) {
        links.push(item);
      } else {
        buttons.push(item);
      }
    });
    const actionName = /^(upload|save draft|publish|list it|list item|post|post listing|next|continue|save and continue)$/i;
    const ordered = [];
    const rest = [];
    buttons.concat(links).forEach((item) => {
      if (actionName.test(item.name)) {
        ordered.push(item);
      } else {
        rest.push(item);
      }
    });
    return JSON.stringify({
      url: location.href,
      title: document.title,
      fields: fields.slice(0, 80),
      buttons: ordered.concat(rest).slice(0, 200),
    });
  }

  function findByText(text) {
    const wanted = String(text || "").replace(/\s+/g, " ").trim().toLowerCase();
    if (!wanted) {
      return null;
    }
    const nodes = Array.from(
      document.querySelectorAll("button, [role='button'], [role='option'], [role='menuitem'], [role='radio'], a, label, li")
    );
    const visible = nodes.filter((el) => notHidden(el));
    const labelOf = (el) => (el.innerText || accessibleName(el) || "").replace(/\s+/g, " ").trim();
    const exact = visible.filter((el) => labelOf(el).toLowerCase() === wanted);
    if (exact.length) {
      return exact[0];
    }
    const includes = visible.filter((el) => {
      const name = labelOf(el).toLowerCase();
      return name.includes(wanted) && name.length < 80;
    });
    includes.sort((a, b) => labelOf(a).length - labelOf(b).length);
    return includes[0] || null;
  }

  function bytesFromBase64(data) {
    const raw = atob(data || "");
    const bytes = new Uint8Array(raw.length);
    for (let i = 0; i < raw.length; i += 1) {
      bytes[i] = raw.charCodeAt(i);
    }
    return bytes;
  }

  function attachFile(input) {
    let el = null;
    if (input.target) {
      const resolved = resolveTarget(input.target);
      if (resolved.error) {
        throw new Error(resolved.error);
      }
      el = findFileInput(resolved.el);
    } else {
      el = findFileInput(document.body);
    }
    if (!el) {
      throw new Error("No file input on this page. Click Select files first, then attach_file.");
    }
    const payload = input.file || {};
    if (!payload.data) {
      return {
        kind: "text",
        text: "Needs an OS picker for " + (payload.name || input.attachment_name || "the file"),
        needs_pick: true,
        filename: payload.name || input.attachment_name || "",
      };
    }
    const file = new File([bytesFromBase64(payload.data)], payload.name || input.attachment_name || "upload", {
      type: payload.type || payload.media_type || "",
    });
    const transfer = new DataTransfer();
    transfer.items.add(file);
    el.files = transfer.files;
    el.dispatchEvent(new Event("input", { bubbles: true, composed: true }));
    el.dispatchEvent(new Event("change", { bubbles: true, composed: true }));
    return { kind: "text", text: "Attached " + file.name };
  }

  function resolveTarget(target) {
    if (!target) {
      return { error: "Missing target" };
    }
    if (typeof target === "string") {
      target = { type: "ref", ref: target };
    }
    if (target.type === "ref" || target.ref) {
      let ref = target.ref;
      if (ref && ref.indexOf("::") >= 0) {
        ref = ref.split("::").pop();
      }
      const el = refMap.get(ref);
      if (!el || !el.isConnected) {
        return { error: "Error: " + target.ref + " is stale or not found on the current page. Re-read the page to get fresh references." };
      }
      return { el, ref: target.ref };
    }
    if (target.type === "coordinate" || ("x" in target && "y" in target)) {
      const x = Number(target.x);
      const y = Number(target.y);
      const el = document.elementFromPoint(x, y);
      showGhost(x, y);
      return { el, x, y };
    }
    return { error: "Unknown target shape" };
  }

  function nativeSet(el, value) {
    if (el instanceof HTMLInputElement || el instanceof HTMLTextAreaElement) {
      const proto = el instanceof HTMLTextAreaElement ? HTMLTextAreaElement.prototype : HTMLInputElement.prototype;
      const descriptor = Object.getOwnPropertyDescriptor(proto, "value");
      if (descriptor && descriptor.set) {
        descriptor.set.call(el, value);
      } else {
        el.value = value;
      }
    } else {
      el.value = value;
    }
    el.dispatchEvent(new Event("input", { bubbles: true, composed: true }));
    el.dispatchEvent(new InputEvent("input", { bubbles: true, composed: true, data: String(value), inputType: "insertText" }));
    el.dispatchEvent(new Event("change", { bubbles: true, composed: true }));
  }

  function fill(el, value) {
    if (!el) {
      throw new Error("No element to fill");
    }
    el.focus();
    const box = el.getBoundingClientRect();
    showGhost(box.left + 8, box.top + 8);
    if (el instanceof HTMLInputElement && (el.type === "checkbox" || el.type === "radio")) {
      el.checked = Boolean(value);
      el.dispatchEvent(new Event("change", { bubbles: true, composed: true }));
      return;
    }
    if (el instanceof HTMLSelectElement) {
      const wanted = String(value);
      const match = Array.from(el.options).find(
        (option) => option.value === wanted || option.text.trim() === wanted
      );
      el.value = match ? match.value : wanted;
      el.dispatchEvent(new Event("change", { bubbles: true, composed: true }));
      return;
    }
    if (el.isContentEditable) {
      el.focus();
      el.innerText = String(value);
      el.dispatchEvent(new InputEvent("input", { bubbles: true, composed: true, data: String(value), inputType: "insertText" }));
      return;
    }
    nativeSet(el, String(value));
  }

  function clickEl(el) {
    const box = el.getBoundingClientRect();
    showGhost(box.left + box.width / 2, box.top + box.height / 2);
    el.scrollIntoView({ block: "center", inline: "nearest" });
    el.focus();
    el.click();
  }

  function lineFor(el) {
    const ref = ensureRef(el);
    const role = roleOf(el);
    const name = accessibleName(el).replace(/\s+/g, " ");
    const off = inViewport(el) ? "" : " [offscreen]";
    let extra = "";
    if (el instanceof HTMLInputElement || el instanceof HTMLTextAreaElement) {
      if (el.value && !isSecret(el)) {
        extra = ' value="' + String(el.value).slice(0, 40) + '"';
      }
    }
    return role + ' "' + name + '" [' + ref + "]" + extra + off;
  }

  function collectInteractive() {
    const seen = new Set();
    const nodes = [];
    collectDeep(document, nodes, seen);
    const formish = [];
    const rest = [];
    for (const el of nodes) {
      if (el instanceof HTMLInputElement && el.type === "file") {
        formish.push(el);
        continue;
      }
      if (!isInteractive(el)) {
        continue;
      }
      const role = roleOf(el);
      const name = accessibleName(el);
      const inForm = Boolean(el.closest && el.closest("form"));
      if (
        ["textbox", "combobox", "checkbox", "radio", "file", "listbox"].includes(role)
        || inForm
        || (role === "button" && /select|submit|yes|no|available|continue|next/i.test(name))
      ) {
        formish.push(el);
      } else if (!inViewport(el) && role === "link") {
        continue;
      } else {
        rest.push(el);
      }
    }
    return formish.concat(rest).slice(0, 200).map(lineFor);
  }

  function readPage(input) {
    const filter = (input && input.filter) || "interactive";
    if (input && input.ref) {
      let ref = input.ref;
      if (ref.indexOf("::") >= 0) {
        ref = ref.split("::").pop();
      }
      const el = refMap.get(ref);
      if (!el) {
        throw new Error(input.ref + " is stale or not found on the current page.");
      }
      const descendants = [];
      const seen = new Set();
      collectDeep(el, descendants, seen);
      const lines = [lineFor(el)].concat(descendants.filter(isInteractive).map(lineFor));
      return lines.join("\n");
    }
    const lines = collectInteractive();
    const header = "url: " + location.href + "\ntitle: " + document.title + "\nframe: " + (window.top === window ? "top" : "iframe") + "\n";
    if (filter === "interactive" || filter === "all") {
      return header + lines.join("\n");
    }
    return header + (document.body && document.body.innerText ? document.body.innerText.slice(0, 8000) : "");
  }

  function findQuery(query) {
    const tokens = String(query || "").toLowerCase().split(/\s+/).filter(Boolean);
    const lines = collectInteractive();
    const matches = lines.filter((line) => {
      const lower = line.toLowerCase();
      return tokens.every((token) => lower.includes(token));
    });
    return matches.slice(0, 20).join("\n") || "No matches for " + query;
  }

  function looksDangerous(el) {
    const blob = [accessibleName(el), el.innerText, el.getAttribute("name"), el.getAttribute("value")]
      .filter(Boolean)
      .join(" ");
    return DANGEROUS.test(blob);
  }

  function formHasDangerousSubmit(el) {
    const form = (el && el.form) || (el && el.closest && el.closest("form"));
    if (!form || !form.querySelectorAll) {
      return false;
    }
    const submitters = form.querySelectorAll("button, input[type='submit']");
    return Array.from(submitters).some((node) => {
      const type = (node.getAttribute("type") || "").toLowerCase();
      if (node.tagName === "BUTTON" && type && type !== "submit") {
        return false;
      }
      return looksDangerous(node);
    });
  }

  function execute(action, allowDangerous) {
    const name = action.name;
    const input = action.input || {};
    if (name === "read_page") {
      return { kind: "text", text: readPage(input) };
    }
    if (name === "find") {
      return { kind: "text", text: findQuery(input.query) };
    }
    if (name === "get_page_text") {
      return { kind: "text", text: (document.body && document.body.innerText ? document.body.innerText : "").slice(0, 12000) };
    }
    if (name === "wait") {
      return { kind: "text", text: "Waited " + (input.duration || 0) + "s" };
    }
    if (name === "scroll") {
      const amount = (Number(input.scroll_amount) || 3) * 120;
      const dir = input.scroll_direction || "down";
      const dx = dir === "left" ? -amount : dir === "right" ? amount : 0;
      const dy = dir === "up" ? -amount : dir === "down" ? amount : 0;
      window.scrollBy(dx, dy);
      return { kind: "text", text: "Scrolled " + dir };
    }
    if (name === "scroll_to") {
      const resolved = resolveTarget(input.target);
      if (resolved.error) throw new Error(resolved.error);
      resolved.el.scrollIntoView({ block: "center" });
      return { kind: "text", text: "Scrolled to element" };
    }
    if (name === "hover" || name === "mouse_move") {
      const resolved = resolveTarget(input.target);
      if (resolved.error) throw new Error(resolved.error);
      const box = resolved.el.getBoundingClientRect();
      showGhost(box.left + box.width / 2, box.top + box.height / 2);
      resolved.el.dispatchEvent(new MouseEvent("mouseover", { bubbles: true }));
      return { kind: "text", text: "Moved pointer" };
    }
    if (name === "left_click" || name === "double_click") {
      const resolved = resolveTarget(input.target);
      if (resolved.error) throw new Error(resolved.error);
      if (!resolved.el) throw new Error("No element at that target");
      const dangerous = looksDangerous(resolved.el);
      if (dangerous && !allowDangerous) {
        return {
          kind: "text",
          text: "Blocked pending confirmation",
          blocked: true,
          dangerous: true,
          dangerousSummary: "About to click " + (accessibleName(resolved.el) || "a submit control"),
        };
      }
      if (name === "double_click") {
        resolved.el.dispatchEvent(new MouseEvent("dblclick", { bubbles: true }));
      } else {
        clickEl(resolved.el);
      }
      return { kind: "text", text: "Clicked " + (resolved.ref || "coordinates") };
    }
    if (name === "form_input") {
      const resolved = resolveTarget(input.target);
      if (resolved.error) throw new Error(resolved.error);
      fill(resolved.el, input.value);
      return { kind: "text", text: "Filled " + (resolved.ref || "field") };
    }
    if (name === "attach_file") {
      return attachFile(input);
    }
    if (name === "form_map") {
      return { kind: "text", text: formMap() };
    }
    if (name === "click_text") {
      const el = findByText(input.text);
      if (!el) {
        throw new Error("No control named " + (input.text || ""));
      }
      if (looksDangerous(el) && !allowDangerous) {
        return {
          kind: "text",
          text: "Blocked pending confirmation",
          blocked: true,
          dangerous: true,
          dangerousSummary: "About to click " + (accessibleName(el) || input.text || "a submit control"),
        };
      }
      clickEl(el);
      return { kind: "text", text: "Clicked " + (input.text || "") };
    }
    if (name === "type") {
      const el = document.activeElement;
      if (!(el instanceof HTMLElement) || el === document.body) {
        throw new Error("No focused field to type into. Click or form_input first.");
      }
      const current = "value" in el ? el.value : el.innerText || "";
      fill(el, current + (input.text || ""));
      return { kind: "text", text: "Typed text" };
    }
    if (name === "key") {
      const el = document.activeElement || document.body;
      const keys = String(input.text || "").split(/\s+/);
      const submits = keys.some((key) => key === "Enter");
      if (submits && el instanceof HTMLElement && formHasDangerousSubmit(el) && !allowDangerous) {
        return {
          kind: "text",
          text: "Blocked pending confirmation",
          blocked: true,
          dangerous: true,
          dangerousSummary: "Enter would submit this form",
        };
      }
      if (keys.length === 1 && (keys[0] === "Tab" || keys[0] === "tab")) {
        const focusable = Array.from(
          document.querySelectorAll("input, textarea, select, button, a, [role='combobox'], [role='button'], [tabindex='0']")
        ).filter((node) => notHidden(node));
        const index = focusable.indexOf(document.activeElement);
        const next = focusable[index + 1] || focusable[0];
        if (next) {
          next.focus();
        }
        return { kind: "text", text: "Pressed Tab" };
      }
      keys.forEach((key) => {
        el.dispatchEvent(new KeyboardEvent("keydown", { key, bubbles: true }));
        el.dispatchEvent(new KeyboardEvent("keyup", { key, bubbles: true }));
      });
      return { kind: "text", text: "Pressed " + input.text };
    }
    throw new Error("Unimplemented member: " + name);
  }

  chrome.runtime.onMessage.addListener((message, _sender, sendResponse) => {
    try {
      if (message.type === "startRecording") {
        startRecording();
        sendResponse({ ok: true });
        return;
      }
      if (message.type === "stopRecording") {
        stopRecording();
        sendResponse({ ok: true });
        return;
      }
      if (message.type === "execute") {
        const result = execute(message.action || {}, Boolean(message.allowDangerous));
        sendResponse({
          ok: true,
          result,
          blocked: Boolean(result.blocked),
          dangerous: Boolean(result.dangerous),
          dangerousSummary: result.dangerousSummary || "",
          href: location.href,
        });
        return;
      }
      sendResponse({ error: "Unknown content message" });
    } catch (error) {
      sendResponse({ error: error.message || String(error) });
    }
  });

  window.addEventListener("popstate", resetRefs);
  window.addEventListener("hashchange", resetRefs);
})();
