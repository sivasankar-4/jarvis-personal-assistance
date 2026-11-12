import time, traceback, webbrowser, os
import jarvis_actions, jarvis_windows, jarvis_vision, jarvis_brain

T0 = time.time()

def stamp(label, extra=""):
    frames = [f"{os.path.basename(f.filename)}:{f.lineno}:{f.name}"
              for f in traceback.extract_stack()[:-2]
              if "jarvis" in os.path.basename(f.filename)]
    print(f"[{time.time()-T0:5.1f}s] {label} {extra} <- {' > '.join(frames)}")

def wrap(mod, name, label):
    orig = getattr(mod, name)
    def w(*a, **k):
        stamp(label, repr(a)[:120])
        return orig(*a, **k)
    setattr(mod, name, w)

wrap(jarvis_actions, "_open", "actions._open")
wrap(jarvis_windows, "open_website", "windows.open_website")
wrap(webbrowser, "open", "webbrowser.open")
wrap(webbrowser, "open_new", "webbrowser.open_new")
wrap(webbrowser, "open_new_tab", "webbrowser.open_new_tab")
if hasattr(os, "startfile"):
    wrap(os, "startfile", "os.startfile")

orig_ask = jarvis_vision.ask_screen
def ask(*a, **k):
    r = orig_ask(*a, **k)
    print(f"[{time.time()-T0:5.1f}s] ask_screen -> {(r.message or '')[:90]!r}")
    return r
jarvis_vision.ask_screen = ask

print(jarvis_brain.think("open youtube and play Lofi."))
