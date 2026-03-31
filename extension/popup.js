/**
 * GFA DATA 수집 - Popup Script
 * Handles button click, communicates with content script, and sends data to server.
 */

// Load saved settings
document.addEventListener('DOMContentLoaded', () => {
  chrome.storage.local.get(['serverUrl', 'apiKey'], (result) => {
    if (result.serverUrl) document.getElementById('serverUrl').value = result.serverUrl;
    if (result.apiKey) document.getElementById('apiKey').value = result.apiKey;
  });
});

// Save settings on change
document.getElementById('serverUrl').addEventListener('change', (e) => {
  chrome.storage.local.set({ serverUrl: e.target.value });
});
document.getElementById('apiKey').addEventListener('change', (e) => {
  chrome.storage.local.set({ apiKey: e.target.value });
});

// Collect button handler
document.getElementById('collectBtn').addEventListener('click', async () => {
  const serverUrl = document.getElementById('serverUrl').value.trim();
  const apiKey = document.getElementById('apiKey').value.trim();
  const statusEl = document.getElementById('status');
  const btn = document.getElementById('collectBtn');

  if (!serverUrl) {
    statusEl.className = 'error';
    statusEl.textContent = '서버 URL을 입력해주세요.';
    return;
  }

  btn.disabled = true;
  statusEl.className = 'loading';
  statusEl.textContent = '데이터 수집 중...';

  try {
    // Send message to content script to scrape the page
    const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });

    const response = await chrome.tabs.sendMessage(tab.id, { action: 'scrapeGFA' });

    if (!response || !response.success) {
      throw new Error(response?.error || 'GFA 페이지에서 데이터를 찾을 수 없습니다.');
    }

    const { rows, date: reportDate } = response;

    statusEl.textContent = `${rows.length}건 수집 완료. 서버로 전송 중...`;

    // Send to server
    const apiResponse = await fetch(`${serverUrl}/api/ingest/gfa`, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        'X-API-Key': apiKey,
      },
      body: JSON.stringify({
        rows: rows,
        date: reportDate,
      }),
    });

    if (!apiResponse.ok) {
      const errorData = await apiResponse.json();
      throw new Error(errorData.detail || `서버 오류: ${apiResponse.status}`);
    }

    const result = await apiResponse.json();
    statusEl.className = 'success';
    statusEl.textContent = `전송 완료! ${result.rows_loaded}건 적재됨.`;

  } catch (error) {
    statusEl.className = 'error';
    statusEl.textContent = `오류: ${error.message}`;
  } finally {
    btn.disabled = false;
  }
});
