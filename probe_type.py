import ctypes, time
import jarvis_windows as jw

text1 = "abcdefghij ABCDEFGHIJ 0123456789 !@# xyz"
text2 = "klmnop qrstuv wxyz 9876"

print("Click into Notepad now - typing starts in 10 seconds...")
time.sleep(10)
title = jw.foreground_window_title()
print("Foreground:", title)
assert jw.verify_window_target("Notepad", title), "Notepad is not focused - aborting"

user32 = ctypes.windll.user32
KEYUP = jw._KEYUP
UNI = jw._KEYEVENTF_UNICODE

events = []
for ch in text1:
    events.append(jw._Input(type=1, ki=jw._KeyBdInput(0, ord(ch), UNI, 0, 0)))
    events.append(jw._Input(type=1, ki=jw._KeyBdInput(0, ord(ch), UNI | KEYUP, 0, 0)))
arr = (jw._Input * len(events))(*events)
sent = user32.SendInput(len(events), arr, ctypes.sizeof(jw._Input))
print("Batched: sent", sent, "of", len(events), "events")

jw.send_shortcut("enter")

for ch in text2:
    down = jw._Input(type=1, ki=jw._KeyBdInput(0, ord(ch), UNI, 0, 0))
    up = jw._Input(type=1, ki=jw._KeyBdInput(0, ord(ch), UNI | KEYUP, 0, 0))
    user32.SendInput(1, ctypes.byref(down), ctypes.sizeof(jw._Input))
    user32.SendInput(1, ctypes.byref(up), ctypes.sizeof(jw._Input))
    time.sleep(0.03)
print("Paced: done - now copy the whole Notepad document and paste it in chat.")
