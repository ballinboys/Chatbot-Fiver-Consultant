import json
from google import genai
from google.genai import types
from fastapi import HTTPException
from .config import settings


def _is_quota_error(e: Exception) -> bool:
    status = getattr(e, "status_code", None)
    if status == 429:
        return True

    err = {}
    if getattr(e, "args", None) and len(e.args) > 0 and isinstance(e.args[0], dict):
        err = e.args[0].get("error") or {}

    if err.get("code") == 429:
        return True

    msg = str(e).lower()
    return ("resource_exhausted" in msg) or ("quota" in msg) or ("429" in msg)


def _is_model_not_found(e: Exception) -> bool:
    status = getattr(e, "status_code", None)
    if status == 404:
        return True

    msg = str(e).lower()
    return ("not_found" in msg) or ("is not found" in msg) or ("404" in msg)


class GeminiClient:
    def __init__(self):
        self.enabled = bool(getattr(settings, "GEMINI_API_KEY", None))
        self.client = genai.Client(api_key=settings.GEMINI_API_KEY) if self.enabled else None

    def generate_text(self, model: str, system: str, user_text: str) -> str:
        if not self.enabled or self.client is None:
            raise HTTPException(503, "AI service unavailable (missing API key)")

        try:
            resp = self.client.models.generate_content(
                model=model,
                contents=[types.Content(role="user", parts=[types.Part(text=user_text)])],
                config=types.GenerateContentConfig(
                    system_instruction=system,
                    response_mime_type="text/plain",
                    temperature=0.7,
                ),
            )
            text = (resp.text or "").strip()
            if not text:
                raise HTTPException(503, "AI returned empty response")
            return text

        except Exception as e:
            if _is_quota_error(e):
                raise HTTPException(503, "AI service temporarily unavailable (quota exceeded)")
            if _is_model_not_found(e):
                raise HTTPException(503, "AI model not available (check model id/version)")
            raise HTTPException(500, f"AI generation failed: {type(e).__name__}: {e}")

    def generate_structured(self, model: str, system: str, user_text: str, schema_model):
        """
        Return object Pydantic (schema_model), agar main.py bisa akses parsed.language, dll.
        """
        if not self.enabled or self.client is None:
            raise HTTPException(503, "AI service unavailable (missing API key)")

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
                raise HTTPException(503, "AI returned empty structured output")

            # pastikan JSON valid biar errornya jelas
            try:
                json.loads(text)
            except Exception:
                raise HTTPException(503, f"AI returned invalid JSON: {text[:200]}")

            # parse -> pydantic model
            try:
                return schema_model.model_validate_json(text)
            except Exception as ve:
                raise HTTPException(503, f"AI JSON did not match schema: {ve}")

        except HTTPException:
            raise
        except Exception as e:
            if _is_quota_error(e):
                raise HTTPException(503, "AI feedback unavailable (quota/rate limit)")
            if _is_model_not_found(e):
                raise HTTPException(503, "AI model not available (check model id/version)")
            raise HTTPException(500, f"AI structured generation failed: {type(e).__name__}: {e}")


gemini = GeminiClient()
