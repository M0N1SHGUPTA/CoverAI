from pydantic import BaseModel

class GenerateRequest(BaseModel):
    resume: str
    job_text: str
    job_url: str
    tone: str
    message_type: str