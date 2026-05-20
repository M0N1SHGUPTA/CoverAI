# main.py — the FastAPI backend that powers the ApplyAI chrome extension
#
# This server sits between the extension and Groq's API. The extension sends
# over a resume + scraped job description, we build a tailored prompt, hit
# Groq, and send back a ready-to-use cover letter or cold email.
#
# Endpoints:
#   POST /generate       — builds the message using AI
#   POST /upload-resume  — extracts text from a PDF resume
#   GET  /health         — simple alive check
#   GET  /privacy        — serves the privacy policy page

from fastapi import FastAPI, HTTPException, Request, UploadFile, File
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, HTMLResponse
from slowapi import Limiter
from slowapi.util import get_remote_address
from slowapi.errors import RateLimitExceeded
from schemas import GenerateRequest
from groq import Groq
from dotenv import load_dotenv
import pdfplumber
import tempfile
from datetime import datetime
import os

load_dotenv()

# --- Rate Limiting ---
#
# Without this, anyone who finds your server URL could spam /generate and
# burn through your Groq API quota in minutes. slowapi tracks requests by
# IP address and returns a 429 (Too Many Requests) if someone goes too fast.
#
# Current limits:
#   /generate      → 10 requests per minute (one cover letter every 6 seconds is plenty)
#   /upload-resume → 5 per minute (you're not uploading 5 resumes a minute)
#   /health        → no limit (it's just a ping)
#
# The key_func tells slowapi how to identify each user. We use their IP address.
# Behind a reverse proxy (Railway, Render, etc.), make sure X-Forwarded-For
# headers are set correctly or everyone will share the same limit.
limiter = Limiter(key_func=get_remote_address)

app = FastAPI(title="ApplyAI BackEnd")
app.state.limiter = limiter

# When someone hits the rate limit, return a clean JSON error instead of
# a generic 500. The extension checks for 429 and shows a friendly message.
@app.exception_handler(RateLimitExceeded)
async def rate_limit_handler(request: Request, exc: RateLimitExceeded):
    return JSONResponse(
        status_code=429,
        content={"detail": "You're sending too many requests. Wait a minute and try again."},
    )

client = Groq(api_key=os.getenv("GROQ_API_KEY"))

# --- Usage Analytics ---
#
# Simple in-memory tracker so you can see how many people are actually using
# the extension after you deploy. It tracks:
#   - unique IPs (≈ unique users)
#   - total calls per endpoint
#   - the last 50 requests with timestamps
#
# This lives in memory, so it resets when the server restarts. That's fine
# for early stage — if you need persistent analytics later, swap this dict
# for a Redis store or a database table.
#
# Check your stats anytime by hitting GET /stats
analytics = {
    "unique_ips": set(),          # set of all IPs that have ever called us
    "total_generate": 0,          # how many times /generate was called
    "total_uploads": 0,           # how many times /upload-resume was called
    "recent_requests": [],        # last 50 requests with timestamps
    "started_at": datetime.now().isoformat(),  # when the server started
}

def track_request(request: Request, endpoint: str):
    """Log a request for analytics. Called inside each endpoint we want to track."""
    ip = get_remote_address(request)
    analytics["unique_ips"].add(ip)

    if endpoint == "generate":
        analytics["total_generate"] += 1
    elif endpoint == "upload":
        analytics["total_uploads"] += 1

    # Keep only the last 50 requests so memory doesn't grow forever
    analytics["recent_requests"].append({
        "ip": ip,
        "endpoint": endpoint,
        "time": datetime.now().isoformat(),
    })
    if len(analytics["recent_requests"]) > 50:
        analytics["recent_requests"] = analytics["recent_requests"][-50:]

# If the main model hits a rate limit or goes down, we fall back to a smaller
# one so the user doesn't just get an error. The 70b model writes better but
# the 8b model is faster and has way higher rate limits on the free tier.
MODEL_CHAIN = [
    "llama-3.3-70b-versatile",
    "llama-3.1-8b-instant",
]

# CORS — controls which websites can call our API.
# In dev, we allow everything so the extension works without hassle.
# In production, set the ALLOWED_ORIGINS env variable to lock it down to
# just your extension. You'll find your extension's origin in chrome://extensions
# after you load it — it looks like "chrome-extension://abcdef1234567890".
#
# Example for .env in production:
#   ALLOWED_ORIGINS=chrome-extension://your-extension-id-here
#
# You can also comma-separate multiple origins if needed:
#   ALLOWED_ORIGINS=chrome-extension://abc123,https://yourdomain.com
allowed_origins = os.getenv("ALLOWED_ORIGINS", "*").split(",")

app.add_middleware(
    CORSMiddleware,
    allow_origins=allowed_origins,
    allow_methods=["*"],
    allow_headers=["*"],
)


# --- System Prompts ---
#
# We keep the "who you are" instructions separate from the "here's the data"
# part. LLMs follow rules way better when the system message is stable and
# the user message is just the variable stuff (resume, JD, tone).

COVER_LETTER_SYSTEM = """150 words max. Three paragraphs. No fluff. Count your words before outputting.

HARD RULES:
- HARD LIMIT: 150 words max. Count and verify before outputting.
- Do not repeat or paraphrase the company's website/JD back to them — they already know what they wrote.
- Do not invent experience not in the candidate's resume. No hallucination. If a skill isn't in the resume, don't mention it.
- Never repeat the same idea twice in the same letter.
- Skip a project if its relevance to the role isn't obvious.
- One paragraph per: hook, proof, CTA — nothing more.
- No closing fluff like "I'd love to discuss" or "I'm excited about the opportunity."
- These rules apply to ALL tones (professional, enthusiastic, concise, technical). No exceptions.

STRUCTURE — exactly 3 paragraphs + sign-off:

Greeting: "Hi [name]," if found in JD. Otherwise "Hi Hiring Team,"

HOOK (2 sentences max):
What specifically about this company/product/role caught the candidate's attention — and why they're a fit. Not a summary of the JD.

PROOF (3 sentences max):
Map 2-3 resume items directly to JD requirements. Name the project, what it does, the tech. Use JD keywords for ATS. Every sentence = one resume-to-JD connection. If a project doesn't clearly match, skip it.

CTA (1 sentence):
One short, direct line. "Happy to walk through any of this." Done.

Sign-off: "Best," then full name. Phone/GitHub/LinkedIn ONLY if in resume.

BANNED — rewrite immediately if any appear:
"I am excited/thrilled/eager to apply", "I am passionate about", "I bring a strong", "I believe I would be", "I look forward to", "leverage my", "Although I'm early in my career", "strong communication skills", "fast learner", "team player", "I'd love to discuss", "excited about the opportunity"

TONE: Direct, human. Not corporate. Not AI.
Output ONLY the letter."""

COLD_EMAIL_SYSTEM = """You write cold emails that get replies. 100-150 words. Every word earns its place.

RULES:
1. ONLY mention skills and projects from the resume. No hallucination.
2. Every claim connects to the JD. If it doesn't, cut it.
3. No begging. You're a peer, not an applicant.

STRUCTURE:

Line 1 — SUBJECT: Max 8 words. Specific. No "Application for..." subjects.

(blank line)

Greeting: "Hi [name]," if found in JD. Otherwise "Hi,"

Paragraph 1 — HOOK (1-2 sentences):
Something specific about the company or role. Show you did research.

Paragraph 2 — VALUE (2-3 sentences):
One project from the resume that solves what they need. Name it, what it does, connect to their JD keywords.

Paragraph 3 — ASK (1 sentence):
Low-friction. "Open to a quick chat this week?" Done.

Sign-off: "Best," then name. Phone, GitHub, LinkedIn — ONLY if in resume.

BANNED PHRASES:
- "I hope you'll consider", "I would be grateful"
- "I am reaching out regarding", "thrilled", "eager", "passionate"
- Any sentence that sounds like begging

TONE: Direct and human. Peer-to-peer.
Output ONLY the email. Nothing else."""

# Each tone needs a different temperature. "Concise" should be really focused
# (low temp = less randomness), while "enthusiastic" benefits from a bit more
# creative freedom.
TONE_TEMPERATURES = {
    "professional": 0.6,
    "enthusiastic": 0.8,
    "concise":      0.5,
    "technical":    0.6,
}

TONE_INSTRUCTIONS = {
    "professional":  "formal and professional",
    "enthusiastic":  "warm, enthusiastic, and genuine — show real excitement",
    "concise":       "extremely concise and punchy — every word earns its place",
    "technical":     "technically focused — highlight specific technical skills that match the JD",
}


def build_prompt(req: GenerateRequest) -> dict:
    """
    Takes the incoming request and builds the two messages we'll send to Groq:
    a system message (the role/rules) and a user message (the actual data).
    Also picks the right temperature for the selected tone.
    """
    if req.message_type == "cold_email":
        system_prompt = COLD_EMAIL_SYSTEM
    else:
        system_prompt = COVER_LETTER_SYSTEM

    tone_style = TONE_INSTRUCTIONS.get(req.tone, "professional")
    temperature = TONE_TEMPERATURES.get(req.tone, 0.6)

    # The user message is just the raw data — JD, resume, and chosen tone.
    # Keeping this clean makes it easier for the model to focus on the task.
    user_prompt = f"""JOB POSTING (from {req.job_url}):
\"\"\"{req.job_text}\"\"\"

CANDIDATE RESUME:
\"\"\"{req.resume}\"\"\"

TONE: {tone_style}"""

    return {
        "system": system_prompt,
        "user": user_prompt,
        "temperature": temperature,
    }


@app.post("/generate")
@limiter.limit("10/minute")
async def generate_message(request: Request, req: GenerateRequest):
    """
    The main endpoint. Extension sends resume + job text + tone + type,
    we generate a tailored message and send it back.
    """
    # Quick sanity checks before we burn a Groq API call
    if len(req.resume.strip()) < 50:
        raise HTTPException(status_code=400, detail="Resume is too short or empty.")
    if len(req.job_text.strip()) < 50:
        raise HTTPException(status_code=400, detail="Job text is too short. Try scanning the page again.")

    # Log this request so we can see usage in /stats
    track_request(request, "generate")

    prompt = build_prompt(req)

    # Loop through models — if the first one fails (rate limit, 503, etc.),
    # we try the backup. This way the user almost never sees an error.
    last_error = None
    for model in MODEL_CHAIN:
        try:
            response = client.chat.completions.create(
                model=model,
                messages=[
                    {"role": "system", "content": prompt["system"]},
                    {"role": "user",   "content": prompt["user"]},
                ],
                max_tokens=350,
                temperature=prompt["temperature"],
            )
            message = response.choices[0].message.content.strip()
            return {"message": message, "model_used": model}
        except Exception as e:
            last_error = e
            continue

    # If we get here, every model in the chain failed
    raise HTTPException(status_code=503, detail=str(last_error))


@app.post("/upload-resume")
@limiter.limit("5/minute")
async def upload_resume(request: Request, file: UploadFile = File(...)):
    """
    Accepts a PDF, extracts all the text from it using pdfplumber, and
    returns the plain text. The extension then saves this text locally
    just like if the user had pasted it manually.
    """
    if not file.filename.lower().endswith(".pdf"):
        raise HTTPException(status_code=400, detail="Only PDF files are supported.")

    # Log this upload for analytics
    track_request(request, "upload")

    try:
        # We write to a temp file because pdfplumber needs a file path.
        # The `delete=True` flag cleans it up automatically after we're done.
        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=True) as tmp:
            content = await file.read()
            tmp.write(content)
            tmp.flush()

            text_parts = []
            with pdfplumber.open(tmp.name) as pdf:
                for page in pdf.pages:
                    page_text = page.extract_text()
                    if page_text:
                        text_parts.append(page_text)

        resume_text = "\n\n".join(text_parts).strip()

        if len(resume_text) < 50:
            raise HTTPException(status_code=400, detail="Could not extract enough text from PDF. Try pasting manually.")

        return {"resume_text": resume_text}

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error reading PDF: {str(e)}")


@app.get("/health")
def health():
    """Quick check to see if the server is up. Hit /health and look for 'ok'."""
    return {"status": "ok"}


@app.get("/privacy", response_class=HTMLResponse)
def privacy_policy():
    """Serves the privacy policy page. This is the URL you paste into the Chrome Web Store."""
    policy_path = os.path.join(os.path.dirname(__file__), "..", "docs", "privacy.html")
    # Fallback: if deployed flat (no docs/ folder alongside), check same directory
    if not os.path.exists(policy_path):
        policy_path = os.path.join(os.path.dirname(__file__), "privacy.html")
    with open(policy_path, "r", encoding="utf-8") as f:
        return HTMLResponse(content=f.read())


@app.get("/stats")
def stats():
    """
    Usage dashboard — hit this endpoint to see how many people are using
    your extension. Shows unique users (by IP), total calls, and recent
    activity. No auth for now since it's your personal project, but add
    a secret token check here if you make the repo public.
    """
    return {
        "unique_users": len(analytics["unique_ips"]),
        "total_generate_calls": analytics["total_generate"],
        "total_upload_calls": analytics["total_uploads"],
        "total_calls": analytics["total_generate"] + analytics["total_uploads"],
        "server_started_at": analytics["started_at"],
        "recent_requests": analytics["recent_requests"][-10:],  # show last 10
    }