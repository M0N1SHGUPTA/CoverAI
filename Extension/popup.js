// popup.js — the brains behind the extension popup
//
// This runs every time the user clicks the extension icon. It handles
// everything in the popup: switching tabs, saving the resume, scanning
// the job page, calling our backend, and showing the generated message.
//
// No API keys live here — all AI calls go through our FastAPI server.

// --- Backend URL ---
// During development, use localhost. When you deploy your backend to
// Railway/Render/Fly.io, swap this to your deployed URL.
//
// For dev:
// const BACKEND_URL = 'http://localhost:8000';
//
// For production:
const BACKEND_URL = 'https://coverai-dqa7.onrender.com';

// We trim the resume before sending to save tokens. 3000 chars is plenty
// for the LLM to understand someone's background.
const MAX_RESUME_CHARS = 3000;

// Grab all the DOM elements we'll need. Doing this once at the top is
// faster than querying inside every event handler.
const tabs = document.querySelectorAll('.tab');
const tabContents = document.querySelectorAll('.tab-content');
const scanBtn = document.getElementById('scanBtn');
const generateBtn = document.getElementById('generateBtn');
const statusDot = document.getElementById('statusDot');
const statusText = document.getElementById('statusText');
const jobPreview = document.getElementById('jobPreview');
const previewTitle = document.getElementById('previewTitle');
const previewUrl = document.getElementById('previewUrl');
const platformBadge = document.getElementById('platformBadge');
const toneSelect = document.getElementById('toneSelect');
const typeSelect = document.getElementById('typeSelect');
const outputSection = document.getElementById('outputSection');
const outputArea = document.getElementById('outputArea');
const copyBtn = document.getElementById('copyBtn');
const regenerateBtn = document.getElementById('regenerateBtn');
const copyFeedback = document.getElementById('copyFeedback');
const charCount = document.getElementById('charCount');
const loadingSection = document.getElementById('loadingSection');
const loadingText = document.getElementById('loadingText');
const errorBox = document.getElementById('errorBox');
const errorText = document.getElementById('errorText');
const resumeInput = document.getElementById('resumeInput');
const saveResumeBtn = document.getElementById('saveResume');
const saveFeedback = document.getElementById('saveFeedback');
const dropZone = document.getElementById('dropZone');
const uploadStatus = document.getElementById('uploadStatus');

// The scanned job data lives here between scan and generate.
// Gets populated when the user clicks "Scan Page".
let scannedJob = null;

// When the popup opens, load whatever resume was saved previously.
// chrome.storage.local persists across popup opens and browser restarts.
document.addEventListener('DOMContentLoaded', async () => {
  const data = await chrome.storage.local.get(['resume']);
  if (data.resume) resumeInput.value = data.resume;
});

// Tab switching — the data-tab attribute on each button maps to the
// id of the corresponding content panel (e.g., data-tab="generate" → #tab-generate)
tabs.forEach(tab => {
  tab.addEventListener('click', () => {
    tabs.forEach(t => t.classList.remove('active'));
    tabContents.forEach(c => c.classList.remove('active'));
    tab.classList.add('active');
    document.getElementById(`tab-${tab.dataset.tab}`).classList.add('active');
  });
});

// Save the resume text to local storage. It stays on the user's machine
// and only gets sent to our server when they actually hit Generate.
saveResumeBtn.addEventListener('click', async () => {
  const resume = resumeInput.value.trim();
  if (!resume) return;
  await chrome.storage.local.set({ resume });
  saveFeedback.classList.remove('hidden');
  setTimeout(() => saveFeedback.classList.add('hidden'), 2000);
});


// --- PDF Drag & Drop ---
//
// We use drag & drop instead of a file picker because Chrome extension
// popups close the moment a native file dialog opens (the popup loses
// focus and Chrome kills it). Drag & drop keeps everything in the popup.

// Prevent the browser from navigating to a dropped file
document.addEventListener('dragover', (e) => e.preventDefault());
document.addEventListener('drop', (e) => e.preventDefault());

// Visual feedback when a file hovers over the drop zone
dropZone.addEventListener('dragover', (e) => {
  e.preventDefault();
  e.stopPropagation();
  dropZone.classList.add('drag-over');
});

dropZone.addEventListener('dragleave', (e) => {
  e.preventDefault();
  e.stopPropagation();
  dropZone.classList.remove('drag-over');
});

// When the user drops a file, send it to our backend for text extraction
dropZone.addEventListener('drop', async (e) => {
  e.preventDefault();
  e.stopPropagation();
  dropZone.classList.remove('drag-over');

  const file = e.dataTransfer.files[0];
  if (!file) return;

  if (!file.name.toLowerCase().endsWith('.pdf')) {
    uploadStatus.textContent = '✗ Only PDF files are supported';
    uploadStatus.className = 'upload-status error';
    return;
  }

  uploadStatus.textContent = 'Extracting text from PDF…';
  uploadStatus.className = 'upload-status uploading';

  try {
    const formData = new FormData();
    formData.append('file', file);

    const response = await fetch(`${BACKEND_URL}/upload-resume`, {
      method: 'POST',
      body: formData,
    });

    if (!response.ok) {
      const err = await response.json().catch(() => ({}));
      throw new Error(err?.detail || 'Upload failed');
    }

    // Paste the extracted text into the textarea and auto-save it
    const data = await response.json();
    resumeInput.value = data.resume_text;
    await chrome.storage.local.set({ resume: data.resume_text });

    uploadStatus.textContent = '✓ PDF imported & saved!';
    uploadStatus.className = 'upload-status success';
    setTimeout(() => { uploadStatus.textContent = ''; uploadStatus.className = 'upload-status'; }, 3000);
  } catch (err) {
    uploadStatus.textContent = `✗ ${err.message}`;
    uploadStatus.className = 'upload-status error';
    console.error('[ApplyAI] PDF upload error:', err);
  }
});


// --- Scan Page ---
//
// This is the trickiest part. The popup can't read another tab's page
// directly, so we inject content.js into the active tab and then ask
// it to scrape the job description and send it back.
scanBtn.addEventListener('click', async () => {
  setStatus('scanning', 'Scanning page...');
  hideError();
  hideOutput();

  try {
    const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });

    // Inject the content script. If it's already there from a previous
    // scan, the catch swallows the "already injected" error silently.
    await chrome.scripting.executeScript({ target: { tabId: tab.id }, files: ['content.js'] }).catch(() => { });

    // Ask content.js to scrape the page and send back the results
    const response = await chrome.tabs.sendMessage(tab.id, { action: 'extractJob' });

    if (!response || !response.text || response.text.length < 50) {
      setStatus('error', "Couldn't read this page. Scroll down and try again.");
      return;
    }

    // Store the scraped data so Generate can use it
    scannedJob = response;
    setStatus('ready', 'Job page scanned!');
    previewTitle.textContent = response.title || 'Job Listing';
    previewUrl.textContent = response.url;
    jobPreview.classList.remove('hidden');
    generateBtn.disabled = false;

    // Show which platform we detected (if any)
    if (response.platform) {
      platformBadge.textContent = response.platform;
      platformBadge.classList.remove('hidden');
    } else {
      platformBadge.classList.add('hidden');
    }
  } catch (err) {
    setStatus('error', 'Error reading page. Refresh the tab and try again.');
    console.error('[ApplyAI] Scan error:', err);
  }
});


// --- Generate ---
//
// Both the "Generate" button and the "Regenerate" icon trigger the same function.
generateBtn.addEventListener('click', () => doGenerate());
regenerateBtn.addEventListener('click', () => doGenerate());

async function doGenerate() {
  hideError();
  hideOutput();
  const { resume } = await chrome.storage.local.get(['resume']);

  // Make sure we have everything before calling the server
  if (!resume) { showError('Paste your resume in the "My Resume" tab first.'); return; }
  if (!scannedJob) { showError('Scan the job page first using "Scan Page".'); return; }

  showLoading('Generating your message…');
  try {
    const message = await callBackend({ resume, job: scannedJob, tone: toneSelect.value, type: typeSelect.value });
    hideLoading();
    showOutput(message);
  } catch (err) {
    hideLoading();
    showError(err.message || 'Something went wrong. Is the server running?');
  }
}

// Send resume + job data to our FastAPI backend and get back a message.
// The resume is trimmed to save tokens on the Groq side.
async function callBackend({ resume, job, tone, type }) {
  const response = await fetch(`${BACKEND_URL}/generate`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ resume: resume.slice(0, MAX_RESUME_CHARS), job_text: job.text, job_url: job.url, tone, message_type: type }),
  });

  if (!response.ok) {
    const err = await response.json().catch(() => ({}));
    if (response.status === 429) throw new Error('Rate limit hit. Try again in a moment.');
    if (response.status === 503) throw new Error('Server starting up. Try again in 10s.');
    throw new Error(err?.detail || `Server error ${response.status}`);
  }

  const data = await response.json();
  if (!data.message) throw new Error('Empty response from server.');
  return data.message;
}

// Copy the generated message to clipboard
copyBtn.addEventListener('click', async () => {
  const text = outputArea.value;
  if (!text) return;
  await navigator.clipboard.writeText(text);
  copyFeedback.classList.remove('hidden');
  setTimeout(() => copyFeedback.classList.add('hidden'), 2000);
});

// Live word count — updates as the user types or edits the output
outputArea.addEventListener('input', () => updateCharCount());
function updateCharCount() {
  const words = outputArea.value.trim().split(/\s+/).filter(w => w.length > 0);
  const len = words.length;
  charCount.textContent = `${len} word${len !== 1 ? 's' : ''}`;
}


// --- UI helpers ---
// Small functions to keep the event handlers above clean and readable.

function setStatus(state, message) {
  statusDot.className = 'status-indicator';
  if (state === 'ready') statusDot.classList.add('ready');
  if (state === 'error') statusDot.classList.add('error');
  statusText.textContent = message || state;
}
function showLoading(msg) { loadingText.textContent = msg || 'Working…'; loadingSection.classList.remove('hidden'); generateBtn.disabled = true; scanBtn.disabled = true; }
function hideLoading() { loadingSection.classList.add('hidden'); generateBtn.disabled = false; scanBtn.disabled = false; }
function showOutput(text) { outputArea.value = text; outputSection.classList.remove('hidden'); updateCharCount(); }
function hideOutput() { outputSection.classList.add('hidden'); outputArea.value = ''; charCount.textContent = '0 words'; }
function showError(msg) { errorText.textContent = msg; errorBox.classList.remove('hidden'); }
function hideError() { errorBox.classList.add('hidden'); }
