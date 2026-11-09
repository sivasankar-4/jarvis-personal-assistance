"""
jarvis_voice.py - Live Speech Recognition & Audio Response Loop
Listens continuously through your microphone, processes commands, and speaks back.

M2 Improvement: Interruptible TTS Architecture
- TTS playback runs in a dedicated background thread using the existing runAndWait path.
- request_tts_stop() signals the engine.stop() from any thread; SAPI5 purges the speech
  queue and runAndWait() returns promptly.
- The interrupt poller uses its OWN sr.Microphone instance (same device index) so it
  never competes with the main-loop Microphone context for the PyAudio device handle.
- Pending approval state is NEVER touched by interruption logic.
- The public speak() API is unchanged so all existing tests pass without modification.

Microphone ownership model (single consumer at a time):
  - listen_loop: holds `mic` (main Microphone) during capture_command().
  - speak_interruptible: holds `_interrupt_mic` (separate Microphone, same device).
  Only one context is active at any moment; they never overlap.
"""

import atexit
import logging
import math
import struct
import os
import queue
import re
import subprocess
import sys
import threading
import time

import speech_recognition as sr
import jarvis_brain

JARVIS_DEBUG = os.environ.get("JARVIS_DEBUG", "").strip().lower() in ("1", "true", "yes")
LOG_FILE = os.environ.get("JARVIS_VOICE_LOG", "jarvis_voice.log")

_logger = logging.getLogger("jarvis.voice")
if not _logger.handlers:
    _logger.setLevel(logging.DEBUG)
    try:
        _fh = logging.FileHandler(LOG_FILE, encoding="utf-8")
        _fh.setLevel(logging.DEBUG)
        _fh.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s"))
        _logger.addHandler(_fh)
    except Exception:
        pass


def _diag(msg: str) -> None:
    """Record detailed diagnostics to log file, and mirror to console only if JARVIS_DEBUG=1."""
    try:
        _logger.debug(msg)
    except Exception:
        pass
    if JARVIS_DEBUG:
        print(msg)

LISTEN_TIMEOUT = float(os.environ.get("JARVIS_LISTEN_TIMEOUT", "15"))
PHRASE_TIME_LIMIT = float(os.environ.get("JARVIS_PHRASE_TIME_LIMIT", "25"))
PAUSE_THRESHOLD = float(os.environ.get("JARVIS_PAUSE_THRESHOLD", "1.6"))
NON_SPEAKING_DURATION = float(os.environ.get("JARVIS_NON_SPEAKING_DURATION", "0.6"))
CALIBRATE_DURATION = float(os.environ.get("JARVIS_CALIBRATE_DURATION", "1.0"))
MAX_RECOGNITION_ATTEMPTS = int(os.environ.get("JARVIS_MAX_RECOGNITION_ATTEMPTS", "3"))
MAX_NETWORK_RETRIES = int(os.environ.get("JARVIS_MAX_NETWORK_RETRIES", "2"))
RECOGNITION_NETWORK_TIMEOUT = float(os.environ.get("JARVIS_RECOGNITION_NETWORK_TIMEOUT", "15"))
# Recalibrating during normal use can make quiet speech harder to detect. It is
# disabled by default; set this to a positive number of idle windows to opt in.
IDLE_RECALIBRATE_AFTER = int(os.environ.get("JARVIS_IDLE_RECALIBRATE_AFTER", "0"))
POST_SPEAK_SETTLE_SECONDS = float(os.environ.get("JARVIS_POST_SPEAK_SETTLE", "0.0"))
# Timeout for each microphone polling window while checking for interruption.
# 2.5 s gives enough headroom for a short command (e.g. "open notepad") to be
# captured completely without truncation.
INTERRUPT_POLL_TIMEOUT = float(os.environ.get("JARVIS_INTERRUPT_POLL_TIMEOUT", "2.5"))
MIN_ENERGY_THRESHOLD = 50

CAPTURE_HEARD = "heard"
CAPTURE_IDLE = "idle"
CAPTURE_FAILED = "failed"
CAPTURE_MIC_ERROR = "mic_error"
CAPTURE_SERVICE_ERROR = "service_error"

_WAKE_WORD = re.compile(r"\bjarvis\b", re.IGNORECASE)
_WAKE_WORD_STRIP = re.compile(r"^(?:(?:hey|ok|okay)\s+)?jarvis[,:]?\s*", re.IGNORECASE)

# Interruption intent classification categories
INTERRUPT_STOP = "STOP"
INTERRUPT_COMMAND = "INTERRUPT_AND_PROCESS_COMMAND"
INTERRUPT_IGNORE = "IGNORE"

# Explicit phrases that reliably indicate intent to halt TTS
_EXPLICIT_STOP_PATTERNS = re.compile(
    r"^(?:(?:hey|ok|okay)\s+)?(?:jarvis\s+)?(?:please\s+|just\s+)?"
    r"(?:stop(?:\s+(?:it|talking|speaking|now))?|be\s+quiet|quiet(?:\s+please)?|shut\s+up|shut\s+down|silence|cancel|pause|halt|enough|that'?s\s+enough|cut\s+it\s+out)"
    r"(?:\s+(?:please|now))?$",
    re.IGNORECASE,
)

# ASR variations where "stop" is recognized as "top", but only when addressed to Jarvis
_STOP_VARIATION_PATTERNS = re.compile(
    r"^(?:(?:hey|ok|okay)\s+)?jarvis\s+(?:please\s+)?(?:top|stopp|stope)(?:\s+please)?$",
    re.IGNORECASE,
)

# Common conversational fillers, short question words, and pleasantries that should NOT interrupt TTS
_IGNORED_FILLERS = {
    "", "how", "what", "why", "who", "when", "where", "which",
    "hello", "hi", "hey", "yeah", "yes", "no", "ok", "okay",
    "uh", "um", "ah", "well", "bright", "so", "right",
    "how are you", "bright how are you", "good morning", "good evening",
}

# Recognized actionable command forms
_ACTIONABLE_COMMAND_PATTERNS = re.compile(
    r"^(?:"
    r"(?:open|launch|start|run|pull\s+up|bring\s+up|take\s+me\s+to)\s+[a-z0-9._\s-]+"
    r"|"
    r"(?:close|quit|exit)\s+[a-z0-9._\s-]+"
    r"|"
    r"(?:search(?:\s+for)?|google|look\s+up|find(?:\s+information\s+on)?)\s+.+"
    r"|"
    r"(?:play)\s+.+"
    r"|"
    r"(?:type\s+.+\s+(?:into|in)\s+.+|press\s+.+|send\s+.+|switch\s+(?:the\s+)?window)"
    r"|"
    r"(?:what(?:'s|\s+is)\s+(?:the\s+)?(?:time|date|weather)|what\s+time\s+is\s+it)"
    r"|"
    r"(?:cancel\s+task|stop\s+task|abort\s+task)"
    r")$",
    re.IGNORECASE,
)

# Backwards compatibility alias
_STOP_PHRASES = _EXPLICIT_STOP_PATTERNS


def classify_interruption(transcript: str) -> tuple[str, str | None]:
    """
    Classify a recognized utterance heard during TTS playback.

    Returns:
        (classification, clean_command)
        where classification is one of:
          - INTERRUPT_STOP: User deliberately wants Jarvis to stop speaking immediately.
          - INTERRUPT_COMMAND: User issued a clear actionable command; halt TTS and execute it.
          - INTERRUPT_IGNORE: Speech is noise, wake-word only, filler, or unrelated; do NOT interrupt.
    """
    if not transcript or not isinstance(transcript, str):
        return INTERRUPT_IGNORE, None

    cleaned = " ".join(transcript.strip().split())
    if not cleaned:
        return INTERRUPT_IGNORE, None

    norm = re.sub(r"[^\w\s']", " ", cleaned.lower())
    norm = " ".join(norm.split())

    # 1. Explicit high-confidence stop phrases
    if _EXPLICIT_STOP_PATTERNS.match(norm):
        return INTERRUPT_STOP, None

    # Handle repeated "stop stop..."
    if re.match(r"^(?:(?:hey|ok|okay)\s+)?(?:jarvis\s+)?(?:stop\s*)+$", norm):
        return INTERRUPT_STOP, None

    # 2. Phonetic variations like "Jarvis top" (only when addressed to Jarvis)
    if _STOP_VARIATION_PATTERNS.match(norm):
        return INTERRUPT_STOP, None

    # 3. Check for wake word and command payload
    had_wake_word = bool(re.match(r"^(?:(?:hey|ok|okay)\s+)?jarvis\b", norm))
    cmd = _WAKE_WORD_STRIP.sub("", norm).strip()

    # Wake word alone ("Jarvis", "Hey Jarvis") -> IGNORE
    if had_wake_word and not cmd:
        return INTERRUPT_IGNORE, None

    # Common non-actionable fillers and chatter ("how", "bright how are you", etc.) -> IGNORE
    if cmd in _IGNORED_FILLERS or norm in _IGNORED_FILLERS:
        return INTERRUPT_IGNORE, None

    # 4. Actionable command matching
    # Check if the command without wake word matches known actionable intents
    if _ACTIONABLE_COMMAND_PATTERNS.match(cmd):
        return INTERRUPT_COMMAND, command_from_transcript(cleaned)

    # If the user explicitly addressed Jarvis with a substantive command payload (>= 2 words)
    # that is not a filler or pleasantry, treat as actionable command
    if had_wake_word and len(cmd.split()) >= 2:
        return INTERRUPT_COMMAND, command_from_transcript(cleaned)

    # 5. Default: Unrelated speech, ambient fragments, or short noise -> IGNORE
    return INTERRUPT_IGNORE, None

_tts_engine = None
_tts_lock = False

# --- Interruption and Session state ---
_session_counter = 0
_session_lock = threading.Lock()
# Set this event from any thread to request TTS stop immediately.
_stop_event = threading.Event()
# Live reference to the currently running TTS background thread.
_tts_thread: threading.Thread | None = None


def _env_device_index():
    raw = os.environ.get("JARVIS_MIC_INDEX", "").strip()
    if not raw:
        return None
    return int(raw)


def apply_recognizer_settings(recognizer):
    """Configure end-of-speech detection for complete natural sentences."""
    if NON_SPEAKING_DURATION > PAUSE_THRESHOLD:
        raise ValueError("JARVIS_NON_SPEAKING_DURATION must be <= JARVIS_PAUSE_THRESHOLD")
    recognizer.pause_threshold = PAUSE_THRESHOLD
    recognizer.non_speaking_duration = NON_SPEAKING_DURATION
    recognizer.phrase_threshold = 0.3
    # Freeze the energy floor after calibration so speech itself cannot
    # ratchet the threshold up and cut the rest of the sentence off.
    recognizer.dynamic_energy_threshold = False
    # SpeechRecognition passes this value to urllib for recognize_google().
    # It limits a stalled Google request separately from microphone listening.
    recognizer.operation_timeout = RECOGNITION_NETWORK_TIMEOUT
    return recognizer


def command_from_transcript(transcript):
    """Strip the wake word only. Do not truncate or lowercase the rest."""
    text = " ".join((transcript or "").split())
    without_wake = _WAKE_WORD.sub(" ", text)
    return " ".join(without_wake.split()).strip(" ,")


def transcribe_audio(recognizer, audio):
    text = recognizer.recognize_google(audio)
    return (text or "").strip()


def _microphone_label(mic):
    try:
        names = sr.Microphone.list_microphone_names()
    except Exception:
        names = []
    if mic.device_index is None:
        try:
            audio = sr.Microphone.get_pyaudio().PyAudio()
            try:
                info = audio.get_default_input_device_info()
                return f"default ({info.get('name', 'unknown')})"
            finally:
                audio.terminate()
        except Exception:
            return "default"
    if 0 <= mic.device_index < len(names) and names[mic.device_index]:
        return f"{mic.device_index}: {names[mic.device_index]}"
    return str(mic.device_index)


def open_microphone():
    try:
        mic = sr.Microphone(device_index=_env_device_index())
    except Exception as exc:
        print(f"Error accessing microphone: {exc}")
        return None
    print(f"Microphone: {_microphone_label(mic)}")
    return mic


def calibrate_for_ambient_noise(recognizer, source, duration=None):
    calibrate_for = CALIBRATE_DURATION if duration is None else duration
    print("Calibrating microphone for ambient noise...")
    recognizer.adjust_for_ambient_noise(source, duration=calibrate_for)
    if recognizer.energy_threshold < MIN_ENERGY_THRESHOLD:
        recognizer.energy_threshold = MIN_ENERGY_THRESHOLD
    print(f"Energy threshold: {recognizer.energy_threshold:.0f}")


def capture_command(
    recognizer,
    microphone,
    max_attempts=MAX_RECOGNITION_ATTEMPTS,
    max_network_retries=MAX_NETWORK_RETRIES,
):
    """
    Listen and transcribe one command.

    Returns (status, text). Every retry captures a new utterance; no unusable
    audio is submitted again. Silence and microphone errors do not retry.

    OWNERSHIP CONTRACT: This function is the SOLE consumer of `microphone`
    during its execution. The interrupt poller uses a separate Microphone
    instance and must never be active at the same time as this function.
    """
    if max_attempts < 1:
        return CAPTURE_FAILED, None

    last_notice = None
    network_failures = 0
    for attempt in range(1, max_attempts + 1):
        stage = "opening microphone"
        t_listen_start = time.monotonic()
        try:
            _diag(f"[Capture#{attempt}]: Microphone capture started")
            with microphone as source:
                _diag(f"[Capture#{attempt}]: listening (timeout={LISTEN_TIMEOUT:.0f}s)")
                stage = "recording audio"
                audio = recognizer.listen(
                    source,
                    timeout=LISTEN_TIMEOUT,
                    phrase_time_limit=PHRASE_TIME_LIMIT,
                )
            _diag(f"[Capture#{attempt}]: Microphone stream released")
            t_audio = time.monotonic()
            _diag(f"({t_audio - t_listen_start:.1f}s) Processing...")
            stage = "contacting Google Speech Recognition"
            text = transcribe_audio(recognizer, audio)
            t_recog = time.monotonic()
            if text:
                _diag(f"({t_recog - t_audio:.1f}s recog)")
                return CAPTURE_HEARD, text
            last_notice = "empty transcript"
            _diag(f"(empty transcript, attempt {attempt}/{max_attempts})")
        except sr.WaitTimeoutError:
            _diag(f"(silence after {time.monotonic() - t_listen_start:.1f}s)")
            return CAPTURE_IDLE, None
        except sr.UnknownValueError:
            _diag(f"(unclear, attempt {attempt}/{max_attempts})")
            last_notice = "speech was not clear"
        except sr.RequestError as exc:
            network_failures += 1
            last_notice = f"Google speech service error: {exc}"
            if network_failures > max_network_retries:
                print(
                    f"\n[Notice]: {last_notice}. Network retry limit "
                    f"({max_network_retries}) reached."
                )
                return CAPTURE_SERVICE_ERROR, None
            backoff = 0.5 * network_failures
            print(
                f"\n[Notice]: {last_notice}. Retrying after {backoff:.1f}s "
                f"({network_failures}/{max_network_retries})."
            )
            time.sleep(backoff)
        except OSError as exc:
            # A few urllib/socket failures can escape RequestError while the
            # response body is being read. At this point the microphone has
            # already been closed, so do not mislabel that as a device fault.
            if stage == "contacting Google Speech Recognition":
                network_failures += 1
                last_notice = f"Google speech transport error: {exc}"
                if network_failures > max_network_retries:
                    print(
                        f"\n[Notice]: {last_notice}. Network retry limit "
                        f"({max_network_retries}) reached."
                    )
                    return CAPTURE_SERVICE_ERROR, None
                backoff = 0.5 * network_failures
                print(
                    f"\n[Notice]: {last_notice}. Retrying after {backoff:.1f}s "
                    f"({network_failures}/{max_network_retries})."
                )
                time.sleep(backoff)
                continue
            last_notice = f"microphone/device error during {stage}: {exc}"
            print(f"\n[Notice]: {last_notice}")
            return CAPTURE_MIC_ERROR, None
        except Exception as exc:
            last_notice = f"unexpected error during {stage}: {type(exc).__name__}: {exc}"
            print(f"\n[Notice]: {last_notice}")
            return CAPTURE_FAILED, None

    if last_notice:
        print(f"[Notice]: Could not understand speech after {max_attempts} attempts.")
    return CAPTURE_FAILED, None


# ---------------------------------------------------------------------------
# TTS engine management
# ---------------------------------------------------------------------------

# _dispatch_pyttsx3 blocks until pyttsx3's runAndWait() finishes — that is full
# synthesis + audio playback for the entire text, not just engine initialisation.
# A generous upper bound: at ~130 wpm (SAPI5's default rate) a 500-word reply
# takes ~230 s.  Round up to 300 s (5 min) to cover any unexpectedly long reply
# while still guaranteeing the caller is not blocked forever if the driver hangs.
_TTS_DISPATCH_TIMEOUT: float = 300.0
_TTS_CONTROL_TIMEOUT: float = 2.0

_speech_queue: queue.Queue = queue.Queue()
_speech_thread: threading.Thread | None = None
_speech_thread_lock = threading.Lock()


class _SpeechControl:
    """Control message sent to the dedicated speech worker thread."""

    def __init__(self, action: str, done_event: threading.Event | None = None):
        self.action = action
        self.done_event = done_event


class _SpeechUtterance:
    """Utterance payload sent to the dedicated speech worker thread."""

    def __init__(self, text: str, done_event: threading.Event, err_holder: list[Exception]):
        self.text = text
        self.done_event = done_event
        self.err_holder = err_holder


def _ensure_speech_thread() -> threading.Thread:
    global _speech_thread
    with _speech_thread_lock:
        if _speech_thread is None or not _speech_thread.is_alive():
            t = threading.Thread(
                target=_speech_worker_loop,
                daemon=True,
                name="jarvis-speech-worker",
            )
            _speech_thread = t
            t.start()
        return _speech_thread


def _stop_speech_worker() -> None:
    global _speech_thread
    with _speech_thread_lock:
        if _speech_thread is not None and _speech_thread.is_alive():
            _speech_queue.put(None)
            _speech_thread.join(timeout=1.0)
            _speech_thread = None


atexit.register(_stop_speech_worker)


def _stop_engine_worker() -> None:
    """Stop the SAPI5 engine on the worker thread."""
    global _tts_engine
    engine = _tts_engine
    if engine is not None:
        try:
            engine.stop()
        except Exception:
            pass


def _discard_tts_engine_worker() -> None:
    """Discard the SAPI5 engine on the worker thread."""
    global _tts_engine
    engine = _tts_engine
    _tts_engine = None
    if engine is not None:
        try:
            engine.stop()
        except Exception:
            pass
        try:
            del engine
        except Exception:
            pass
    try:
        import pyttsx3
        pyttsx3._activeEngines.clear()
    except Exception:
        pass
    try:
        import gc
        gc.collect()
    except Exception:
        pass


def _speech_worker_loop() -> None:
    has_com = False
    try:
        import pythoncom
        pythoncom.CoInitialize()
        has_com = True
    except Exception:
        has_com = False

    try:
        while True:
            item = _speech_queue.get()
            if item is None:
                _speech_queue.task_done()
                break

            if isinstance(item, _SpeechControl):
                try:
                    if item.action == "stop":
                        _stop_engine_worker()
                    elif item.action == "discard":
                        _discard_tts_engine_worker()
                finally:
                    if item.done_event is not None:
                        item.done_event.set()
                    _speech_queue.task_done()
                continue

            if isinstance(item, _SpeechUtterance):
                text, done_event, err_holder = item.text, item.done_event, item.err_holder
            else:
                text, done_event, err_holder = item

            try:
                if _stop_event.is_set():
                    _diag("[TTS]: speech worker abandoning stopped utterance")
                else:
                    _speak_with_pyttsx3(text)
            except Exception as exc:
                err_holder.append(exc)
            finally:
                done_event.set()
                _speech_queue.task_done()
    finally:
        _discard_tts_engine_worker()
        if has_com:
            try:
                import pythoncom
                pythoncom.CoUninitialize()
            except Exception:
                pass


def _discard_tts_engine():
    """Drop the SAPI5 engine on the speech worker thread so the next utterance gets a fresh COM voice."""
    global _speech_thread, _tts_engine
    if threading.current_thread() == _speech_thread:
        _discard_tts_engine_worker()
        return

    with _speech_thread_lock:
        worker_alive = _speech_thread is not None and _speech_thread.is_alive()

    if not worker_alive:
        _discard_tts_engine_worker()
        return

    done_event = threading.Event()
    ctrl = _SpeechControl("discard", done_event)
    _speech_queue.put(ctrl)
    if not done_event.wait(timeout=_TTS_CONTROL_TIMEOUT):
        _diag("[TTS]: worker discard timed out; executing fallback discard")
        _discard_tts_engine_worker()


def _new_tts_engine():
    """
    Create a new pyttsx3 engine.

    Windows SAPI5 cannot safely reuse an engine after runAndWait(): the driver
    is left with _busy=False, so the next say() starts outside the event loop
    and the following runAndWait() can stop playback or return immediately.
    """
    global _tts_engine
    import pyttsx3

    _discard_tts_engine_worker()
    engine = pyttsx3.init()
    try:
        volume = engine.getProperty("volume")
        if volume is not None and float(volume) < 0.2:
            engine.setProperty("volume", 1.0)
            _diag("[TTS]: engine volume was low; restored to 1.0")
    except Exception:
        pass

    try:
        def _on_word(name, location, length):
            if _stop_event.is_set():
                try:
                    engine.stop()
                except Exception:
                    pass

        def _on_utterance(name):
            if _stop_event.is_set():
                try:
                    engine.stop()
                except Exception:
                    pass

        engine.connect("started-word", _on_word)
        engine.connect("started-utterance", _on_utterance)
    except Exception:
        pass

    _tts_engine = engine
    return engine


def _dispatch_pyttsx3(text: str) -> None:
    _ensure_speech_thread()
    done_event = threading.Event()
    err_holder: list[Exception] = []
    _speech_queue.put(_SpeechUtterance(text, done_event, err_holder))
    if not done_event.wait(timeout=_TTS_DISPATCH_TIMEOUT):
        raise TimeoutError(f"pyttsx3 speech dispatch timed out after {_TTS_DISPATCH_TIMEOUT}s")
    if err_holder:
        raise err_holder[0]


def _speak_with_pyttsx3(text):
    if threading.current_thread() != _speech_thread:
        _dispatch_pyttsx3(text)
        return
    if _stop_event.is_set():
        _diag("[TTS]: speech worker abandoning stopped utterance")
        return
    engine = _new_tts_engine()
    _diag(f"[TTS]: speaking {len(text)} characters")
    engine.say(text)
    engine.runAndWait()
    _diag("[TTS]: playback finished")


_TTS_KEYFILE = os.path.expanduser("~/.openjarvis/free.key")
_ORPHEUS_VOICES = {"autumn", "diana", "hannah", "austin", "daniel", "troy"}
_groq_tts_client = None
_orpheus_cooldown_until = 0.0


def _split_for_tts(text: str) -> list[str]:
    """Split text into chunks of at most 200 characters, respecting sentence/word boundaries."""
    if not text or not text.strip():
        return []

    raw_sentences = re.split(r'(?<=[.!?])\s+|\r?\n+', text.strip())
    sentences = [" ".join(s.split()) for s in raw_sentences if s.strip()]
    if not sentences:
        return []

    units = []
    for s in sentences:
        if len(s) <= 200:
            units.append(s)
        else:
            rem = s
            while len(rem) > 200:
                idx = rem[:200].rfind(" ")
                if len(rem) > 200 and rem[200] == " ":
                    idx = 200
                if idx != -1:
                    chunk = rem[:idx].strip()
                    rem = rem[idx:].strip()
                else:
                    chunk = rem[:200].strip()
                    rem = rem[200:].strip()
                if chunk:
                    units.append(chunk)
            if rem.strip():
                units.append(rem.strip())

    chunks = []
    current = ""
    for u in units:
        if not current:
            current = u
        elif len(current) + 1 + len(u) <= 200:
            current = current + " " + u
        else:
            chunks.append(current)
            current = u
    if current:
        chunks.append(current)

    return chunks


def _tts_api_key() -> str | None:
    api_key = os.environ.get("GROQ_API_KEY", "").strip()
    if api_key:
        return api_key
    if os.path.exists(_TTS_KEYFILE):
        try:
            with open(_TTS_KEYFILE, encoding="utf-8") as key_file:
                return key_file.read().strip() or None
        except Exception:
            return None
    return None


def _tts_client():
    global _groq_tts_client
    api_key = _tts_api_key()
    if not api_key:
        return None
    if _groq_tts_client is None:
        try:
            from openai import OpenAI
            _groq_tts_client = OpenAI(api_key=api_key, base_url="https://api.groq.com/openai/v1")
        except Exception:
            return None
    return _groq_tts_client


def _tts_model() -> str:
    return os.environ.get("JARVIS_TTS_MODEL", "").strip() or "canopylabs/orpheus-v1-english"


def _tts_voice() -> str:
    voice = os.environ.get("JARVIS_TTS_VOICE", "").strip().lower()
    return voice if voice in _ORPHEUS_VOICES else "troy"


def _play_wav(data: bytes) -> None:
    import tempfile
    import winsound
    fd, path = tempfile.mkstemp(suffix=".wav")
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
        winsound.PlaySound(path, winsound.SND_FILENAME)
    finally:
        try:
            os.unlink(path)
        except OSError:
            pass


def _purge_playback() -> None:
    try:
        import winsound
        winsound.PlaySound(None, winsound.SND_PURGE)
    except Exception:
        pass


def _response_bytes(response) -> bytes:
    if hasattr(response, "read") and callable(response.read):
        return response.read()
    if hasattr(response, "content"):
        return response.content
    return bytes(response)


def _speak_with_orpheus(text: str) -> bool:
    client = _tts_client()
    if client is None:
        return False
    for chunk in _split_for_tts(text):
        if _stop_event.is_set():
            return True
        try:
            response = client.audio.speech.create(
                model=_tts_model(),
                voice=_tts_voice(),
                input=chunk,
                response_format="wav",
            )
            _play_wav(_response_bytes(response))
        except Exception:
            return False
    return True


def request_tts_stop():
    """
    Signal the active TTS to stop immediately.
    Sets _stop_event, purges playback, and requests the speech worker thread
    to stop the engine on its dedicated thread.
    Safe to call from any thread even if no TTS is active.
    Pending approval state is NOT touched.
    """
    _stop_event.set()
    _purge_playback()

    if threading.current_thread() == _speech_thread:
        _stop_engine_worker()
        return

    with _speech_thread_lock:
        worker_alive = _speech_thread is not None and _speech_thread.is_alive()

    # If an engine was explicitly set (e.g. in tests) but the worker is not running,
    # start the worker so the stop executes on the jarvis-speech-worker thread.
    if _tts_engine is not None and not worker_alive:
        _ensure_speech_thread()
        worker_alive = True

    if worker_alive:
        done_event = threading.Event()
        ctrl = _SpeechControl("stop", done_event)
        _speech_queue.put(ctrl)
        if not done_event.wait(timeout=_TTS_CONTROL_TIMEOUT):
            _diag("[TTS]: worker stop timed out; attempting fallback stop")
            engine = _tts_engine
            if engine is not None:
                try:
                    engine.stop()
                except Exception:
                    pass


def is_tts_active() -> bool:
    """Return True if a TTS background thread is currently running."""
    t = _tts_thread
    return t is not None and t.is_alive()


def _run_tts_background(text: str) -> None:
    """
    TTS worker that runs in a background daemon thread.

    Uses the standard runAndWait() path so existing mocks and tests continue
    to work. When request_tts_stop() is called from the main thread, SAPI5's
    engine.stop() purges the speech queue and runAndWait() returns, so this
    thread exits cleanly.
    """
    global _tts_lock, _orpheus_cooldown_until
    if sys.platform == "darwin":
        proc = subprocess.Popen(["say", "-v", "Samantha", text])
        # Poll so the stop event can terminate the subprocess.
        while proc.poll() is None:
            if _stop_event.is_set():
                proc.terminate()
                break
            time.sleep(0.05)
        _tts_lock = False
        return

    try:
        engine = os.environ.get("JARVIS_TTS_ENGINE", "orpheus").strip().lower()
        if engine != "local" and time.time() >= _orpheus_cooldown_until:
            if _speak_with_orpheus(text):
                return
            _orpheus_cooldown_until = time.time() + 300
            print("[TTS]: natural voice unavailable; using the local voice.")

        try:
            _dispatch_pyttsx3(text)
        except Exception as exc:
            print(f"[TTS]: playback failed ({type(exc).__name__}: {exc}). Retrying with a new engine.")
            _discard_tts_engine()
            try:
                _dispatch_pyttsx3(text)
            except Exception as retry_exc:
                print(
                    f"[TTS]: retry failed ({type(retry_exc).__name__}: {retry_exc}). "
                    "The reply is in the console above."
                )
                _discard_tts_engine()
    finally:
        _tts_lock = False
        if sys.platform != "darwin":
            _discard_tts_engine()


def speak(text):
    """
    Print the reply, speak it in a background thread, and block until
    playback finishes (or is interrupted via request_tts_stop()).

    The public API is identical to the previous blocking version so all
    existing tests and call-sites continue to work unchanged.
    """
    global _tts_lock, _tts_thread, _session_counter
    if not text:
        return
    print(f"\n[JARVIS]: {text}\n")
    if _tts_lock:
        print("[TTS]: skipped overlapping speech request")
        return

    with _session_lock:
        _session_counter += 1
        sid = f"S{_session_counter}"

        _tts_lock = True
        _stop_event.clear()

        _diag(f"[Session {sid}]: TTS session started")

        t = threading.Thread(target=_run_tts_background, args=(text,), daemon=True, name=f"jarvis-tts-{sid}")
        _tts_thread = t
        t.start()
        t.join(timeout=10.0)

        _diag(f"[Session {sid}]: TTS worker terminated")
        _tts_thread = None
        _tts_lock = False
        _stop_event.clear()
        if sys.platform != "darwin":
            _discard_tts_engine()

        _diag(f"[Session {sid}]: Session cleanup completed")


# ---------------------------------------------------------------------------
# Interruptible speak used exclusively by listen_loop
# ---------------------------------------------------------------------------

def _calculate_rms(buffer, sample_width: int = 2) -> int:
    """Calculate Root Mean Square (RMS) energy of an audio buffer."""
    if not isinstance(buffer, (bytes, bytearray)) or len(buffer) == 0:
        return 0
    try:
        import audioop
        return audioop.rms(buffer, sample_width)
    except Exception:
        pass
    if sample_width == 2:
        num_samples = len(buffer) // 2
        if num_samples == 0:
            return 0
        samples = struct.unpack(f"<{num_samples}h", buffer[:num_samples * 2])
        sum_sq = sum(s * s for s in samples)
        return int(math.sqrt(sum_sq / num_samples))
    elif sample_width == 1:
        num_samples = len(buffer)
        if num_samples == 0:
            return 0
        sum_sq = sum((b - 128) * (b - 128) for b in buffer)
        return int(math.sqrt(sum_sq / num_samples))
    elif sample_width == 4:
        num_samples = len(buffer) // 4
        if num_samples == 0:
            return 0
        samples = struct.unpack(f"<{num_samples}i", buffer[:num_samples * 4])
        sum_sq = sum(s * s for s in samples)
        return int(math.sqrt(sum_sq / num_samples))
    return 0


class _EnergyTrackingStream:
    """Pass-through wrapper around a PyAudio stream that records peak RMS energy."""

    def __init__(self, stream, sample_width: int = 2):
        self._stream = stream
        self._sample_width = sample_width if isinstance(sample_width, int) and sample_width > 0 else 2
        self.peak_energy = 0

    def reset_peak(self) -> None:
        self.peak_energy = 0

    def read(self, *args, **kwargs):
        data = self._stream.read(*args, **kwargs)
        if data:
            energy = _calculate_rms(data, self._sample_width)
            if energy > self.peak_energy:
                self.peak_energy = energy
        return data

    def close(self):
        if hasattr(self._stream, "close"):
            return self._stream.close()

    def __getattr__(self, name):
        return getattr(self._stream, name)


def _listen_for_interrupt(interrupt_mic, session_id=None):
    """
    Poll a DEDICATED microphone (interrupt_mic) for stop phrases or new
    commands while TTS is playing in the background thread.

    IMPORTANT: interrupt_mic must be a SEPARATE sr.Microphone instance from the
    one used by capture_command(). It MUST point at the same physical device
    (same device_index) but must NOT be the same Python object, so the two
    PyAudio streams are independent and do not compete for the device handle.

    Returns the first non-empty transcript heard, or None if TTS ends first.
    """
    poll_recognizer = sr.Recognizer()
    # Use shorter thresholds so the interrupt window is responsive.
    poll_recognizer.pause_threshold = 0.8
    poll_recognizer.non_speaking_duration = 0.4
    poll_recognizer.dynamic_energy_threshold = False
    poll_recognizer.operation_timeout = RECOGNITION_NETWORK_TIMEOUT
    # Inherit the calibrated energy floor from the module-level constant so we
    # don't silently lower detection sensitivity during interruption.
    poll_recognizer.energy_threshold = MIN_ENERGY_THRESHOLD

    sid_prefix = f"[Session {session_id}]: " if session_id else "[Interrupt]: "
    _diag(f"{sid_prefix}Interrupt listener started")
    rate = getattr(interrupt_mic, "SAMPLE_RATE", "unknown")
    width = getattr(interrupt_mic, "SAMPLE_WIDTH", "unknown")
    chunk = getattr(interrupt_mic, "CHUNK", "unknown")
    _diag(f"{sid_prefix}Interrupt stream: rate={rate} width={width} chunk={chunk}")
    try:
        while is_tts_active() and not _stop_event.is_set():
            peak_energy = 0
            outcome = "error"
            try:
                try:
                    with interrupt_mic as source:
                        src_width = getattr(source, "SAMPLE_WIDTH", 2)
                        tracker = _EnergyTrackingStream(source.stream, sample_width=src_width)
                        source.stream = tracker
                        try:
                            audio = poll_recognizer.listen(
                                source,
                                timeout=INTERRUPT_POLL_TIMEOUT,
                                phrase_time_limit=5.0,
                            )
                            outcome = "phrase"
                        except sr.WaitTimeoutError:
                            outcome = "timeout"
                            audio = None
                        finally:
                            peak_energy = tracker.peak_energy
                finally:
                    _diag(f"{sid_prefix}Interrupt cycle: peak_energy={peak_energy} outcome={outcome}")

                if outcome == "timeout":
                    continue

                _diag(f"{sid_prefix}Microphone stream released")
                try:
                    heard = (poll_recognizer.recognize_google(audio) or "").strip()
                except (sr.UnknownValueError, sr.RequestError, OSError):
                    continue
                if not heard:
                    continue

                decision, cmd = classify_interruption(heard)
                if decision == INTERRUPT_IGNORE:
                    _diag(f"{sid_prefix}Ignored non-interrupt speech ('{heard}')")
                    continue

                if decision == INTERRUPT_STOP:
                    _diag(f"{sid_prefix}Interrupt detected ('{heard}') -> STOP")
                    return heard

                if decision == INTERRUPT_COMMAND:
                    _diag(f"{sid_prefix}Interrupt detected ('{heard}') -> COMMAND: '{cmd}'")
                    return heard
            except Exception as exc:
                _diag(f"{sid_prefix}poller error ({type(exc).__name__}: {exc})")
                break
    finally:
        _diag(f"{sid_prefix}Interrupt listener terminated")
    return None


def speak_interruptible(text, *args, interrupt_mic=None, **kwargs):
    """
    Speak text in a background thread while listening for interruptions on
    a dedicated interrupt microphone (separate from the main capture mic).

    Supports both:
      - speak_interruptible(text, interrupt_mic)
      - speak_interruptible(text, recognizer, mic)

    Parameters
    ----------
    text : str
        The text to speak.
    interrupt_mic : sr.Microphone, optional
        A SEPARATE Microphone instance (same device index as the main mic)
        reserved exclusively for the interrupt poller. It must NOT be the
        same object as the Microphone used by capture_command() so the two
        PyAudio streams never compete for the same device handle.

    Returns the command transcript (wake-word stripped) if a non-stop utterance
    was captured during TTS, or None if TTS finished naturally or only a stop
    phrase was heard.

    IMPORTANT:
    - Stop phrases halt TTS but return None (caller does not process them).
    - Non-stop utterances halt TTS and are returned so the caller can think().
    - Pending approval state is NEVER touched here.
    """
    if len(args) >= 2:
        interrupt_mic = args[1]
    elif len(args) == 1:
        interrupt_mic = args[0]
    elif interrupt_mic is None:
        interrupt_mic = kwargs.get("mic", kwargs.get("interrupt_mic"))

    if interrupt_mic is None:
        try:
            interrupt_mic = sr.Microphone(device_index=_env_device_index())
        except Exception as exc:
            print(f"[Interrupt]: failed to initialize interrupt microphone ({exc})")
            speak(text)
            return None

    global _tts_lock, _tts_thread, _session_counter
    if not text:
        return None
    print(f"\n[JARVIS]: {text}\n")
    if _tts_lock:
        print("[TTS]: skipped overlapping speech request")
        return None

    with _session_lock:
        _session_counter += 1
        sid = f"S{_session_counter}"

        _tts_lock = True
        _stop_event.clear()

        _diag(f"[Session {sid}]: TTS session started")

        t = threading.Thread(target=_run_tts_background, args=(text,), daemon=True, name=f"jarvis-tts-{sid}")
        _tts_thread = t
        t.start()

        captured = _listen_for_interrupt(interrupt_mic, session_id=sid)

        if captured is not None:
            decision, cmd = classify_interruption(captured)
            if decision == INTERRUPT_STOP:
                _diag(f"[Session {sid}]: classified as stop_only=True")
                _diag(f"[Session {sid}]: TTS stop requested")
                request_tts_stop()
                captured_cmd = None
            elif decision == INTERRUPT_COMMAND:
                _diag(f"[Session {sid}]: classified as stop_only=False")
                _diag(f"[Session {sid}]: TTS stop requested")
                request_tts_stop()
                captured_cmd = cmd
            else:
                _diag(f"[Session {sid}]: Ignored non-interrupt speech ('{captured}')")
                captured_cmd = None
        else:
            captured_cmd = None

        # Cleanup phase: Wait for TTS thread to finish completely before next session can begin
        t.join(timeout=5.0)
        if t.is_alive():
            print(f"[Session {sid}]: Warning: TTS worker still alive after 5s, forcing stop")
            request_tts_stop()
            t.join(timeout=2.0)

        _diag(f"[Session {sid}]: TTS worker terminated")
        _tts_thread = None
        _tts_lock = False
        _stop_event.clear()
        if sys.platform != "darwin":
            _discard_tts_engine()

        _diag(f"[Session {sid}]: Session cleanup completed")

        return captured_cmd


def handle_recognized_text(transcript, think_fn=None, speak_fn=None):
    """Print the full transcript, then route the wake-word-stripped command."""
    think_fn = jarvis_brain.think if think_fn is None else think_fn
    speak_fn = speak if speak_fn is None else speak_fn

    print(f'\nYou said: "{transcript}"')
    command = command_from_transcript(transcript)
    if not command:
        speak_fn("Yes sir, I'm listening.")
        return "wake"

    response = think_fn(command)
    speak_fn(response)
    return response


def _text_fallback_loop():
    print("Falling back to text console input mode.")
    while True:
        try:
            user_input = input("You: ")
            if user_input.strip():
                handle_recognized_text(user_input)
        except (KeyboardInterrupt, EOFError):
            break


def listen_loop():
    try:
        import jarvis_routines
        jarvis_routines.start_scheduler()
    except Exception:
        pass
    recognizer = apply_recognizer_settings(sr.Recognizer())
    mic = open_microphone()
    if mic is None:
        _text_fallback_loop()
        return

    print("\n" + "=" * 50)
    print("  JARVIS AI ASSISTANT IS ACTIVE AND LISTENING")
    print(f"  Listen timeout: {LISTEN_TIMEOUT:.0f}s | Phrase limit: {PHRASE_TIME_LIMIT:.0f}s")
    print(f"  Pause threshold: {PAUSE_THRESHOLD:.1f}s | Google timeout: {RECOGNITION_NETWORK_TIMEOUT:.0f}s")
    print("  Say commands like:")
    print("    - 'Jarvis, open VS Code'")
    print("    - 'Play lofi on YouTube'")
    print("    - 'Stock market update' / 'Analyze Nifty'")
    print("    - 'Send WhatsApp message'")
    print("    - 'Read unread emails'")
    print("=" * 50 + "\n")

    try:
        with mic as source:
            calibrate_for_ambient_noise(recognizer, source)
    except Exception as exc:
        print(f"Error accessing microphone: {exc}")
        _text_fallback_loop()
        return

    # Create a SEPARATE Microphone instance on the same device for the
    # interrupt poller. This gives the poller its own PyAudio stream so it
    # never competes with `mic` (used by capture_command) for the device handle.
    interrupt_mic = sr.Microphone(device_index=_env_device_index())

    def _interruptible_speak(text):
        """Speak via the interrupt-aware path; recurse if a follow-up is captured."""
        interrupted_with = speak_interruptible(text, interrupt_mic)
        if interrupted_with:
            follow_up_response = jarvis_brain.think(interrupted_with)
            _interruptible_speak(follow_up_response)

    speak("Hi boss, welcome back. Jarvis is online and ready to assist you.")
    print("Listening...", flush=True)
    idle_cycles = 0
    while True:
        try:
            _diag("[Voice]: Normal listening resumed")
            status, transcript = capture_command(recognizer, mic)
            if status == CAPTURE_IDLE:
                idle_cycles += 1
                if IDLE_RECALIBRATE_AFTER > 0 and idle_cycles >= IDLE_RECALIBRATE_AFTER:
                    try:
                        with mic as source:
                            calibrate_for_ambient_noise(recognizer, source)
                    except Exception as exc:
                        print(f"[Notice]: calibration skipped ({exc})")
                    idle_cycles = 0
                continue
            if status == CAPTURE_MIC_ERROR:
                time.sleep(0.5)
                print("Listening...", flush=True)
                continue
            if status != CAPTURE_HEARD:
                continue

            idle_cycles = 0
            handle_recognized_text(transcript, speak_fn=_interruptible_speak)
            print("\nListening...", flush=True)
        except KeyboardInterrupt:
            print("\nShutting down Jarvis. Goodbye!")
            break
        except Exception as e:
            print(f"\n[Notice]: {e}")
            time.sleep(0.5)
            print("Listening...", flush=True)


if __name__ == "__main__":
    listen_loop()
