"""Bounded Gemini transport; output still requires local recommendation validation."""
import re
from dataclasses import dataclass

import httpx
from google import genai
from google.genai import errors, types

from config import DEFAULT_AI_MODEL
from services.prompts import RecommendationPrompt
from services.validators import MAX_OUTPUT_LENGTH


DEFAULT_MODEL = DEFAULT_AI_MODEL


class AiServiceError(RuntimeError):
    """Safe error messages and stable codes; never include provider response bodies."""
    def __init__(self, message, code):
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class AiGeneration:
    text: str  # Raw structured JSON, not yet validated or safe for presentation.
    model: str


def _integer_setting(value, minimum, maximum):
    if (type(value) not in (int, str) or not re.fullmatch(r"[0-9]{1,5}", str(value))
            or not minimum <= int(value) <= maximum):
        raise AiServiceError("Check the AI timeout and output-limit configuration.", "configuration")
    return int(value)


class AiService:
    """One explicit-key Gemini request per call; no hidden retries or external tools."""
    def __init__(self, api_key=None, *, model=DEFAULT_MODEL, timeout_seconds=20,
                 max_output_tokens=4096, client_factory=None):
        if (not isinstance(api_key, str) or not api_key.strip()
                or api_key.strip() in {"your-gemini-key", "paste_your_gemini_api_key_here"}):
            raise AiServiceError("Add your Gemini key to AI_SERVICE_API_KEY in .env.", "missing_key")
        if not isinstance(model, str) or len(model) > 120 or not re.fullmatch(r"(?:models/)?gemini-[A-Za-z0-9._-]+", model):
            raise AiServiceError("Choose a valid Gemini model in AI_SERVICE_MODEL.", "configuration")
        self.api_key = api_key.strip()
        self.model = model
        self.timeout_seconds = _integer_setting(timeout_seconds, 1, 60)
        self.max_output_tokens = _integer_setting(max_output_tokens, 256, 8192)
        self.client_factory = client_factory or genai.Client

    @classmethod
    def from_config(cls, config):
        return cls(config.get("AI_SERVICE_API_KEY"), model=config.get("AI_SERVICE_MODEL", DEFAULT_MODEL),
                   timeout_seconds=config.get("AI_SERVICE_TIMEOUT_SECONDS", 20),
                   max_output_tokens=config.get("AI_SERVICE_MAX_OUTPUT_TOKENS", 4096))

    def generate(self, prompt):
        if not isinstance(prompt, RecommendationPrompt):
            raise AiServiceError("Build a recommendation prompt before generation.", "configuration")
        client = None
        try:
            client = self.client_factory(api_key=self.api_key, vertexai=False, http_options=types.HttpOptions(
                timeout=self.timeout_seconds * 1000, retry_options=types.HttpRetryOptions(attempts=1)))
            response = client.models.generate_content(
                model=self.model, contents=prompt.user_prompt,
                config=types.GenerateContentConfig(
                    system_instruction=prompt.system_instruction,
                    response_mime_type="application/json", response_json_schema=prompt.response_schema,
                    max_output_tokens=self.max_output_tokens,
                    thinking_config=(types.ThinkingConfig(thinking_level="LOW")
                                     if self.model.removeprefix("models/") == "gemini-3.8-flash" else None),
                    automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True)))
            if not response.candidates or len(response.candidates) != 1:
                raise AiServiceError("Gemini did not return a complete recommendation response.", "invalid_response")
            candidate = response.candidates[0]
            if candidate.finish_reason != types.FinishReason.STOP or not candidate.content:
                raise AiServiceError("Gemini did not return a complete recommendation response.", "invalid_response")
            parts = [part for part in (candidate.content.parts or []) if not part.thought]
            if not parts or any(part.text is None for part in parts):
                raise AiServiceError("Gemini did not return a text recommendation response.", "invalid_response")
            text = "".join(part.text for part in parts)
            if not text.strip() or len(text) > MAX_OUTPUT_LENGTH:
                raise AiServiceError("Gemini returned an empty or oversized recommendation response.", "invalid_response")
            model = response.model_version or self.model
            if len(model) > 120:
                raise AiServiceError("Gemini returned unexpected model metadata.", "invalid_response")
            return AiGeneration(text, model)
        except errors.APIError as error:
            if error.code == 429:
                raise AiServiceError("Gemini is rate limited. Please try again later.", "rate_limited") from None
            if error.code in (408, 504):
                raise AiServiceError("Gemini took too long to respond. Please try again.", "timeout") from None
            if error.code in (401, 403):
                raise AiServiceError("Gemini authentication failed. Check the configured key and API access.", "authentication") from None
            if error.code == 404:
                raise AiServiceError("The configured Gemini model is unavailable. Check AI_SERVICE_MODEL.", "model_unavailable") from None
            raise AiServiceError("Gemini could not complete this request. Please try again later.", "unavailable") from None
        except (httpx.TimeoutException, TimeoutError):
            raise AiServiceError("Gemini took too long to respond. Please try again.", "timeout") from None
        except httpx.TransportError:
            raise AiServiceError("Gemini could not be reached. Please try again later.", "unavailable") from None
        except (ValueError, TypeError, KeyError, AttributeError):
            raise AiServiceError("Gemini returned an unexpected response.", "invalid_response") from None
        finally:
            if client is not None:
                client.close()
