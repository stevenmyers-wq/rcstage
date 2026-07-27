"""
Network Requirements document generator for RingCentral UC.

This module is a *self-contained content library*. It holds RingCentral's
published RingEX network requirements and turns a set of tick-box selections
into a customer-specific, print-ready HTML document. There are no RingCentral
API calls here — the document is assembled purely from the catalogues below.

SOURCE OF TRUTH
---------------
The data below is transcribed from RingCentral's "Network requirements" article
for RingEX (support.ringcentral.com → Resources → Network requirements). Port
tables, supernets and provisioning FQDNs are reproduced faithfully from that
article. Bandwidth / latency / jitter / DSCP figures are intentionally NOT
listed here — RingCentral keeps those in the separate "Quality of Service
Guidelines" document, so this tool references that document rather than
reproducing (and risking staling) numbers it cannot verify.

Every generated document carries a source + date line and a link back to the
live article so the engineer can confirm the current values before delivery.
"""

import html
from datetime import datetime

# ---------------------------------------------------------------------------
# Reference links (verified live paths)
# ---------------------------------------------------------------------------
KB_NETWORK_REQUIREMENTS = "https://support.ringcentral.com/network-and-system-requirements/network-requirements.html"
KB_SYSTEM_REQUIREMENTS = "https://support.ringcentral.com/network-and-system-requirements/system-requirements.html"
KB_DESKPHONES = "https://support.ringcentral.com/getting-started/desk-phones.html"
ADMIN_PORTAL = "https://service.ringcentral.com/"
APP_GALLERY = "https://www.ringcentral.com/apps/"
MS_DIRECT_ROUTING_PLAN = "https://learn.microsoft.com/en-us/microsoftteams/direct-routing-plan"

# Verified RingCentral KB article URLs (confirmed on the RingCentral support site).
KB_SSO_ENTRA = "https://support.ringcentral.com/article-v2/Using-Microsoft-Entra-ID-for-single-sign-on.html?brand=RingCentral&product=RingEX&language=en_US"
KB_SCIM_ENTRA = "https://support.ringcentral.com/article-v2/Using-Azure-Active-Directory-for-automatic-user-provisioning.html?brand=RingCentral&product=RingEX&language=en_US"
KB_GOOGLE_PROVISIONING = "https://support.ringcentral.com/article-v2/10587-G-Suite-Auto-User-Provisioning.html?brand=RC_US&product=RingEX&language=en_US"
KB_MST_DIRECT_ROUTING = "https://support.ringcentral.com/article-v2/Setting-up-RingCentral-Cloud-PBX-for-Microsoft-Teams.html?brand=RingCentral&product=RingEX&language=en_US"
KB_MST_EMBEDDED = "https://support.ringcentral.com/article-v2/Install-RingCentral-for-Teams-2-0-from-Microsoft-Admin-Center.html?brand=RingCentral&product=RingEX&language=en_US"
KB_CRM_SALESFORCE = "https://support.ringcentral.com/article-v2/10676-ringcentral-salesforce-app-install.html?brand=RC_US&product=RingEX&language=en_US"
KB_CRM_DYNAMICS = "https://support.ringcentral.com/article-v2/9579.html?brand=RC_US&product=RingEX&language=en_US"
KB_CRM_SERVICENOW = "https://support.ringcentral.com/article-v2/RingCentral-ServiceNow-Overview.html?brand=RC_US&product=RingEX&language=en_US"
KB_CRM_ZENDESK = "https://support.ringcentral.com/article-v2/Connecting-your-RingCentral-account-to-Zendesk.html?brand=RingCentral&product=RingEX&language=en_US"
KB_CRM_HUBSPOT = "https://support.ringcentral.com/article-v2/Installing-RingCentral-for-Hubspot.html?brand=RingCentral&product=RingEX&language=en_US"
KB_CRM_GOOGLE = "https://support.ringcentral.com/article-v2/Using-the-RingCentral-for-Google-Chrome-extension.html?brand=RingCentral&product=RingEX&language=en_US"

# ---------------------------------------------------------------------------
# SECTION 1 — Standard requirements (always included in every document)
# These apply across all RingCentral UC services.
# ---------------------------------------------------------------------------

# Table 1.1 — Advertised IP supernets (BGP-advertised by the RingCentral cloud)
SUPERNETS = [
    "66.81.240.0/20",
    "80.81.128.0/20",
    "103.44.68.0/22",
    "103.129.102.0/23",
    "104.245.56.0/21",
    "185.23.248.0/22",
    "192.209.24.0/21",
    "199.68.212.0/22",
    "199.255.120.0/22",
    "208.87.40.0/22",
]

SUPERNET_NOTES = [
    "The supernets are advertised by the RingCentral cloud using BGP to support Unified Communication Services. All voice and video media, signalling traffic and some other traffic use supernet addresses.",
    "Your enterprise network must accept the supernets at all locations where UC services are used, and must use them for: firewall rules (signalling and media ports); DSCP markings per the QoS Guidelines; and selectively disabling Layer-7 functions such as Deep Packet Inspection for UDP traffic to/from the UC cloud.",
    "A domain marked (S) in the tables resolves to a supernet address — for firewall/proxy configuration the (S) marker itself should be ignored. If a .com domain resolves to a supernet it is part of 66.81.240.0/20; if a .biz domain resolves to a supernet it is part of 80.81.128.0/20.",
    "Additional requirements apply for enterprises with private connections to the RingCentral cloud — contact RingCentral Support.",
]

# Table 2.1 — Common cloud services that must always be whitelisted.
# (Purpose, Protocol, Domain(s), Ports)
COMMON_CLOUD_SERVICES = [
    ("Company website", "HTTPS", "www.ringcentral.com (S)", "TCP 443"),
    ("Service status portal", "HTTPS", "status.ringcentral.com (S)", "TCP 443"),
    ("Customer support", "HTTPS", "support.ringcentral.com (S)", "TCP 443"),
    ("Federated enterprise accounts portal", "HTTPS", "enterprise-service.ringcentral.com (S) / .co.uk (S) / .eu (S)", "TCP 443"),
    ("Linked account management portal", "HTTPS", "accounts.ringcentral.com (S)", "TCP 443"),
    ("Admin Portal", "HTTPS", "service.ringcentral.com (S) / .co.uk (S) / .eu (S)", "TCP 443"),
    ("Administrator/User account login portal", "HTTPS", "login.ringcentral.com (S) / .co.uk (S) / .eu (S)", "TCP 443"),
    ("Analytics portal", "HTTPS", "analytics.ringcentral.com / .ca / .co.uk / .eu", "TCP 443"),
    ("Developer portal", "HTTPS", "developers.ringcentral.com (S)", "TCP 443"),
]

COMMON_CLOUD_NOTES = [
    "Whitelist only the set of services you actually use (for example, ignore the EU Analytics domain if you do not use the European Analytics portal).",
    "Domains that resolve to supernet addresses (marked (S)) do not need to be whitelisted separately, provided the supernets are whitelisted in the endpoint tables.",
    "The Administrator/User account portal, Admin Portal and Analytics portals may be country- or region-specific for compliance; .com is used in North America / globally, while .eu / .co.uk refer to Europe / UK.",
]

# Section 18 / 19 / 20 — DNS, NAT and security software
DNS_NOTES = [
    "All endpoints and services require access to a public DNS. Endpoints rely on DNS to resolve the provisioning service domain name (e.g. pp.ringcentral.com).",
    "If you use a private DNS, it must perform forward lookups to an internet-based DNS.",
]

NAT_NOTES = [
    "Configure a minimum NAT timeout to ensure correct hardphone operation.",
    "Cisco phones send a REGISTER refresh every 4 minutes.",
    "Poly phones re-register every 5 minutes — set the NAT entry expiration timeout to more than 5 minutes.",
    "For Android push notifications over ports 5228-5230, implement a 30-minute or larger NAT / SPI timeout for reliable connectivity and reduced battery consumption.",
]

SECURITY_SOFTWARE_NOTES = [
    "Configure cloud-based security software (network firewalls and web proxies) to whitelist the domains listed in the endpoint tables.",
    "Allow incoming and outgoing RTP/SRTP packets (audio and video) on both endpoint security software and network firewall devices.",
]

# Section 21 / 22 — QoS and VLAN (reference the dedicated guidelines; do not invent DSCP values)
QOS_NOTES = [
    "Follow RingCentral's Quality of Service Guidelines to ensure correct prioritisation of traffic — otherwise either party may experience intermittent call-control or media-quality issues.",
    "Apply DSCP markings in IP packet headers according to the QoS Guidelines, using the supernets in Section 1 to match the traffic.",
    "QoS markings are only honoured if trusted end-to-end across every hop the traffic traverses.",
    "The QoS Guidelines are linked directly from the Network requirements article.",
]

VLAN_NOTES = [
    "Follow RingCentral's VLAN configuration guidelines to ensure VLANs are correctly configured for IP phones (see the Desk Phones endpoint section).",
    "Where possible, place phones on a dedicated voice VLAN with LLDP-MED / 802.1p.",
]

# Section 23 — VPN & firewall/proxy recommendations
VPN_PROXY_NOTES = [
    "VPN and firewall platforms can degrade real-time RingCentral voice and video when proxied — bypass RingCentral traffic from inline proxies.",
    "Turn on SSL-inspection bypass for RingCentral traffic through your proxy / secure web gateway.",
    "Add a Proxy Auto-Configuration (PAC) file that returns DIRECT for the RingCentral domains below, so real-time traffic bypasses the corporate proxy.",
    "Create VPN and firewall bypass rules for the RingCentral supernet ranges in Section 1.",
]

# Section 23 — PAC bypass domains (return DIRECT)
PAC_BYPASS_DOMAINS = [
    "*.ringcentral.com", "*.ringcentral.biz", "*.rcv.com", "*.glip.com",
    "*.pubnubapi.com", "*.testrtc.com", "*.pubnub.com", "*.pndsn.com",
    "*.mixpanel.com", "*.pendo.io", "*.segment.com", "*.mxpnl.com",
    "*.sentry.io", "*.outreach.io", "*.outreach.cloud", "*.mux.com",
    "*.stream-io-api.com", "*.live-video.net", "*.addevent.com", "*.pusher.com",
    "*.cloudfront.net", "*.segment.io", "*.launchdarkly.com",
    "*.ringcentral.eu", "*.ringcentral.co.uk", "*.ringcentral.ca", "*.ringcentral.in",
]

# ---------------------------------------------------------------------------
# SECTION 2 — Service / endpoint selectable requirements (tick boxes)
# key -> {label, rows[(purpose, protocol, domain, ports)], notes[]}
# ---------------------------------------------------------------------------

_PUBNUB = "ringcentral.pubnubapi.com and ringcentral-0 … ringcentral-9.pubnubapi.com"

SERVICES = {
    "ringcentral_app": {
        "label": "RingCentral App — web, desktop & mobile (Table 4.1)",
        "rows": [
            ("Media / media secured & media access control", "RTP/SRTP (DTLS) and STUN", "IP supernets", "UDP 20000-64999; for STUN: UDP 443 and UDP 19302"),
            ("Signalling — mobile app", "SIP/TCP", "IP supernets", "TCP 5091"),
            ("Signalling secured — mobile app", "SIP/TLS", "IP supernets", "TCP 5097"),
            ("Signalling secured — mobile app", "SIP/WSS/TLS", "IP supernets", "TCP 443"),
            ("Signalling secured — desktop & web app", "SIP/WSS/DTLS", "IP supernets", "TCP 8083"),
            ("Signalling secured — desktop app", "SIP/TLS", "IP supernets", "TCP 5095"),
            ("Platform API for media services", "HTTPS", "media.ringcentral.com (S)", "TCP 443"),
            ("Discovery service API (login)", "HTTPS", "discovery.ringcentral.biz (S)", "TCP 443"),
            ("IOVATION SDK (two-factor login)", "HTTPS", "mpsnare.iesnare.com, ringcentral.112.2o7.net", "TCP 443"),
            ("Application file upload/download", "HTTPS", "glip-vault-1.s3.amazonaws.com, glip-vault-1.s3-accelerate.amazonaws.com", "TCP 443"),
            ("Application service API — web/desktop", "HTTPS", "api-ucc.ringcentral.com (S)", "TCP 443"),
            ("Application service API — mobile", "HTTPS", "api-mucc.ringcentral.com (S)", "TCP 443"),
            ("Messaging service API", "HTTPS", "*.glip.com, mvp.ringcentral.com, dl.mvp.ringcentral.com", "TCP 443"),
            ("Push notifications", "HTTPS", _PUBNUB, "TCP 443"),
            ("WebSocket push notifications — US/EU/APAC", "WSS", "us-01…06 / eu-01 / apac-01 .ws-api.ringcentral.com (S)", "TCP 443"),
            ("Android app push notifications", "HTTPS", "mtalk.google.com", "TCP 443, 5228, 5229, 5230"),
            ("iOS app push notifications", "HTTPS", "api.push.apple.com", "TCP 443, 2197, 5223"),
            ("Feature enablement control", "HTTPS", "*.launchdarkly.com", "TCP 443"),
            ("Software & provisioning updates", "HTTPS", "*.cloudfront.net", "TCP 443"),
            ("Client issue reporting", "HTTPS", "cpr-na.ringcentral.com / cpr-eu.ringcentral.com", "TCP 443"),
            ("QoS & usage reporting", "HTTPS", "edr.ringcentral.com / edr-eu.ringcentral.com", "TCP 443"),
        ],
        "notes": [
            "For RingCentral Video within the app, also apply the RingCentral Video table.",
            "Messaging content (Giphy/embed.ly), Help/community and LaunchDarkly/IOVATION rows were required prior to RingCentral App v23.4.x — confirm against the current article for your deployment.",
        ],
    },
    "rc_video": {
        "label": "RingCentral Video App — web, desktop & mobile (Table 5.1)",
        "rows": [
            ("Media secured", "SRTP", "IP supernets", "UDP 10001-10010 (default) or UDP 8801-8810"),
            ("Media secured — UDP unavailable fallback", "SRTP", "IP supernets", "TCP 443 (avoid regular use — affects voice quality)"),
            ("STUN", "STUN", "IP supernets", "UDP 443, UDP 19302, UDP 3478"),
            ("TURN / TURNS", "TURN/TURNS", "IP supernets", "UDP 3478, TCP 3478, UDP 443, TCP 443"),
            ("Signalling secured", "HTTPS/WSS/TLS", "IP supernets", "TCP 443"),
            ("Platform API for media services", "HTTPS", "media.ringcentral.com (S)", "TCP 443"),
            ("Static resources (HTML/JS/images)", "HTTPS", "v.ringcentral.com", "TCP 443"),
            ("Connect platform API", "HTTPS", "api-meet.ringcentral.com (S), api.ringcentral.com (S), api-mucc.ringcentral.com (S)", "TCP 443"),
            ("Statistics collector", "HTTPS", "edr.ringcentral.com", "TCP 443"),
            ("Presence / call log / voicemail notifications", "HTTPS", _PUBNUB, "TCP 443"),
            ("Application configuration", "HTTPS", "downloads.ringcentral.com", "TCP 443"),
            ("Application download / update", "HTTPS", "app.ringcentral.com", "TCP 443"),
            ("Network connectivity test app", "HTTPS", "rcv.testrtc.com (api.nettest / kong / *.turn / *.speed .testrtc.com)", "TCP 443, UDP 443"),
        ],
        "notes": [
            "Allow incoming and outgoing RTP/SRTP (audio and video) on both endpoint security software and network firewalls.",
            "You need not whitelist the Video web client if only using desktop/mobile. Whitelist the connectivity-test app so users can test their network.",
        ],
    },
    "rc_webinar": {
        "label": "RingCentral Webinar — host & attendee (Table 5.2)",
        "rows": [
            ("RingCentral Video", "—", "Apply RingCentral Video (Table 5.1)", "See Table 5.1"),
            ("Fetch webinar live-streaming media segments", "HTTPS", "*.cloudfront.net", "TCP 443"),
        ],
        "notes": [
            "RingCentral Webinar is based on RingCentral Video — apply the Video table for both host and attendee clients.",
            "If Cloudfront is already whitelisted for the RingCentral App, it need not be whitelisted again.",
        ],
    },
    "rc_rooms": {
        "label": "RingCentral Rooms (Table 5.3)",
        "rows": [
            ("RingCentral Video — media secured", "SRTP", "IP supernets", "UDP 10001-10010 (default); UDP 443, 19302 (STUN); TCP 443 (UDP fallback)"),
            ("RingCentral Video — signalling secured", "HTTPS/WSS/TLS", "IP supernets", "TCP 443"),
            ("SIP registration service", "HTTPS/TLS", "*.ringcentral.com", "TCP 8085-8090"),
            ("Rooms host device", "HTTPS", "Internal enterprise private IP (no WAN traversal)", "TCP 9520-9530"),
            ("Application software updates", "HTTPS", "rooms.ringcentral.com", "TCP 443"),
            ("Equipment vendor software updates", "HTTPS", "swupdate.lens.poly.com, yl-us-dmfile.yealinkops.com, www.logitech.com", "TCP 443"),
            ("3rd-party meeting — MS Teams", "SIP/TLS + SRTP/UDP", "rc.pex.ms", "5060, 5061; SRTP UDP 10000-65535"),
            ("3rd-party meeting — Webex", "SIP/TLS + SRTP/UDP", "webex.com, wwt.webex.com", "5060-5070; SRTP UDP 36000-59999"),
            ("3rd-party meeting — Zoom", "SIP/TLS + SRTP/UDP", "wcrc/ecrc.ringcentral.com, zoomcrc.com, zm*.us domains", "5060-5061; SRTP UDP 3000-4000"),
            ("3rd-party meeting — Google", "SIP/TLS + SRTP/UDP", "rc.pex.ms", "5060, 5061; SRTP UDP 10000-65535"),
        ],
        "notes": [
            "RingCentral App, Video and Rooms share many of the same domains.",
        ],
    },
    "sip_room_connector": {
        "label": "SIP Room Connector (Table 5.4)",
        "rows": [
            ("Media", "RTP/SRTP", "IP supernets", "UDP 10001-10010 (default); for STUN UDP 443 and UDP 19302"),
            ("Signalling", "SIP", "sip.rcv.com (region-independent); ws/es/nld/deu/sgp/aus/jpn.rcv.com", "UDP 5060 or TCP 5060"),
            ("Signalling secured", "SIP/TLS", "sip.rcv.com (region-independent); regional *.rcv.com", "TCP 5061"),
        ],
        "notes": [
            "Whitelist the region-independent domain plus the regional domain only where a Room Connector is used.",
            "Customer video devices determine whether connectivity is secured or unsecured.",
        ],
    },
    "rc_events": {
        "label": "RingCentral Events (Table 5.5)",
        "rows": [
            ("Network connectivity settings", "—", "See RingCentral Events 'Network Connectivity Settings' documentation", "—"),
        ],
        "notes": [
            "Refer to the dedicated 'Network Connectivity Settings for RingCentral Events' article for the current list.",
        ],
    },
    "deskphones": {
        "label": "RingCentral IP Phones — desk, conference & cordless (Table 6.1)",
        "rows": [
            ("Media and media secured", "RTP/SRTP", "IP supernets", "UDP 20000-64999"),
            ("Signalling", "SIP", "IP supernets", "TCP 5090, TCP 5099**; UDP 5090, UDP 5099**"),
            ("Signalling secured", "SIP/TLS", "IP supernets", "TCP 5096, TCP 5098**"),
            ("Network time service", "NTP", "ntp1.ringcentral.com (S), ntp2.ringcentral.com (S)", "UDP 123"),
            ("LDAP directory service", "LDAP", "cd.ringcentral.com (S)", "TCP 636"),
        ],
        "notes": [
            "**Ports 5098 and 5099 are opened for Busy Lamp Appearance only when using line sharing. BLA uses the signalling ports and standard SIP NOTIFY packets — no separate ports.",
            "All listed phone-brand domains resolve to IP addresses in the 66.81.240.0/20 supernet.",
            "Endpoints require public DNS to resolve the provisioning domain (e.g. pp.ringcentral.com). Set NAT timeouts above the phone re-registration interval (Cisco 4 min, Poly 5 min).",
            "Some third-party devices (e.g. Poly IP7000 speakerphone) do not support signalling/media encryption and should be avoided where full security is required.",
        ],
    },
    "softphone_desktop": {
        "label": "RingCentral Softphone App — desktop (Table 7.1)",
        "rows": [
            ("Media and media secured", "RTP/SRTP", "IP supernets", "UDP 20000-64999; for STUN UDP 443 and UDP 19302"),
            ("Signalling", "SIP", "IP supernets", "TCP 5091"),
            ("Signalling secured", "SIP/TLS", "IP supernets", "TCP 5097"),
            ("Presence / call log / voicemail notifications", "HTTPS", _PUBNUB, "TCP 443"),
            ("Software & provisioning updates", "HTTP/HTTPS", "sp-download.ringcentral.com (S)", "TCP 80, TCP 443"),
            ("Platform API (auth & call features)", "HTTPS", "api-sp.ringcentral.com (S)", "TCP 443"),
            ("Platform API for media services", "HTTPS", "media.ringcentral.com (S)", "TCP 443"),
            ("Google services (contacts & calendar)", "HTTPS", "www.google.com, www.googleapis.com", "TCP 443"),
        ],
        "notes": [],
    },
    "softphone_mobile": {
        "label": "RingCentral Softphone App — mobile, deprecated (Table 8.1)",
        "rows": [
            ("Media", "RTP/SRTP", "IP supernets", "UDP 20000-64999; for STUN UDP 443 and UDP 19302"),
            ("Signalling", "SIP", "IP supernets", "TCP 5091, UDP 5091"),
            ("Signalling secured", "SIP/TLS", "IP supernets", "TCP 5097, TCP 443"),
            ("Signalling (IPv6 client)", "SIP/TLS", "IP supernets", "TCP 5090-5098, TCP 443"),
            ("SIP registration service", "HTTPS", "*.ringcentral.com", "TCP 443"),
            ("Presence / call log / voicemail (Android)", "HTTPS", _PUBNUB, "TCP 443"),
            ("Cloud data synchronisation", "HTTPS", "api-mob.ringcentral.com (S)", "TCP 443"),
            ("Software & provisioning updates", "HTTPS", "*.cloudfront.net", "TCP 443"),
        ],
        "notes": [
            "The softphone mobile app is deprecated and replaced by the RingCentral App (mobile). Only applies to customers still on the deprecated client, on a WiFi network.",
        ],
    },
    "frontline": {
        "label": "RingCentral for Frontline Workers (Push to Talk)",
        "rows": [
            ("Media framework", "—", "Uses RingCentral Video (mobile) as the backend", "See RingCentral Video (Table 5.1)"),
        ],
        "notes": [
            "No additional ports/domains/IPs beyond those already listed for RingCentral Video on mobile.",
        ],
    },
    "teams_embedded": {
        "label": "Microsoft Teams Embedded App — desktop, web & mobile (Table 10.1)",
        "rows": [
            ("Media / media secured & media access control", "RTP/SRTP (DTLS) and STUN", "IP supernets", "UDP 20000-64999; for STUN UDP 443 and UDP 19302"),
            ("Signalling secured — desktop & web", "SIP/WSS/DTLS", "IP supernets", "TCP 8083"),
            ("Platform API for media services", "HTTPS", "media.ringcentral.com (S)", "TCP 443"),
            ("Teams user/presence/contacts & app install", "HTTPS", "graph.microsoft.com", "TCP 443"),
            ("Microsoft OAuth authentication", "HTTPS", "login.microsoftonline.com", "TCP 443"),
            ("Bot notification", "HTTPS", "smba.trafficmanager.net, smba.infra.gcc.teams.microsoft.com", "TCP 443"),
            ("RingCentral apps API (calls/messages/presence/OAuth)", "HTTPS", "api-rcapps.ringcentral.com (S)", "TCP 443"),
            ("Software update", "HTTPS", "teams.ringcentral.com", "TCP 443"),
        ],
        "notes": [
            "This is the embedded dialer/app inside Teams — it is separate from Direct Routing (see the Integrations section).",
        ],
    },
    "archiver": {
        "label": "RingCentral Archiver (Table 12.1)",
        "rows": [
            ("Content archiving (cloud repositories)", "HTTPS", "Box, Dropbox, Google Drive, Smarsh systems", "TCP 443 (does not traverse enterprise network)"),
            ("Content archiving (enterprise SFTP)", "SFTP", "Whitelist RingCentral SFTP client IPs: 3.211.163.136, 3.223.170.110, 34.225.218.68, 34.226.29.169, 34.234.210.244, 34.236.210.8, 34.239.13.99, 35.172.123.110, 52.87.7.127, 54.80.51.95, 54.147.91.15", "TCP 22"),
            ("Content fetching", "HTTPS", "api-rcapps.ringcentral.com (S)", "TCP 443"),
        ],
        "notes": [
            "Any of the listed SFTP client IPs may be dynamically selected to connect to an enterprise SFTP server.",
        ],
    },
    "api": {
        "label": "Application Programming Interface (Table 13.1)",
        "rows": [
            ("Discovery service API", "HTTPS", "discovery.ringcentral.biz (S)", "TCP 443"),
            ("Platform API — RingCentral web portals", "HTTPS", "api.ringcentral.com (S)", "TCP 443"),
            ("Platform API — third-party / customer integrations", "HTTPS", "platform.ringcentral.com (S)", "TCP 443"),
            ("Platform API — media services", "HTTPS", "media.ringcentral.com (S)", "TCP 443"),
            ("Platform API — Softphone desktop", "HTTPS", "api-sp.ringcentral.com (S)", "TCP 443"),
            ("Platform API — Communication Integration apps (MS Teams, Salesforce)", "HTTPS", "api-rcapps.ringcentral.com (S)", "TCP 443"),
            ("Platform API — RingCentral App web/desktop", "HTTPS", "api-ucc.ringcentral.com (S)", "TCP 443"),
            ("Platform API — RingCentral App mobile", "HTTPS", "api-mucc.ringcentral.com (S)", "TCP 443"),
            ("Platform API — Video app desktop/web", "HTTPS", "api-meet.ringcentral.com (S)", "TCP 443"),
        ],
        "notes": [],
    },
    "sip_trunks": {
        "label": "SIP trunks (Table 14.1)",
        "rows": [
            ("Media", "RTP", "Public IPs provided by RingCentral during project definition", "UDP 20000-64999"),
            ("Signalling", "SIP", "Public IPs provided by RingCentral during project definition", "UDP 5060, TCP 5061-5065"),
        ],
        "notes": [],
    },
    "integration_services": {
        "label": "Communication integration services (Table 15.1)",
        "rows": [
            ("Endpoint registration service (WebRTC)", "HTTPS", "sip*.ringcentral.com (S)", "TCP 8083"),
            ("Video scheduling service", "HTTPS", "api-meet.ringcentral.biz (S)", "TCP 443"),
            ("Microsoft Teams integration service", "HTTPS", "teams.ringcentral.com", "TCP 443"),
            ("Slack integration service", "HTTPS", "slack.ringcentral.com", "TCP 443"),
        ],
        "notes": [
            "Whitelist only the services you use, plus the endpoint registration service (registers integration WebRTC endpoints). These services must be augmented with the relevant Platform APIs (Table 13.1).",
        ],
    },
    "exchange_onprem": {
        "label": "On-premises Microsoft Exchange contact sync (Table 16.1)",
        "rows": [
            ("North America", "HTTPS", "3.223.170.110, 54.147.91.15, 3.211.163.136", "TCP 443"),
            ("Europe", "HTTPS", "18.196.95.223, 3.122.161.21, 3.122.122.53", "TCP 443"),
        ],
        "notes": [
            "Whitelist these RingCentral cloud IPs to synchronise on-prem Exchange contacts with RingCentral apps.",
        ],
    },
    "pots_ata": {
        "label": "Analog Terminal Adapter — POTS line replacement (Table 17.1)",
        "rows": [
            ("Media Voice", "RTP/UDP", "199.255.120.197/32, .198/32, .201/32, .202/32", "UDP 20000-64999"),
            ("Signalling Voice", "TLS/TCP", "199.255.120.197/32, .198/32, .201/32, .202/32", "TCP 5096-5097"),
            ("Media Modem/Fax/Alarm", "RTP/UDP", "12.22.54.0/24, 12.111.243.0/24, 128.254.140.0/22", "UDP 20000-64999"),
            ("Signalling Modem/Fax/Alarm", "TLS/TCP", "12.22.54.0/24, 12.111.243.0/24, 128.254.140.0/22", "TCP 7160, TCP 10000, TCP 10100"),
            ("NTP", "UDP", "0.0.0.0/0", "UDP 123"),
            ("WAN failover", "ICMP", "DNS server IP address", "See DNS server documentation"),
            ("Management", "TLS/TCP", "128.254.140.0/22", "TCP 443, TCP 8883"),
        ],
        "notes": [
            "All inbound/outbound communication must originate from the same static IP address. Installation behind a WAN load balancer is not supported.",
            "The POTS-ATA requires NTP; ICMP ping to a public DNS must be whitelisted for WAN failover control.",
            "When NAT is used at the enterprise edge, no inbound traffic needs whitelisting. Without NAT, whitelist Media Voice and Media Modem/Fax/Alarm inbound. Subnets 128.254.140.0/22, 12.111.243.0/24 and 12.22.54.0/24 are accessed at dataremote.com domains.",
        ],
    },
}

# Desk-phone vendor provisioning rows (Table 6.1). Rendered only when the
# "deskphones" service is selected and the vendor is ticked.
# key -> {label, row (purpose, protocol, domain, ports)}
DESKPHONE_VENDORS = {
    "poly": {
        "label": "Poly / Polycom",
        "row": ("Poly phone API, provisioning, firmware", "HTTPS",
                "API: pp.api.ringcentral.com (S); Provisioning: pp.ringcentral.com (S), ztp.polycom.com; Firmware: pp.s3.ringcentral.com, pp.fw.ringcentral.com (S)", "TCP 443"),
    },
    "cisco": {
        "label": "Cisco",
        "row": ("Cisco phone provisioning, firmware", "HTTPS",
                "Provisioning: cp.ringcentral.com (S); Firmware: cp.s3.ringcentral.com, cp.fw.ringcentral.com (S)", "TCP 443"),
    },
    "yealink": {
        "label": "Yealink",
        "row": ("Yealink phone API, provisioning, firmware", "HTTPS",
                "API: yp.api.ringcentral.com (S); Provisioning: rps.yealink.com, rpscloud.yealink.com, yp.ringcentral.com (S); Firmware: yp.s3.ringcentral.com, yp.fw.ringcentral.com (S); Mgmt: ymcs.yealink.com", "TCP 443"),
    },
    "avaya": {
        "label": "Avaya",
        "row": ("Avaya phone API, provisioning, firmware", "HTTPS",
                "API: avaya.api.ringcentral.com (S); Provisioning: des.avaya.com, av.ringcentral.com (S); Firmware: av.s3.ringcentral.com, av.fw.ringcentral.com (S)", "TCP 443"),
    },
    "unify": {
        "label": "Unify",
        "row": ("Unify phone API, provisioning, firmware", "HTTPS",
                "API: unf.api.ringcentral.com (S); Provisioning: cloud-setup.com; Firmware: unf.ringcentral.com (S)", "Provisioning TCP 18443; Firmware TCP 443"),
    },
    "mitel": {
        "label": "Mitel",
        "row": ("Mitel phone API, provisioning, firmware", "HTTPS",
                "API: mtl.api.ringcentral.com (S); Provisioning: mtl.ringcentral.com (S), rcs.aastra.com; Firmware: mtl.s3.ringcentral.com, mtl.fw.ringcentral.com (S)", "TCP 443"),
    },
    "snom": {
        "label": "SNOM",
        "row": ("SNOM phone provisioning, firmware", "HTTPS",
                "Provisioning: snm.ringcentral.com (S), secure-provisioning.snom.com, sraps.snom.com; Firmware: snm.s3.ringcentral.com, snm.fw.ringcentral.com (S)", "TCP 443"),
    },
    "ale": {
        "label": "ALE (Alcatel-Lucent Enterprise)",
        "row": ("ALE phone provisioning, firmware", "HTTPS",
                "Provisioning: devices.eds.al-enterprise.com; Firmware: ale.ringcentral.com (S)", "TCP 443"),
    },
}

# ---------------------------------------------------------------------------
# SECTION 3 — Integrations & setup links (tick boxes -> setup guidance)
# key -> {label, portal_path, url, prerequisites[], notes}
# Network-level requirements for Teams options come from the endpoint tables
# above; this section is the admin/setup guidance the request asked for.
# ---------------------------------------------------------------------------

# Each integration carries:
#   url        — primary setup link (verified KB article where confirmed,
#                otherwise a RingCentral support-site search that lands on the
#                current article; never a guessed article slug)
#   link_label — friendly text for the link
#   url_verified — True if url is a confirmed direct KB article
INTEGRATIONS = {
    "sso": {
        "label": "Single Sign-On (SAML 2.0)",
        "portal_path": "Admin Portal → More → Security and Compliance → Single Sign-on",
        "url": KB_SSO_ENTRA,
        "link_label": "KB: Using Microsoft Entra ID for single sign-on",
        "url_verified": True,
        "prerequisites": [
            "Identity Provider metadata (Entra ID / Okta / Ping / Google) XML or URL.",
            "A verified email domain owned by the customer.",
            "Admin access to both the RingCentral Admin Portal and the IdP.",
        ],
        "notes": "Configure the IdP entity ID and ACS/SSO URL from the RingCentral SSO settings, upload IdP metadata, enable, then test with a pilot user before enforcing. The linked article covers Microsoft Entra ID; for Okta / Ping / Google, search the same IdP name on the RingCentral support site.",
    },
    "scim": {
        "label": "SCIM — Automated User Provisioning",
        "portal_path": "Admin Portal → More → Security and Compliance → Directory / Automated provisioning",
        "url": KB_SCIM_ENTRA,
        "link_label": "KB: Using Microsoft Entra ID for automatic user provisioning",
        "url_verified": True,
        "prerequisites": [
            "SSO configured first (SCIM rides on the same IdP integration).",
            "A RingCentral SCIM bearer token generated from the Admin Portal.",
            "IdP provisioning app (Entra ID / Okta) with attribute mapping.",
        ],
        "notes": "Generate the SCIM token in RingCentral, paste it plus the SCIM base URL into the IdP provisioning app, map attributes, then enable provisioning/de-provisioning. For Google Workspace auto-provisioning see: " + KB_GOOGLE_PROVISIONING,
    },
    "mst_direct_routing": {
        "label": "Microsoft Teams — Direct Routing",
        "portal_path": "Admin Portal → Phone System → Microsoft Teams (Cloud PBX for Teams)",
        "url": KB_MST_DIRECT_ROUTING,
        "link_label": "KB: Setting up RingCentral Cloud PBX for Microsoft Teams",
        "url_verified": True,
        "prerequisites": [
            "Microsoft 365 tenant with Teams Phone (Phone System) licences.",
            "Global admin on the Microsoft 365 tenant + RingCentral admin.",
            "Users assigned Teams Phone / voice-enabled licences.",
        ],
        "notes": "Network firewall for Direct Routing follows Microsoft's guidance (Media & Signalling): " + MS_DIRECT_ROUTING_PLAN + ". Authentication uses login.microsoftonline.com; user data uses graph.microsoft.com; the RingCentral integration uses customer-specific *.cloudpbx.ringcentral.com domains provided during project definition (all TCP 443).",
    },
    "mst_embedded": {
        "label": "Microsoft Teams — Embedded App (RingCentral for Teams)",
        "portal_path": "Microsoft Teams Admin Center → Manage apps → RingCentral",
        "url": KB_MST_EMBEDDED,
        "link_label": "KB: Install RingCentral for Teams 2.0 from Microsoft Admin Center",
        "url_verified": True,
        "prerequisites": [
            "Teams admin able to approve/allow third-party apps.",
            "RingEX licences for the users.",
        ],
        "notes": "Install RingCentral for Microsoft Teams from the Teams app store / admin center and allow it in the app permission policy. Network requirements are in the 'Microsoft Teams Embedded App' endpoint section above. This adds the embedded dialer — it does not require Direct Routing.",
    },
    "crm_salesforce": {
        "label": "CRM — Salesforce",
        "portal_path": "Salesforce AppExchange → RingCentral for Salesforce / RingCX for Salesforce",
        "url": KB_CRM_SALESFORCE,
        "link_label": "KB: RingCentral for Salesforce App — Install",
        "url_verified": True,
        "prerequisites": ["Salesforce admin (System Administrator profile).", "RingCentral edition supporting CRM integration."],
        "notes": "Install the managed package from AppExchange, add the CTI softphone to the call center, assign users, and configure click-to-dial / screen-pop.",
    },
    "crm_dynamics": {
        "label": "CRM — Microsoft Dynamics 365",
        "portal_path": "RingCentral App Gallery → RingCentral for Microsoft Dynamics 365",
        "url": KB_CRM_DYNAMICS,
        "link_label": "KB: Setting up RingCentral for Microsoft Dynamics 365",
        "url_verified": True,
        "prerequisites": ["Dynamics 365 admin access.", "RingCentral CRM-capable licences."],
        "notes": "Deploy the RingCentral for Dynamics 365 solution, enable the embedded softphone, and map call logging to the relevant Dynamics entities.",
    },
    "crm_servicenow": {
        "label": "CRM — ServiceNow",
        "portal_path": "ServiceNow Store → RingCentral for ServiceNow",
        "url": KB_CRM_SERVICENOW,
        "link_label": "KB: RingCentral for ServiceNow Overview",
        "url_verified": True,
        "prerequisites": ["ServiceNow admin (admin role).", "RingCentral CRM-capable licences."],
        "notes": "Install from the ServiceNow Store, configure the OpenFrame/CTI phone, and enable incident/case screen-pop and click-to-dial.",
    },
    "crm_zendesk": {
        "label": "CRM — Zendesk",
        "portal_path": "Zendesk Marketplace → RingCentral for Zendesk",
        "url": KB_CRM_ZENDESK,
        "link_label": "KB: Connecting your RingCentral account to Zendesk",
        "url_verified": True,
        "prerequisites": ["Zendesk admin access.", "RingCentral CRM-capable licences."],
        "notes": "Add the RingCentral app from the Zendesk Marketplace, then enable click-to-dial and automatic ticket creation / screen-pop.",
    },
    "crm_hubspot": {
        "label": "CRM — HubSpot",
        "portal_path": "HubSpot Marketplace → RingCentral for HubSpot",
        "url": KB_CRM_HUBSPOT,
        "link_label": "KB: Installing RingCentral for HubSpot",
        "url_verified": True,
        "prerequisites": ["HubSpot admin (Super Admin).", "RingCentral CRM-capable licences."],
        "notes": "Connect the RingCentral app from the HubSpot Marketplace and enable calling, click-to-dial and call logging against contacts.",
    },
    "crm_google": {
        "label": "Google Workspace",
        "portal_path": "RingCentral App Gallery → RingCentral for Google Workspace",
        "url": KB_CRM_GOOGLE,
        "link_label": "KB: Using the RingCentral for Google Chrome extension",
        "url_verified": True,
        "prerequisites": ["Google Workspace admin.", "RingCentral licences."],
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
    return f'<ul class="nr-list">{lis}</ul>'


def _chips(items):
    spans = "".join(f'<span class="nr-chip">{_esc(i)}</span>' for i in items)
    return f'<div class="nr-chips">{spans}</div>'


def _standard_section():
    parts = ['<section class="nr-section"><h2>1. Standard requirements — all RingCentral services</h2>']
    parts.append(
        '<p class="nr-lead">This baseline applies to every RingCentral RingEX endpoint on the '
        'customer network, regardless of the specific services or devices deployed. Apply the '
        'per-service requirements in Section 2 in addition to this section.</p>'
    )

    parts.append("<h3>1.1 IP supernets</h3>")
    parts.append(_chips(SUPERNETS))
    parts.append(_ul(SUPERNET_NOTES))

    parts.append("<h3>1.2 Common cloud services (always whitelist)</h3>")
    parts.append(_table(["Purpose", "Protocol", "Domain / IP", "Ports"], COMMON_CLOUD_SERVICES))
    parts.append(_ul(COMMON_CLOUD_NOTES))

    parts.append("<h3>1.3 Domain Name Service (DNS)</h3>")
    parts.append(_ul(DNS_NOTES))

    parts.append("<h3>1.4 Network Address Translation (NAT)</h3>")
    parts.append(_ul(NAT_NOTES))

    parts.append("<h3>1.5 Security software</h3>")
    parts.append(_ul(SECURITY_SOFTWARE_NOTES))

    parts.append("<h3>1.6 Quality of Service (QoS)</h3>")
    parts.append(_ul(QOS_NOTES))

    parts.append("<h3>1.7 VLAN configuration</h3>")
    parts.append(_ul(VLAN_NOTES))

    parts.append("<h3>1.8 VPN &amp; firewall / proxy recommendations</h3>")
    parts.append(_ul(VPN_PROXY_NOTES))
    parts.append('<p class="nr-note"><strong>PAC bypass domains (return DIRECT):</strong></p>')
    parts.append(_chips(PAC_BYPASS_DOMAINS))

    parts.append("</section>")
    return "".join(parts)


def _services_section(selected_services, selected_vendors):
    chosen = [(k, SERVICES[k]) for k in selected_services if k in SERVICES]
    if not chosen:
        return ""

    parts = ['<section class="nr-section"><h2>2. Selected services &amp; endpoints</h2>']
    parts.append(
        '<p class="nr-lead">These requirements are in addition to Section 1 and apply to the '
        'services selected for this customer. Domains marked (S) resolve to a supernet address; '
        'the (S) marker should be ignored for firewall/proxy configuration.</p>'
    )

    idx = 0
    for key, svc in chosen:
        idx += 1
        parts.append(f'<div class="nr-block"><h3>2.{idx} {_esc(svc["label"])}</h3>')
        parts.append(_table(["Purpose", "Protocol", "Domain / IP", "Destination ports"], svc["rows"]))
        if svc.get("notes"):
            parts.append(_ul(svc["notes"]))

        # Desk-phone vendor provisioning rows nest under the deskphones service.
        if key == "deskphones":
            vendors = [(vk, DESKPHONE_VENDORS[vk]) for vk in selected_vendors if vk in DESKPHONE_VENDORS]
            if vendors:
                rows = [v["row"] for _, v in vendors]
                parts.append('<p class="nr-note"><strong>Selected desk-phone vendor provisioning (Table 6.1):</strong></p>')
                parts.append(_table(["Purpose", "Protocol", "Domain / IP", "Ports"], rows))
            else:
                parts.append(
                    '<p class="nr-note">No specific desk-phone vendor selected — confirm the vendor and '
                    'add its provisioning/firmware FQDNs (all resolve to the 66.81.240.0/20 supernet) before issuing this document.</p>'
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
        '<p class="nr-lead">The following integrations were selected for this customer. Each entry '
        'lists where to configure it, the prerequisites, and a link to the relevant setup resource.</p>'
    )

    idx = 0
    for key, item in chosen:
        idx += 1
        url = item["url"]
        label = item.get("link_label") or url
        verified = item.get("url_verified")
        badge = "" if verified else ' <span class="nr-searchtag">search link</span>'
        parts.append(f'<div class="nr-block"><h3>3.{idx} {_esc(item["label"])}</h3>')
        parts.append(
            '<p class="nr-kv"><span class="nr-k">Configure at</span>'
            f'<span class="nr-v">{_esc(item["portal_path"])}</span></p>'
        )
        parts.append(
            '<p class="nr-kv"><span class="nr-k">Setup link</span>'
            f'<span class="nr-v"><a href="{_esc(url)}" target="_blank" rel="noopener">{_esc(label)}</a>{badge}</span></p>'
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
        '<p class="nr-sub">RingCentral Unified Communications (RingEX) — customer deployment</p>',
        _table(["Field", "Detail"], meta_rows),
        '<p class="nr-warning">Content is transcribed from RingCentral\'s published RingEX '
        '<strong>Network requirements</strong> article and applies to firewall/proxy configuration. '
        'Bandwidth, latency, jitter and DSCP values are defined in the separate '
        '<strong>Quality of Service Guidelines</strong> and are not reproduced here. Confirm the '
        'current values before issuing to the customer: '
        f'<a href="{_esc(KB_NETWORK_REQUIREMENTS)}" target="_blank" rel="noopener">Network requirements</a>.</p>',
        "</header>",
    ]

    body_parts = ["".join(head)]

    # Scope summary
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
        '<footer class="nr-footer">Generated with the RCAU API Tools — Network Requirements '
        f'generator on {_esc(today)}. Source: RingCentral RingEX Network requirements. '
        'Confirm all values against the live article before customer delivery.</footer>'
    )

    return "".join(body_parts)


def get_catalog():
    """Expose the selectable options so the frontend renders the checkboxes."""
    return {
        "services": [{"key": k, "label": v["label"]} for k, v in SERVICES.items()],
        "deskphone_vendors": [{"key": k, "label": v["label"]} for k, v in DESKPHONE_VENDORS.items()],
        "integrations": [{"key": k, "label": v["label"]} for k, v in INTEGRATIONS.items()],
    }
