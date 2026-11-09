"""In-memory, cloud-backed semantic screen inspection for Jarvis."""

import base64
import ctypes
import io
import json
import os
import re

try:
    from PIL import Image, ImageDraw, ImageGrab
except ImportError:
    Image = None
    ImageDraw = None
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


def _capture_with_origin(target: str):
    """Return (PIL Image, origin_x, origin_y) or None when capture is impossible."""
    if ImageGrab is None:
        return None
    try:
        if target == "screen":
            return ImageGrab.grab(), 0, 0
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
        return ImageGrab.grab(bbox=(rect.left, rect.top, rect.right, rect.bottom)), rect.left, rect.top
    except Exception:
        return None


def _capture_image(target: str):
    """Return a PIL Image for target, or None when capture is impossible."""
    captured = _capture_with_origin(target)
    return captured[0] if captured else None


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


def _coarse_locate_attempt(client, image, description, target):
    """One coarse locate+confirm pass over a captured image.

    Returns (outcome, payload): "confirmed" with payload (x_img, y_img)
    in original-image coordinates; "retry" with the failure ToolResult
    when the model answered well-formed but negatively (absent claim or
    confirmation NO); "fatal" with the failure ToolResult when retrying
    cannot help (API error or malformed answer).
    """
    img_w, img_h = image.size
    sent = image.convert("RGB")
    if max(img_w, img_h) > 1280:
        scale = 1280 / max(img_w, img_h)
        sent = sent.resize((round(img_w * scale), round(img_h * scale)), Image.LANCZOS)
    encoded = io.BytesIO()
    sent.save(encoded, format="JPEG", quality=70)
    data_uri = "data:image/jpeg;base64," + base64.b64encode(encoded.getvalue()).decode("ascii")
    try:
        response = client.chat.completions.create(
            model=_vision_model(),
            messages=[
                {"role": "system", "content": "You are the eyes of a desktop assistant. You locate UI elements in screenshots."},
                {"role": "user", "content": [
                    {"type": "text", "text": f"Find the center of this element: {description}. Reply with ONLY a JSON object {{\"x\": <int>, \"y\": <int>}} with coordinates on a 0-1000 scale relative to image width and height. If the element is not present, reply {{\"x\": -1, \"y\": -1}}."},
                    {"type": "image_url", "image_url": {"url": data_uri}},
                ]},
            ],
            max_completion_tokens=100,
        )
        content = response.choices[0].message.content or ""
    except Exception as exc:
        return "fatal", ToolResult(False, f"I couldn't locate that ({type(exc).__name__}).")
    match = re.search(r"\{.*?\}", content, re.DOTALL)
    if not match:
        return "fatal", ToolResult(False, f"I couldn't work out where '{description}' is.")
    try:
        coordinates = json.loads(match.group(0))
        raw_x, raw_y = coordinates["x"], coordinates["y"]
        if isinstance(raw_x, bool) or isinstance(raw_y, bool) or not isinstance(raw_x, (int, float)) or not isinstance(raw_y, (int, float)):
            raise ValueError("invalid coordinates")
    except (json.JSONDecodeError, KeyError, TypeError, ValueError):
        return "fatal", ToolResult(False, f"I couldn't work out where '{description}' is.")
    if raw_x < 0 or raw_y < 0:
        return "retry", ToolResult(False, f"I couldn't find '{description}' on the screen.")
    x_img = min(max(round(raw_x / 1000 * img_w), 0), img_w - 1)
    y_img = min(max(round(raw_y / 1000 * img_w), 0), img_h - 1)
    scale = sent.width / img_w
    mx, my = x_img * scale, y_img * scale
    marked_image = sent.copy()
    marker = ImageDraw.Draw(marked_image)
    marker.ellipse((mx - 22, my - 22, mx + 22, my + 22), outline=(255, 0, 0), width=4)
    marker.line((mx - 15, my, mx + 15, my), fill=(255, 0, 0), width=4)
    marker.line((mx, my - 15, mx, my + 15), fill=(255, 0, 0), width=4)
    marker_buffer = io.BytesIO()
    marked_image.save(marker_buffer, format="JPEG", quality=70)
    marker_uri = "data:image/jpeg;base64," + base64.b64encode(marker_buffer.getvalue()).decode("ascii")
    try:
        confirmation = client.chat.completions.create(
            model=_vision_model(),
            messages=[
                {"role": "system", "content": "You verify UI element locations in screenshots. Answer with exactly one word."},
                {"role": "user", "content": [
                    {"type": "text", "text": f"A red circle marks a location in this screenshot. Is the circled location on {description}? Reply with ONLY the word YES or NO."},
                    {"type": "image_url", "image_url": {"url": marker_uri}},
                ]},
            ],
            max_completion_tokens=10,
        )
        confirmed = (confirmation.choices[0].message.content or "").strip().upper().startswith("YES")
    except Exception:
        confirmed = False
    if not confirmed:
        return "retry", ToolResult(
            False, f"I couldn't confirm '{description}' is really there, so I won't guess.",
            {"target": target, "model": _vision_model()},
        )
    return "confirmed", (x_img, y_img)


def locate_element(description: str, target: str) -> ToolResult:
    if target not in ("screen", "active_window"):
        return ToolResult(False, "I can look at 'screen' or 'active_window'.")
    if not isinstance(description, str) or not description.strip():
        return ToolResult(False, "I need a description of the element to find.")
    if os.name != "nt":
        return ToolResult(False, "Vision is available only on Windows.")

    captured = _capture_with_origin(target)
    if captured is None:
        message = "I couldn't capture the active window." if target == "active_window" else "I couldn't capture the screen."
        return ToolResult(False, message)
    if len(captured) == 2:
        image, (origin_x, origin_y) = captured
    else:
        image, origin_x, origin_y = captured
    original_image = image
    img_w, img_h = original_image.size
    if not _get_api_key():
        return ToolResult(False, "Vision needs a Groq API key (set GROQ_API_KEY or the Jarvis key file).")
    try:
        client = _get_client()
    except ValueError:
        return ToolResult(False, "Vision needs a Groq API key (set GROQ_API_KEY or the Jarvis key file).")
    except Exception:
        return ToolResult(False, "I couldn't reach the vision service.")

    failure = None
    x_img = y_img = None
    for attempt in range(2):
        if attempt == 1:
            captured = _capture_with_origin(target)
            if captured is None:
                message = "I couldn't capture the active window." if target == "active_window" else "I couldn't capture the screen."
                return ToolResult(False, message)
            if len(captured) == 2:
                image, (origin_x, origin_y) = captured
            else:
                image, origin_x, origin_y = captured
            original_image = image
            img_w, img_h = original_image.size
        outcome, payload = _coarse_locate_attempt(client, original_image, description, target)
        if outcome == "confirmed":
            x_img, y_img = payload
            break
        failure = payload
        if outcome == "fatal":
            return failure
    if x_img is None:
        return failure
    if target == "screen":
        side = min(480, img_w, img_h)
        crop_x0 = min(max(x_img - side // 2, 0), img_w - side)
        crop_y0 = min(max(y_img - side // 2, 0), img_h - side)
        crop_image = original_image.convert("RGB").crop(
            (crop_x0, crop_y0, crop_x0 + side, crop_y0 + side)
        )
        crop_buffer = io.BytesIO()
        crop_image.save(crop_buffer, format="JPEG", quality=70)
        crop_uri = "data:image/jpeg;base64," + base64.b64encode(crop_buffer.getvalue()).decode("ascii")
        try:
            refine_response = client.chat.completions.create(
                model=_vision_model(),
                messages=[
                    {"role": "system", "content": "You are the eyes of a desktop assistant. You locate UI elements in screenshots."},
                    {"role": "user", "content": [
                        {"type": "text", "text": f"Find the center of this element: {description}. Reply with ONLY a JSON object {{\"x\": <int>, \"y\": <int>}} with coordinates on a 0-1000 scale relative to image width and height. If the element is not present, reply {{\"x\": -1, \"y\": -1}}."},
                        {"type": "image_url", "image_url": {"url": crop_uri}},
                    ]},
                ],
                max_completion_tokens=100,
            )
            refine_content = refine_response.choices[0].message.content or ""
        except Exception as exc:
            return ToolResult(False, f"I couldn't locate that ({type(exc).__name__}).")
        refine_match = re.search(r"\{.*?\}", refine_content, re.DOTALL)
        if not refine_match:
            return ToolResult(False, f"I couldn't work out where '{description}' is.")
        try:
            refine_coordinates = json.loads(refine_match.group(0))
            refine_raw_x, refine_raw_y = refine_coordinates["x"], refine_coordinates["y"]
            if isinstance(refine_raw_x, bool) or isinstance(refine_raw_y, bool) or not isinstance(refine_raw_x, (int, float)) or not isinstance(refine_raw_y, (int, float)):
                raise ValueError("invalid coordinates")
        except (json.JSONDecodeError, KeyError, TypeError, ValueError):
            return ToolResult(False, f"I couldn't work out where '{description}' is.")
        if refine_raw_x < 0 or refine_raw_y < 0:
            return ToolResult(
                False, f"I couldn't confirm '{description}' is really there, so I won't guess.",
                {"target": target, "model": _vision_model()},
            )
        refine_x = min(max(round(refine_raw_x / 1000 * side), 0), side - 1)
        refine_y = min(max(round(refine_raw_y / 1000 * side), 0), side - 1)
        refine_marked = crop_image.copy()
        refine_marker = ImageDraw.Draw(refine_marked)
        refine_marker.ellipse((refine_x - 12, refine_y - 12, refine_x + 12, refine_y + 12), outline=(255, 0, 0), width=3)
        refine_marker.line((refine_x - 9, refine_y, refine_x + 9, refine_y), fill=(255, 0, 0), width=3)
        refine_marker.line((refine_x, refine_y - 9, refine_x, refine_y + 9), fill=(255, 0, 0), width=3)
        refine_buffer = io.BytesIO()
        refine_marked.save(refine_buffer, format="JPEG", quality=70)
        refine_marker_uri = "data:image/jpeg;base64," + base64.b64encode(refine_buffer.getvalue()).decode("ascii")
        try:
            refine_confirmation = client.chat.completions.create(
                model=_vision_model(),
                messages=[
                    {"role": "system", "content": "You verify UI element locations in screenshots. Answer with exactly one word."},
                    {"role": "user", "content": [
                        {"type": "text", "text": f"A red circle marks a location in this screenshot. Is the center of the red crosshair exactly on {description}? Reply with ONLY the word YES or NO."},
                        {"type": "image_url", "image_url": {"url": refine_marker_uri}},
                    ]},
                ],
                max_completion_tokens=10,
            )
            refine_confirmed = (refine_confirmation.choices[0].message.content or "").strip().upper().startswith("YES")
        except Exception:
            refine_confirmed = False
        if not refine_confirmed:
            return ToolResult(
                False, f"I couldn't confirm '{description}' is really there, so I won't guess.",
                {"target": target, "model": _vision_model()},
            )
        x_img, y_img = crop_x0 + refine_x, crop_y0 + refine_y
    x, y = origin_x + x_img, origin_y + y_img
    return ToolResult(
        True, f"Found '{description}' at approximately ({x}, {y}).",
        {"x": x, "y": y, "target": target, "model": _vision_model(), "confirmed": True},
    )
