"""In-memory, cloud-backed semantic screen inspection for Jarvis."""

import base64
import ctypes
import io
import os

try:
    from PIL import Image, ImageGrab
except ImportError:
    Image = None
    ImageGrab = None

from openai import OpenAI
from jarvis_windows import ToolResult


_client = None
KEYFILE = os.path.expanduser("~/.openjarvis/free.key")


class _RECT(ctypes.Structure):
    _fields_ = [
        ("left", ctypes.c_long), ("top", ctypes.c_long),
        ("right", ctypes.c_long), ("bottom", ctypes.c_long),
    ]


def _get_client():
    global _client
    api_key = _get_api_key()
    if not api_key:
        raise ValueError("Groq API key is missing")
    if _client is None:
        _client = OpenAI(api_key=api_key, base_url="https://api.groq.com/openai/v1")
    return _client


def _get_api_key():
    api_key = os.environ.get("GROQ_API_KEY", "").strip()
    if api_key:
        return api_key
    if os.path.exists(KEYFILE):
        with open(KEYFILE, encoding="utf-8") as key_file:
            return key_file.read().strip() or None
    return None


def _vision_model() -> str:
    return os.environ.get("JARVIS_VISION_MODEL", "qwen/qwen3.8-27b")


def _capture_image(target: str):
    """Return a PIL Image for target, or None when capture is impossible."""
    if ImageGrab is None:
        return None
    try:
        if target == "screen":
            return ImageGrab.grab()
        if target != "active_window":
            return None
        hwnd = ctypes.windll.user32.GetForegroundWindow()
        if not hwnd:
            return None
        rect = _RECT()
        if not ctypes.windll.user32.GetWindowRect(hwnd, ctypes.byref(rect)):
            return None
        if rect.right <= rect.left or rect.bottom <= rect.top:
            return None
        return ImageGrab.grab(bbox=(rect.left, rect.top, rect.right, rect.bottom))
    except Exception:
        return None


def ask_screen(question: str, target: str) -> ToolResult:
    if target not in ("screen", "active_window"):
        return ToolResult(False, "I can look at 'screen' or 'active_window'.")
    if not isinstance(question, str) or not question.strip():
        return ToolResult(False, "I need a question to answer about the screen.")
    if os.name != "nt":
        return ToolResult(False, "Vision is available only on Windows.")

    image = _capture_image(target)
    if image is None:
        message = "I couldn't capture the active window." if target == "active_window" else "I couldn't capture the screen."
        return ToolResult(False, message)
    if not _get_api_key():
        return ToolResult(False, "Vision needs a Groq API key (set GROQ_API_KEY or the Jarvis key file).")
    try:
        client = _get_client()
    except ValueError:
        return ToolResult(False, "Vision needs a Groq API key (set GROQ_API_KEY or the Jarvis key file).")
    except Exception:
        return ToolResult(False, "I couldn't reach the vision service.")

    image = image.convert("RGB")
    width, height = image.size
    if max(width, height) > 1280:
        scale = 1280 / max(width, height)
        image = image.resize((round(width * scale), round(height * scale)), Image.LANCZOS)
    encoded = io.BytesIO()
    image.save(encoded, format="JPEG", quality=70)
    data_uri = "data:image/jpeg;base64," + base64.b64encode(encoded.getvalue()).decode("ascii")
    try:
        response = client.chat.completions.create(
            model=_vision_model(),
            messages=[
                {"role": "system", "content": "You are the eyes of a desktop assistant. Answer the question about the screenshot factually and concisely, based only on what is visible. If you cannot tell from the image, say you cannot tell. Do not guess."},
                {"role": "user", "content": [
                    {"type": "text", "text": question.strip()},
                    {"type": "image_url", "image_url": {"url": data_uri}},
                ]},
            ],
            max_completion_tokens=300,
        )
        answer = (response.choices[0].message.content or "").strip()
    except Exception as exc:
        return ToolResult(False, f"I couldn't get a vision answer ({type(exc).__name__}).")
    if not answer:
        return ToolResult(False, "The vision model returned no answer.")
    return ToolResult(True, answer, {"target": target, "model": _vision_model()})
