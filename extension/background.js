const API = 'http://127.0.0.1:8765';

chrome.runtime.onMessage.addListener((message, _sender, sendResponse) => {
  if (!message || message.type !== 'triage-api') return false;

  fetch(`${API}${message.path}`, {
    method: message.method || 'POST',
    headers: { 'content-type': 'application/json' },
    body: message.body === undefined ? undefined : JSON.stringify(message.body),
  })
    .then(async (response) => {
      const text = await response.text();
      let body;
      try {
        body = text ? JSON.parse(text) : null;
      } catch (_error) {
        body = { raw: text };
      }
      sendResponse({ ok: response.ok, status: response.status, statusText: response.statusText, body });
    })
    .catch((error) => {
      sendResponse({ ok: false, status: 0, statusText: error.message, body: { error: error.name, message: error.message } });
    });

  return true;
});
