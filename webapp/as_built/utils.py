# webapp/as_built/utils.py
"""
As-Built Documentation — data collection, section registry, rendering and export.

This module turns a live RingCentral account (the currently bridged customer)
into a single, human-readable "as-built" document that can be previewed in the
browser and downloaded as a PDF or a Word (.doc) file.

Design
------
Everything is driven by a SECTIONS registry. Each section describes one item
type (Users, Sites, Call Queues, IVRs, Park Zones, ...) and knows how to:

    collect(ctx, detail) -> section-specific data dict
    render(data, detail) -> HTML string

`detail` is one of DETAIL_LEVELS ("summary", "standard", "full"). Each
collector honours the requested level so the operator controls how deep the
document goes per item type — a light inventory or a full configuration dump.

A CollectContext is built once per run and shared by every collector so the
common lookups (all extensions, phone-number → extension map, id → name) are
fetched a single time rather than per-section.

Export re-uses the proven approach from the Network Requirements tool:
    - PDF  : xhtml2pdf (pisa) renders a self-contained styled HTML page.
    - Word : the same styled HTML is served as .doc — Word opens well-formed
             HTML natively, so no extra document-building dependency is needed.
"""

import re
import logging

from webapp.rc_api import rc_api_call

logger = logging.getLogger(__name__)

# Ordered from lightest to heaviest. The UI offers these per selected section.
DETAIL_LEVELS = ("summary", "standard", "full")
DEFAULT_DETAIL = "standard"

# RingCentral extension `type` values grouped into the friendly buckets this
# document uses. Mirrors the mapping already used by site_allocation/custom_rules
# so labels stay consistent across the app.
USER_TYPES = ("User", "DigitalUser", "FlexibleUser", "Limited")
VIRTUAL_TYPES = ("VirtualUser",)


# ---------------------------------------------------------------------------
# Thin RC API helpers (same shape as account_health.utils)
# ---------------------------------------------------------------------------

def _api(endpoint, params=None):
    """Single GET returning parsed JSON (or None). Token is resolved inside
    rc_api_call from the session (SM bridge token preferred)."""
    return rc_api_call(endpoint, params=params, method="GET")


def _fetch_all_pages(endpoint, record_key="records", page_size=1000, max_pages=100):
    """Follow RingCentral paging and return the flattened list of records."""
    records = []
    sep = "&" if "?" in endpoint else "?"
    page = 1
    while page <= max_pages:
        paged = f"{endpoint}{sep}page={page}&perPage={page_size}"
        data = _api(paged)
        if not data:
            break
        batch = data.get(record_key, []) if isinstance(data, dict) else []
        records.extend(batch)
        navigation = (data.get("navigation") or {}) if isinstance(data, dict) else {}
        paging = (data.get("paging") or {}) if isinstance(data, dict) else {}
        # Stop when there is no next page (both shapes appear in the RC API).
        if navigation.get("nextPage") is None and not paging.get("nextPageToken"):
            if not paging or page >= paging.get("totalPages", page):
                break
        if not batch:
            break
        page += 1
    return records


# ---------------------------------------------------------------------------
# Shared collection context
# ---------------------------------------------------------------------------

class CollectContext:
    """Holds account-wide data fetched once and reused by every section."""

    def __init__(self):
        self.account = _api("/restapi/v1.0/account/~") or {}
        # service-info carries the service plan, brand and feature flags; fetched
        # once and shared by the overview and integrations sections.
        self.service_info = _api("/restapi/v1.0/account/~/service-info") or {}
        self.extensions = _fetch_all_pages("/restapi/v1.0/account/~/extension")
        self.phone_numbers = _fetch_all_pages("/restapi/v1.0/account/~/phone-number")

        # Resolve the account name. The account resource doesn't always carry a
        # populated "name"; when bridged, the SM bridge already knows the target
        # account's name/id, so fall back to that before giving up.
        self.account_name = self.account.get("name") or ""
        self.account_id = str(self.account.get("id") or "")
        try:
            from flask import session
            if not self.account_name:
                self.account_name = session.get("sm_target_name") or ""
            if not self.account_id:
                self.account_id = str(session.get("sm_target_id") or "")
        except Exception:
            pass
        if not self.account_name:
            self.account_name = "Customer"

        # id -> extension record
        self.ext_by_id = {str(e.get("id")): e for e in self.extensions if e.get("id")}

        # extension id -> list of assigned phone numbers
        self.numbers_by_ext = {}
        for p in self.phone_numbers:
            ext = p.get("extension") or {}
            ext_id = str(ext.get("id")) if ext.get("id") else None
            if ext_id:
                self.numbers_by_ext.setdefault(ext_id, []).append(p)

    def ext_name(self, ext_id):
        """Human-readable 'Name (ext)' for an extension id, if known."""
        rec = self.ext_by_id.get(str(ext_id))
        if not rec:
            return str(ext_id)
        name = rec.get("name") or "Unknown"
        num = rec.get("extensionNumber") or ""
        return f"{name} ({num})" if num else name

    def numbers_for(self, ext_id):
        """Sorted list of DID/company numbers assigned to an extension id."""
        nums = self.numbers_by_ext.get(str(ext_id), [])
        return sorted(p.get("phoneNumber", "") for p in nums if p.get("phoneNumber"))


# ---------------------------------------------------------------------------
# HTML rendering helpers
# ---------------------------------------------------------------------------

def _esc(value):
    if value is None:
        return ""
    return (
        str(value)
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


def _h2(text, count=None):
    suffix = f' <span class="ab-count">{count}</span>' if count is not None else ""
    return f"<h2>{_esc(text)}{suffix}</h2>"


def _h3(text):
    return f"<h3>{_esc(text)}</h3>"


def _p(text, cls="ab-note"):
    return f'<p class="{cls}">{_esc(text)}</p>'


def _empty(text):
    return f'<p class="ab-empty">{_esc(text)}</p>'


def _table(headers, rows, widths=None):
    """rows: list of lists (cells may contain pre-escaped HTML from _cell).

    widths: optional list of column widths (percentages that should sum to 100).
    xhtml2pdf keys column widths off the widths declared on the header cells, so
    the widths are applied to each <th> (and mirrored via a colgroup for the
    browser preview). Combined with the fixed table layout this keeps columns
    aligned and lets long content wrap inside its own cell — reportlab splits
    long words by character on its own, so no manual break hints are needed
    (those produced the black tofu boxes in the previous build).
    """
    if not rows:
        return ""
    colgroup, th_style = "", [""] * len(headers)
    if widths:
        colgroup = "<colgroup>" + "".join(
            f'<col style="width:{w}%">' for w in widths) + "</colgroup>"
        th_style = [f' style="width:{w}%"' for w in widths]
    head = "".join(f"<th{th_style[i] if i < len(th_style) else ''}>{_esc(h)}</th>"
                   for i, h in enumerate(headers))
    body = ""
    for r in rows:
        cells = "".join(f"<td>{c}</td>" for c in r)
        body += f"<tr>{cells}</tr>"
    return (f'<table class="ab-table">{colgroup}'
            f'<thead><tr>{head}</tr></thead><tbody>{body}</tbody></table>')


def _wrap_long(value, n=20):
    """Escape a value and force hard <br/> breaks inside any unbroken run longer
    than n characters.

    xhtml2pdf does not reliably honour fixed table-layout / column widths, and
    reportlab only splits a long word when the column width is constrained — so
    a long email or ID overflows and overlaps the next column in the PDF. Rather
    than fight the column sizing, we insert real break opportunities so a token
    physically cannot be wider than n characters. Each chunk is escaped
    individually so entities are never split. Renders identically in the browser
    preview and the PDF/Word export, with no zero-width glyphs (which tofu'd).
    """
    s = str(value)
    out = []
    for tok in re.split(r"(\s+)", s):
        if tok == "" or tok.isspace():
            out.append(_esc(tok))
        elif len(tok) > n:
            out.append("<br/>".join(_esc(tok[i:i + n]) for i in range(0, len(tok), n)))
        else:
            out.append(_esc(tok))
    return "".join(out)


def _cell(value):
    """Escape a plain value for use inside a table cell (long tokens wrapped)."""
    if value is None or value == "":
        return '<span class="ab-muted">—</span>'
    return _wrap_long(value)


def _lines(values):
    """Multi-value cell rendered one value per line so it wraps cleanly in a
    fixed-width column (used for phone numbers, devices, members)."""
    vals = [v for v in (values or []) if v]
    if not vals:
        return '<span class="ab-muted">—</span>'
    return "<br/>".join(_wrap_long(v) for v in vals)


def _chips(values):
    if not values:
        return '<span class="ab-muted">—</span>'
    return "".join(f'<span class="ab-chip">{_esc(v)}</span>' for v in values)


def _weekly_hours_lines(schedule):
    """Compact multi-line schedule (one day per line) for a business-hours
    schedule payload — used inline in queue/site blocks."""
    weekly = (schedule or {}).get("weeklyRanges") or {}
    if not weekly:
        return "24/7 (always open)"
    order = ["monday", "tuesday", "wednesday", "thursday", "friday",
             "saturday", "sunday"]
    parts = []
    for day in order:
        ranges = weekly.get(day) or []
        if ranges:
            spans = ", ".join(f'{r.get("from","")}–{r.get("to","")}' for r in ranges)
            parts.append(f"{day.capitalize()[:3]}: {spans}")
    return "<br>".join(_esc(p) for p in parts) if parts else "No open hours set"


def _fetch_business_hours(ext_id):
    """Return the schedule payload for an extension's business hours."""
    bh = _api(f"/restapi/v1.0/account/~/extension/{ext_id}/business-hours") or {}
    return bh.get("schedule") or {}


def _kv(label, value):
    return f'<div class="ab-kv"><span class="ab-k">{_esc(label)}</span>' \
           f'<span class="ab-v">{value if value else "—"}</span></div>'


def _summarise_hours(hours):
    """Compact one-line summary of a business-hours schedule payload."""
    if not hours:
        return "24/7 (always open)"
    schedule = hours.get("schedule") or hours
    weekly = schedule.get("weeklyRanges") if isinstance(schedule, dict) else None
    if not weekly:
        return "24/7 (always open)"
    days = [d for d, ranges in weekly.items() if ranges]
    return f"{len(days)} day(s) configured" if days else "No open hours set"


# ===========================================================================
# SECTION COLLECTORS + RENDERERS
# ===========================================================================
#
# Each pair below implements one item type. The detail level controls how much
# each collector fetches and how much each renderer emits. The tier contents
# here are sensible defaults — they are the exact thing to tune per item type.

# ---- Account Overview -----------------------------------------------------

def collect_overview(ctx, detail):
    exts = ctx.extensions
    type_counts = {}
    for e in exts:
        t = e.get("type", "Unknown")
        type_counts[t] = type_counts.get(t, 0) + 1

    # Service plan lives under serviceInfo on the account resource (and on
    # /service-info) — not under a top-level "servicePlan", which was the blank.
    svc_info = ctx.service_info or {}
    service_plan = (
        (ctx.account.get("serviceInfo") or {}).get("servicePlan", {}).get("name")
        or (svc_info.get("servicePlan") or {}).get("name")
        or ""
    )
    brand = (
        (ctx.account.get("serviceInfo") or {}).get("brand", {}).get("name")
        or (svc_info.get("brand") or {}).get("name")
        or ""
    )
    data = {
        "name": ctx.account_name,
        "account_id": ctx.account_id,
        "main_number": ctx.account.get("mainNumber", ""),
        "service_plan": service_plan,
        "brand": brand,
        "status": ctx.account.get("status", ""),
        "counts": {
            "Users": sum(type_counts.get(t, 0) for t in USER_TYPES),
            "Virtual Extensions": sum(type_counts.get(t, 0) for t in VIRTUAL_TYPES),
            "Sites": type_counts.get("Site", 0),
            "Call Queues": type_counts.get("Department", 0),
            "IVR Menus": type_counts.get("IvrMenu", 0),
            "Park Locations": type_counts.get("ParkLocation", 0),
            "Paging Groups": type_counts.get("PagingOnly", 0),
            "Message-Only": type_counts.get("Voicemail", 0) + type_counts.get("MessageOnly", 0),
            "Announcement-Only": type_counts.get("AnnouncementOnly", 0) + type_counts.get("Announcement", 0),
            "Phone Numbers": len(ctx.phone_numbers),
        },
    }
    return data


def render_overview(data, detail):
    html = _h2("Account Overview")
    html += _kv("Account Name", _esc(data["name"]))
    html += _kv("Account ID", _esc(data["account_id"]))
    html += _kv("Main Number", _esc(data["main_number"]))
    html += _kv("Service Plan", _esc(data["service_plan"]))
    if detail != "summary":
        if data.get("brand"):
            html += _kv("Brand", _esc(data["brand"]))
        html += _kv("Status", _esc(data["status"]))
    rows = [[k, str(v)] for k, v in data["counts"].items() if v]
    html += _h3("Inventory Summary")
    html += _table(["Item Type", "Count"], [[_cell(a), _cell(b)] for a, b in rows])
    return html


# ---- Sites ----------------------------------------------------------------

def collect_sites(ctx, detail):
    sites = _fetch_all_pages("/restapi/v1.0/account/~/sites")
    out = []
    for s in sites:
        sid = str(s.get("id"))
        row = {
            "id": sid,
            "name": s.get("name", "Unknown"),
            "extensionNumber": s.get("extensionNumber", ""),
        }
        if detail != "summary":
            detail_rec = _api(f"/restapi/v1.0/account/~/sites/{sid}") or {}
            addr = detail_rec.get("businessAddress") or {}
            row["address"] = ", ".join(
                x for x in [
                    addr.get("street"), addr.get("city"), addr.get("state"),
                    addr.get("zip"), addr.get("country"),
                ] if x
            )
            row["operator"] = (detail_rec.get("operator") or {}).get("extensionNumber", "")
            row["timezone"] = (detail_rec.get("regionalSettings") or {}).get(
                "timezone", {}).get("description", "")
            row["code"] = detail_rec.get("code", "")
            if detail == "full":
                bh = _api(f"/restapi/v1.0/account/~/extension/{sid}/business-hours")
                row["hours"] = _summarise_hours(bh)
        out.append(row)
    return {"sites": out}


def render_sites(data, detail):
    sites = data["sites"]
    html = _h2("Sites", len(sites))
    if not sites:
        return html + _empty("No sites configured on this account.")
    if detail == "summary":
        rows = [[_cell(s["name"]), _cell(s["extensionNumber"])] for s in sites]
        return html + _table(["Site", "Ext"], rows)
    headers = ["Site", "Ext", "Address", "Operator", "Timezone"]
    if detail == "full":
        headers.append("Hours")
    rows = []
    for s in sites:
        r = [_cell(s["name"]), _cell(s["extensionNumber"]), _cell(s.get("address")),
             _cell(s.get("operator")), _cell(s.get("timezone"))]
        if detail == "full":
            r.append(_cell(s.get("hours")))
        rows.append(r)
    return html + _table(headers, rows)


# ---- Users ----------------------------------------------------------------

def collect_users(ctx, detail):
    users = [e for e in ctx.extensions if e.get("type") in USER_TYPES]
    out = []
    for u in sorted(users, key=lambda e: (e.get("extensionNumber") or "")):
        uid = str(u.get("id"))
        contact = u.get("contact") or {}
        rec = {
            "id": uid,
            "name": u.get("name") or contact.get("firstName", "Unknown"),
            "extensionNumber": u.get("extensionNumber", ""),
            "type": u.get("type", ""),
            "status": u.get("status", ""),
        }
        if detail != "summary":
            rec["email"] = contact.get("email", "")
            rec["site"] = (u.get("site") or {}).get("name", "")
            rec["numbers"] = ctx.numbers_for(uid)
        if detail == "full":
            devices = _api(f"/restapi/v1.0/account/~/extension/{uid}/device") or {}
            rec["devices"] = [
                d.get("name") or d.get("model", {}).get("name", "Device")
                for d in (devices.get("records") or [])
            ]
            rec["department"] = ", ".join(u.get("departments", []) or []) if isinstance(
                u.get("departments"), list) else ""
        out.append(rec)
    return {"users": out}


def render_users(data, detail):
    users = data["users"]
    html = _h2("Users", len(users))
    if not users:
        return html + _empty("No users configured on this account.")
    if detail == "summary":
        rows = [[_cell(u["name"]), _cell(u["extensionNumber"]), _cell(u["type"])]
                for u in users]
        return html + _table(["Name", "Ext", "Type"], rows, widths=[55, 15, 30])
    if detail == "standard":
        rows = [[
            _cell(u["name"]), _cell(u["extensionNumber"]), _cell(u.get("email")),
            _cell(u.get("status")), _cell(u.get("site")),
            _lines(u.get("numbers")),
        ] for u in users]
        return html + _table(
            ["Name", "Ext", "Email", "Status", "Site", "Direct Numbers"], rows,
            widths=[20, 8, 28, 12, 14, 18])
    # full
    rows = [[
        _cell(u["name"]), _cell(u["extensionNumber"]), _cell(u.get("email")),
        _cell(u.get("status")), _cell(u.get("site")), _lines(u.get("numbers")),
        _lines(u.get("devices")), _cell(u.get("department")),
    ] for u in users]
    return html + _table(
        ["Name", "Ext", "Email", "Status", "Site", "Direct Numbers",
         "Devices", "Department"], rows,
        widths=[16, 7, 22, 10, 12, 13, 13, 7])


# ---- Call Queues ----------------------------------------------------------

def collect_call_queues(ctx, detail):
    queues = _fetch_all_pages("/restapi/v1.0/account/~/call-queues")
    out = []
    for q in queues:
        qid = str(q.get("id"))
        rec = {
            "id": qid,
            "name": q.get("name", "Unknown"),
            "extensionNumber": q.get("extensionNumber", ""),
            "status": q.get("status", ""),
        }
        if detail != "summary":
            members = _api(f"/restapi/v1.0/account/~/call-queues/{qid}/members") or {}
            member_recs = members.get("records") or []
            rec["members"] = [ctx.ext_name(m.get("id")) for m in member_recs]
            rec["member_count"] = members.get("totalElements", len(member_recs))
            # Call-handling settings live in the business-hours answering rule's
            # `queue` object (same source the Call Queue Manager reads), not on
            # the /call-queues/{id} resource — reading the wrong place is why
            # these values came back blank.
            rule = _api(
                f"/restapi/v1.0/account/~/extension/{qid}/answering-rule/business-hours-rule") or {}
            q = rule.get("queue") or {}
            rec["schedule"] = _fetch_business_hours(qid)
            rec["ring_type"] = q.get("transferMode", "")
            rec["hold_time"] = q.get("holdTime")
            rec["wrap_up"] = q.get("wrapUpTime")
            rec["max_callers"] = q.get("maxCallers")
            rec["when_full"] = q.get("maxCallersAction", "")
            rec["when_max_time"] = q.get("holdTimeExpirationAction", "")
        if detail == "full":
            # After-hours handling for the queue, if any.
            ah = _api(
                f"/restapi/v1.0/account/~/extension/{qid}/answering-rule/after-hours-rule")
            if isinstance(ah, dict):
                rec["after_hours_action"] = ah.get("callHandlingAction", "")
        out.append(rec)
    return {"queues": out}


def render_call_queues(data, detail):
    queues = data["queues"]
    html = _h2("Call Queues", len(queues))
    if not queues:
        return html + _empty("No call queues configured on this account.")
    if detail == "summary":
        rows = [[_cell(q["name"]), _cell(q["extensionNumber"]), _cell(q.get("status"))]
                for q in queues]
        return html + _table(["Queue", "Ext", "Status"], rows, widths=[60, 15, 25])
    # standard + full render each queue as its own block (members can be long)
    html_parts = [html]
    for q in queues:
        html_parts.append(_h3(f'{q["name"]}  ·  Ext {q["extensionNumber"]}'))
        rows = [
            ["Status", _cell(q.get("status"))],
            ["Members", _cell(str(q.get("member_count", 0)))],
            ["Ring / Transfer Mode", _cell(q.get("ring_type"))],
            ["Total Ring Time (s)", _cell(q.get("hold_time"))],
            ["Wrap-Up Time (s)", _cell(q.get("wrap_up"))],
            ["Max Callers in Queue", _cell(q.get("max_callers"))],
            ["When Queue is Full", _cell(q.get("when_full"))],
            ["When Max Time Reached", _cell(q.get("when_max_time"))],
            ["Business Hours", _weekly_hours_lines(q.get("schedule"))],
        ]
        if detail == "full":
            rows.append(["After-Hours Handling", _cell(q.get("after_hours_action"))])
        html_parts.append(_table(["Setting", "Value"], rows, widths=[32, 68]))
        html_parts.append('<p class="ab-note"><strong>Members:</strong></p>')
        html_parts.append('<div class="ab-block">' + _lines(q.get("members")) + "</div>")
    return "".join(html_parts)


# ---- IVRs -----------------------------------------------------------------

def collect_ivrs(ctx, detail):
    ivr_exts = [e for e in ctx.extensions if e.get("type") == "IvrMenu"]
    out = []
    for e in ivr_exts:
        iid = str(e.get("id"))
        rec = {
            "id": iid,
            "name": e.get("name", "Unknown IVR"),
            "extensionNumber": e.get("extensionNumber", ""),
        }
        if detail != "summary":
            cfg = _api(f"/restapi/v1.0/account/~/ivr-menus/{iid}") or {}
            actions = cfg.get("actions", []) or []
            rec["key_count"] = len(actions)
            prompt = cfg.get("prompt") or {}
            rec["prompt_mode"] = prompt.get("mode", "")
            rec["prompt"] = _describe_ivr_prompt(prompt)
            rec["actions"] = []
            for a in actions:
                dest = a.get("extension") or {}
                dest_label = ""
                if dest.get("id"):
                    dest_label = ctx.ext_name(dest.get("id"))
                elif a.get("phoneNumber"):
                    dest_label = a.get("phoneNumber")
                rec["actions"].append({
                    "key": a.get("input", ""),
                    "action": a.get("action", ""),
                    "destination": dest_label,
                })
        out.append(rec)
    return {"ivrs": out}


def _describe_ivr_prompt(prompt):
    """Human-readable description of an IVR menu's greeting/prompt.

    TextToSpeech → the spoken text; Audio → the uploaded prompt's filename
    (resolved from /ivr-prompts/{id}); otherwise the mode."""
    if not prompt:
        return ""
    mode = prompt.get("mode", "")
    if mode == "TextToSpeech":
        return f'Text-to-speech: "{prompt.get("text", "").strip()}"'
    if mode in ("Audio", "Recording"):
        audio = prompt.get("audio") or {}
        pid = audio.get("id") or (audio.get("uri", "").rstrip("/").split("/")[-1]
                                  if audio.get("uri") else "")
        if pid:
            detail = _api(f"/restapi/v1.0/account/~/ivr-prompts/{pid}") or {}
            fname = detail.get("filename") or detail.get("name")
            return f"Audio prompt: {fname}" if fname else f"Audio prompt (id {pid})"
        return "Audio prompt"
    return mode or "None"


def render_ivrs(data, detail):
    ivrs = data["ivrs"]
    html = _h2("IVR Menus", len(ivrs))
    if not ivrs:
        return html + _empty("No IVR menus configured on this account.")
    if detail == "summary":
        rows = [[_cell(i["name"]), _cell(i["extensionNumber"])] for i in ivrs]
        return html + _table(["IVR", "Ext"], rows, widths=[70, 30])
    html_parts = [html]
    for i in ivrs:
        html_parts.append(_h3(f'{i["name"]}  ·  Ext {i["extensionNumber"]}'))
        html_parts.append(_kv("Prompt", _cell(i.get("prompt"))))
        rows = [[_cell(a["key"]), _cell(a["action"]), _cell(a["destination"])]
                for a in i.get("actions", [])]
        html_parts.append(_table(["Key", "Action", "Destination"], rows,
                                 widths=[12, 33, 55])
                          or _empty("No key actions defined."))
    return "".join(html_parts)


# ---- Park Locations -------------------------------------------------------

def collect_park_zones(ctx, detail):
    # Park locations are extensions of type ParkLocation. The dedicated
    # /park-locations endpoint returns empty on many bridged accounts (like
    # /cost-center), so derive them from the already-fetched extension list.
    parks = [e for e in ctx.extensions if e.get("type") == "ParkLocation"]
    out = []
    for e in sorted(parks, key=lambda x: (x.get("extensionNumber") or "")):
        rec = {
            "id": str(e.get("id")),
            "name": e.get("name", "Unknown"),
            "extensionNumber": e.get("extensionNumber", ""),
            "status": e.get("status", ""),
            "site": (e.get("site") or {}).get("name", ""),
        }
        out.append(rec)
    return {"parks": out}


def render_park_zones(data, detail):
    parks = data["parks"]
    html = _h2("Park Locations", len(parks))
    if not parks:
        return html + _empty("No park locations configured on this account.")
    if detail == "summary":
        rows = [[_cell(p["name"]), _cell(p["extensionNumber"])] for p in parks]
        return html + _table(["Park Location", "Ext"], rows, widths=[70, 30])
    rows = [[_cell(p["name"]), _cell(p["extensionNumber"]), _cell(p.get("status")),
             _cell(p.get("site"))] for p in parks]
    return html + _table(["Park Location", "Ext", "Status", "Site"], rows,
                         widths=[40, 15, 20, 25])


# ---- Paging Groups --------------------------------------------------------

def collect_paging_groups(ctx, detail):
    # Same as park locations: paging groups are PagingOnly extensions; the
    # dedicated endpoint is unreliable on bridged accounts, so use the list.
    groups = [e for e in ctx.extensions if e.get("type") == "PagingOnly"]
    out = []
    for e in sorted(groups, key=lambda x: (x.get("extensionNumber") or "")):
        rec = {
            "id": str(e.get("id")),
            "name": e.get("name", "Unknown"),
            "extensionNumber": e.get("extensionNumber", ""),
        }
        out.append(rec)
    return {"groups": out}


def render_paging_groups(data, detail):
    groups = data["groups"]
    html = _h2("Paging Groups", len(groups))
    if not groups:
        return html + _empty("No paging groups configured on this account.")
    rows = [[_cell(g["name"]), _cell(g["extensionNumber"])] for g in groups]
    return html + _table(["Paging Group", "Ext"], rows)


# ---- Generic extension-type section (Message-Only / Announcement-Only / SLG) --

def _notification_emails(ext_id):
    """Voicemail/notification email recipients for an extension."""
    ns = _api(f"/restapi/v1.0/account/~/extension/{ext_id}/notification-settings") or {}
    emails = list(ns.get("emailAddresses") or [])
    vm = ns.get("voicemails") or {}
    emails += list(vm.get("advancedEmailAddresses") or [])
    # De-duplicate, preserve order.
    seen, out = set(), []
    for e in emails:
        if e and e not in seen:
            seen.add(e)
            out.append(e)
    return out


def _collect_ext_type(ctx, detail, types, notify_email=False):
    """Shared collector for simple extension-type inventories."""
    items = [e for e in ctx.extensions if e.get("type") in types]
    out = []
    for e in sorted(items, key=lambda x: (x.get("extensionNumber") or "")):
        eid = str(e.get("id"))
        rec = {
            "id": eid,
            "name": e.get("name", "Unknown"),
            "extensionNumber": e.get("extensionNumber", ""),
            "status": e.get("status", ""),
        }
        if detail != "summary":
            rec["site"] = (e.get("site") or {}).get("name", "")
            rec["numbers"] = ctx.numbers_for(eid)
            if notify_email:
                emails = _notification_emails(eid)
                if not emails:
                    contact_email = (e.get("contact") or {}).get("email")
                    if contact_email:
                        emails = [contact_email]
                rec["notify_email"] = emails
        out.append(rec)
    return {"items": out}


def _render_ext_type(data, detail, title, name_header, show_notify_email=False):
    items = data["items"]
    html = _h2(title, len(items))
    if not items:
        return html + _empty(f"No {title.lower()} configured on this account.")
    if detail == "summary":
        rows = [[_cell(i["name"]), _cell(i["extensionNumber"]), _cell(i.get("status"))]
                for i in items]
        return html + _table([name_header, "Ext", "Status"], rows, widths=[55, 15, 30])
    if show_notify_email:
        rows = [[
            _cell(i["name"]), _cell(i["extensionNumber"]), _cell(i.get("status")),
            _cell(i.get("site")), _lines(i.get("numbers")),
            _lines(i.get("notify_email")),
        ] for i in items]
        return html + _table(
            [name_header, "Ext", "Status", "Site", "Numbers", "Notification Email"],
            rows, widths=[20, 8, 12, 15, 20, 25])
    rows = [[
        _cell(i["name"]), _cell(i["extensionNumber"]), _cell(i.get("status")),
        _cell(i.get("site")), _lines(i.get("numbers")),
    ] for i in items]
    return html + _table([name_header, "Ext", "Status", "Site", "Numbers"], rows,
                         widths=[26, 10, 14, 22, 28])


def collect_message_only(ctx, detail):
    return _collect_ext_type(ctx, detail, ("Voicemail", "MessageOnly"),
                             notify_email=True)


def render_message_only(data, detail):
    return _render_ext_type(data, detail, "Message-Only Extensions", "Name",
                            show_notify_email=True)


def collect_announcement_only(ctx, detail):
    return _collect_ext_type(ctx, detail, ("AnnouncementOnly", "Announcement"))


def render_announcement_only(data, detail):
    return _render_ext_type(data, detail, "Announcement-Only Extensions", "Name")


def collect_shared_line_groups(ctx, detail):
    return _collect_ext_type(ctx, detail, ("SharedLinesGroup",))


def render_shared_line_groups(data, detail):
    return _render_ext_type(data, detail, "Shared Line Groups", "Group")


# ---- Phone Numbers --------------------------------------------------------

def collect_phone_numbers(ctx, detail):
    numbers = ctx.phone_numbers
    by_usage = {}
    for p in numbers:
        u = p.get("usageType", "Unknown")
        by_usage[u] = by_usage.get(u, 0) + 1
    rows = []
    if detail != "summary":
        for p in sorted(numbers, key=lambda x: x.get("phoneNumber", "")):
            ext = p.get("extension") or {}
            assigned = ctx.ext_name(ext.get("id")) if ext.get("id") else ""
            row = {
                "number": p.get("phoneNumber", ""),
                "usage": p.get("usageType", ""),
                "status": p.get("status", ""),
                "assigned": assigned,
            }
            if detail == "full":
                loc = p.get("location", "")
                row["type"] = p.get("type", "")
                row["location"] = loc
            rows.append(row)
    return {"by_usage": by_usage, "total": len(numbers), "rows": rows}


def render_phone_numbers(data, detail):
    html = _h2("Phone Numbers", data["total"])
    breakdown = [[_cell(k), _cell(str(v))] for k, v in sorted(data["by_usage"].items())]
    html += _h3("By Usage Type")
    html += _table(["Usage Type", "Count"], breakdown)
    if detail == "summary":
        return html
    html += _h3("Number Inventory")
    if detail == "full":
        rows = [[_cell(r["number"]), _cell(r["usage"]), _cell(r.get("type")),
                 _cell(r["status"]), _cell(r.get("location")), _cell(r["assigned"])]
                for r in data["rows"]]
        return html + _table(
            ["Number", "Usage", "Type", "Status", "Location", "Assigned To"], rows)
    rows = [[_cell(r["number"]), _cell(r["usage"]), _cell(r["status"]), _cell(r["assigned"])]
            for r in data["rows"]]
    return html + _table(["Number", "Usage", "Status", "Assigned To"], rows)


# ---- Devices / Hardware ---------------------------------------------------

def collect_devices(ctx, detail):
    devices = _fetch_all_pages("/restapi/v1.0/account/~/device")
    out = []
    for d in devices:
        rec = {
            "name": d.get("name", "Unnamed Device"),
            "model": (d.get("model") or {}).get("name", "Unknown"),
            "status": d.get("status", ""),
        }
        if detail != "summary":
            ext = d.get("extension") or {}
            rec["assigned"] = ctx.ext_name(ext.get("id")) if ext.get("id") else ""
            rec["serial"] = d.get("serial", "")
            rec["site"] = (d.get("site") or {}).get("name", "")
        if detail == "full":
            rec["type"] = d.get("type", "")
            rec["sku"] = d.get("sku", "")
        out.append(rec)
    # summary breakdown by model
    by_model = {}
    for d in out:
        by_model[d["model"]] = by_model.get(d["model"], 0) + 1
    return {"devices": out, "by_model": by_model}


def render_devices(data, detail):
    devices = data["devices"]
    html = _h2("Devices", len(devices))
    if not devices:
        return html + _empty("No devices provisioned on this account.")
    if detail == "summary":
        html += _h3("By Model")
        rows = [[_cell(k), _cell(str(v))] for k, v in sorted(data["by_model"].items())]
        return html + _table(["Model", "Count"], rows)
    headers = ["Name", "Model", "Status", "Serial/MAC", "Assigned To", "Site"]
    if detail == "full":
        headers = ["Name", "Model", "Type", "Status", "Serial/MAC", "SKU", "Assigned To", "Site"]
    rows = []
    for d in devices:
        if detail == "full":
            rows.append([_cell(d["name"]), _cell(d["model"]), _cell(d.get("type")),
                         _cell(d["status"]), _cell(d.get("serial")), _cell(d.get("sku")),
                         _cell(d.get("assigned")), _cell(d.get("site"))])
        else:
            rows.append([_cell(d["name"]), _cell(d["model"]), _cell(d["status"]),
                         _cell(d.get("serial")), _cell(d.get("assigned")), _cell(d.get("site"))])
    return html + _table(headers, rows)


# ---- Custom Roles ---------------------------------------------------------

def collect_custom_roles(ctx, detail):
    # /user-role is the reliable roles endpoint (returns predefined + custom,
    # each with a friendly displayName). /custom-roles returns empty on many
    # bridged accounts, which produced the false "none".
    roles = _cc_fetch("/restapi/v1.0/account/~/user-role")
    out = []
    for r in roles:
        rec = {
            "name": r.get("displayName") or r.get("name", "Unknown"),
            "scope": r.get("scope", ""),
            "custom": "Yes" if r.get("custom") else "No",
        }
        if detail != "summary":
            rec["description"] = r.get("description", "")
        out.append(rec)
    # Custom roles first, then predefined; alphabetical within each.
    out.sort(key=lambda x: (x["custom"] != "Yes", x["name"].lower()))
    return {"roles": out}


def render_custom_roles(data, detail):
    roles = data["roles"]
    html = _h2("User Roles", len(roles))
    if not roles:
        return html + _empty("No roles returned for this account.")
    if detail == "summary":
        rows = [[_cell(r["name"]), _cell(r.get("scope")), _cell(r.get("custom"))]
                for r in roles]
        return html + _table(["Role", "Scope", "Custom"], rows, widths=[45, 35, 20])
    rows = [[_cell(r["name"]), _cell(r.get("scope")), _cell(r.get("custom")),
             _cell(r.get("description"))] for r in roles]
    return html + _table(["Role", "Scope", "Custom", "Description"], rows,
                         widths=[24, 20, 12, 44])


# ---- Cost Centres ---------------------------------------------------------

def _cc_fetch(endpoint):
    """Proven paginated fetch from the Cost Centres tool (params-based, honours
    navigation.nextPage). token=None → rc_api_call resolves the session/bridge
    token. Used for endpoints where our generic fetcher returned empty."""
    from webapp.cost_centres.utils import fetch_all_pages
    try:
        return fetch_all_pages(endpoint, None)
    except Exception:
        logger.exception("[as_built] _cc_fetch failed for %s", endpoint)
        return []


def _cost_centre_bundle():
    """Reuse the Cost Centres tool's data path wholesale. It works around the
    /cost-center list endpoint 404ing on many accounts by harvesting cost-centre
    names from the costCenter object on every extension/number/device, resolves
    each object's effective cost centre (explicit → site → account default), and
    includes the authoritative v2 license inventory. token=None → session/bridge
    token."""
    from webapp.cost_centres.utils import get_cost_centres_data
    try:
        return get_cost_centres_data(None) or {}
    except Exception:
        logger.exception("[as_built] cost centre bundle failed")
        return {}


def collect_cost_centres(ctx, detail):
    bundle = _cost_centre_bundle()
    centres = bundle.get("cost_centres", []) or []
    assets = bundle.get("assets", []) or []
    inv_by_cc = {str(cc.get("costCenterId")): cc
                 for cc in bundle.get("license_inventory", []) or []}

    # Count assigned objects per cost centre (by id, falling back to name).
    count_by_id, count_by_name = {}, {}
    for a in assets:
        cid = str(a.get("costCenterId") or "")
        count_by_id[cid] = count_by_id.get(cid, 0) + 1
        nm = a.get("costCenterName") or ""
        count_by_name[nm] = count_by_name.get(nm, 0) + 1

    out = []
    for c in centres:
        cid = str(c.get("id", ""))
        node = inv_by_cc.get(cid)
        objects = count_by_id.get(cid) or count_by_name.get(c.get("name", ""), 0)
        out.append({
            "name": c.get("name", "Unknown"),
            "id": cid,
            "objects": objects,
            "assigned": node["totalAssigned"] if node else 0,
            "available": node["totalAvailable"] if node else 0,
        })
    out.sort(key=lambda x: x["name"].lower())
    return {"centres": out}


def render_cost_centres(data, detail):
    centres = data["centres"]
    html = _h2("Cost Centres", len(centres))
    if not centres:
        return html + _empty("No cost centres configured on this account.")
    if detail == "summary":
        rows = [[_cell(c["name"]), _cell(str(c.get("objects", 0)))] for c in centres]
        return html + _table(["Cost Centre", "Assigned Objects"], rows, widths=[70, 30])
    rows = [[_cell(c["name"]), _cell(str(c.get("objects", 0))),
             _cell(str(c.get("assigned", 0))), _cell(str(c.get("available", 0)))]
            for c in centres]
    return html + _table(
        ["Cost Centre", "Assigned Objects", "Licenses Assigned", "Licenses Available"],
        rows, widths=[40, 20, 20, 20])


# ---- Licensing ------------------------------------------------------------

def collect_licensing(ctx, detail):
    inventory = _cost_centre_bundle().get("license_inventory", []) or []
    by_type = {}
    for cc in inventory:
        for lic in cc.get("licenses", []):
            node = by_type.setdefault(lic["name"], {"assigned": 0, "available": 0, "total": 0})
            node["assigned"] += lic.get("assigned", 0)
            node["available"] += lic.get("available", 0)
            node["total"] += lic.get("total", 0)
    data = {
        "by_type": by_type,
        "total_assigned": sum(n["assigned"] for n in by_type.values()),
        "total_available": sum(n["available"] for n in by_type.values()),
        "total": sum(n["total"] for n in by_type.values()),
    }
    if detail == "full":
        data["inventory"] = inventory
    return data


def render_licensing(data, detail):
    html = _h2("Licensing")
    html += _kv("Total Licenses", _esc(str(data["total"])))
    html += _kv("Assigned", _esc(str(data["total_assigned"])))
    html += _kv("Available", _esc(str(data["total_available"])))
    if not data["by_type"]:
        return html + _empty(
            "No license data returned (the v2 licenses endpoint may be "
            "unavailable on this account).")
    if detail == "summary":
        return html
    html += _h3("By License Type")
    rows = [[_cell(name), _cell(str(n["assigned"])), _cell(str(n["available"])),
             _cell(str(n["total"]))]
            for name, n in sorted(data["by_type"].items())]
    html += _table(["License Type", "Assigned", "Available", "Total"], rows)
    if detail == "full" and data.get("inventory"):
        html += _h3("By Cost Centre")
        for cc in data["inventory"]:
            html += _h3(cc["costCenterName"])
            crows = [[_cell(l["name"]), _cell(str(l["assigned"])),
                      _cell(str(l["available"])), _cell(str(l["total"]))]
                     for l in cc.get("licenses", [])]
            html += _table(["License Type", "Assigned", "Available", "Total"], crows)
    return html


# ---- Integrations & Provisioning ------------------------------------------

# Substrings that flag a service feature as integration/provisioning relevant.
INTEGRATION_KEYWORDS = (
    "sso", "saml", "scim", "federation", "teams", "microsoft", "salesforce",
    "google", "hubspot", "zendesk", "servicenow", "okta", "slack", "integration",
    "presence", "directory", "sync", "crm", "hud", "archiver", "contactcenter",
    "contact center", "webhook", "developer", "api",
)


def collect_integrations(ctx, detail):
    features = ctx.service_info.get("serviceFeatures", []) or []

    scim = _api("/scim/v2/ServiceProviderConfig")
    scim_enabled = bool(
        scim and not (isinstance(scim, dict) and scim.get("errorCode")))

    # Federation is only "on" when the account is actually linked to others —
    # i.e. the directory federation resource lists 2+ accounts. The
    # AccountFederation service feature merely means the account is *entitled* to
    # federation, so it is deliberately NOT used for the headline and is excluded
    # from the feature list below to avoid reading as "federation enabled".
    fed = _api("/restapi/v1.0/account/~/directory/federation")
    fed_accounts = 0
    if isinstance(fed, dict):
        fed_accounts = len(fed.get("records") or fed.get("accounts") or [])

    def is_notable(f):
        n = (f.get("featureName", "") or "").lower()
        if n == "accountfederation":
            return False  # represented by the dedicated federation headline
        return any(k in n for k in INTEGRATION_KEYWORDS)

    data = {
        "scim_enabled": scim_enabled,
        "federation_active": fed_accounts >= 2,
        "federation_accounts": fed_accounts,
        "enabled_count": sum(1 for f in features if f.get("enabled")),
        "feature_count": len(features),
        "notable": [
            {"name": f.get("featureName", ""), "enabled": bool(f.get("enabled"))}
            for f in features if is_notable(f)
        ],
    }
    if detail == "full":
        data["all_features"] = [
            {"name": f.get("featureName", ""), "enabled": bool(f.get("enabled"))}
            for f in sorted(features, key=lambda x: x.get("featureName", ""))
            if (f.get("featureName", "") or "").lower() != "accountfederation"
        ]
    return data


def render_integrations(data, detail):
    html = _h2("Integrations & Provisioning")
    html += _kv("SSO Auto-Provisioning (SCIM)",
                _esc("Enabled" if data["scim_enabled"] else "Not enabled"))
    if data.get("federation_active"):
        fed_txt = f'Federated ({data.get("federation_accounts", 0)} linked accounts)'
    else:
        fed_txt = "Not federated"
    html += _kv("Account Federation", _esc(fed_txt))
    html += _kv("Service Features Enabled",
                _esc(f'{data["enabled_count"]} of {data["feature_count"]}'))

    if detail != "summary":
        notable = data.get("notable", [])
        html += _h3(f"Notable Integration Features ({len(notable)})")
        if notable:
            rows = [[_cell(f["name"]), _cell("Enabled" if f["enabled"] else "Disabled")]
                    for f in notable]
            html += _table(["Feature", "Status"], rows)
        else:
            html += _empty("No integration-related service features detected.")

    if detail == "full" and data.get("all_features"):
        html += _h3(f"All Service Features ({len(data['all_features'])})")
        rows = [[_cell(f["name"]), _cell("Enabled" if f["enabled"] else "Disabled")]
                for f in data["all_features"]]
        html += _table(["Feature", "Status"], rows)

    html += _p(
        "Note: Microsoft Teams Direct Routing and the RingCentral embedded app "
        "for Teams are not exposed as discrete flags in the account API; where "
        "licensed they surface among the service features above. Per-user "
        "presence sync is a user-level setting, not an account-wide flag.")
    return html


# ---- Company Business Hours & Answering Rules -----------------------------

def _render_weekly_hours(schedule):
    """Full weekly business-hours table from a schedule payload."""
    weekly = (schedule or {}).get("weeklyRanges") or {}
    order = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]
    rows = []
    for day in order:
        ranges = weekly.get(day) or []
        if ranges:
            spans = ", ".join(f'{r.get("from","")}–{r.get("to","")}' for r in ranges)
        else:
            spans = "Closed"
        rows.append([_cell(day.capitalize()), _cell(spans)])
    return _table(["Day", "Open Hours"], rows)


def collect_company_hours_rules(ctx, detail):
    hours = _api("/restapi/v1.0/account/~/business-hours") or {}
    data = {"hours_summary": _summarise_hours(hours), "schedule": hours.get("schedule") or {}}
    if detail != "summary":
        rules = _fetch_all_pages(
            "/restapi/v1.0/account/~/answering-rule?view=Detailed")
        data["rules"] = [{
            "name": r.get("name", ""),
            "type": r.get("type", ""),
            "enabled": r.get("enabled", ""),
            "action": (r.get("callHandlingAction") or ""),
        } for r in rules]
    return data


def render_company_hours_rules(data, detail):
    html = _h2("Company Business Hours & Answering Rules")
    if detail == "summary":
        return html + _kv("Business Hours", _esc(data["hours_summary"]))
    html += _h3("Company Business Hours")
    html += _render_weekly_hours(data.get("schedule"))
    rules = data.get("rules", [])
    html += _h3(f"Company Answering Rules ({len(rules)})")
    if not rules:
        return html + _empty("No company answering rules defined.")
    rows = [[_cell(r["name"]), _cell(r["type"]),
             _cell("Yes" if r.get("enabled") else "No"), _cell(r["action"])]
            for r in rules]
    return html + _table(["Rule", "Type", "Enabled", "Call Handling"], rows)


# ---- Call Recording -------------------------------------------------------

def collect_call_recording(ctx, detail):
    cfg = _api("/restapi/v1.0/account/~/call-recording") or {}
    auto = cfg.get("automatic") or {}
    ondemand = cfg.get("onDemand") or {}
    data = {
        "automatic_enabled": auto.get("enabled"),
        "ondemand_enabled": ondemand.get("enabled"),
    }
    # The account resource only exposes on/off. The inbound/outbound direction
    # lives per extension in /call-recording/extensions (callDirection), so
    # derive the direction breakdown from there rather than from non-existent
    # account-level fields (the cause of the earlier "enabled but both
    # disabled" contradiction).
    if detail != "summary":
        exts = _cc_fetch("/restapi/v1.0/account/~/call-recording/extensions")
        directions = {"Inbound": 0, "Outbound": 0, "All": 0}
        for e in exts:
            d = e.get("callDirection", "All") or "All"
            directions[d] = directions.get(d, 0) + 1
        data["auto_ext_count"] = len(exts)
        data["directions"] = directions
    return data


def render_call_recording(data, detail):
    html = _h2("Call Recording")

    def yn(v):
        return "Enabled" if v else "Disabled"

    html += _kv("Automatic Recording", _esc(yn(data.get("automatic_enabled"))))
    html += _kv("On-Demand Recording", _esc(yn(data.get("ondemand_enabled"))))
    if detail != "summary":
        html += _kv("Extensions with Automatic Recording",
                    _esc(str(data.get("auto_ext_count", 0))))
        d = data.get("directions") or {}
        if data.get("auto_ext_count"):
            html += _kv("Recorded Directions",
                        _esc(f'Inbound {d.get("Inbound", 0)}, '
                             f'Outbound {d.get("Outbound", 0)}, '
                             f'Both {d.get("All", 0)}'))
    return html


# ===========================================================================
# SECTION REGISTRY
# ===========================================================================
# Order here is the order sections appear in the generated document.

SECTIONS = [
    {
        "key": "overview",
        "label": "Account Overview",
        "description": "Account identity, service plan and an inventory count of every item type.",
        "collect": collect_overview,
        "render": render_overview,
        "default_detail": "standard",
        "always": True,
    },
    {
        "key": "sites",
        "label": "Sites",
        "description": "Multi-site locations, addresses, operators, timezones and hours.",
        "collect": collect_sites,
        "render": render_sites,
        "default_detail": "standard",
    },
    {
        "key": "users",
        "label": "Users",
        "description": "User extensions, contact details, sites, direct numbers and devices.",
        "collect": collect_users,
        "render": render_users,
        "default_detail": "standard",
    },
    {
        "key": "call_queues",
        "label": "Call Queues",
        "description": "Queues, members, business hours and routing thresholds.",
        "collect": collect_call_queues,
        "render": render_call_queues,
        "default_detail": "standard",
    },
    {
        "key": "ivrs",
        "label": "IVR Menus",
        "description": "Auto-attendant menus, prompts and key-press routing.",
        "collect": collect_ivrs,
        "render": render_ivrs,
        "default_detail": "standard",
    },
    {
        "key": "park_zones",
        "label": "Park Locations",
        "description": "Call park zones and their assigned members.",
        "collect": collect_park_zones,
        "render": render_park_zones,
        "default_detail": "standard",
    },
    {
        "key": "paging_groups",
        "label": "Paging Groups",
        "description": "Paging-only groups.",
        "collect": collect_paging_groups,
        "render": render_paging_groups,
        "default_detail": "standard",
    },
    {
        "key": "shared_line_groups",
        "label": "Shared Line Groups",
        "description": "Shared line groups and their assigned numbers.",
        "collect": collect_shared_line_groups,
        "render": render_shared_line_groups,
        "default_detail": "standard",
    },
    {
        "key": "message_only",
        "label": "Message-Only Extensions",
        "description": "Message-only (voicemail) extensions.",
        "collect": collect_message_only,
        "render": render_message_only,
        "default_detail": "standard",
    },
    {
        "key": "announcement_only",
        "label": "Announcement-Only Extensions",
        "description": "Announcement-only extensions.",
        "collect": collect_announcement_only,
        "render": render_announcement_only,
        "default_detail": "standard",
    },
    {
        "key": "phone_numbers",
        "label": "Phone Numbers",
        "description": "DID and company number inventory, by usage type.",
        "collect": collect_phone_numbers,
        "render": render_phone_numbers,
        "default_detail": "standard",
    },
    {
        "key": "devices",
        "label": "Devices",
        "description": "Provisioned hardware/soft phones, models, serials and assignments.",
        "collect": collect_devices,
        "render": render_devices,
        "default_detail": "standard",
    },
    {
        "key": "custom_roles",
        "label": "User Roles",
        "description": "User roles (predefined and custom), scope and description.",
        "collect": collect_custom_roles,
        "render": render_custom_roles,
        "default_detail": "standard",
    },
    {
        "key": "cost_centres",
        "label": "Cost Centres",
        "description": "Cost centres with assigned/available license counts.",
        "collect": collect_cost_centres,
        "render": render_cost_centres,
        "default_detail": "standard",
    },
    {
        "key": "licensing",
        "label": "Licensing",
        "description": "License counts by type and cost centre (assigned vs available).",
        "collect": collect_licensing,
        "render": render_licensing,
        "default_detail": "standard",
    },
    {
        "key": "integrations",
        "label": "Integrations & Provisioning",
        "description": "SSO/SCIM, account federation and enabled service features.",
        "collect": collect_integrations,
        "render": render_integrations,
        "default_detail": "standard",
    },
    {
        "key": "company_hours_rules",
        "label": "Company Hours & Answering Rules",
        "description": "Company business hours and company-level answering rules.",
        "collect": collect_company_hours_rules,
        "render": render_company_hours_rules,
        "default_detail": "standard",
    },
    {
        "key": "call_recording",
        "label": "Call Recording",
        "description": "Automatic and on-demand call recording settings.",
        "collect": collect_call_recording,
        "render": render_call_recording,
        "default_detail": "standard",
    },
]

SECTION_BY_KEY = {s["key"]: s for s in SECTIONS}


def get_catalog():
    """Section metadata for the UI (no callables)."""
    return [
        {
            "key": s["key"],
            "label": s["label"],
            "description": s["description"],
            "default_detail": s.get("default_detail", DEFAULT_DETAIL),
            "always": s.get("always", False),
        }
        for s in SECTIONS
    ]


# ===========================================================================
# DOCUMENT ASSEMBLY
# ===========================================================================

def collect_document(selections):
    """Run the collectors for the requested sections.

    selections: list of {"key": str, "detail": str}. Overview is always
    included first. Returns a list of rendered section dicts.
    """
    ctx = CollectContext()

    # Normalise requested detail levels, keyed by section.
    requested = {}
    for sel in selections or []:
        key = sel.get("key")
        detail = sel.get("detail", DEFAULT_DETAIL)
        if detail not in DETAIL_LEVELS:
            detail = DEFAULT_DETAIL
        if key in SECTION_BY_KEY:
            requested[key] = detail

    rendered = []
    for section in SECTIONS:
        key = section["key"]
        if not section.get("always") and key not in requested:
            continue
        detail = requested.get(key, section.get("default_detail", DEFAULT_DETAIL))
        try:
            data = section["collect"](ctx, detail)
            html = section["render"](data, detail)
            rendered.append({"key": key, "label": section["label"],
                             "detail": detail, "html": html, "data": data,
                             "error": None})
        except Exception as e:  # one bad section must not sink the whole doc
            logger.exception("[as_built] section %s failed", key)
            rendered.append({
                "key": key, "label": section["label"], "detail": detail,
                "html": _h2(section["label"]) + _empty(f"Could not collect this section: {e}"),
                "data": None, "error": str(e),
            })

    return {"account_name": ctx.account_name,
            "account_id": ctx.account_id,
            "sections": rendered}


def build_body_html(doc, customer_name=None):
    """Assemble the full document body HTML from collected sections."""
    from datetime import datetime, timezone
    customer = (customer_name or doc.get("account_name") or "Customer").strip()
    generated = datetime.now(timezone.utc).strftime("%d %b %Y %H:%M UTC")

    header = (
        '<div class="ab-header">'
        '<div class="ab-logo"><span class="ab-wordmark">RingCentral</span></div>'
        '<h1>As-Built Documentation</h1>'
        f'<p class="ab-sub">{_esc(customer)}'
        + (f' &middot; Account {_esc(doc.get("account_id"))}' if doc.get("account_id") else "")
        + "</p>"
        f'<p class="ab-lead">Generated {generated}</p>'
        "</div>"
    )
    body = header + "".join(s["html"] for s in doc.get("sections", []))
    footer = (
        '<div class="ab-footer">RingCentral Australia &mdash; As-Built Documentation. '
        "Configuration captured live from the account at generation time.</div>"
    )
    return body + footer


# ===========================================================================
# EXPORT (PDF / Word) — mirrors network_requirements approach
# ===========================================================================

# Export stylesheet: flexbox-free so it renders identically in the browser,
# xhtml2pdf (PDF) and Microsoft Word.
EXPORT_CSS = (
    "@page{size:A4;margin:1.7cm;}"
    "body{font-family:'Helvetica Neue',Arial,sans-serif;color:#0f172a;font-size:10.5pt;line-height:1.5;}"
    ".ab-wordmark{font-size:20pt;font-weight:800;color:#0b5cab;letter-spacing:-.5px;}"
    ".ab-logo{margin-bottom:8px;}"
    "h1{font-size:20pt;margin:0 0 4px;color:#0f172a;}"
    "h2{font-size:14pt;margin:22px 0 8px;padding-bottom:5px;border-bottom:2px solid #2563eb;color:#0f172a;}"
    "h3{font-size:11.5pt;color:#1d4ed8;margin:14px 0 5px;}"
    "p{margin:5px 0;}"
    ".ab-sub{color:#334155;font-weight:600;margin:0 0 4px;font-size:12pt;}"
    ".ab-lead{color:#64748b;margin:0 0 6px;font-size:9.5pt;}"
    ".ab-note{color:#475569;font-size:10pt;}"
    ".ab-empty{color:#94a3b8;font-style:italic;font-size:9.5pt;}"
    ".ab-muted{color:#94a3b8;}"
    ".ab-count{display:inline-block;margin-left:6px;padding:1px 8px;border-radius:9px;"
    "background:#eff6ff;border:1px solid #bfdbfe;color:#1e40af;font-size:9pt;font-weight:700;}"
    "table.ab-table{width:100%;border-collapse:collapse;margin:6px 0 14px;font-size:8.5pt;table-layout:fixed;}"
    "table.ab-table th{text-align:left;background:#f1f5f9;border:1px solid #cbd5e1;padding:5px 7px;font-weight:700;}"
    "table.ab-table td{border:1px solid #cbd5e1;padding:5px 7px;vertical-align:top;word-break:break-word;overflow-wrap:break-word;}"
    ".ab-kv{margin:3px 0;font-size:10pt;}"
    ".ab-kv .ab-k{padding-right:8px;font-weight:700;color:#475569;}"
    ".ab-block{margin:4px 0 12px;line-height:2;}"
    ".ab-chip{padding:2px 7px;border-radius:5px;background:#eff6ff;border:1px solid #bfdbfe;"
    "color:#1e40af;font-size:8pt;font-family:'Courier New',monospace;white-space:nowrap;}"
    ".ab-footer{margin-top:24px;padding-top:8px;border-top:1px solid #e2e8f0;color:#94a3b8;font-size:8pt;}"
)


def customer_slug(name):
    base = re.sub(r"[^a-z0-9]+", "_", (name or "").strip(), flags=re.IGNORECASE).strip("_")
    return base or "Customer"


def _standalone_html(body_html, customer_name):
    return (
        '<!DOCTYPE html><html><head><meta charset="utf-8">'
        f"<title>As-Built - {_esc(customer_name)}</title>"
        f"<style>{EXPORT_CSS}</style></head>"
        f'<body class="ab-doc">{body_html}</body></html>'
    )


def build_pdf_bytes(body_html, customer_name):
    from io import BytesIO
    from xhtml2pdf import pisa

    source = _standalone_html(body_html, customer_name)
    buffer = BytesIO()
    result = pisa.CreatePDF(src=source, dest=buffer, encoding="utf-8")
    if result.err:
        raise RuntimeError("Failed to render PDF document.")
    return buffer.getvalue()


def build_word_bytes(body_html, customer_name):
    """Word opens well-formed styled HTML natively, keeping the same house style
    as the PDF with no extra document-building dependency."""
    return _standalone_html(body_html, customer_name).encode("utf-8")


# ===========================================================================
# EXCEL EXPORT — granular, audit-grade data dump (one+ sheet per item type)
# ===========================================================================
# The PDF/Word are the readable as-built document; the workbook is the raw,
# filterable data behind it — the same low-level detail our audit tools export.
# Sheets are built from the structured data collected per section, so the depth
# reflects the detail level chosen at generation time (generate at "full" for
# the most granular workbook).

def _join(values):
    return "\n".join(str(v) for v in (values or []) if v)


def _sheets_for(section):
    """Return [{"name","columns","rows"}] for one collected section.

    Each dict is one worksheet. Sections that are naturally per-item produce
    one row per item; queues/IVRs/park also emit a membership/action sheet so
    audits can filter at the row level.
    """
    key = section.get("key")
    data = section.get("data")
    if not data:
        return []

    if key == "overview":
        rows = [{"Item": "Account Name", "Value": data.get("name")},
                {"Item": "Account ID", "Value": data.get("account_id")},
                {"Item": "Main Number", "Value": data.get("main_number")},
                {"Item": "Service Plan", "Value": data.get("service_plan")},
                {"Item": "Status", "Value": data.get("status")}]
        rows += [{"Item": k, "Value": v} for k, v in data.get("counts", {}).items()]
        return [{"name": "Overview", "columns": ["Item", "Value"], "rows": rows}]

    if key == "sites":
        cols = ["name", "extensionNumber", "address", "operator", "timezone", "code", "hours"]
        rows = [{c: s.get(c, "") for c in cols} for s in data.get("sites", [])]
        return [{"name": "Sites", "columns": cols, "rows": rows}]

    if key == "users":
        cols = ["name", "extensionNumber", "type", "status", "email", "site",
                "numbers", "devices", "department"]
        rows = []
        for u in data.get("users", []):
            r = {c: u.get(c, "") for c in cols}
            r["numbers"] = _join(u.get("numbers"))
            r["devices"] = _join(u.get("devices"))
            rows.append(r)
        return [{"name": "Users", "columns": cols, "rows": rows}]

    if key == "call_queues":
        qcols = ["name", "extensionNumber", "status", "member_count", "ring_type",
                 "hold_time", "wrap_up", "max_callers", "when_full", "when_max_time",
                 "hours", "after_hours_action"]
        qrows, mrows = [], []
        for q in data.get("queues", []):
            r = {c: q.get(c, "") for c in qcols}
            # Flatten the schedule to a single-cell text summary for the sheet.
            weekly = (q.get("schedule") or {}).get("weeklyRanges") or {}
            r["hours"] = "; ".join(
                f'{d[:3].capitalize()} '
                + ", ".join(f'{x.get("from","")}-{x.get("to","")}' for x in weekly[d])
                for d in weekly if weekly[d]) or "24/7"
            qrows.append(r)
            for m in q.get("members", []):
                mrows.append({"Queue": q.get("name"),
                              "Queue Ext": q.get("extensionNumber"), "Member": m})
        sheets = [{"name": "Call Queues", "columns": qcols, "rows": qrows}]
        if mrows:
            sheets.append({"name": "Queue Members",
                           "columns": ["Queue", "Queue Ext", "Member"], "rows": mrows})
        return sheets

    if key == "ivrs":
        icols = ["name", "extensionNumber", "prompt", "prompt_mode", "key_count"]
        irows, arows = [], []
        for i in data.get("ivrs", []):
            irows.append({c: i.get(c, "") for c in icols})
            for a in i.get("actions", []):
                arows.append({"IVR": i.get("name"), "IVR Ext": i.get("extensionNumber"),
                              "Key": a.get("key"), "Action": a.get("action"),
                              "Destination": a.get("destination")})
        sheets = [{"name": "IVR Menus", "columns": icols, "rows": irows}]
        if arows:
            sheets.append({"name": "IVR Actions",
                           "columns": ["IVR", "IVR Ext", "Key", "Action", "Destination"],
                           "rows": arows})
        return sheets

    if key == "park_zones":
        cols = ["name", "extensionNumber", "status", "site"]
        rows = [{c: p.get(c, "") for c in cols} for p in data.get("parks", [])]
        return [{"name": "Park Locations", "columns": cols, "rows": rows}]

    if key == "paging_groups":
        rows = [{"name": g.get("name"), "extensionNumber": g.get("extensionNumber")}
                for g in data.get("groups", [])]
        return [{"name": "Paging Groups", "columns": ["name", "extensionNumber"], "rows": rows}]

    if key in ("shared_line_groups", "message_only", "announcement_only"):
        label = {"shared_line_groups": "Shared Line Groups",
                 "message_only": "Message-Only", "announcement_only": "Announcement-Only"}[key]
        cols = ["name", "extensionNumber", "status", "site", "numbers"]
        if key == "message_only":
            cols = cols + ["notify_email"]
        rows = []
        for i in data.get("items", []):
            r = {c: i.get(c, "") for c in cols}
            r["numbers"] = _join(i.get("numbers"))
            if key == "message_only":
                r["notify_email"] = _join(i.get("notify_email"))
            rows.append(r)
        return [{"name": label, "columns": cols, "rows": rows}]

    if key == "phone_numbers":
        rows = data.get("rows", [])
        if rows:
            cols = ["number", "usage", "type", "status", "location", "assigned"]
            rows = [{c: r.get(c, "") for c in cols} for r in rows]
            return [{"name": "Phone Numbers", "columns": cols, "rows": rows}]
        rows = [{"Usage Type": k, "Count": v} for k, v in data.get("by_usage", {}).items()]
        return [{"name": "Phone Numbers", "columns": ["Usage Type", "Count"], "rows": rows}]

    if key == "devices":
        cols = ["name", "model", "type", "status", "serial", "sku", "assigned", "site"]
        rows = [{c: d.get(c, "") for c in cols} for d in data.get("devices", [])]
        return [{"name": "Devices", "columns": cols, "rows": rows}]

    if key == "custom_roles":
        cols = ["name", "scope", "custom", "description"]
        rows = [{c: r.get(c, "") for c in cols} for r in data.get("roles", [])]
        return [{"name": "User Roles", "columns": cols, "rows": rows}]

    if key == "cost_centres":
        cols = ["name", "id", "objects", "assigned", "available"]
        rows = [{c: c2.get(c, "") for c in cols} for c2 in data.get("centres", [])]
        return [{"name": "Cost Centres", "columns": cols, "rows": rows}]

    if key == "licensing":
        rows = [{"License Type": k, "Assigned": v.get("assigned"),
                 "Available": v.get("available"), "Total": v.get("total")}
                for k, v in data.get("by_type", {}).items()]
        sheets = [{"name": "Licensing",
                   "columns": ["License Type", "Assigned", "Available", "Total"], "rows": rows}]
        cc_rows = []
        for cc in data.get("inventory", []) or []:
            for l in cc.get("licenses", []):
                cc_rows.append({"Cost Centre": cc.get("costCenterName"),
                                "License Type": l.get("name"), "Assigned": l.get("assigned"),
                                "Available": l.get("available"), "Total": l.get("total")})
        if cc_rows:
            sheets.append({"name": "Licensing by Cost Centre",
                           "columns": ["Cost Centre", "License Type", "Assigned",
                                       "Available", "Total"], "rows": cc_rows})
        return sheets

    if key == "company_hours_rules":
        sheets = []
        weekly = (data.get("schedule") or {}).get("weeklyRanges") or {}
        if weekly:
            hrows = []
            for day in ["monday", "tuesday", "wednesday", "thursday", "friday",
                        "saturday", "sunday"]:
                ranges = weekly.get(day) or []
                spans = ", ".join(f'{r.get("from","")}-{r.get("to","")}' for r in ranges) \
                    or "Closed"
                hrows.append({"Day": day.capitalize(), "Open Hours": spans})
            sheets.append({"name": "Company Hours",
                           "columns": ["Day", "Open Hours"], "rows": hrows})
        rrows = [{"Rule": r.get("name"), "Type": r.get("type"),
                  "Enabled": "Yes" if r.get("enabled") else "No",
                  "Call Handling": r.get("action")}
                 for r in data.get("rules", []) or []]
        if rrows:
            sheets.append({"name": "Answering Rules",
                           "columns": ["Rule", "Type", "Enabled", "Call Handling"],
                           "rows": rrows})
        return sheets

    if key == "integrations":
        if data.get("federation_active"):
            fed_status = f'Federated ({data.get("federation_accounts", 0)} linked accounts)'
        else:
            fed_status = "Not federated"
        rows = [{"Feature": "SSO Auto-Provisioning (SCIM)",
                 "Status": "Enabled" if data.get("scim_enabled") else "Not enabled"},
                {"Feature": "Account Federation", "Status": fed_status}]
        source = data.get("all_features") or data.get("notable") or []
        for f in source:
            rows.append({"Feature": f.get("name"),
                         "Status": "Enabled" if f.get("enabled") else "Disabled"})
        return [{"name": "Integrations", "columns": ["Feature", "Status"], "rows": rows}]

    if key == "call_recording":
        def yn(v):
            return "Enabled" if v else "Disabled"
        rows = [{"Setting": "Automatic Recording", "Value": yn(data.get("automatic_enabled"))},
                {"Setting": "On-Demand Recording", "Value": yn(data.get("ondemand_enabled"))},
                {"Setting": "Extensions with Automatic Recording",
                 "Value": data.get("auto_ext_count", 0)}]
        d = data.get("directions") or {}
        if data.get("auto_ext_count"):
            rows += [{"Setting": "Recorded - Inbound", "Value": d.get("Inbound", 0)},
                     {"Setting": "Recorded - Outbound", "Value": d.get("Outbound", 0)},
                     {"Setting": "Recorded - Both", "Value": d.get("All", 0)}]
        return [{"name": "Call Recording", "columns": ["Setting", "Value"], "rows": rows}]

    return []


def _safe_sheet_name(name, used):
    """Excel sheet names: <=31 chars, unique, no []:*?/\\ characters."""
    clean = re.sub(r"[\[\]:*?/\\]", " ", str(name)).strip()[:31] or "Sheet"
    candidate = clean
    n = 2
    while candidate.lower() in used:
        suffix = f" ({n})"
        candidate = clean[:31 - len(suffix)] + suffix
        n += 1
    used.add(candidate.lower())
    return candidate


def build_workbook_bytes(doc, customer_name):
    """Build a multi-sheet .xlsx with the granular data behind the document."""
    import io
    import pandas as pd

    sheets = []
    for section in doc.get("sections", []):
        try:
            sheets.extend(_sheets_for(section))
        except Exception:
            logger.exception("[as_built] xlsx sheet build failed for %s",
                             section.get("key"))

    output = io.BytesIO()
    used_names = set()
    with pd.ExcelWriter(output, engine="openpyxl") as writer:
        wrote_any = False
        for sheet in sheets:
            rows = sheet.get("rows") or []
            cols = sheet.get("columns") or []
            df = pd.DataFrame(rows, columns=cols) if cols else pd.DataFrame(rows)
            name = _safe_sheet_name(sheet.get("name", "Sheet"), used_names)
            df.to_excel(writer, index=False, sheet_name=name)
            wrote_any = True
            ws = writer.sheets[name]
            for column in ws.columns:
                length = max((len(str(c.value)) if c.value is not None else 0)
                             for c in column)
                ws.column_dimensions[column[0].column_letter].width = min(length + 3, 60)
        if not wrote_any:
            pd.DataFrame([{"Info": "No data collected for the selected sections."}]) \
                .to_excel(writer, index=False, sheet_name="As-Built")

    output.seek(0)
    return output.getvalue()
