import json
from google import genai
from google.genai import types
from fastapi import HTTPException
from .config import settings


def _is_quota_error(e: Exception) -> bool:
    status = getattr(e, "status_code", None)
    if status == 429:
        return True
    msg = str(e).lower()
    return ("resource_exhausted" in msg) or ("quota" in msg) or ("429" in msg)


def _is_model_not_found(e: Exception) -> bool:
    status = getattr(e, "status_code", None)
    if status == 404:
        return True
    msg = str(e).lower()
    return ("not_found" in msg) and ("model" in msg)


class GeminiClient:
    def __init__(self):
        self.enabled = bool(getattr(settings, "GEMINI_API_KEY", None))
        self.client = genai.Client(api_key=settings.GEMINI_API_KEY) if self.enabled else None

    def generate_structured(self, model: str, system: str, user_text: str, schema_model):
        if not self.enabled or self.client is None:
            raise HTTPException(status_code=503, detail="AI service unavailable (missing API key)")

        # IMPORTANT: enforce JSON via instructions (NOT via response_schema)
        json_guard = (
            "Return ONLY valid JSON. No markdown. No extra keys. "
            "If you cannot comply, return an empty JSON object: {}."
        )
        sys = system.strip() + "\n\n" + json_guard

        try:
            resp = self.client.models.generate_content(
                model=model,
                contents=[types.Content(role="user", parts=[types.Part(text=user_text)])],
                config=types.GenerateContentConfig(
                    system_instruction=sys,
                    response_mime_type="application/json",
                    temperature=0.3,
                ),
            )

            text = (resp.text or "").strip()
            if not text:
                # sesuai request: jangan dipaksa ada hasil
                raise HTTPException(status_code=503, detail="AI returned empty structured output")

            # Pastikan JSON valid dulu (biar errornya jelas)
            try:
                _ = json.loads(text)
            except Exception:
                raise HTTPException(status_code=503, detail=f"AI returned invalid JSON: {text[:200]}")

            # Validasi ke schema Pydantic
            try:
                return schema_model.model_validate_json(text)
            except Exception as ve:
                # jangan 500 (biar kamu bisa bedain: AI output salah format)
                raise HTTPException(status_code=503, detail=f"AI JSON did not match schema: {ve}")

        except HTTPException:
            raise
        except Exception as e:
            if _is_quota_error(e):
                raise HTTPException(status_code=503, detail="AI feedback unavailable (quota/rate limit)")
            if _is_model_not_found(e):
                raise HTTPException(status_code=503, detail="AI model not available (wrong model name / not enabled)")
            raise HTTPException(status_code=500, detail=f"AI structured generation failed: {type(e).__name__}: {e}")
gemini = GeminiClient()