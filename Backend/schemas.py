# schemas.py — request models for our API
#
# Pydantic validates all incoming JSON automatically. If someone sends a
# request missing a field or with the wrong type, FastAPI returns a clean
# 422 error without us writing any validation code.

from pydantic import BaseModel

class GenerateRequest(BaseModel):
    resume: str        # plain text resume (pasted or extracted from PDF)
    job_text: str      # scraped job description from the page
    job_url: str       # URL of the job posting (used in the prompt for context)
    tone: str          # "professional" | "enthusiastic" | "concise" | "technical"
    message_type: str  # "cover_letter" | "cold_email"