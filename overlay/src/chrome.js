export class ExtensionBridge {
  available() {
    return typeof chrome !== "undefined" && chrome.runtime && chrome.runtime.sendMessage;
  }

  send(message) {
    if (!this.available()) {
      return Promise.reject(new Error("Open this UI from the Chrome extension side panel."));
    }
    return chrome.runtime.sendMessage(message);
  }
}
