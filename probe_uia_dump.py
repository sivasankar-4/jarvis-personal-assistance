# probe_uia_dump.py - dump the taskbar UIA subtree + top-level windows.
import time
from pywinauto import Desktop

desk = Desktop(backend="uia")

print("=== TOP-LEVEL WINDOWS ===")
for w in desk.windows():
    try:
        print(f"- {w.element_info.name!r} | class={w.element_info.class_name} | rect={w.rectangle()}")
    except Exception as exc:
        print(f"- <error {type(exc).__name__}>")

print()
print("=== TASKBAR (Shell_TrayWnd) subtree ===")
tray = None
for w in desk.windows():
    if w.element_info.class_name == "Shell_TrayWnd":
        tray = w
        break
if tray is None:
    print("Shell_TrayWnd not found")
else:
    start = time.time()
    count = 0
    def walk(el, depth):
        global count
        count += 1
        info = el.element_info
        try:
            r = info.rectangle
            rect = f"({r.left},{r.top})-({r.right},{r.bottom})"
        except Exception:
            rect = "?"
        name = (info.name or "").replace("\n", " ")
        print("  " * depth + f"[{info.control_type}] name={name!r} class={info.class_name} auto={info.automation_id!r} rect={rect}")
        if depth >= 8:
            return
        for child in el.children():
            walk(child, depth + 1)
    walk(tray, 0)
    print(f"--- {count} elements in {time.time() - start:.2f}s ---")
