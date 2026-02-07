from __future__ import annotations

from datetime import datetime, timezone, timedelta
import random
from typing import Any, Dict, List, Optional, Tuple
from fastapi import HTTPException
from .db import supabase


Row = Dict[str, Any]
Filter = Tuple[str, str, Any]  # (op, column, value)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def iso_week_start(dt: datetime) -> datetime:
    """Monday 00:00 UTC for the week containing dt."""
    d = dt.astimezone(timezone.utc)
    return d - timedelta(
        days=d.weekday(),
        hours=d.hour,
        minutes=d.minute,
        seconds=d.second,
        microseconds=d.microsecond,
    )


def _raise_http(msg: str, status: int = 500):
    raise HTTPException(status_code=status, detail=msg)


def _exec(fn, msg: str):
    """
    Execute a supabase call and raise a readable HTTPException.

    - Postgrest API errors often contain useful dict fields (code/message).
    - We forward them as 400 by default because they're usually request/schema issues.
    """
    try:
        return fn()
    except HTTPException:
        raise
    except Exception as e:
        # postgrest-py raises APIError with `.args[0]` being a dict
        details = None
        if getattr(e, "args", None) and len(e.args) > 0 and isinstance(e.args[0], dict):
            details = e.args[0]
        if details:
            # Common: invalid uuid, missing column, RLS, etc.
            code = details.get("code")
            message = details.get("message")
            hint = details.get("hint")
            det = details.get("details")
            parts = [p for p in [code and f"code={code}", message, hint, det] if p]
            _raise_http(f"{msg}: " + " | ".join(parts), status=400)
        _raise_http(f"{msg}: {type(e).__name__}: {e}", status=500)


def _select_rows(
    table: str,
    select: str = "*",
    *,
    filters: Optional[List[Filter]] = None,
    or_: Optional[str] = None,
    order: Optional[Tuple[str, bool]] = None,  # (column, desc)
    limit: Optional[int] = None,
) -> List[Row]:
    """
    Robust select returning a list of rows.
    """
    def run():
        q = supabase.table(table).select(select)
        if filters:
            for op, col, val in filters:
                if op == "eq":
                    q = q.eq(col, val)
                elif op == "gte":
                    q = q.gte(col, val)
                elif op == "gt":
                    q = q.gt(col, val)
                elif op == "lte":
                    q = q.lte(col, val)
                elif op == "lt":
                    q = q.lt(col, val)
                else:
                    raise ValueError(f"Unsupported filter op: {op}")
        if or_:
            q = q.or_(or_)
        if order:
            col, desc = order
            q = q.order(col, desc=desc)
        if limit is not None:
            q = q.limit(limit)
        return q.execute()

    resp = _exec(run, f"{table} select failed")
    return getattr(resp, "data", None) or []


def _select_first(
    table: str,
    select: str = "*",
    *,
    filters: Optional[List[Filter]] = None,
    or_: Optional[str] = None,
    order: Optional[Tuple[str, bool]] = None,
) -> Optional[Row]:
    rows = _select_rows(table, select, filters=filters, or_=or_, order=order, limit=1)
    return rows[0] if rows else None


def sessions_completed_this_week(user_id: str) -> int:
    start = iso_week_start(_now())
    end = start + timedelta(days=7)

    def run():
        return (
            supabase.table("sessions")
            .select("id", count="exact")
            .eq("user_id", user_id)
            .eq("status", "completed")
            .gte("ended_at", start.isoformat())
            .lt("ended_at", end.isoformat())
            .execute()
        )

    res = _exec(run, "sessions_completed_this_week query failed")
    return getattr(res, "count", None) or 0


def ensure_user_program(user_id: str) -> None:
    prog = _select_first(
        "student_program",
        "*",
        filters=[("eq", "user_id", user_id)],
    )
    if prog:
        return

    # pick 2 immediate + 2 delayed among sessions 2..16 (avoid session 1)
    all_sessions = list(range(2, 17))
    random.shuffle(all_sessions)
    immediate = sorted(all_sessions[:2])
    delayed = sorted(all_sessions[2:4])

    def run_insert():
        return (
            supabase.table("student_program")
            .insert(
                {
                    "user_id": user_id,
                    "reorientation_immediate_sessions": immediate,
                    "reorientation_delayed_sessions": delayed,
                }
            )
            .execute()
        )

    _exec(run_insert, "student_program insert failed")


def ensure_16_sessions_seeded(user_id: str) -> None:
    ensure_user_program(user_id)

    existing_rows = _select_rows(
        "sessions",
        "session_number",
        filters=[("eq", "user_id", user_id)],
    )
    existing_nums = {r["session_number"] for r in existing_rows if "session_number" in r}

    if len(existing_nums) == 16:
        return

    prog = _select_first(
        "student_program",
        "*",
        filters=[("eq", "user_id", user_id)],
    )
    if not prog:
        _raise_http("student_program missing after ensure_user_program", status=500)

    immediate = set(prog.get("reorientation_immediate_sessions") or [])
    delayed = set(prog.get("reorientation_delayed_sessions") or [])

    # difficulty assignment: session1 L1, others randomized with no consecutive repetition
    diffs: List[str] = []
    prev: Optional[str] = None
    for sn in range(1, 17):
        if sn == 1:
            d = "L1"
        else:
            choices = ["L1", "L2", "L3"]
            if prev in choices:
                choices.remove(prev)
            d = random.choice(choices)
        diffs.append(d)
        prev = d

    rows_to_insert: List[Row] = []
    for sn in range(1, 17):
        if sn in existing_nums:
            continue

        reor = "none"
        if sn in immediate:
            reor = "immediate"
        elif sn in delayed:
            reor = "delayed"

        age = random.choice([12, 15, 18, 24, 32, 41, 52, 67, 74])
        gender = random.choice(["female", "male"])
        patient_starts = random.choice([True, False])

        status = "available" if sn == 1 else "locked"

        rows_to_insert.append(
            {
                "user_id": user_id,
                "session_number": sn,
                "status": status,
                "difficulty": diffs[sn - 1],
                "reorientation": reor,
                "patient_age": age,
                "patient_gender": gender,
                "patient_opening_starts": patient_starts,
            }
        )

    if rows_to_insert:
        def run_insert_sessions():
            return supabase.table("sessions").insert(rows_to_insert).execute()

        _exec(run_insert_sessions, "sessions seed insert failed")


def get_available_session(user_id: str) -> Optional[Row]:
    ensure_16_sessions_seeded(user_id)

    # status == in_progress OR available (PostgREST OR filter string)
    return _select_first(
        "sessions",
        "*",
        filters=[("eq", "user_id", user_id)],
        or_="status.eq.in_progress,status.eq.available",
        order=("session_number", False),
    )


def lock_and_unlock_next(user_id: str, session_number: int) -> None:
    if session_number >= 16:
        return
    next_num = session_number + 1

    def run():
        return (
            supabase.table("sessions")
            .update({"status": "available"})
            .eq("user_id", user_id)
            .eq("session_number", next_num)
            .eq("status", "locked")
            .execute()
        )

    _exec(run, "unlock next session failed")


def gender_label(age: int, gender: str, lang: str) -> str:
    if lang == "en":
        if age < 17:
            return "Girl" if gender == "female" else "Boy"
        return "Woman" if gender == "female" else "Man"

    # fr
    if age < 17:
        return "Fille" if gender == "female" else "Garçon"
    return "Femme" if gender == "female" else "Homme"


def next_turn_index(session_id: str) -> int:
    rows = _select_rows(
        "messages",
        "turn_index",
        filters=[("eq", "session_id", session_id)],
        order=("turn_index", True),
        limit=1,
    )
    if not rows:
        return 1
    return int(rows[0]["turn_index"]) + 1


def get_history(session_id: str, limit: int) -> List[Row]:
    rows = _select_rows(
        "messages",
        "role,content,turn_index",
        filters=[("eq", "session_id", session_id)],
        order=("turn_index", True),
        limit=limit,
    )
    return list(reversed(rows))

# ===== BADGES =====

MILESTONE_BADGES: Dict[int, str] = {
    1: "MILESTONE_SESSION_1",
    4: "MILESTONE_SESSION_4",
    8: "MILESTONE_SESSION_8",
    12: "MILESTONE_SESSION_12",
    16: "MILESTONE_SESSION_16",
}

SKILL_BADGES: Dict[str, str] = {
    "active_listening": "SKILL_ACTIVE_LISTENING",
    "reformulation": "SKILL_REFORMULATION",
    "emotional_validation": "SKILL_EMOTIONAL_VALIDATION",
    "open_questions": "SKILL_OPEN_QUESTIONS",
    "structure_clarity": "SKILL_STRUCTURE_CLARITY",
}

def award_badge(user_id: str, badge_code: str) -> None:
    existing = (
        supabase.table("badges")
        .select("id")
        .eq("user_id", user_id)
        .eq("badge_code", badge_code)
        .maybe_single()
        .execute()
    )
    if existing and existing.data:
        return

    supabase.table("badges").insert({
        "user_id": user_id,
        "badge_code": badge_code,
    }).execute()

def award_milestone_badge(user_id: str, session_number: int) -> None:
    code = MILESTONE_BADGES.get(session_number)
    if not code:
        return
    award_badge(user_id, code)

def award_skill_badges_if_ready(user_id: str, threshold: int = 3) -> None:
    """
    Award skill badges if the student has skill=True in >= threshold feedback sessions.
    This avoids random 1-session badges and is stable for MVP.
    """
    rows = (
        supabase.table("feedback")
        .select("skill_indicators")
        .eq("user_id", user_id)
        .execute()
    ).data or []

    counts = {k: 0 for k in SKILL_BADGES.keys()}

    for r in rows:
        si = r.get("skill_indicators") or {}
        for k in counts:
            if si.get(k) is True:
                counts[k] += 1

    for skill_key, badge_code in SKILL_BADGES.items():
        if counts.get(skill_key, 0) >= threshold:
            award_badge(user_id, badge_code)

def normalize_skill_indicators(raw: dict) -> dict:
    """
    Ensure skill_indicators always has ALL required keys.
    Missing keys (old data) are defaulted to False.
    """
    return {
        "active_listening": bool(raw.get("active_listening", False)),
        "reformulation": bool(raw.get("reformulation", False)),
        "emotional_validation": bool(raw.get("emotional_validation", False)),
        "open_questions": bool(raw.get("open_questions", False)),
        "structure_clarity": bool(raw.get("structure_clarity", False)),
    }
