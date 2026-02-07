from __future__ import annotations
from pydantic import BaseModel, EmailStr
from pydantic import BaseModel, Field
from typing import List, Literal, Dict, Any

Language = Literal["fr", "en"]

class StudentFacingFeedback(BaseModel):
    strengths: List[str] = Field(min_length=3, max_length=5)
    areas_to_improve: List[str] = Field(min_length=3, max_length=5)
    reflective_question: str = Field(min_length=10, max_length=400)

class SkillIndicators(BaseModel):
    active_listening: bool
    reformulation: bool
    emotional_validation: bool
    open_questions: bool
    structure_clarity: bool

class FeedbackSchema(BaseModel):
    language: Language
    student_facing: StudentFacingFeedback
    internal_scores: Dict[str, int]          # empathy/structure/alliance
    skill_indicators: Dict[str, bool]        # active_listening/reformulation/therapeutic_alliance
    kpis: Dict[str, Any]

class FeedbackStudentResponse(BaseModel):
    """
    Student-facing API response:
    - allowed: student_facing + internal_scores (shown as 'indicators', not grades)
    - NOT allowed: skill_indicators, kpis
    """
    language: Language
    student_facing: StudentFacingFeedback
    internal_scores: Dict[str, int]  # empathy/structure/alliance (1..5)

class FeedbackAdminResponse(BaseModel):
    """
    Admin-facing API response includes all internal data.
    """
    language: Language
    student_facing: StudentFacingFeedback
    internal_scores: Dict[str, int]
    skill_indicators: SkillIndicators
    kpis: Dict[str, Any]

class SignupProfileUpdate(BaseModel):
    level: Literal["4e", "5e", "autre"]
    preferred_language: Language = "fr"

class DashboardResponse(BaseModel):
    completed: int
    available_session_number: int
    sessions: List[Dict[str, Any]]
    badges: List[str]

class ChatSendRequest(BaseModel):
    message: str = Field(min_length=1, max_length=4000)

class ChatSendResponse(BaseModel):
    patient_message: str
    language: Language
    session_number: int
    patient_age: int
    patient_gender_label: str

class EndSessionResponse(BaseModel):
    session_id: str
    status: str

class StudentFacingFeedback(BaseModel):
    strengths: List[str] = Field(min_length=3, max_length=5)
    areas_to_improve: List[str] = Field(min_length=3, max_length=5)
    reflective_question: str = Field(min_length=10, max_length=400)

class FeedbackSchema(BaseModel):
    language: Language
    student_facing: StudentFacingFeedback
    internal_scores: Dict[str, int]  # empathy/structure/alliance 1-5
    skill_indicators: Dict[str, bool]
    kpis: Dict[str, Any]

class QuestionnaireSubmit(BaseModel):
    q1: int = Field(ge=1, le=5)
    q2: int = Field(ge=1, le=5)
    open_answer: str = Field(min_length=0, max_length=2000)

class LoginRequest(BaseModel):
    email: EmailStr
    password: str

class LoginResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user: dict