import base64, io, json, math, re
from PIL import Image, ImageDraw, ImageFont
from openai import OpenAI

W, H = 1920, 1080
img = Image.new("RGB", (W, H), (240, 240, 245))
d = ImageDraw.Draw(img)
font = ImageFont.load_default(size=30)
small = ImageFont.load_default(size=22)
d.rectangle([0, 0, W, 60], fill=(60, 60, 70))
d.text((30, 15), "Probe App 16:9", fill=(255, 255, 255), font=font)
for i in range(5):
    y = 300 + i * 60
    d.rectangle([120, y, 1000, y + 22], fill=(205, 205, 215))

TARGETS = {
    "SETTINGS button": (120, 100, 360, 170),
    "Search field": (700, 100, 1300, 150),
    "SAVE button": (120, 900, 320, 970),
    "CANCEL button": (860, 900, 1060, 970),
    "DELETE button": (1600, 900, 1800, 970),
}
colors = {"SETTINGS button": (70, 140, 70), "SAVE button": (50, 100, 200),
          "CANCEL button": (130, 130, 130), "DELETE button": (190, 50, 50)}
for name, (x0, y0, x1, y1) in TARGETS.items():
    if name == "Search field":
        d.rectangle([x0, y0, x1, y1], outline=(120, 120, 130), width=3)
        d.text((x0 + 15, y0 + 8), "Search", fill=(110, 110, 120), font=small)
    else:
        d.rectangle([x0, y0, x1, y1], fill=colors[name])
        label = name.replace(" button", "")
        d.text(((x0 + x1) / 2 - 40, (y0 + y1) / 2 - 15), label, fill=(255, 255, 255), font=font)
img.save("probe_ui2.png")

sent = img.copy()
sent.thumbnail((1280, 1280), Image.LANCZOS)
buf = io.BytesIO()
sent.save(buf, format="JPEG", quality=70)
uri = "data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode()

client = OpenAI()
for name in list(TARGETS) + ["PRINT button"]:
    prompt = (f'Find the center of the {name} in this application screenshot. '
              'Reply with ONLY a JSON object {"x": <int>, "y": <int>} with coordinates '
              'on a 0-1000 scale relative to image width and height. '
              'If it is not present, reply {"x": -1, "y": -1}.')
    r = client.chat.completions.create(
        model="qwen/qwen3.8-27b",
        messages=[{"role": "user", "content": [
            {"type": "text", "text": prompt},
            {"type": "image_url", "image_url": {"url": uri}}]}],
        max_completion_tokens=100)
    raw = r.choices[0].message.content or ""
    m = re.search(r"\{[^}]+\}", raw)
    try:
        p = json.loads(m.group(0)) if m else {}
    except Exception:
        p = {}
    if name not in TARGETS:
        print(f"{name}: ABSENT target -> model said {p}")
        continue
    x0, y0, x1, y1 = TARGETS[name]
    cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
    if p.get("x", -1) < 0:
        print(f"{name}: model reported ABSENT (miss)")
        continue
    px, py = p["x"] / 1000 * W, p["y"] / 1000 * H
    cy_corr = py * (W / H)
    err_raw = math.hypot(px - cx, py - cy)
    err_corr = math.hypot(px - cx, cy_corr - cy)
    in_raw = x0 <= px <= x1 and y0 <= py <= y1
    in_corr = x0 <= px <= x1 and y0 <= cy_corr <= y1
    print(f"{name}: true=({cx:.0f},{cy:.0f}) raw=({px:.0f},{py:.0f}) err={err_raw:.0f}px {'INSIDE' if in_raw else 'MISS'}"
          f" | corrected=({px:.0f},{cy_corr:.0f}) err={err_corr:.0f}px {'INSIDE' if in_corr else 'MISS'}")
