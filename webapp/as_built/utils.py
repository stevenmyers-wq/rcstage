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
        self.extensions = _fetch_all_pages("/restapi/v1.0/account/~/extension")
        self.phone_numbers = _fetch_all_pages("/restapi/v1.0/account/~/phone-number")

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


def _table(headers, rows):
    """rows: list of lists (cells may contain pre-escaped HTML from _cell)."""
    if not rows:
        return ""
    head = "".join(f"<th>{_esc(h)}</th>" for h in headers)
    body = ""
    for r in rows:
        cells = "".join(f"<td>{c}</td>" for c in r)
        body += f"<tr>{cells}</tr>"
    return f'<table class="ab-table"><thead><tr>{head}</tr></thead><tbody>{body}</tbody></table>'


def _cell(value):
    """Escape a plain value for use inside a table cell."""
    if value is None or value == "":
        return '<span class="ab-muted">—</span>'
    return _esc(value)


def _chips(values):
    if not values:
        return '<span class="ab-muted">—</span>'
    return "".join(f'<span class="ab-chip">{_esc(v)}</span>' for v in values)


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

    data = {
        "name": ctx.account.get("name", "Unknown"),
        "account_id": ctx.account.get("id", ""),
        "main_number": ctx.account.get("mainNumber", ""),
        "service_plan": (ctx.account.get("servicePlan") or {}).get("name", ""),
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
        return html + _table(["Name", "Ext", "Type"], rows)
    if detail == "standard":
        rows = [[
            _cell(u["name"]), _cell(u["extensionNumber"]), _cell(u.get("email")),
            _cell(u.get("status")), _cell(u.get("site")),
            _chips(u.get("numbers")),
        ] for u in users]
        return html + _table(
            ["Name", "Ext", "Email", "Status", "Site", "Direct Numbers"], rows)
    # full
    rows = [[
        _cell(u["name"]), _cell(u["extensionNumber"]), _cell(u.get("email")),
        _cell(u.get("status")), _cell(u.get("site")), _chips(u.get("numbers")),
        _chips(u.get("devices")), _cell(u.get("department")),
    ] for u in users]
    return html + _table(
        ["Name", "Ext", "Email", "Status", "Site", "Direct Numbers",
         "Devices", "Department"], rows)


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
            bh = _api(f"/restapi/v1.0/account/~/extension/{qid}/business-hours")
            rec["hours"] = _summarise_hours(bh)
        if detail == "full":
            cfg = _api(f"/restapi/v1.0/account/~/call-queues/{qid}") or {}
            rec["ring_type"] = cfg.get("serviceLevelSettings", {}).get(
                "serviceLevelThresholdSeconds")
            rec["max_wait"] = (cfg.get("maxCallers") or cfg.get("holdTimeExpirationAction"))
            rec["managers"] = ""  # placeholder for /managers, see notes
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
        return html + _table(["Queue", "Ext", "Status"], rows)
    # standard + full render each queue as its own block (members can be long)
    html_parts = [html]
    for q in queues:
        html_parts.append(_h3(f'{q["name"]}  ·  Ext {q["extensionNumber"]}'))
        html_parts.append(_kv("Members", str(q.get("member_count", 0))))
        html_parts.append(_kv("Business Hours", _esc(q.get("hours"))))
        if detail == "full":
            html_parts.append(_kv("Service Level (s)", _esc(q.get("ring_type"))))
        html_parts.append(
            '<div class="ab-block">' + _chips(q.get("members")) + "</div>")
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
            rec["prompt_mode"] = (cfg.get("prompt") or {}).get("mode", "")
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


def render_ivrs(data, detail):
    ivrs = data["ivrs"]
    html = _h2("IVR Menus", len(ivrs))
    if not ivrs:
        return html + _empty("No IVR menus configured on this account.")
    if detail == "summary":
        rows = [[_cell(i["name"]), _cell(i["extensionNumber"])] for i in ivrs]
        return html + _table(["IVR", "Ext"], rows)
    html_parts = [html]
    for i in ivrs:
        html_parts.append(_h3(f'{i["name"]}  ·  Ext {i["extensionNumber"]}'))
        if detail == "full":
            html_parts.append(_kv("Prompt Mode", _esc(i.get("prompt_mode"))))
        rows = [[_cell(a["key"]), _cell(a["action"]), _cell(a["destination"])]
                for a in i.get("actions", [])]
        html_parts.append(_table(["Key", "Action", "Destination"], rows)
                          or _empty("No key actions defined."))
    return "".join(html_parts)


# ---- Park Locations -------------------------------------------------------

def collect_park_zones(ctx, detail):
    parks = _fetch_all_pages("/restapi/v1.0/account/~/park-locations")
    out = []
    for p in parks:
        pid = str(p.get("id"))
        rec = {
            "id": pid,
            "name": p.get("name", "Unknown"),
            "extensionNumber": p.get("extensionNumber", ""),
        }
        if detail != "summary":
            members = (p.get("members") or {})
            member_recs = members.get("records") if isinstance(members, dict) else members
            rec["members"] = [ctx.ext_name(m.get("id")) for m in (member_recs or [])]
        out.append(rec)
    return {"parks": out}


def render_park_zones(data, detail):
    parks = data["parks"]
    html = _h2("Park Locations", len(parks))
    if not parks:
        return html + _empty("No park locations configured on this account.")
    if detail == "summary":
        rows = [[_cell(p["name"]), _cell(p["extensionNumber"])] for p in parks]
        return html + _table(["Park Location", "Ext"], rows)
    rows = [[_cell(p["name"]), _cell(p["extensionNumber"]), _chips(p.get("members"))]
            for p in parks]
    return html + _table(["Park Location", "Ext", "Members"], rows)


# ---- Paging Groups --------------------------------------------------------

def collect_paging_groups(ctx, detail):
    groups = _fetch_all_pages("/restapi/v1.0/account/~/paging-only-groups")
    out = []
    for g in groups:
        gid = str(g.get("id"))
        rec = {
            "id": gid,
            "name": g.get("name", "Unknown"),
            "extensionNumber": g.get("extensionNumber", ""),
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
                             "detail": detail, "html": html, "error": None})
        except Exception as e:  # one bad section must not sink the whole doc
            logger.exception("[as_built] section %s failed", key)
            rendered.append({
                "key": key, "label": section["label"], "detail": detail,
                "html": _h2(section["label"]) + _empty(f"Could not collect this section: {e}"),
                "error": str(e),
            })

    return {"account_name": ctx.account.get("name", "Customer"),
            "account_id": ctx.account.get("id", ""),
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
    "table.ab-table{width:100%;border-collapse:collapse;margin:6px 0 14px;font-size:8.5pt;}"
    "table.ab-table th{text-align:left;background:#f1f5f9;border:1px solid #cbd5e1;padding:5px 7px;font-weight:700;}"
    "table.ab-table td{border:1px solid #cbd5e1;padding:5px 7px;vertical-align:top;word-break:break-word;}"
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
