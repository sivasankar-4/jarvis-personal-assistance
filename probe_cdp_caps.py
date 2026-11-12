import sys
import os
import time
import json
import subprocess
import urllib.request
import urllib.error
import websocket

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

CHROME_PATH = os.path.join(os.environ.get("LOCALAPPDATA", ""), r"Google\Chrome\Application\chrome.exe")
PROFILE_PATH = os.path.join(os.environ.get("TEMP", ""), "jarvis-cdp-capprobe")
PORT = 9223


class CDPClient:
    def __init__(self, ws_url):
        self.ws = websocket.create_connection(ws_url, timeout=10.0)
        self._id = 0

    def send(self, method, params=None):
        self._id += 1
        msg_id = self._id
        payload = {"id": msg_id, "method": method}
        if params is not None:
            payload["params"] = params
        self.ws.send(json.dumps(payload))

        start = time.time()
        while True:
            remaining = 10.0 - (time.time() - start)
            if remaining <= 0:
                raise TimeoutError(f"CDP call {method} timed out after 10s (id {msg_id})")
            self.ws.settimeout(remaining)
            raw = self.ws.recv()
            if not raw:
                continue
            data = json.loads(raw)
            if data.get("id") == msg_id:
                if "error" in data:
                    raise RuntimeError(f"CDP error for {method}: {data['error']}")
                return data.get("result", {})

    def ev(self, js):
        res = self.send("Runtime.evaluate", {
            "expression": js,
            "returnByValue": True,
            "awaitPromise": True
        })
        if "exceptionDetails" in res:
            raise RuntimeError(f"CDP evaluate exception: {res['exceptionDetails']}")
        return res.get("result", {}).get("value")

    def close(self):
        try:
            self.ws.close()
        except Exception:
            pass


def launch():
    chrome = CHROME_PATH
    prof = PROFILE_PATH
    if not os.path.exists(chrome):
        print(f"Error: Chrome executable not found at {chrome}")
        sys.exit(1)

    cmd = [
        chrome,
        f"--remote-debugging-port={PORT}",
        f"--user-data-dir={prof}",
        "--no-first-run",
        "--no-default-browser-check",
        "about:blank"
    ]
    p = subprocess.Popen(cmd)

    start = time.time()
    last_err = None
    while time.time() - start < 20.0:
        if p.poll() is not None:
            print(f"Chrome exited prematurely with code {p.returncode}")
            sys.exit(1)
        try:
            req = urllib.request.urlopen(f"http://127.0.0.1:{PORT}/json/version", timeout=1.0)
            if req.status == 200:
                return p
        except Exception as e:
            last_err = e
        time.sleep(0.5)

    print(f"Timeout waiting for Chrome CDP /json/version up to 20s. Last error: {last_err}")
    try:
        subprocess.run(["taskkill", "/F", "/T", "/PID", str(p.pid)], capture_output=True)
    except Exception:
        pass
    sys.exit(1)


def main():
    p = None
    records = []
    site_times = {}

    def record(site, step, status, evidence):
        records.append((site, step, status, evidence))

    try:
        p = launch()

        # Connect to first page target
        with urllib.request.urlopen(f"http://127.0.0.1:{PORT}/json/list", timeout=5.0) as resp:
            targets = json.loads(resp.read().decode("utf-8"))
        page_targets = [t for t in targets if t.get("type") == "page"]
        if not page_targets:
            raise RuntimeError("No page target found in /json/list")
        ws_url = page_targets[0]["webSocketDebuggerUrl"]
        cdp = CDPClient(ws_url)

        sites = [
            ("wikipedia", "https://en.wikipedia.org", "lofi"),
            ("youtube", "https://www.youtube.com", "lofi")
        ]

        for site, url, query in sites:
            t0 = time.time()
            search_box_info = None

            # S1 navigate
            try:
                cdp.send("Page.navigate", {"url": url})
                start_t = time.time()
                ok = False
                while time.time() - start_t < 15.0:
                    time.sleep(0.3)
                    try:
                        if cdp.ev("document.readyState") == "complete":
                            ok = True
                            break
                    except Exception:
                        pass
                href = cdp.ev("location.href")
                title = str(cdp.ev("document.title") or "")[:60]
                ev_str = f"{href} | {title}"
                record(site, "S1", "PASS" if ok else "FAIL", ev_str)
            except Exception as e:
                record(site, "S1", "FAIL", f"Exception: {e}")

            # S2 find search box
            try:
                find_box_js = """
                (() => {
                    const sel = 'input[type="search"], input[role="searchbox"], input[role="combobox"], input[name="q"], input[name="search"], input[aria-label*="earch" i], input[placeholder*="earch" i]';
                    const els = document.querySelectorAll(sel);
                    for (const el of els) {
                        const rect = el.getBoundingClientRect();
                        const style = window.getComputedStyle(el);
                        if (rect.width > 0 && rect.height > 0 && style.visibility !== "hidden" && style.display !== "none") {
                            return {
                                tag: el.tagName.toLowerCase(),
                                id: el.id || "",
                                name: el.getAttribute("name") || "",
                                type: el.getAttribute("type") || "",
                                aria: el.getAttribute("aria-label") || "",
                                x: Math.round(rect.left + rect.width / 2),
                                y: Math.round(rect.top + rect.height / 2)
                            };
                        }
                    }
                    return null;
                })()
                """
                res = cdp.ev(find_box_js)
                if res:
                    search_box_info = res
                    record(site, "S2", "PASS", str(res))
                else:
                    record(site, "S2", "FAIL", "none found")
            except Exception as e:
                record(site, "S2", "FAIL", f"Exception: {e}")

            # S3 type and submit
            try:
                if not search_box_info:
                    record(site, "S3", "FAIL", "Skipped: search box not found in S2")
                else:
                    href_before = cdp.ev("location.href")
                    x = search_box_info["x"]
                    y = search_box_info["y"]
                    cdp.send("Input.dispatchMouseEvent", {"type": "mousePressed", "x": x, "y": y, "button": "left", "clickCount": 1})
                    cdp.send("Input.dispatchMouseEvent", {"type": "mouseReleased", "x": x, "y": y, "button": "left", "clickCount": 1})
                    time.sleep(0.1)
                    cdp.send("Input.insertText", {"text": query})
                    time.sleep(0.1)
                    cdp.send("Input.dispatchKeyEvent", {"type": "keyDown", "key": "Enter", "code": "Enter", "windowsVirtualKeyCode": 13, "text": "\r"})
                    cdp.send("Input.dispatchKeyEvent", {"type": "keyUp", "key": "Enter", "code": "Enter", "windowsVirtualKeyCode": 13})

                    start_t = time.time()
                    changed = False
                    href_after = href_before
                    while time.time() - start_t < 15.0:
                        time.sleep(0.3)
                        try:
                            curr_href = cdp.ev("location.href")
                            rs = cdp.ev("document.readyState")
                            if curr_href != href_before and rs == "complete":
                                changed = True
                                href_after = curr_href
                                break
                        except Exception:
                            pass
                    if not changed:
                        try:
                            href_after = cdp.ev("location.href")
                        except Exception:
                            pass
                    ev_str = f"before: {href_before} -> after: {href_after}"
                    record(site, "S3", "PASS" if changed else "FAIL", ev_str)
            except Exception as e:
                record(site, "S3", "FAIL", f"Exception: {e}")

            # S4 scroll
            try:
                s_before = cdp.ev("window.scrollY")
                cdp.send("Input.dispatchMouseEvent", {
                    "type": "mouseWheel",
                    "x": 400,
                    "y": 400,
                    "deltaX": 0,
                    "deltaY": 600
                })
                time.sleep(0.7)
                s_after = cdp.ev("window.scrollY")
                ev_str = f"before: {s_before}, after: {s_after}"
                ok = (s_before is not None and s_after is not None and s_after > s_before)
                record(site, "S4", "PASS" if ok else "FAIL", ev_str)
            except Exception as e:
                record(site, "S4", "FAIL", f"Exception: {e}")

            # S5 click first result
            try:
                candidates = (
                    ['a#video-title[href^="/watch"]', 'ytd-video-renderer a#thumbnail[href^="/watch"]']
                    if site == "youtube"
                    else ['.mw-search-result-heading a', 'a[data-serp-pos="0"]']
                )
                find_result_js = f"""
                (() => {{
                    const candidates = {json.dumps(candidates)};
                    for (const sel of candidates) {{
                        const els = document.querySelectorAll(sel);
                        for (const el of els) {{
                            el.scrollIntoView({{block: 'center', inline: 'center'}});
                            const rect = el.getBoundingClientRect();
                            const style = window.getComputedStyle(el);
                            if (rect.width > 0 && rect.height > 0 && style.visibility !== "hidden" && style.display !== "none") {{
                                return {{
                                    matched: true,
                                    selector: sel,
                                    href: el.getAttribute("href") || el.href || "",
                                    x: Math.round(rect.left + rect.width / 2),
                                    y: Math.round(rect.top + rect.height / 2)
                                }};
                            }}
                        }}
                    }}
                    const visibleA = Array.from(document.querySelectorAll('a')).filter(a => {{
                        const r = a.getBoundingClientRect();
                        const s = window.getComputedStyle(a);
                        return r.width > 0 && r.height > 0 && s.visibility !== "hidden" && s.display !== "none";
                    }}).length;
                    return {{matched: false, visible_a_count: visibleA}};
                }})()
                """
                start_find = time.time()
                match_data = None
                while time.time() - start_find < 5.0:
                    match_data = cdp.ev(find_result_js)
                    if match_data and match_data.get("matched"):
                        break
                    time.sleep(0.5)

                if not match_data or not match_data.get("matched"):
                    cnt = match_data.get("visible_a_count", 0) if match_data else 0
                    record(site, "S5", "FAIL", f"no candidate matched (visible <a> count: {cnt})")
                else:
                    elem_href = match_data.get("href", "")
                    tx = match_data["x"]
                    ty = match_data["y"]
                    href_before_click = cdp.ev("location.href")

                    cdp.send("Input.dispatchMouseEvent", {"type": "mousePressed", "x": tx, "y": ty, "button": "left", "clickCount": 1})
                    cdp.send("Input.dispatchMouseEvent", {"type": "mouseReleased", "x": tx, "y": ty, "button": "left", "clickCount": 1})

                    start_poll = time.time()
                    nav_changed = False
                    new_href = href_before_click
                    while time.time() - start_poll < 15.0:
                        time.sleep(0.3)
                        try:
                            curr_href = cdp.ev("location.href")
                            if curr_href != href_before_click:
                                nav_changed = True
                                new_href = curr_href
                                break
                        except Exception:
                            pass
                    if not nav_changed:
                        try:
                            new_href = cdp.ev("location.href")
                        except Exception:
                            pass
                    ev_str = f"elem href: {elem_href} -> new location: {new_href}"
                    record(site, "S5", "PASS" if nav_changed else "FAIL", ev_str)
            except Exception as e:
                record(site, "S5", "FAIL", f"Exception: {e}")

            # S6 (youtube only) playing state
            if site == "youtube":
                try:
                    time.sleep(4.0)
                    js_video = "(()=>{const v=document.querySelector('video');return v?{paused:v.paused,t:v.currentTime,rs:v.readyState}:null})()"
                    r1 = cdp.ev(js_video)
                    time.sleep(3.0)
                    r2 = cdp.ev(js_video)
                    ev_str = f"r1: {r1}, r2: {r2}"
                    ok = False
                    if r1 and r2 and isinstance(r1, dict) and isinstance(r2, dict):
                        paused2 = r2.get("paused")
                        t1 = r1.get("t", 0)
                        t2 = r2.get("t", 0)
                        if paused2 is False and t2 > t1:
                            ok = True
                    record(site, "S6", "PASS" if ok else "FAIL", ev_str)
                except Exception as e:
                    record(site, "S6", "FAIL", f"Exception: {e}")

            site_times[site] = time.time() - t0

        cdp.close()

    finally:
        if p is not None:
            try:
                subprocess.run(["taskkill", "/F", "/T", "/PID", str(p.pid)], capture_output=True)
            except Exception as e:
                print(f"Error terminating Chrome process tree: {e}")

    lines = []
    lines.append(f"{'Site':<12} {'Step':<6} {'Status':<8} Evidence")
    lines.append("-" * 80)
    for s, st, stat, ev in records:
        lines.append(f"{s:<12} {st:<6} {stat:<8} {ev}")
    lines.append("-" * 80)
    lines.append("Elapsed time:")
    for s, el in site_times.items():
        lines.append(f"  {s}: {el:.2f}s")

    report_text = "\n".join(lines)
    print(report_text)
    with open("probe_cdp_report.txt", "w", encoding="utf-8") as f:
        f.write(report_text + "\n")


if __name__ == "__main__":
    main()
