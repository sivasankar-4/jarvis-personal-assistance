"""
jarvis_actions.py - Intent Interpretation and Action Routing for Jarvis
Converts natural language commands into validated tool registry executions.
All external and desktop side effects strictly go through jarvis_tools.execute_tool.
"""

from __future__ import annotations

import datetime
import os
import re
import urllib.parse

import jarvis_market
import jarvis_tools

SITES = {
    "youtube": "https://www.youtube.com",
    "whatsapp": "https://web.whatsapp.com",
    "gmail": "https://mail.google.com",
    "google": "https://www.google.com",
    "github": "https://github.com",
    "maps": "https://maps.google.com",
    "calendar": "https://calendar.google.com",
    "drive": "https://drive.google.com",
}

ALIASES = {
    "vs code": "Visual Studio Code",
    "vscode": "Visual Studio Code",
    "visual studio": "Visual Studio Code",
    "code": "Visual Studio Code",
    "chrome": "Google Chrome",
    "word": "Microsoft Word",
    "excel": "Microsoft Excel",
    "powerpoint": "Microsoft PowerPoint",
    "settings": "System Settings",
    "system preferences": "System Settings",
    "terminal": "Terminal",
    "spotify": "Spotify",
}


def _apps():
    """Return common installed Windows apps by display name."""
    return {
        "visual studio code": "code",
        "google chrome": "chrome",
        "microsoft edge": "msedge",
        "spotify": "spotify",
        "notepad": "notepad",
        "calculator": "calc",
        "file explorer": "explorer",
    }


def find_app(name):
    name = ALIASES.get(name.lower().strip(), name).lower().strip()
    apps = _apps()

    if name in apps:
        return apps[name]

    for app_name, command in apps.items():
        if name in app_name or app_name in name:
            return command

    return None


def _open(args):
    """Route website open requests through the validated tool registry."""
    url = args[0] if isinstance(args, (list, tuple)) and args else str(args)
    result = jarvis_tools.execute_tool("open_website", {"url": url})
    return result.ok


def send_whatsapp_message(contact_name, message="Hello from Jarvis"):
    """Automated sending of WhatsApp messages is not supported in M1."""
    return "Automated WhatsApp messaging is not supported. I can only open WhatsApp Web for you."


def get_mail_brief():
    """Reads local mail cache or reports that live email reading is unsupported."""
    cache_file = os.path.expanduser("~/.openjarvis/mail_cache.txt")
    if os.path.exists(cache_file):
        try:
            with open(cache_file, "r", errors="ignore", encoding="utf-8") as f:
                content = f.read().strip()
                if content:
                    return "Here is your latest mail briefing: " + content[:600]
        except Exception:
            pass
    return "No unread email cache is available, and live email reading is not supported yet."


def try_action(q):
    """
    Interprets natural language action intents and routes all side effects
    exclusively through the validated tool registry. Returns a response string
    if handled, or None to delegate to the conversational AI brain.
    """
    q_clean = q.lower().strip(" .,!?")
    # Normalize conversational and polite wrappers
    q_clean = re.sub(r"^(?:hey\s+)?(?:jarvis[,:]?\s+)?", "", q_clean)
    q_clean = re.sub(r"^(?:(?:can|could|would|will)\s+you\s+|i\s+(?:need|want)\s+you\s+to\s+)", "", q_clean)
    q_clean = re.sub(r"^please\s+", "", q_clean).strip()

    # Compound Command Handling: "Open [App] and [Action]"
    m = re.match(r"open\s+(.+?)\s+and\s+(.+)", q_clean)
    if m:
        app_name = m.group(1).strip()
        second_action = m.group(2).strip()

        # Handle Open App via validated tools
        if app_name in SITES:
            _open([SITES[app_name]])
        else:
            jarvis_tools.execute_tool("launch_application", {"name": app_name})

        # Execute second action with context if YouTube
        if "youtube" in app_name.lower():
            if not any(k in second_action for k in ("on youtube", "youtube")):
                second_action = f"{second_action} on YouTube"

        result = try_action(second_action)
        return result if result else f"Action '{second_action}' performed."

    # 1. Stock Market Queries & Buy/Sell Analysis (read-only query)
    if any(k in q_clean for k in ("stock", "market", "nifty", "sensex", "crypto", "bitcoin", "buy or sell")):
        for sym, name in jarvis_market.DEFAULT_TICKERS.items():
            if name.lower() in q_clean or sym.lower().replace(".ns", "") in q_clean:
                data = jarvis_market.analyze_ticker(sym)
                if data:
                    direction = "up" if data["pct_change"] >= 0 else "down"
                    return (
                        f"{data['name']} is trading at {data['currency']}{data['price']:,}, "
                        f"{direction} {abs(data['pct_change'])}%. "
                        f"Technical indicator: {data['signal']}. {data['reason']}"
                    )
        return "Here is the market overview: " + jarvis_market.market_briefing()

    # 2. WhatsApp Auto-Message / Open
    m = re.search(r"(?:send|reply|message)\s+(?:on\s+whatsapp\s+to|whatsapp\s+to|to)\s+(.+?)(?:\s+saying\s+(.+))?$", q_clean)
    if "whatsapp" in q_clean and m:
        contact = m.group(1).strip()
        msg = m.group(2).strip() if m.group(2) else "I received your message, will get back shortly."
        return send_whatsapp_message(contact, msg)

    if q_clean in ("open whatsapp", "check whatsapp", "whatsapp"):
        _open([SITES["whatsapp"]])
        return "Opening WhatsApp."

    # 3. Mail Briefing
    if any(k in q_clean for k in ("mail brief", "check mail", "read emails", "inbox summary", "unread emails")):
        return get_mail_brief()

    # 4. YouTube Music / Search
    if "youtube" in q_clean:
        m = re.search(
            r"\b(?:play|search|find)\s+(?:for\s+)?(.+?)"
            r"(?:\s+on\s+youtube\b|\s+on\s+youtube$|$)",
            q_clean,
        )

        if m:
            query = m.group(1).strip()
            query = re.sub(
                r"^(?:the\s+)?(?:song|video)\s+",
                "",
                query,
            ).strip()

            if query:
                url = "https://www.youtube.com/results?search_query=" + urllib.parse.quote(query)
                _open([url])
                return f"Searching YouTube for {query}."

        _open([SITES["youtube"]])
        return "Opened YouTube."

    # 5. Google / Web Search
    m = re.match(r"(?:search|google|look up|find information (?:about|on)|search the web for)(?: for)?\s+(.+)", q_clean)
    if m:
        query = m.group(1).strip()
        if "youtube" not in q_clean:
            url = "https://www.google.com/search?q=" + urllib.parse.quote(query)
            _open([url])
            return f"Searching Google for {query}."

    # 6. App Launch
    m = re.match(r"(?:open|launch|start|run|pull up|bring up|take me to)\s+(.+)", q_clean)
    if m:
        name = m.group(1).strip()

        if name in SITES:
            _open([SITES[name]])
            site_names = {
                "youtube": "YouTube",
                "whatsapp": "WhatsApp",
                "gmail": "Gmail",
                "google": "Google",
                "github": "GitHub",
                "maps": "Google Maps",
                "calendar": "Google Calendar",
                "drive": "Google Drive",
            }
            display = site_names.get(name.lower(), name.capitalize())
            return f"Opened {display}."

        result = jarvis_tools.execute_tool("launch_application", {"name": name})
        return result.message

    # 7. Screenshot observation
    if any(phrase in q_clean for phrase in ("take a screenshot", "capture the screen", "capture screen")):
        result = jarvis_tools.execute_tool("take_screenshot", {"target": "screen"})
        return result.message

    # 8. Volume Controls
    if any(k in q_clean for k in ("volume up", "louder", "increase volume")):
        result = jarvis_tools.execute_tool("adjust_volume", {"action": "up"})
        return result.message
    if any(k in q_clean for k in ("volume down", "quieter", "decrease volume", "lower the volume", "lower volume")):
        result = jarvis_tools.execute_tool("adjust_volume", {"action": "down"})
        return result.message
    if any(k in q_clean for k in ("mute", "mute volume", "mute the volume")):
        result = jarvis_tools.execute_tool("adjust_volume", {"action": "mute"})
        return result.message

    # 9. Date and Time (informational, read-only)
    if re.search(r"^(?:tell\s+me\s+)?(?:what(?:'s|\s+is)\s+(?:the\s+)?time|what\s+time\s+is\s+it|current\s+time)(?:\s+(?:right\s+)?now)?$", q_clean):
        return "It is currently " + datetime.datetime.now().strftime("%I:%M %p")
    if re.search(r"^(?:tell\s+me\s+)?(?:what(?:'s|\s+is)\s+(?:(?:the|today(?:'s)?)\s+date|the\s+day)(?:\s+today)?|what\s+day\s+is\s+(?:it\s+)?today|what\s+day\s+is\s+it|current\s+date|today(?:'s)?\s+date)$", q_clean):
        return "Today is " + datetime.datetime.now().strftime("%A, %B %d, %Y")

    return None
