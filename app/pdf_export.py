from io import BytesIO
from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas

def build_session_pdf(title: str, meta: dict, feedback: dict, transcript: list) -> bytes:
    buf = BytesIO()
    c = canvas.Canvas(buf, pagesize=A4)
    width, height = A4

    y = height - 40
    c.setFont("Helvetica-Bold", 14)
    c.drawString(40, y, title)
    y -= 24

    c.setFont("Helvetica", 10)
    for k, v in meta.items():
        c.drawString(40, y, f"{k}: {v}")
        y -= 14

    y -= 10
    c.setFont("Helvetica-Bold", 12)
    c.drawString(40, y, "Feedback (student-facing)")
    y -= 18
    c.setFont("Helvetica", 10)

    sf = feedback.get("student_facing", {})
    for label, items in [("Strengths", sf.get("strengths", [])), ("Areas to improve", sf.get("areas_to_improve", []))]:
        c.drawString(40, y, label + ":")
        y -= 14
        for it in items:
            c.drawString(60, y, f"- {it[:110]}")
            y -= 14

    rq = sf.get("reflective_question", "")
    if rq:
        y -= 6
        c.drawString(40, y, "Reflective question:")
        y -= 14
        c.drawString(60, y, rq[:120])
        y -= 18

    y -= 6
    c.setFont("Helvetica-Bold", 12)
    c.drawString(40, y, "Transcript")
    y -= 18
    c.setFont("Helvetica", 9)

    for m in transcript:
        line = f"[{m['role']}] {m['content']}"
        for chunk in _wrap(line, 120):
            if y < 60:
                c.showPage()
                y = height - 40
                c.setFont("Helvetica", 9)
            c.drawString(40, y, chunk)
            y -= 12

    c.showPage()
    c.save()
    return buf.getvalue()

def _wrap(text: str, n: int):
    out, cur = [], ""
    for w in text.split():
        if len(cur) + len(w) + 1 > n:
            out.append(cur)
            cur = w
        else:
            cur = (cur + " " + w).strip()
    if cur:
        out.append(cur)
    return out

def build_summary_pdf(title: str, meta: dict, rows: list) -> bytes:
    """
    rows: list of dicts with keys:
      - session_number
      - ended_at (str)
      - difficulty
      - internal_scores (dict)
      - skill_indicators (dict)
    """
    buf = BytesIO()
    c = canvas.Canvas(buf, pagesize=A4)
    width, height = A4

    y = height - 40
    c.setFont("Helvetica-Bold", 14)
    c.drawString(40, y, title)
    y -= 24

    c.setFont("Helvetica", 10)
    for k, v in meta.items():
        c.drawString(40, y, f"{k}: {v}")
        y -= 14

    y -= 10
    c.setFont("Helvetica-Bold", 12)
    c.drawString(40, y, "Résumé des 16 sessions")
    y -= 18
    c.setFont("Helvetica", 9)

    for r in rows:
        line1 = f"Session {r.get('session_number')} | Date: {str(r.get('ended_at') or '')[:19]} | Level: {r.get('difficulty')}"
        scores = r.get("internal_scores") or {}
        si = r.get("skill_indicators") or {}
        line2 = f"Scores: empathy={scores.get('empathy')} structure={scores.get('structure')} alliance={scores.get('alliance')}"
        line3 = (
            f"Skills: AL={si.get('active_listening')} "
            f"REF={si.get('reformulation')} "
            f"EV={si.get('emotional_validation')} "
            f"OQ={si.get('open_questions')} "
            f"SC={si.get('structure_clarity')}"
        )

        for chunk in _wrap(line1, 120):
            if y < 70:
                c.showPage()
                y = height - 40
                c.setFont("Helvetica", 9)
            c.drawString(40, y, chunk)
            y -= 12

        for chunk in _wrap(line2, 120):
            if y < 70:
                c.showPage()
                y = height - 40
                c.setFont("Helvetica", 9)
            c.drawString(50, y, chunk)
            y -= 12

        for chunk in _wrap(line3, 120):
            if y < 70:
                c.showPage()
                y = height - 40
                c.setFont("Helvetica", 9)
            c.drawString(50, y, chunk)
            y -= 12

        y -= 6

    c.showPage()
    c.save()
    return buf.getvalue()

