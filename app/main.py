from fastapi import FastAPI, Depends, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response
from .config import settings
from .auth import get_current_user, require_admin
from uuid import UUID
from datetime import datetime, timezone
from .db import supabase
from fastapi.openapi.utils import get_openapi
from .models import (
    LoginResponse, SignupProfileUpdate, DashboardResponse,
    ChatSendRequest, ChatSendResponse,
    EndSessionResponse,
    FeedbackStudentResponse, FeedbackAdminResponse,
    QuestionnaireSubmit, LoginRequest, LoginResponse
)
from .services import (
    ensure_16_sessions_seeded, get_available_session, normalize_skill_indicators, sessions_completed_this_week,
    next_turn_index, get_history, gender_label, lock_and_unlock_next,
    award_milestone_badge, award_skill_badges_if_ready, normalize_skill_indicators
)

from .gemini_client import gemini
from .prompts import PATIENT_SYSTEM_FR, PATIENT_SYSTEM_EN, EVAL_SYSTEM_FR, EVAL_SYSTEM_EN
from .pdf_export import build_session_pdf, build_summary_pdf
from fastapi.requests import Request

app = FastAPI(title="ALLIANCE OSTEO 2026 - MVP Backend")

app.add_middleware(
    CORSMiddleware,
    allow_origins=[settings.APP_ORIGIN],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

def custom_openapi():
    if app.openapi_schema:
        return app.openapi_schema
    schema = get_openapi(
        title=app.title,
        version="1.0.0",
        routes=app.routes,
    )
    for path in schema["paths"].values():
        for op in path.values():
            op.setdefault("security", [{"BearerAuth": []}])
    app.openapi_schema = schema
    return schema

app.openapi = custom_openapi
@app.post("/auth/login", response_model=LoginResponse)
def login(payload: LoginRequest):
    """
    Explicit login endpoint.
    Returns Bearer token for frontend usage.
    """
    try:
        res = supabase.auth.sign_in_with_password({
            "email": payload.email,
            "password": payload.password,
        })
    except Exception:
        raise HTTPException(status_code=401, detail="Invalid email or password")

    if not res or not res.session:
        raise HTTPException(status_code=401, detail="Invalid email or password")

    access_token = res.session.access_token
    user = res.user

    return LoginResponse(
        access_token=access_token,
        token_type="bearer",
        user={
            "user_id": user.id,
            "email": user.email,
        },
    )
    

@app.get("/debug/auth")
def debug_auth(request: Request):
    return {"authorization": request.headers.get("authorization")}
@app.get("/debug/db")
def debug_db():
    r = supabase.table("profiles").select("user_id").limit(1).execute()
    return {"ok": True, "rows": len(r.data or [])}


@app.get("/health")
def health():
    return {"ok": True}

@app.post("/me/profile")
def update_profile(payload: SignupProfileUpdate, user=Depends(get_current_user)):
    supabase.table("profiles").upsert(
        {
            "user_id": user["user_id"],
            "level": payload.level,  # enum
            "preferred_language": payload.preferred_language,
        },
        on_conflict="user_id",
    ).execute()

    return {"ok": True}


@app.get("/student/dashboard", response_model=DashboardResponse)
def student_dashboard(user=Depends(get_current_user)):
    ensure_16_sessions_seeded(user["user_id"])
    sessions = supabase.table("sessions").select("id,session_number,status,patient_age,patient_gender").eq("user_id", user["user_id"]).order("session_number").execute().data

    completed = sum(1 for s in sessions if s["status"] == "completed")
    available = next((s["session_number"] for s in sessions if s["status"] in ("available", "in_progress")), 16 if completed==16 else 1)

    badges = supabase.table("badges").select("badge_code").eq("user_id", user["user_id"]).execute().data
    badge_codes = [b["badge_code"] for b in (badges or [])]

    return {
        "completed": completed,
        "available_session_number": available,
        "sessions": sessions,
        "badges": badge_codes,
    }

@app.get("/student/sessions/current")
def current_session(user=Depends(get_current_user)):
    ses = get_available_session(user["user_id"])
    if not ses:
        return {"done": True}
    lang = user.get("preferred_language", "fr")
    return {
        "session_id": ses["id"],
        "session_number": ses["session_number"],
        "status": ses["status"],
        "patient_age": ses["patient_age"],
        "patient_gender_label": gender_label(ses["patient_age"], ses["patient_gender"], lang),
    }

@app.get("/student/sessions/current-id")
def current_session_id(user=Depends(get_current_user)):
    ses = get_available_session(user["user_id"])
    return {"session_id": ses["id"], "session_number": ses["session_number"]}

@app.post("/student/sessions/{session_id}/chat", response_model=ChatSendResponse)
def chat_send(session_id: str, payload: ChatSendRequest, user=Depends(get_current_user)):
    ses = (
        supabase.table("sessions")
        .select("*")
        .eq("id", session_id)
        .eq("user_id", user["user_id"])
        .single()
        .execute()
        .data
    )

    if ses["status"] == "completed":
        raise HTTPException(400, "Session already completed")
    if ses["status"] == "locked":
        raise HTTPException(403, "Session locked")

    # Weekly limit: only blocks starting a NEW session (available -> in_progress)
    if ses["status"] == "available":
        if sessions_completed_this_week(user["user_id"]) >= 2:
            raise HTTPException(403, "Limite: 2 sessions par semaine")
        supabase.table("sessions").update({
            "status": "in_progress",
            "started_at": ses.get("started_at") or datetime.now(timezone.utc).isoformat(),
        }).eq("id", session_id).execute()

    # ✅ PATIENT STARTS SOMETIMES (MUST BE HERE)
    # Only once, only if no messages yet
    existing_msg = (
        supabase.table("messages")
        .select("id")
        .eq("session_id", session_id)
        .limit(1)
        .execute()
    ).data or []

    if (not existing_msg) and bool(ses.get("patient_opening_starts")):
        lang = user.get("preferred_language", "fr")
        system = PATIENT_SYSTEM_FR if lang == "fr" else PATIENT_SYSTEM_EN

        ctx_open = {
            "session_number": ses["session_number"],
            "patient_age": ses["patient_age"],
            "patient_gender_label": gender_label(ses["patient_age"], ses["patient_gender"], lang),
            "difficulty": ses["difficulty"],
            "reorientation": ses["reorientation"],
            "opening_patient_starts": True,
            "language": lang,
        }

        opening_user_text = f"""
CONTEXTE (JSON):
{ctx_open}

INSTRUCTION:
Tu es le PATIENT. Commence la consultation de manière naturelle et humaine.
1 à 3 phrases. Pas de diagnostic, pas de pathologie, pas de phrases génériques.
"""

        opening_msg = gemini.generate_text(settings.GEMINI_MODEL_CHAT, system, opening_user_text)

        t0 = next_turn_index(session_id)
        supabase.table("messages").insert({
            "session_id": session_id,
            "user_id": user["user_id"],
            "turn_index": t0,
            "role": "patient",
            "content": opening_msg
        }).execute()

    # store student message
    t = next_turn_index(session_id)
    supabase.table("messages").insert({
        "session_id": session_id,
        "user_id": user["user_id"],
        "turn_index": t,
        "role": "student",
        "content": payload.message
    }).execute()

    # build prompt with short history
    hist = get_history(session_id, settings.HISTORY_TURNS)
    lang = user.get("preferred_language", "fr")
    system = PATIENT_SYSTEM_FR if lang == "fr" else PATIENT_SYSTEM_EN

    ctx = {
        "session_number": ses["session_number"],
        "patient_age": ses["patient_age"],
        "patient_gender_label": gender_label(ses["patient_age"], ses["patient_gender"], lang),
        "difficulty": ses["difficulty"],  # hidden to student UI
        "reorientation": ses["reorientation"],
        "opening_patient_starts": ses["patient_opening_starts"],
        "language": lang,
    }

    history_text = "\n".join([f"{m['role'].upper()}: {m['content']}" for m in hist])
    user_text = f"""
CONTEXTE (JSON):
{ctx}

HISTORIQUE:
{history_text}

INSTRUCTION:
Réponds uniquement comme le PATIENT. Réponse naturelle, courte à moyenne (1-5 phrases).
"""

    patient_msg = gemini.generate_text(settings.GEMINI_MODEL_CHAT, system, user_text)

    # store patient message
    t2 = next_turn_index(session_id)
    supabase.table("messages").insert({
        "session_id": session_id,
        "user_id": user["user_id"],
        "turn_index": t2,
        "role": "patient",
        "content": patient_msg
    }).execute()

    return ChatSendResponse(
        patient_message=patient_msg,
        language=lang,
        session_number=ses["session_number"],
        patient_age=ses["patient_age"],
        patient_gender_label=gender_label(ses["patient_age"], ses["patient_gender"], lang),
    )

@app.post("/student/sessions/{session_id}/end", response_model=EndSessionResponse)
def end_session(session_id: str, user=Depends(get_current_user)):
    ses = supabase.table("sessions").select("*").eq("id", session_id).eq("user_id", user["user_id"]).single().execute().data
    if ses["status"] == "completed":
        return {"session_id": session_id, "status": "completed"}

    # mark ended
    
    supabase.table("sessions").update({
    "status": "completed",
    "ended_at": datetime.now(timezone.utc).isoformat(),
}).eq("id", session_id).execute()
    # unlock next
    lock_and_unlock_next(user["user_id"], ses["session_number"])
    award_milestone_badge(user["user_id"], ses["session_number"])


    return {"session_id": session_id, "status": "completed"}




@app.get("/student/badges")
def student_badges(user=Depends(get_current_user)):
    resp = (
        supabase.table("badges")
        .select("badge_code")
        .eq("user_id", user["user_id"])
        .execute()
    )
    return {"badges": (resp.data or [])}



@app.post("/student/sessions/{session_id}/generate-feedback", response_model=FeedbackStudentResponse)
def generate_feedback(session_id: UUID, user=Depends(get_current_user)):
    session_id = str(session_id)

    ses = (
        supabase.table("sessions")
        .select("*")
        .eq("id", session_id)
        .eq("user_id", user["user_id"])
        .limit(1)
        .execute()
        .data
    )
    if not ses:
        raise HTTPException(404, "Session not found")
    ses = ses[0]

    if ses["status"] != "completed":
        raise HTTPException(403, "Feedback only after completion")

    existing_resp = (
        supabase.table("feedback")
        .select("*")
        .eq("session_id", session_id)
        .limit(1)
        .execute()
    )
    if (existing_resp.data or []):
        return existing_resp.data[0]

    hist = get_history(session_id, 400)  # full transcript for eval
    lang = user.get("preferred_language", "fr")
    system = EVAL_SYSTEM_FR if lang == "fr" else EVAL_SYSTEM_EN

    transcript = "\n".join([f"{m['role'].upper()}: {m['content']}" for m in hist])

    prompt = f"""
Tu vas analyser une anamnèse (sans diagnostic). Tu dois produire STRICTEMENT du JSON suivant le schéma.
Langue attendue: {lang}.

METADATA:
- session_number: {ses["session_number"]}
- patient_age: {ses["patient_age"]}
- patient_gender: {ses["patient_gender"]}
- difficulty(hidden): {ses["difficulty"]}
- reorientation: {ses["reorientation"]}

TRANSCRIPT:
{transcript}

CONTRAINTES JSON:
- internal_scores: empathy/structure/alliance entiers 1..5
- student_facing: strengths 3..5, areas_to_improve 3..5, reflective_question 1
- skill_indicators booleans: active_listening, reformulation, emotional_validation, open_questions, structure_clarity
- kpis: peux inclure open_questions_ratio (0..1), interruptions_estimate (int), etc.
"""
    try:
    # 1️⃣ Generate FULL feedback (admin/internal version)
        parsed = gemini.generate_structured(
            settings.GEMINI_MODEL_EVAL,
            system,
            prompt,
            FeedbackAdminResponse,   # ⬅️ PENTING
        )

    # 2️⃣ Store FULL internal data in DB
        supabase.table("feedback").insert({
            "session_id": session_id,
            "user_id": user["user_id"],
            "language": parsed.language,
            "student_facing": parsed.student_facing.model_dump(),
            "internal_scores": parsed.internal_scores,
            "skill_indicators": parsed.skill_indicators.model_dump(),
            "kpis": parsed.kpis,
        }).execute()

    # 3️⃣ Award skill-based badges (threshold = 3 sessions)
        award_skill_badges_if_ready(user["user_id"], threshold=3)

    except Exception as e:
        raise HTTPException(503, f"LLM unavailable: {e}")

# 4️⃣ Return STUDENT-SAFE response only
    return FeedbackStudentResponse(
        language=parsed.language,
        student_facing=parsed.student_facing,
        internal_scores=parsed.internal_scores,  # boleh ditampilkan sebagai "indicators"
    )


@app.post("/student/sessions/{session_id}/questionnaire")
def submit_questionnaire(session_id: UUID, payload: QuestionnaireSubmit, user=Depends(get_current_user)):
    session_id = str(session_id)

    fb_resp = (
        supabase.table("feedback")
        .select("session_id")
        .eq("session_id", session_id)
        .limit(1)
        .execute()
    )

    if not (fb_resp.data or []):
        raise HTTPException(403, "Questionnaire after feedback")

    supabase.table("questionnaire").upsert({
        "session_id": session_id,
        "user_id": user["user_id"],
        "q1": payload.q1,
        "q2": payload.q2,
        "open_answer": payload.open_answer
    }).execute()

    return {"ok": True}

# ======================
# ADMIN ENDPOINTS
# ======================

@app.get("/admin/stats")
def admin_stats(admin=Depends(require_admin)):
    # simple aggregates (MVP)
    students = supabase.table("profiles").select("user_id", count="exact").eq("role", "student").execute().count or 0
    sessions_completed = supabase.table("sessions").select("id", count="exact").eq("status", "completed").execute().count or 0
    return {"students": students, "sessions_completed": sessions_completed}

@app.get("/admin/students")
def admin_students(admin=Depends(require_admin)):
    res = supabase.table("profiles").select("user_id,email,level,preferred_language,created_at").eq("role", "student").order("created_at", desc=True).execute().data
    return {"students": res}

@app.get("/admin/student/{user_id}/sessions")
def admin_student_sessions(user_id: str, admin=Depends(require_admin)):
    resp = (
        supabase.table("sessions")
        .select("*")
        .eq("user_id", user_id)
        .order("session_number")
        .execute()
    )
    sess = (resp.data if resp else None) or []
    return {"sessions": sess}


@app.get("/admin/sessions/{session_id}/pdf")
def admin_session_pdf(session_id: str, admin=Depends(require_admin)):
    ses_resp = (
        supabase.table("sessions")
        .select("*")
        .eq("id", session_id)
        .single()
        .execute()
    )
    ses = ses_resp.data if ses_resp else None
    if not ses:
        raise HTTPException(404, "Session not found")

    fb_resp = (
        supabase.table("feedback")
        .select("*")
        .eq("session_id", session_id)
        .maybe_single()
        .execute()
    )
    fb = fb_resp.data if fb_resp else None
    if not fb:
        raise HTTPException(404, "No feedback for this session yet")

    msgs_resp = (
        supabase.table("messages")
        .select("role,content,turn_index")
        .eq("session_id", session_id)
        .order("turn_index")
        .execute()
    )
    msgs = (msgs_resp.data if msgs_resp else None) or []

    prof_resp = (
        supabase.table("profiles")
        .select("level")
        .eq("user_id", ses["user_id"])
        .maybe_single()
        .execute()
    )
    prof = prof_resp.data if prof_resp else None
    academic_year = (prof or {}).get("level")

    meta = {
        "Student (user_id)": ses["user_id"],
        "Session": ses["session_number"],
        "Date": str(ses.get("ended_at") or ""),
        "Level(hidden)": ses["difficulty"],
        "Academic year": academic_year,
        "Scores": str(fb.get("internal_scores")),
        "Indicators": str(fb.get("skill_indicators")),
    }

    pdf_bytes = build_session_pdf(
        title=f"ALLIANCE OSTEO 2026 — Session {ses['session_number']}",
        meta=meta,
        feedback={"student_facing": fb["student_facing"]},
        transcript=msgs,
    )
    return Response(content=pdf_bytes, media_type="application/pdf")

@app.get("/admin/student/{user_id}/summary-pdf")
def admin_student_summary_pdf(user_id: str, admin=Depends(require_admin)):
    # fetch sessions for this student
    sess_rows = (
        supabase.table("sessions")
        .select("id,session_number,ended_at,difficulty")
        .eq("user_id", user_id)
        .order("session_number")
        .execute()
    ).data or []

    if not sess_rows:
        raise HTTPException(404, "Student has no sessions")

    # fetch all feedback for these sessions
    session_ids = [s["id"] for s in sess_rows]
    fb_rows = (
        supabase.table("feedback")
        .select("session_id,internal_scores,skill_indicators")
        .in_("session_id", session_ids)
        .execute()
    ).data or []

    fb_map = {f["session_id"]: f for f in fb_rows}

    # profile academic year
    prof = (
        supabase.table("profiles")
        .select("level,email")
        .eq("user_id", user_id)
        .maybe_single()
        .execute()
    ).data or {}

    rows = []
    completed_count = 0
    for s in sess_rows:
        f = fb_map.get(s["id"]) or {}
        if s.get("ended_at"):
            completed_count += 1
        rows.append({
            "session_number": s["session_number"],
            "ended_at": str(s.get("ended_at") or ""),
            "difficulty": s.get("difficulty"),
            "internal_scores": f.get("internal_scores") or {},
            "skill_indicators": f.get("skill_indicators") or {},
        })

    meta = {
        "Student (user_id)": user_id,
        "Email": prof.get("email") or "",
        "Academic year": prof.get("level") or "",
        "Completed sessions": f"{completed_count}/16",
    }

    pdf_bytes = build_summary_pdf(
        title="ALLIANCE OSTEO 2026 — Summary Report (16 sessions)",
        meta=meta,
        rows=rows,
    )
    return Response(content=pdf_bytes, media_type="application/pdf")

@app.get("/admin/analytics/summary")
def admin_analytics_summary(admin=Depends(require_admin)):
    # Pull all feedback
    fb_rows = (
        supabase.table("feedback")
        .select("session_id,user_id,internal_scores")
        .execute()
    ).data or []

    # Sessions map: session_id -> session_number
    sess_rows = (
        supabase.table("sessions")
        .select("id,session_number,user_id")
        .execute()
    ).data or []
    sess_map = {s["id"]: s for s in sess_rows}

    # Profiles map: user_id -> level
    prof_rows = (
        supabase.table("profiles")
        .select("user_id,level")
        .execute()
    ).data or []
    level_map = {p["user_id"]: p.get("level") for p in prof_rows}

    def add_score(acc, key, val):
        if val is None:
            return
        acc[key]["sum"] += int(val)
        acc[key]["n"] += 1

    def finalize(acc):
        out = {}
        for k, v in acc.items():
            out[k] = (v["sum"] / v["n"]) if v["n"] else None
        return out

    # overall accumulators
    overall = {
        "empathy": {"sum": 0, "n": 0},
        "structure": {"sum": 0, "n": 0},
        "alliance": {"sum": 0, "n": 0},
    }

    # by level (4e/5e/autre)
    by_level = {}

    # by session_number (1..16)
    by_session = {}

    for f in fb_rows:
        scores = f.get("internal_scores") or {}
        sid = f.get("session_id")
        uid = f.get("user_id")

        # overall
        add_score(overall, "empathy", scores.get("empathy"))
        add_score(overall, "structure", scores.get("structure"))
        add_score(overall, "alliance", scores.get("alliance"))

        # by level
        lvl = level_map.get(uid) or "autre"
        by_level.setdefault(lvl, {
            "empathy": {"sum": 0, "n": 0},
            "structure": {"sum": 0, "n": 0},
            "alliance": {"sum": 0, "n": 0},
        })
        add_score(by_level[lvl], "empathy", scores.get("empathy"))
        add_score(by_level[lvl], "structure", scores.get("structure"))
        add_score(by_level[lvl], "alliance", scores.get("alliance"))

        # by session_number
        sn = None
        srow = sess_map.get(sid)
        if srow:
            sn = srow.get("session_number")
        if sn:
            by_session.setdefault(str(sn), {
                "empathy": {"sum": 0, "n": 0},
                "structure": {"sum": 0, "n": 0},
                "alliance": {"sum": 0, "n": 0},
            })
            add_score(by_session[str(sn)], "empathy", scores.get("empathy"))
            add_score(by_session[str(sn)], "structure", scores.get("structure"))
            add_score(by_session[str(sn)], "alliance", scores.get("alliance"))

    return {
        "overall_avg": finalize(overall),
        "by_level_avg": {lvl: finalize(acc) for lvl, acc in by_level.items()},
        "by_session_number_avg": {sn: finalize(acc) for sn, acc in by_session.items()},
    }
@app.get("/admin/sessions/{session_id}/feedback", response_model=FeedbackAdminResponse)
def admin_session_feedback(session_id: str, admin=Depends(require_admin)):
    resp = (
        supabase.table("feedback")
        .select("*")
        .eq("session_id", session_id)
        .limit(1)
        .execute()
    )

    rows = resp.data or []
    if not rows:
        raise HTTPException(404, "No feedback for this session")

    fb = rows[0]

    raw_si = fb.get("skill_indicators") or {}

    return FeedbackAdminResponse(
    language=fb.get("language", "fr"),
    student_facing=fb["student_facing"],
    internal_scores=fb["internal_scores"],
    skill_indicators=normalize_skill_indicators(raw_si),
    kpis=fb.get("kpis") or {},
)
