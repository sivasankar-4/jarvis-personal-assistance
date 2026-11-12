import base64, io
from types import SimpleNamespace
from PIL import Image
import jarvis_vision

real_client = jarvis_vision._get_client()
records = []

def recorder(**kwargs):
    content = kwargs["messages"][-1]["content"]
    text = next(p.get("text", "") for p in content if p.get("type") == "text")
    uri = next(p["image_url"]["url"] for p in content if p.get("type") == "image_url")
    raw = base64.b64decode(uri.split(",", 1)[1])
    with Image.open(io.BytesIO(raw)) as im:
        size = im.size
    resp = real_client.chat.completions.create(**kwargs)
    reply = (resp.choices[0].message.content or "").strip()
    records.append((size, text[:45], reply[:120]))
    return resp

fake = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=recorder)))
jarvis_vision._get_client = lambda: fake

for desc in ("the clock", "start"):
    records.clear()
    result = jarvis_vision.locate_element(desc, "screen")
    print(f"=== {desc!r} -> ok={result.ok} | {result.message} | data={result.data}")
    for i, (size, prompt, reply) in enumerate(records, 1):
        print(f"  call {i}: image={size} prompt={prompt!r} reply={reply!r}")
