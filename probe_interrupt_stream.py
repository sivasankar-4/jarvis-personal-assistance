import math, time
from array import array
import speech_recognition as sr

# Replicate listen_loop conditions: BOTH mic objects exist, main is used first.
main_mic = sr.Microphone()
interrupt_mic = sr.Microphone()
r = sr.Recognizer()
with main_mic as source:
    r.adjust_for_ambient_noise(source, duration=1.0)
print("Main mic calibration done. Now reading the INTERRUPT stream raw for 10s - TALK NOW.")
peak, total, n = 0.0, 0.0, 0
with interrupt_mic as source:
    end = time.time() + 10
    while time.time() < end:
        buf = source.stream.read(source.CHUNK)
        a = array("h"); a.frombytes(buf)
        rms = math.sqrt(sum(x * x for x in a) / max(1, len(a)))
        peak = max(peak, rms); total += rms; n += 1
print(f"INTERRUPT STREAM: avg RMS {total / max(1, n):.0f}   peak RMS {peak:.0f}")
print("Healthy speech at a laptop mic = peak in the hundreds or thousands. Silence = peak under 50.")
print("Control arm: now reading the MAIN mic raw for 10s - TALK NOW.")
peak, total, n = 0.0, 0.0, 0
with main_mic as source:
    end = time.time() + 10
    while time.time() < end:
        buf = source.stream.read(source.CHUNK)
        a = array("h"); a.frombytes(buf)
        rms = math.sqrt(sum(x * x for x in a) / max(1, len(a)))
        peak = max(peak, rms); total += rms; n += 1
print(f"MAIN STREAM: avg RMS {total / max(1, n):.0f}   peak RMS {peak:.0f}")

