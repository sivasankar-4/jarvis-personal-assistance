import ctypes, time
import jarvis_windows as jw

TEXT = "Jarvis typed this EXACTLY right: 100% working (Nov 6)!"

print("Click into Notepad now - paste happens in 10 seconds...")
time.sleep(10)
title = jw.foreground_window_title()
print("Foreground:", title)
assert jw.verify_window_target("Notepad", title), "Notepad is not focused - aborting"

user32 = ctypes.windll.user32
kernel32 = ctypes.windll.kernel32
kernel32.GlobalAlloc.restype = ctypes.c_void_p
kernel32.GlobalLock.restype = ctypes.c_void_p
CF_UNICODETEXT = 13
GMEM_MOVEABLE = 0x0002

user32.OpenClipboard(0)
user32.EmptyClipboard()
data = TEXT.encode("utf-16-le") + b"\x00\x00"
h = kernel32.GlobalAlloc(GMEM_MOVEABLE, len(data))
ptr = kernel32.GlobalLock(h)
ctypes.memmove(ptr, data, len(data))
kernel32.GlobalUnlock(h)
user32.SetClipboardData(CF_UNICODETEXT, h)
user32.CloseClipboard()

r = jw.send_shortcut("ctrl+v")
print("Paste sent:", r.ok, r.message)
print("Now copy the whole Notepad document and paste it in chat.")
