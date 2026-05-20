# ApplyAI

AI-powered Chrome extension that reads job listings and generates tailored cover letters and cold emails.

## How it works

1. Open any job posting (LinkedIn, Naukri, Indeed, etc.)
2. Click the ApplyAI extension icon
3. Hit **Scan Page** — the extension reads the job description
4. Hit **Generate** — get a tailored cover letter or cold email in seconds

The extension talks to a FastAPI backend that uses Groq's LLM API to generate messages. Your resume stays in your browser's local storage and is only sent to your own server when you generate.

## Project Structure

```
CoverAI/
├── Backend/
│   ├── main.py           # FastAPI server — prompts, endpoints, rate limiting
│   ├── schemas.py        # Pydantic request models
│   ├── requirements.txt  # Python dependencies
│   ├── Procfile           # Tells Railway/Render how to start the server
│   └── .env              # Your Groq API key (not committed to git)
│
└── Extension/
    ├── manifest.json     # Chrome extension config
    ├── popup.html        # Extension popup UI
    ├── popup.css         # Styles
    ├── popup.js          # UI logic, backend calls, drag-drop upload
    └── content.js        # Injected into job pages to scrape the description
```

## Setup

### Backend

```bash
cd Backend
python -m venv env
source env/bin/activate
pip install -r requirements.txt
```

Create a `.env` file:
```
GROQ_API_KEY=your_groq_api_key_here
```

Run the server:
```bash
uvicorn main:app --reload
```

### Extension

1. Go to `chrome://extensions`
2. Enable **Developer mode** (top right)
3. Click **Load unpacked** → select the `Extension/` folder
4. Pin the extension and you're good to go


## Features

- **ATS-optimized** — uses exact keywords from the job description
- **Cover letters & cold emails** — two message types with different strategies
- **4 tones** — Professional, Enthusiastic, Short & Punchy, Technical
- **PDF resume upload** — drag & drop, text extracted automatically
- **Platform detection** — recognizes LinkedIn, Naukri, Indeed, Glassdoor, and 8 more
- **Model fallback** — if the primary model is rate-limited, falls back to a faster one
- **Rate limiting** — protects your API key from abuse
- **Editable output** — tweak the generated message before copying
- **Character count** — live counter on the output

## Tech Stack

- **Backend**: Python, FastAPI, Groq API (Llama 3.3 70B)
- **Extension**: Vanilla JS, Chrome Extensions API (Manifest V3)
- **PDF parsing**: pdfplumber
- **Rate limiting**: slowapi
