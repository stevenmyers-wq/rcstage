"""
Network Requirements document generator for RingCentral UC.

This module is a *self-contained content library*. It holds RingCentral's
published network requirements and turns a set of tick-box selections into a
customer-specific, print-ready HTML document. There are no RingCentral API
calls here — the document is assembled purely from the catalogues below.

IMPORTANT: RingCentral occasionally revises its published supernets, ports and
provisioning FQDNs. The generated document carries a "verify against the live
KB" banner and links back to the official articles so the engineer always has a
way to confirm the current values before handing the document to a customer.
"""

import html
from datetime import datetime

# ---------------------------------------------------------------------------
# Reference links (official RingCentral documentation)
# ---------------------------------------------------------------------------
KB_NETWORK_REQUIREMENTS = "https://support.ringcentral.com/network-and-system-requirements/network-requirements.html"
KB_QOS = "https://support.ringcentral.com/network-and-system-requirements/network-requirements/overview/quality-of-service-guidelines.html"
ADMIN_PORTAL = "https://service.ringcentral.com/"
APP_GALLERY = "https://www.ringcentral.com/apps/"

# ---------------------------------------------------------------------------
# SECTION 1 — Standard requirements (always included in every document)
# These apply across all RingCentral UC services.
# ---------------------------------------------------------------------------

STANDARD_BANDWIDTH = [
    ("Voice (G.711)", "~100 kbps per call, each direction (bidirectional)"),
    ("Voice (G.722 / Opus HD)", "~100 kbps per call, each direction"),
    ("HD Video", "512 kbps – 2 Mbps per stream, each direction"),
    ("Screen share", "~300 kbps – 1 Mbps"),
    ("Provisioning headroom", "Add ~20% overhead on top of the sum of concurrent media streams"),
]

STANDARD_QUALITY = [
    ("Latency (one-way)", "< 150 ms (ideal < 100 ms)"),
    ("Jitter", "< 100 ms (ideal < 30 ms)"),
    ("Packet loss", "< 1% (ideal < 0.5%)"),
    ("MTU", "1500 bytes (avoid fragmentation; no double-NAT)"),
]

# (Function, Protocol, Port(s), Direction)
STANDARD_PORTS = [
    ("SIP signalling", "TCP / UDP", "5060, 5090", "Outbound"),
    ("SIP over TLS (secure signalling)", "TCP", "5061, 5091, 5096, 5097", "Outbound"),
    ("RTP / SRTP media (audio, video, screen share)", "UDP", "20000 – 64000", "Outbound"),
    ("HTTPS (apps, API, phone provisioning, media)", "TCP", "443", "Outbound"),
    ("HTTP (legacy provisioning / firmware)", "TCP", "80", "Outbound"),
    ("DNS", "UDP / TCP", "53", "Outbound"),
    ("NTP (time sync)", "UDP", "123", "Outbound"),
    ("Web / Desktop / Mobile app services", "TCP", "443, 8443", "Outbound"),
]

# RingCentral published supernets to whitelist (all regions).
STANDARD_SUPERNETS = [
    ("104.245.56.0/21", "Global"),
    ("192.209.24.0/21", "Global"),
    ("199.68.212.0/22", "Global / AMER"),
    ("199.255.120.0/22", "Global / AMER"),
    ("208.87.40.0/22", "Global / AMER"),
    ("66.81.240.0/20", "AMER (legacy)"),
    ("80.81.128.0/20", "EMEA"),
    ("185.23.248.0/22", "EMEA"),
    ("103.44.68.0/22", "APAC"),
]

STANDARD_DOMAINS = [
    "*.ringcentral.com",
    "*.ringcentral.biz",
    "*.rccloud.com",
    "*.rccs.io",
    "*.ringcx.ringcentral.com",
    "*.glip.com / *.glip.net",
    "*.pubnub.com / *.pndsn.com (presence & push)",
]

STANDARD_QOS = [
    ("Voice media (RTP/SRTP)", "EF", "46"),
    ("Video media", "AF41", "34"),
    ("Call signalling (SIP)", "CS3 / AF31", "24 / 26"),
]

STANDARD_GENERAL_NOTES = [
    "Disable SIP ALG on all routers and firewalls. SIP ALG re-writes SIP headers and is the single most common cause of one-way audio, dropped and failed calls.",
    "Disable SPI / deep packet inspection (DPI) and any SIP transformation features for RingCentral traffic.",
    "Use a static, consistent (cone) NAT — avoid symmetric NAT and double-NAT for the voice VLAN.",
    "Do not proxy or SSL-inspect RingCentral traffic. Bypass web proxies for the domains and supernets listed above.",
    "Prioritise voice and video with QoS end-to-end; QoS markings are only honoured if trusted across every hop the traffic traverses.",
    "Where possible, place phones on a dedicated voice VLAN with LLDP-MED / 802.1p and DHCP option 66 (or 160/66) for provisioning.",
    "Ensure DNS and NTP are reachable — desk phones will fail to provision or register if time sync is unavailable.",
]

# ---------------------------------------------------------------------------
# SECTION 2 — Service / endpoint selectable requirements (tick boxes)
# key -> {label, requirements[], notes[]}
# ---------------------------------------------------------------------------

SERVICES = {
    "ringex_app": {
        "label": "RingEX Desktop & Mobile App",
        "requirements": [
            "HTTPS (TCP 443) outbound to *.ringcentral.com and *.rccloud.com.",
            "UDP 20000–64000 outbound for media (audio, video, screen share).",
            "WebSocket / push over TCP 443 to *.pubnub.com and *.pndsn.com for presence and notifications.",
            "Allow app auto-update endpoints over TCP 443.",
        ],
        "notes": [
            "Mobile clients on cellular are outside the corporate network — no firewall change needed, but Wi-Fi calling should follow the same QoS/VLAN rules as desktop.",
        ],
    },
    "browser_calling": {
        "label": "RingCentral for Browser / WebRTC calling",
        "requirements": [
            "WebRTC media over UDP 20000–64000 (falls back to TCP 443 / TURN if UDP is blocked).",
            "TCP 443 to *.ringcentral.com and *.rccloud.com.",
            "Do not block or throttle WebRTC (STUN/TURN) traffic at the firewall.",
        ],
        "notes": [
            "Supported browsers: current Chrome and Edge. Microphone/camera permissions must be allowed for the RingCentral domain.",
        ],
    },
    "deskphones": {
        "label": "Desk Phones (hardware endpoints)",
        "requirements": [
            "HTTPS (TCP 443) outbound to the RingCentral provisioning servers for zero-touch provisioning and firmware.",
            "SIP over TLS (TCP 5096/5097) and SRTP media (UDP 20000–64000).",
            "NTP (UDP 123) and DNS (UDP/TCP 53) must be reachable for registration.",
            "Dedicated voice VLAN with LLDP-MED / 802.1p recommended; DHCP option 66 (provisioning URL) where used.",
        ],
        "notes": [
            "Phones provision over HTTPS — TFTP is not used. Ensure the provisioning FQDNs below are not SSL-inspected.",
        ],
    },
    "ms_teams_embedded": {
        "label": "Microsoft Teams — RingCentral Embedded App",
        "requirements": [
            "TCP 443 outbound from the Teams client to *.ringcentral.com and *.pndsn.com.",
            "Allow the RingCentral for Microsoft Teams app through any Teams app-permission policy.",
            "Media follows the RingEX app path (UDP 20000–64000).",
        ],
        "notes": [
            "This is the embedded dialer/app inside Teams — it is separate from Direct Routing. See the Integrations section if Direct Routing is also required.",
        ],
    },
    "rc_rooms": {
        "label": "RingCentral Rooms / Room Systems",
        "requirements": [
            "TCP 443 and UDP 20000–64000 for the Rooms host and controller.",
            "Calendar sync (Microsoft 365 / Google) over TCP 443.",
            "Static IP or DHCP reservation recommended for room hardware.",
        ],
        "notes": [],
    },
    "ringcx": {
        "label": "RingCX / Contact Center",
        "requirements": [
            "TCP 443 to *.ringcx.ringcentral.com and the agent/admin consoles.",
            "WebRTC or SIP softphone media over UDP 20000–64000.",
            "Whitelist the RingCX / NICE CXone data-centre ranges for the customer's region in addition to the RingEX supernets.",
        ],
        "notes": [
            "Contact Center agents are sensitive to jitter/loss — enforce QoS on the agent subnet.",
        ],
    },
    "paging": {
        "label": "Overhead / SIP Paging & Analog (ATA)",
        "requirements": [
            "SIP over TLS (TCP 5096/5097) and SRTP media (UDP 20000–64000) from the paging adapter / ATA.",
            "HTTPS (TCP 443) provisioning to the RingCentral provisioning server.",
        ],
        "notes": [
            "Third-party SIP paging devices (e.g. Algo, CyberData) must support the RingCentral certified firmware and TLS/SRTP.",
        ],
    },
    "fax": {
        "label": "Cloud Fax / Fax over IP",
        "requirements": [
            "HTTPS (TCP 443) for online fax and the RingCentral apps.",
            "For fax hardware via ATA: SIP/TLS + SRTP as per Desk Phones, with T.38 disabled (RingCentral uses G.711 pass-through).",
        ],
        "notes": [],
    },
}

# Desk-phone vendor provisioning detail (only rendered when "deskphones" is selected
# and the specific vendor is ticked).
# key -> {label, fqdns[], notes}
DESKPHONE_VENDORS = {
    "poly": {
        "label": "Poly / Polycom (VVX, Edge E, Trio, Rove)",
        "fqdns": ["https://pp.ringcentral.com", "https://cp.ringcentral.com"],
        "notes": "Provisioning and firmware over HTTPS 443. Do not SSL-inspect these hosts.",
    },
    "yealink": {
        "label": "Yealink (T4x, T5x, W-series DECT)",
        "fqdns": ["https://yp.ringcentral.com"],
        "notes": "Provisioning and firmware over HTTPS 443. DECT base stations follow the same path.",
    },
    "cisco": {
        "label": "Cisco (8811 / 8861 and certified models)",
        "fqdns": ["https://sp.ringcentral.com"],
        "notes": "Confirm the model is on the current RingCentral certified device list before deployment.",
    },
    "avaya": {
        "label": "Avaya (J-series with RingCentral)",
        "fqdns": ["https://sp.ringcentral.com"],
        "notes": "RingCentral-branded firmware required; standard Avaya firmware will not register.",
    },
    "unify": {
        "label": "Unify / Mitel (legacy certified)",
        "fqdns": ["https://sp.ringcentral.com"],
        "notes": "Legacy — verify continued certification before relying on these models.",
    },
    "dect": {
        "label": "Cordless / DECT (multi-cell)",
        "fqdns": ["https://yp.ringcentral.com", "https://pp.ringcentral.com"],
        "notes": "Multi-cell DECT needs the base stations on the voice VLAN with reliable multicast for handover.",
    },
    "ata": {
        "label": "ATA / Analog Telephone Adapters",
        "fqdns": ["https://sp.ringcentral.com"],
        "notes": "For analog phones, fax machines and paging. Disable T.38; use G.711 pass-through.",
    },
}

# ---------------------------------------------------------------------------
# SECTION 3 — Integrations & setup links (tick boxes -> setup guidance)
# key -> {label, portal_path, url, prerequisites[], notes}
# ---------------------------------------------------------------------------

INTEGRATIONS = {
    "sso": {
        "label": "Single Sign-On (SAML 2.0)",
        "portal_path": "Admin Portal → More → Security and Compliance → Single Sign-on",
        "url": "https://support.ringcentral.com/article-v2/Configuring-single-sign-on-in-the-account.html",
        "prerequisites": [
            "Identity Provider metadata (Entra ID / Okta / Ping / Google) XML or URL.",
            "A verified email domain owned by the customer.",
            "Admin access to both the RingCentral Admin Portal and the IdP.",
        ],
        "notes": "Configure the IdP entity ID and ACS/SSO URL from the RingCentral SSO settings, upload IdP metadata, then enable and test with a pilot user before enforcing.",
    },
    "scim": {
        "label": "SCIM — Automated User Provisioning",
        "portal_path": "Admin Portal → More → Security and Compliance → Directory / Automated provisioning",
        "url": "https://support.ringcentral.com/article-v2/Automating-user-provisioning-with-SCIM.html",
        "prerequisites": [
            "SSO configured first (SCIM rides on the same IdP integration).",
            "A RingCentral SCIM bearer token generated from the Admin Portal.",
            "IdP provisioning app (Entra ID / Okta) with attribute mapping.",
        ],
        "notes": "Generate the SCIM token in RingCentral, paste it into the IdP provisioning app along with the SCIM base URL, map attributes, then enable provisioning/de-provisioning.",
    },
    "mst_direct_routing": {
        "label": "Microsoft Teams — Direct Routing (Cloud PBX for Teams)",
        "portal_path": "Admin Portal → Phone System → Microsoft Teams (Cloud PBX)",
        "url": "https://support.ringcentral.com/product/cloud-pbx-microsoft-teams.html",
        "prerequisites": [
            "Microsoft 365 tenant with Teams Phone (Phone System) licences.",
            "Global admin on the Microsoft 365 tenant + RingCentral admin.",
            "A validated domain in Microsoft 365 for the SBC connection.",
            "Users assigned Teams Phone / voice-enabled licences.",
        ],
        "notes": "RingCentral Cloud PBX for Microsoft Teams delivers Direct Routing without a customer-managed SBC. Enable in RingCentral, authorise the Microsoft tenant, then assign numbers and the voice routing policy to users.",
    },
    "mst_embedded": {
        "label": "Microsoft Teams — Embedded App (RingCentral for Teams)",
        "portal_path": "Microsoft Teams Admin Center → Manage apps → RingCentral",
        "url": "https://www.ringcentral.com/apps/rc-for-microsoft-teams",
        "prerequisites": [
            "Teams admin able to approve/allow third-party apps.",
            "RingEX licences for the users.",
        ],
        "notes": "Install RingCentral for Microsoft Teams from the Teams app store / admin center and allow it in the app permission policy. This adds the embedded dialer — it does not require Direct Routing.",
    },
    "crm_salesforce": {
        "label": "CRM — Salesforce",
        "portal_path": "Salesforce AppExchange → RingCentral for Salesforce / RingCX for Salesforce",
        "url": "https://www.ringcentral.com/apps/rc-for-salesforce",
        "prerequisites": [
            "Salesforce admin (System Administrator profile).",
            "RingCentral edition that supports CRM integration.",
        ],
        "notes": "Install the managed package from AppExchange, add the CTI softphone to the call center, assign users, and configure click-to-dial / screen-pop.",
    },
    "crm_dynamics": {
        "label": "CRM — Microsoft Dynamics 365",
        "portal_path": "RingCentral App Gallery → RingCentral for Microsoft Dynamics 365",
        "url": "https://www.ringcentral.com/apps/rc-for-dynamics",
        "prerequisites": [
            "Dynamics 365 admin access.",
            "RingCentral CRM-capable licences.",
        ],
        "notes": "Deploy the RingCentral for Dynamics 365 solution, enable the embedded softphone, and map call logging to the relevant Dynamics entities.",
    },
    "crm_servicenow": {
        "label": "CRM — ServiceNow",
        "portal_path": "ServiceNow Store → RingCentral for ServiceNow",
        "url": "https://www.ringcentral.com/apps/rc-for-servicenow",
        "prerequisites": [
            "ServiceNow admin (admin role).",
            "RingCentral CRM-capable licences.",
        ],
        "notes": "Install from the ServiceNow Store, configure the OpenFrame/CTI phone, and enable incident/case screen-pop and click-to-dial.",
    },
    "crm_zendesk": {
        "label": "CRM — Zendesk",
        "portal_path": "Zendesk Marketplace → RingCentral for Zendesk",
        "url": "https://www.ringcentral.com/apps/rc-for-zendesk",
        "prerequisites": [
            "Zendesk admin access.",
            "RingCentral CRM-capable licences.",
        ],
        "notes": "Add the RingCentral app from the Zendesk Marketplace, then enable click-to-dial and automatic ticket creation / screen-pop.",
    },
    "crm_hubspot": {
        "label": "CRM — HubSpot",
        "portal_path": "HubSpot Marketplace → RingCentral for HubSpot",
        "url": "https://www.ringcentral.com/apps/rc-for-hubspot",
        "prerequisites": [
            "HubSpot admin (Super Admin).",
            "RingCentral CRM-capable licences.",
        ],
        "notes": "Connect the RingCentral app from the HubSpot Marketplace and enable calling, click-to-dial and call logging against contacts.",
    },
    "crm_google": {
        "label": "Google Workspace",
        "portal_path": "RingCentral App Gallery → RingCentral for Google Workspace",
        "url": "https://www.ringcentral.com/apps/rc-for-google",
        "prerequisites": [
            "Google Workspace admin.",
            "RingCentral licences.",
        ],
        "notes": "Install the RingCentral extension/add-on, authorise Google, and enable click-to-dial and calendar/meeting scheduling.",
    },
}


# ---------------------------------------------------------------------------
# Document assembly
# ---------------------------------------------------------------------------

def _esc(value):
    return html.escape(str(value if value is not None else ""))


def _table(headers, rows):
    thead = "".join(f"<th>{_esc(h)}</th>" for h in headers)
    body = ""
    for row in rows:
        cells = "".join(f"<td>{_esc(c)}</td>" for c in row)
        body += f"<tr>{cells}</tr>"
    return (
        f'<table class="nr-table"><thead><tr>{thead}</tr></thead>'
        f"<tbody>{body}</tbody></table>"
    )


def _ul(items):
    lis = "".join(f"<li>{_esc(i)}</li>" for i in items)
    return f"<ul class=\"nr-list\">{lis}</ul>"


def _standard_section():
    parts = ['<section class="nr-section"><h2>1. Standard requirements — all RingCentral services</h2>']
    parts.append(
        '<p class="nr-lead">The following baseline applies to every RingCentral UC '
        "endpoint on the customer network and must be met regardless of the specific "
        "services or devices deployed.</p>"
    )

    parts.append("<h3>1.1 Bandwidth</h3>")
    parts.append(_table(["Stream", "Requirement"], STANDARD_BANDWIDTH))

    parts.append("<h3>1.2 Network quality thresholds</h3>")
    parts.append(_table(["Metric", "Target"], STANDARD_QUALITY))

    parts.append("<h3>1.3 Firewall ports</h3>")
    parts.append(_table(["Function", "Protocol", "Port(s)", "Direction"], STANDARD_PORTS))

    parts.append("<h3>1.4 IP ranges (supernets) to whitelist</h3>")
    parts.append(
        '<p class="nr-note">Whitelist these supernets for both signalling and media in '
        "the firewall ACLs. Apply the QoS policy against the same ranges.</p>"
    )
    parts.append(_table(["Supernet", "Region"], STANDARD_SUPERNETS))

    parts.append("<h3>1.5 Domains to whitelist</h3>")
    parts.append(_ul(STANDARD_DOMAINS))

    parts.append("<h3>1.6 Quality of Service (QoS / DSCP)</h3>")
    parts.append(_table(["Traffic", "DSCP class", "DSCP value"], STANDARD_QOS))

    parts.append("<h3>1.7 General configuration notes</h3>")
    parts.append(_ul(STANDARD_GENERAL_NOTES))

    parts.append("</section>")
    return "".join(parts)


def _services_section(selected_services, selected_vendors):
    chosen = [(k, SERVICES[k]) for k in selected_services if k in SERVICES]
    if not chosen:
        return ""

    parts = ['<section class="nr-section"><h2>2. Selected services &amp; endpoints</h2>']
    parts.append(
        '<p class="nr-lead">Requirements below are in addition to the standard '
        "requirements in Section 1, and apply to the services selected for this customer.</p>"
    )

    idx = 0
    for key, svc in chosen:
        idx += 1
        parts.append(f'<div class="nr-block"><h3>2.{idx} {_esc(svc["label"])}</h3>')
        parts.append(_ul(svc["requirements"]))
        if svc.get("notes"):
            parts.append('<p class="nr-note"><strong>Notes:</strong></p>')
            parts.append(_ul(svc["notes"]))

        # Desk-phone vendor provisioning detail nests under the deskphones service.
        if key == "deskphones":
            vendors = [(vk, DESKPHONE_VENDORS[vk]) for vk in selected_vendors if vk in DESKPHONE_VENDORS]
            if vendors:
                rows = []
                notes = []
                for vk, v in vendors:
                    rows.append([v["label"], ", ".join(v["fqdns"])])
                    if v.get("notes"):
                        notes.append(f'{v["label"]}: {v["notes"]}')
                parts.append('<p class="nr-note"><strong>Provisioning servers by vendor (HTTPS 443):</strong></p>')
                parts.append(_table(["Vendor / models", "Provisioning FQDN(s)"], rows))
                if notes:
                    parts.append(_ul(notes))
            else:
                parts.append(
                    '<p class="nr-note">No specific desk-phone vendor selected — confirm the '
                    "vendor and add its provisioning FQDN before issuing this document.</p>"
                )
        parts.append("</div>")

    parts.append("</section>")
    return "".join(parts)


def _integrations_section(selected_integrations):
    chosen = [(k, INTEGRATIONS[k]) for k in selected_integrations if k in INTEGRATIONS]
    if not chosen:
        return ""

    parts = ['<section class="nr-section"><h2>3. Integrations &amp; setup links</h2>']
    parts.append(
        '<p class="nr-lead">The following integrations were selected for this customer. '
        "Each entry lists where to configure it, the prerequisites, and a link to the "
        "official setup guide.</p>"
    )

    idx = 0
    for key, item in chosen:
        idx += 1
        url = item["url"]
        parts.append(f'<div class="nr-block"><h3>3.{idx} {_esc(item["label"])}</h3>')
        parts.append(
            '<p class="nr-kv"><span class="nr-k">Configure at</span>'
            f'<span class="nr-v">{_esc(item["portal_path"])}</span></p>'
        )
        parts.append(
            '<p class="nr-kv"><span class="nr-k">Setup guide</span>'
            f'<span class="nr-v"><a href="{_esc(url)}" target="_blank" rel="noopener">{_esc(url)}</a></span></p>'
        )
        if item.get("prerequisites"):
            parts.append('<p class="nr-note"><strong>Prerequisites:</strong></p>')
            parts.append(_ul(item["prerequisites"]))
        if item.get("notes"):
            parts.append(f'<p class="nr-note"><strong>Setup:</strong> {_esc(item["notes"])}</p>')
        parts.append("</div>")

    parts.append("</section>")
    return "".join(parts)


def build_document(payload):
    """
    Assemble the customer-specific network requirements document as an HTML
    fragment (the body only — the frontend wraps it for preview / print).

    payload keys:
      customer_name (str), site (str), prepared_by (str), notes (str),
      services (list[str]), deskphone_vendors (list[str]),
      integrations (list[str])
    """
    payload = payload or {}
    customer_name = (payload.get("customer_name") or "").strip() or "Customer"
    site = (payload.get("site") or "").strip()
    prepared_by = (payload.get("prepared_by") or "").strip()
    extra_notes = (payload.get("notes") or "").strip()
    services = payload.get("services") or []
    vendors = payload.get("deskphone_vendors") or []
    integrations = payload.get("integrations") or []

    today = datetime.utcnow().strftime("%d %B %Y")

    # --- Header / metadata ---
    meta_rows = [["Customer", customer_name]]
    if site:
        meta_rows.append(["Site / location", site])
    meta_rows.append(["Document date", today])
    if prepared_by:
        meta_rows.append(["Prepared by", prepared_by])
    meta_rows.append(["Product", "RingCentral RingEX (UC)"])

    head = [
        '<header class="nr-header">',
        f'<h1>Network Requirements — {_esc(customer_name)}</h1>',
        '<p class="nr-sub">RingCentral Unified Communications — customer deployment</p>',
        _table(["Field", "Detail"], meta_rows),
        '<p class="nr-warning">This document is generated from RingCentral\'s published '
        "network requirements. Values (supernets, ports and provisioning FQDNs) are "
        "revised periodically — verify against the current RingCentral KB before issuing "
        f'to the customer: <a href="{_esc(KB_NETWORK_REQUIREMENTS)}" target="_blank" '
        f'rel="noopener">Network requirements</a> · '
        f'<a href="{_esc(KB_QOS)}" target="_blank" rel="noopener">QoS guidelines</a>.</p>',
        "</header>",
    ]

    body_parts = ["".join(head)]

    # Table of contents-ish scope summary
    scope_bits = []
    if services:
        scope_bits.append(f"{len([s for s in services if s in SERVICES])} service(s)")
    if integrations:
        scope_bits.append(f"{len([i for i in integrations if i in INTEGRATIONS])} integration(s)")
    if scope_bits:
        body_parts.append(
            f'<p class="nr-scope">Scope for this customer: {_esc(", ".join(scope_bits))}, '
            "plus the standard requirements that apply to all services.</p>"
        )

    body_parts.append(_standard_section())
    body_parts.append(_services_section(services, vendors))
    body_parts.append(_integrations_section(integrations))

    if extra_notes:
        body_parts.append(
            '<section class="nr-section"><h2>4. Customer-specific notes</h2>'
            f'<p class="nr-freetext">{_esc(extra_notes)}</p></section>'
        )

    body_parts.append(
        '<footer class="nr-footer">Generated with the RCAU API Tools — Network '
        f"Requirements generator on {_esc(today)}. Confirm all values against the live "
        "RingCentral documentation before customer delivery.</footer>"
    )

    return "".join(body_parts)


def get_catalog():
    """Expose the selectable options so the frontend can stay in sync if needed."""
    return {
        "services": [{"key": k, "label": v["label"]} for k, v in SERVICES.items()],
        "deskphone_vendors": [{"key": k, "label": v["label"]} for k, v in DESKPHONE_VENDORS.items()],
        "integrations": [{"key": k, "label": v["label"]} for k, v in INTEGRATIONS.items()],
    }
