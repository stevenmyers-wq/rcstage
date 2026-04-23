# webapp/account_health/utils.py
import time
from datetime import datetime, timezone, timedelta
from webapp.rc_api import rc_api_call


def _api(endpoint, rc_token, method="GET", json=None, params=None):
    """Thin wrapper that always passes the explicit token (no session needed)."""
    try:
        return rc_api_call(endpoint, method=method, json=json, params=params, token=rc_token)
    except Exception as e:
        print(f"[account_health] API error {endpoint}: {e}")
        return None


def _fetch_all_pages(endpoint, rc_token, record_key="records"):
    """Paginate through all pages of a list endpoint."""
    results = []
    page = 1
    while True:
        sep = "&" if "?" in endpoint else "?"
        resp = _api(f"{endpoint}{sep}perPage=250&page={page}", rc_token)
        if not resp:
            break
        results.extend(resp.get(record_key, []))
        nav = resp.get("navigation", {})
        if not nav.get("nextPage"):
            break
        page += 1
        time.sleep(0.05)
    return results


# ------------------------------------------------------------------
# Phase 1 — Config data
# ------------------------------------------------------------------

def collect_config_data(rc_token):
    result = {}

    # Account info
    account = _api("/restapi/v1.0/account/~", rc_token)
    if account:
        result["account_name"] = account.get("name", "Unknown")
        result["main_number"]  = account.get("mainNumber", "")
        result["service_plan"] = account.get("servicePlan", {}).get("name", "")

    # All extensions — count by type
    extensions = _fetch_all_pages("/restapi/v1.0/account/~/extension", rc_token)
    type_counts = {}
    for ext in extensions:
        t = ext.get("type", "Unknown")
        type_counts[t] = type_counts.get(t, 0) + 1

    result["total_users"] = sum(
        type_counts.get(t, 0)
        for t in ["User", "DigitalUser", "VirtualUser", "FlexibleUser", "Limited"]
    )
    result["total_sites"]             = type_counts.get("Site", 0)
    result["total_ivrs"]              = type_counts.get("IvrMenu", 0)
    result["total_auto_receptionists"]= type_counts.get("AnnouncementOnly", 0)

    ivr_extensions = [e for e in extensions if e.get("type") == "IvrMenu"]

    # Phone numbers
    phone_numbers = _fetch_all_pages("/restapi/v1.0/account/~/phone-number", rc_token)
    result["total_numbers"]   = len(phone_numbers)
    result["direct_numbers"]  = sum(1 for p in phone_numbers if p.get("usageType") == "DirectNumber")
    result["company_numbers"] = sum(1 for p in phone_numbers
                                    if p.get("usageType") in ("CompanyNumber", "MainCompanyNumber"))

    # Call queues
    queues = _fetch_all_pages("/restapi/v1.0/account/~/call-queues", rc_token)
    result["total_queues"] = len(queues)
    result["queues"] = [
        {
            "id":              str(q["id"]),
            "name":            q.get("name", "Unknown"),
            "extensionNumber": q.get("extensionNumber", ""),
        }
        for q in queues
    ]

    # SCIM
    scim_resp = _api("/scim/v2/ServiceProviderConfig", rc_token)
    result["scim_enabled"] = bool(scim_resp and not scim_resp.get("errorCode"))

    # Teams Direct Routing not supported via API — skipped
    result["teams_dr_users"] = None

    # IVR complexity — count key actions per IVR
    ivr_complexity = []
    for ivr_ext in ivr_extensions:
        ivr_id     = str(ivr_ext["id"])
        ivr_detail = _api(f"/restapi/v1.0/account/~/ivr-menus/{ivr_id}", rc_token)
        if ivr_detail:
            action_count = len(ivr_detail.get("actions", []))
            ivr_complexity.append({
                "id":              ivr_id,
                "name":            ivr_ext.get("name", "Unknown IVR"),
                "extensionNumber": ivr_ext.get("extensionNumber", ""),
                "key_count":       action_count,
            })

    ivr_complexity.sort(key=lambda x: x["key_count"], reverse=True)
    result["ivr_complexity"] = ivr_complexity

    # Lightweight extension lookup used by the Sankey builder to resolve
    # extension IDs from call-log legs into human-readable names/types.
    # Not exposed to the frontend (stripped in run_discovery before returning).
    result["_ext_lookup"] = {
        str(e["id"]): {
            "name":            e.get("name", "Unknown"),
            "type":            e.get("type", "Unknown"),
            "extensionNumber": e.get("extensionNumber", ""),
        }
        for e in extensions
    }

    return result


# ------------------------------------------------------------------
# Phase 2 — Analytics data
# ------------------------------------------------------------------

def collect_analytics_data(queue_ids, rc_token):
    """
    Fetches aggregated call analytics for all queues over the last 30 days.

    Confirmed response structure from live API:
      resp["data"]["records"] -> list of queue records
      record["key"]           -> queue extension ID
      record["counters"]["allCalls"]["values"]                    -> total calls (float)
      record["counters"]["callsByResponse"]["values"]["answered"] -> answered calls
      record["counters"]["callsByResult"]["values"]["abandoned"]  -> abandoned calls
      record["counters"]["callsByResult"]["values"]["voicemail"]  -> voicemail calls
      record["timers"]["allCalls"]["values"]                      -> total seconds
      record["timers"]["callsSegments"]["values"]["ringing"]      -> seconds spent ringing (= wait time)
    """
    if not queue_ids:
        return []

    # timeTo must not be in the future — use current moment.
    # timeFrom is 30 days ago at midnight UTC.
    now       = datetime.now(timezone.utc)
    time_from = (now - timedelta(days=30)).strftime("%Y-%m-%dT00:00:00.000Z")
    time_to   = now.strftime("%Y-%m-%dT%H:%M:%S.000Z")

    # Payload matches the confirmed working curl that returned data
    payload = {
        "grouping": {
            "groupBy": "Queues"
        },
        "timeSettings": {
            "timeZone": "Australia/Sydney",
            "timeRange": {
                "timeFrom": time_from,
                "timeTo":   time_to,
            }
        },
        "responseOptions": {
            "counters": {
                "allCalls":            {"aggregationType": "Sum"},
                "callsByDirection":    {"aggregationType": "Sum"},
                "callsByOrigin":       {"aggregationType": "Sum"},
                "callsByResponse":     {"aggregationType": "Sum"},
                "callsSegments":       {"aggregationType": "Sum"},
                "callsByResult":       {"aggregationType": "Sum"},
                "callsByCompanyHours": {"aggregationType": "Sum"},
                "callsByQueueSla":     {"aggregationType": "Sum"},
                "callsByActions":      {"aggregationType": "Sum"},
                "callsByType":         {"aggregationType": "Sum"},
                "callsByEndingParty":  {"aggregationType": "Sum"},
                "callsByQueueHours":   {"aggregationType": "Sum"},
            },
            "timers": {
                "allCallsDuration":            {"aggregationType": "Sum"},
                "callsDurationByDirection":    {"aggregationType": "Sum"},
                "callsDurationByOrigin":       {"aggregationType": "Sum"},
                "callsDurationByResponse":     {"aggregationType": "Sum"},
                "callsSegmentsDuration":       {"aggregationType": "Sum"},
                "callsDurationByResult":       {"aggregationType": "Sum"},
                "callsDurationByCompanyHours": {"aggregationType": "Sum"},
                "callsDurationByQueueSla":     {"aggregationType": "Sum"},
                "callsDurationByType":         {"aggregationType": "Sum"},
                "callsDurationByEndingParty":  {"aggregationType": "Sum"},
                "callsDurationByQueueHours":   {"aggregationType": "Sum"},
            }
        }
    }

    resp = _api(
        "/analytics/calls/v1/accounts/~/aggregation/fetch",
        rc_token,
        method="POST",
        json=payload
    )

    if not resp:
        return []

    # Response is under resp["data"]["records"]
    data_block = resp.get("data", {})
    records    = data_block.get("records", [])

    if not records:
        return []

    queue_metrics = []
    for item in records:
        q_id     = str(item.get("key", ""))
        counters = item.get("counters", {})
        timers   = item.get("timers", {})

        # All values are floats under .values
        total_calls  = counters.get("allCalls",  {}).get("values", 0) or 0

        by_response  = counters.get("callsByResponse", {}).get("values", {})
        answered     = by_response.get("answered",    0) or 0

        by_result    = counters.get("callsByResult",  {}).get("values", {})
        abandoned    = by_result.get("abandoned",  0) or 0
        voicemail    = by_result.get("voicemail",  0) or 0

        abandonment_rate = round((abandoned  / total_calls * 100) if total_calls > 0 else 0, 1)
        voicemail_rate   = round((voicemail  / total_calls * 100) if total_calls > 0 else 0, 1)

        # Total seconds / total calls = avg handle time
        # Note: response field is "allCalls" even though request field is "allCallsDuration"
        total_secs   = timers.get("allCalls", {}).get("values", 0) or 0
        avg_handle   = round(total_secs / total_calls) if total_calls > 0 else 0

        # Ringing seconds / total calls = avg wait time
        # Note: response field is "callsSegments" even though request field is "callsSegmentsDuration"
        segments     = timers.get("callsSegments", {}).get("values", {})
        ringing_secs = segments.get("ringing", 0) or 0
        avg_wait     = round(ringing_secs / total_calls) if total_calls > 0 else 0

        queue_metrics.append({
            "id":                 q_id,
            "total_calls":        int(total_calls),
            "answered":           int(answered),
            "abandoned":          int(abandoned),
            "abandonment_rate":   abandonment_rate,
            "voicemail":          int(voicemail),
            "voicemail_rate":     voicemail_rate,
            "avg_handle_seconds": avg_handle,
            "avg_wait_seconds":   avg_wait,
        })

    return queue_metrics


# ------------------------------------------------------------------
# Phase 3 — Call Flow Sankey Data
# ------------------------------------------------------------------

def _analytics_post(rc_token, payload):
    """Helper to POST analytics payload and return parsed records."""
    # Direct request so we can see the exact error body on 400
    import requests as _requests
    url = "https://platform.ringcentral.com/analytics/calls/v1/accounts/~/aggregation/fetch"
    headers = {
        "Authorization": f"Bearer {rc_token}",
        "Accept":        "application/json",
        "Content-Type":  "application/json",
    }
    try:
        raw = _requests.post(url, headers=headers, json=payload, timeout=30)
        if raw.status_code != 200:
            print(f"[account_health] Analytics {raw.status_code} for groupBy={payload.get('grouping',{}).get('groupBy')} "
                  f"filter={payload.get('callFilters')}: {raw.text[:400]}")
            return []
        resp = raw.json()
    except Exception as e:
        print(f"[account_health] Analytics exception: {e}")
        return []
    records = resp.get("data", {}).get("records", [])
    gb = payload.get('grouping', {}).get('groupBy')
    cf = payload.get('callFilters')
    print(f"[account_health] Analytics OK groupBy={gb} filter={cf} -> {len(records)} records")
    return records


def _make_time_range():
    """Returns timeFrom/timeTo for last 30 days, ending at current moment."""
    now       = datetime.now(timezone.utc)
    time_from = (now - timedelta(days=30)).strftime("%Y-%m-%dT00:00:00.000Z")
    time_to   = now.strftime("%Y-%m-%dT%H:%M:%S.000Z")
    return time_from, time_to


def _analytics_payload(group_by, called_numbers=None):
    """Builds a standard analytics payload."""
    time_from, time_to = _make_time_range()
    payload = {
        "grouping": {"groupBy": group_by},
        "timeSettings": {
            "timeZone": "Australia/Sydney",
            "timeRange": {"timeFrom": time_from, "timeTo": time_to},
        },
        "responseOptions": {
            "counters": {
                "allCalls": {"aggregationType": "Sum"},
            }
        }
    }
    if called_numbers:
        payload["callFilters"] = {"calledNumbers": called_numbers}
    return payload


def get_top_dids(rc_token, limit=10):
    """
    Returns the top N DIDs by call volume over the last 30 days.
    Each entry: {id, phoneNumber, name, total_calls}
    """
    records = _analytics_post(rc_token, _analytics_payload("CompanyNumbers"))

    dids = []
    for r in records:
        info        = r.get("info", {})
        phone       = info.get("phoneNumber", "") or str(r.get("key", ""))
        name        = info.get("name", "")
        total_calls = int(r.get("counters", {}).get("allCalls", {}).get("values", 0) or 0)
        if total_calls > 0 and phone:
            dids.append({
                "id":          str(r.get("key", phone)),
                "phoneNumber": phone,
                "name":        name or phone,
                "total_calls": total_calls,
            })

    dids.sort(key=lambda d: d["total_calls"], reverse=True)
    return dids[:limit]


def _call_log_for_did(rc_token, phone_number, days=30, max_records=1000):
    """
    Pulls detailed call-log records for inbound external calls to a specific
    DID over the last N days. Uses view=Detailed so each record includes a
    `legs` array describing the full routing path.

    Issued directly (rather than via _api) so HTTP errors surface in the
    logs — _api returns None on any error, which would look indistinguishable
    from "no records" and silently break the Sankey.
    """
    import requests as _requests

    now = datetime.now(timezone.utc)
    date_from = (now - timedelta(days=days)).strftime("%Y-%m-%dT00:00:00.000Z")
    date_to   = now.strftime("%Y-%m-%dT%H:%M:%S.000Z")

    url = "https://platform.ringcentral.com/restapi/v1.0/account/~/call-log"
    headers = {
        "Authorization": f"Bearer {rc_token}",
        "Accept":        "application/json",
    }
    base_params = {
        "dateFrom":    date_from,
        "dateTo":      date_to,
        "direction":   "Inbound",
        "type":        "Voice",
        "view":        "Detailed",
        "phoneNumber": phone_number,
        "perPage":     250,
    }

    records = []
    page = 1
    while True:
        params = {**base_params, "page": page}
        try:
            raw = _requests.get(url, headers=headers, params=params, timeout=30)
        except Exception as e:
            print(f"[account_health] CallLog {phone_number} page {page} exception: {e}")
            break

        if raw.status_code != 200:
            print(f"[account_health] CallLog {phone_number} page {page}: "
                  f"HTTP {raw.status_code} {raw.text[:200]}")
            break

        try:
            body = raw.json()
        except Exception as e:
            print(f"[account_health] CallLog {phone_number} page {page}: bad JSON: {e}")
            break

        batch = body.get("records", [])
        records.extend(batch)

        if len(records) >= max_records:
            records = records[:max_records]
            break
        if not body.get("navigation", {}).get("nextPage"):
            break
        page += 1
        time.sleep(0.05)

    return records


def _classify_ext_type(ext_type):
    """Collapses RC extension types into Sankey node categories."""
    if ext_type == "IvrMenu":
        return "ivr"
    if ext_type in ("AnnouncementOnly", "Site"):
        return "ar"
    if ext_type == "Department":
        return "queue"
    if ext_type in ("User", "DigitalUser", "VirtualUser",
                    "FlexibleUser", "Limited", "Bot", "Room",
                    "SharedLinesGroup", "ParkLocation",
                    "ApplicationExtension"):
        return "user"
    return "other"


def _walk_legs(legs, ext_lookup, queue_ids_set):
    """
    Walks a call's legs in order and returns the ordered hops through
    AR / IVR / queue / user nodes. Consecutive duplicates are collapsed.

    Queues are detected via queue_ids_set (since call queues don't appear as
    a distinct 'type' in the extension list — they come back as Department
    or similar, and we already have the authoritative queue list).
    """
    hops = []
    for leg in legs:
        ext = leg.get("extension") or {}
        ext_id = str(ext.get("id", "") or "")
        if not ext_id:
            continue
        info = ext_lookup.get(ext_id)
        if not info:
            continue

        if ext_id in queue_ids_set:
            category = "queue"
        else:
            category = _classify_ext_type(info["type"])
        if category == "other":
            continue

        node_key = f"{category}:{ext_id}"
        if hops and hops[-1]["key"] == node_key:
            continue
        hops.append({
            "key":      node_key,
            "category": category,
            "ext_id":   ext_id,
            "name":     info["name"],
        })
    return hops


def build_sankey_data(rc_token, ext_lookup, queue_ids, top_n=10):
    """
    Builds the Sankey dataset by walking the actual call-log for each top DID.
    This gives exact per-link call counts — no analytics filter gymnastics.

    Returns {"nodes": [...], "links": [...]}.
    Node types: "did" | "ar" | "ivr" | "queue" | "user"
    """
    top_dids = get_top_dids(rc_token, limit=top_n)
    print(f"[account_health] Sankey: got {len(top_dids)} top DIDs")
    for d in top_dids[:3]:
        print(f"  - {d.get('phoneNumber')} ({d.get('name')}): {d.get('total_calls')} calls")
    if not top_dids:
        return {"nodes": [], "links": []}

    queue_ids_set = {str(q) for q in queue_ids}

    nodes   = []
    links   = []
    node_ix = {}
    link_ix = {}

    def _add_node(key, label, ntype, calls_to_add=0):
        if key not in node_ix:
            node_ix[key] = len(nodes)
            nodes.append({"id": key, "label": label, "type": ntype, "calls": calls_to_add})
        else:
            nodes[node_ix[key]]["calls"] += calls_to_add
        return node_ix[key]

    def _add_link(src_key, tgt_key, value):
        pair = (src_key, tgt_key)
        if pair in link_ix:
            links[link_ix[pair]]["value"] += value
        else:
            link_ix[pair] = len(links)
            links.append({
                "source": node_ix[src_key],
                "target": node_ix[tgt_key],
                "value":  value,
            })

    for did in top_dids:
        phone = did["phoneNumber"]
        did_key = f"did:{phone}"
        _add_node(did_key, did.get("name") or phone, "did", did["total_calls"])

        call_records = _call_log_for_did(rc_token, phone)
        print(f"[account_health] Sankey: DID {phone} -> {len(call_records)} call-log records")

        paths_counted = 0
        for rec in call_records:
            legs = rec.get("legs") or []
            hops = _walk_legs(legs, ext_lookup, queue_ids_set)
            if not hops:
                continue
            paths_counted += 1

            first = hops[0]
            _add_node(first["key"], first["name"], first["category"], 1)
            _add_link(did_key, first["key"], 1)

            for i in range(len(hops) - 1):
                a, b = hops[i], hops[i + 1]
                _add_node(b["key"], b["name"], b["category"], 1)
                _add_link(a["key"], b["key"], 1)

        print(f"[account_health] Sankey: DID {phone} -> "
              f"{paths_counted} paths traced across "
              f"{len(call_records)} calls")

    print(f"[account_health] Sankey result: {len(nodes)} nodes, {len(links)} links")
    return {"nodes": nodes, "links": links}


# ------------------------------------------------------------------
# Main entry point
# ------------------------------------------------------------------

def run_discovery(rc_token):
    """
    Runs the full account discovery — config + analytics + sankey.
    rc_token must be passed explicitly (no Flask session in background thread).
    """
    config    = collect_config_data(rc_token)
    queue_ids = [q["id"] for q in config.get("queues", [])]
    analytics = collect_analytics_data(queue_ids, rc_token)

    # Merge analytics into queue list by ID
    analytics_map = {a["id"]: a for a in analytics}
    for q in config["queues"]:
        q.update(analytics_map.get(q["id"], {}))

    # Extension lookup used internally by the Sankey builder.
    # Not sent to the frontend — popped before returning.
    ext_lookup = config.pop("_ext_lookup", {})

    # Build the Sankey flow data from top DIDs
    try:
        config["sankey"] = build_sankey_data(
            rc_token, ext_lookup, queue_ids, top_n=10
        )
    except Exception as e:
        print(f"[account_health] Sankey build failed: {e}")
        config["sankey"] = {"nodes": [], "links": []}

    config["discovery_timestamp"] = datetime.now(timezone.utc).isoformat()
    return config
