from fastapi import FastAPI, HTTPException, UploadFile, File
from fastapi.middleware.cors import CORSMiddleware
from schemas import GenerateRequest
from groq import Groq
from dotenv import load_dotenv
import pdfplumber
import tempfile

import os

load_dotenv()

app = FastAPI(title="ApplyAI BackEnd")

client = Groq(api_key = os.getenv("GROQ_API_KEY"))

# Model fallback chain — if the primary model fails (rate limit, overload),
# we try the next one. Keeps the extension working even during Groq outages.
MODEL_CHAIN = [
    "llama-3.3-70b-versatile",   # best quality, but rate-limited on free tier
    "llama-3.1-8b-instant",       # faster, lower quality, higher rate limits
]

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],       
    allow_methods=["*"],
    allow_headers=["*"],
)

# ─────────────────────────────────────────────────────────────────────────────
# SYSTEM PROMPTS — separated from user data for better LLM instruction-following
#
# Why split system vs user?
# - system = "who you are and how to behave" (stable across requests)
# - user   = "here's the data, do the task" (changes every request)
# - LLMs follow instructions better when roles are clearly separated
# ─────────────────────────────────────────────────────────────────────────────

COVER_LETTER_SYSTEM = """You are an expert job application strategist. Your goal is to get this candidate shortlisted — by both human recruiters AND ATS systems.

STRATEGY — follow this thinking process before writing:
1. Extract the top 5-6 keywords/skills the JD is asking for (these are what ATS scans for)
2. Find where those keywords appear in the candidate's resume — projects, skills, experience
3. For each match, note one concrete thing the candidate built or did with that skill
4. Identify what value this candidate brings to the team based on these matches

STRUCTURE (greeting + 3 short paragraphs + sign-off, blank line between each):

GREETING:
Start with "Hi [recruiter's name]," if a recruiter/hiring manager name is found in the JD. If no name is found, use "Hi Hiring Team,". Keep it simple and warm.

Paragraph 1 — THE HOOK (1-2 sentences):
Why this specific role at this specific company is a fit. Mention the role title and company name. No generic openers.

Paragraph 2 — THE PROOF (3-4 sentences):
Map the candidate's resume directly to what the JD needs. For each matching skill or project, briefly say what they built and what it shows. Use the exact keywords from the JD naturally — this is what gets past ATS. If the JD asks for React and the resume has a React project, name the project and what it does in one line.

Paragraph 3 — THE CLOSE (1-2 sentences):
What they bring to the team — a specific skill, mindset, or capability the team needs. End with a confident, direct call to action.

SIGN-OFF:
End with "Thank you," on its own line, then the candidate's full name from the resume. Below the name, include their phone number, GitHub link, and/or LinkedIn link — ONLY if these are present in the resume. Each on its own line. Do NOT invent or guess contact details that aren't in the resume.

HARD RULES:
- Max 150 words total (excluding the sign-off block) — recruiters skim, make every word count
- Use keywords from the JD naturally throughout (ATS optimization)
- NO bullet points — write in flowing paragraphs
- NO filler phrases: "I am writing to apply", "I believe I would be", "Although I'm early in my career", "I may not have all"
- NO placeholders like [Your Name] or [Company] — use real names from resume/JD
- If there's an experience gap, don't mention it — focus on what DOES match
- Sound like a confident human, not an AI template
- Output ONLY the message. No preamble, no labels, nothing extra."""

COLD_EMAIL_SYSTEM = """You are an expert cold outreach strategist. Your goal is to get a reply — not just a read. You write emails that are short, specific, and impossible to ignore.

STRATEGY — before writing:
1. Identify the ONE thing from the candidate's resume that would make the recipient care
2. Find the specific pain point or need from the JD that this candidate solves
3. Make the connection obvious in as few words as possible

STRUCTURE:

LINE 1 — SUBJECT LINE:
Short, specific, curiosity-driven. Reference the role or a specific skill match. No generic "Application for..." subjects. Max 8 words.

GREETING:
"Hi [name]," if a name is in the JD. Otherwise "Hi Hiring Team,". Nothing else.

Paragraph 1 — THE HOOK (1-2 sentences):
Lead with something specific about the company or role that caught their attention. Show you've done your research.

Paragraph 2 — THE VALUE (2-3 sentences):
One concrete project or skill from the resume that directly solves what they need. Name the project, what it does, and why it matters for THIS role. Use JD keywords naturally.

Paragraph 3 — THE ASK (1 sentence):
A clear, low-friction ask. "Would love to chat for 10 minutes" or "Happy to share more details" — not "Please consider my application".

SIGN-OFF:
"Best," or "Cheers," then candidate's name. Below: phone, GitHub, LinkedIn — ONLY if present in resume.

HARD RULES:
- Max 100 words total (excluding subject line and sign-off) — cold emails must be scannable
- Subject line on the FIRST line, then a blank line, then the email
- NO bullet points — flowing sentences only
- NO desperation: "I hope you'll consider", "I would be grateful for any opportunity"
- Sound like a peer reaching out, not a job applicant begging
- Output ONLY the email. No preamble, no labels."""

# Temperature per tone — controls creativity vs focus
TONE_TEMPERATURES = {
    "professional": 0.6,   # focused, polished
    "enthusiastic": 0.8,   # more creative, energetic
    "concise":      0.5,   # tight, every word precise
    "technical":    0.6,    # accurate, specific
}

TONE_INSTRUCTIONS = {
    "professional":  "formal and professional",
    "enthusiastic":  "warm, enthusiastic, and genuine — show real excitement",
    "concise":       "extremely concise and punchy — every word earns its place",
    "technical":     "technically focused — highlight specific technical skills that match the JD",
}

# Prompt Builder — returns system message, user message, and temperature
def build_prompt(req: GenerateRequest) -> dict:
    # Pick system prompt based on message type
    if req.message_type == "cold_email":
        system_prompt = COLD_EMAIL_SYSTEM
    else:
        system_prompt = COVER_LETTER_SYSTEM

    tone_style = TONE_INSTRUCTIONS.get(req.tone, "professional")
    temperature = TONE_TEMPERATURES.get(req.tone, 0.6)

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

# ─────────────────────────────────────────────────────────────────────────────
# /generate — the only endpoint the extension calls
#
# Flow:
# 1. Extension POSTs { resume, job_text, job_url, tone, message_type }
# 2. We build the system + user prompts
# 3. We call Groq with llama-3.3-70b-versatile (free, fast, great quality)
# 4. We return { message: "..." } back to the extension
# ─────────────────────────────────────────────────────────────────────────────
@app.post("/generate")
async def generate_message(req: GenerateRequest):
    # Basic validation — don't waste a Groq call on empty data
    if len(req.resume.strip()) < 50:
        raise HTTPException(status_code=400, detail="Resume is too short or empty.")
    if len(req.job_text.strip()) < 50:
        raise HTTPException(status_code=400, detail="Job text is too short. Try scanning the page again.")
 
    prompt = build_prompt(req)
 
    # Try each model in the fallback chain
    # If the primary model fails (rate limit, overload), we try the next one
    last_error = None
    for model in MODEL_CHAIN:
        try:
            response = client.chat.completions.create(
                model=model,
                messages=[
                    {"role": "system", "content": prompt["system"]},
                    {"role": "user",   "content": prompt["user"]},
                ],
                max_tokens=700,
                temperature=prompt["temperature"],
            )
            message = response.choices[0].message.content.strip()
            return {"message": message, "model_used": model}
        except Exception as e:
            last_error = e
            continue  # try next model in the chain
 
    # All models failed
    raise HTTPException(status_code=503, detail=str(last_error))

 
 
# ─────────────────────────────────────────────────────────────────────────────
# /upload-resume — PDF resume upload
#
# Accepts a PDF file, extracts text using pdfplumber, returns plain text.
# The extension saves this text in chrome.storage.local just like pasted text.
# ─────────────────────────────────────────────────────────────────────────────
@app.post("/upload-resume")
async def upload_resume(file: UploadFile = File(...)):
    if not file.filename.lower().endswith(".pdf"):
        raise HTTPException(status_code=400, detail="Only PDF files are supported.")
 
    try:
        # Save to temp file, extract with pdfplumber, clean up
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




# ─────────────────────────────────────────────────────────────────────────────
# HEALTH CHECK
# Optional but useful — hit http://localhost:8000/health to confirm server is up
# ─────────────────────────────────────────────────────────────────────────────
@app.get("/health")
def health():
    return {"status": "ok"}