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

    # Phone → extension_id lookup. Used by build_sankey_data to decide
    # whether a DID routes via account-level answering rules (no extension
    # assigned) or via a specific extension's rules (extension assigned).
    # The visualiser tab's search does this same distinction implicitly
    # by bucketing phones under their extensions in the search results.
    # Stored privately — stripped before returning to frontend.
    phone_to_ext = {}
    phone_usage  = {}
    for p in phone_numbers:
        p_num = p.get("phoneNumber", "")
        usage = p.get("usageType", "")
        ext_id = p.get("extension", {}).get("id") if p.get("extension") else None
        if p_num:
            phone_usage[p_num] = usage
            if ext_id:
                phone_to_ext[p_num] = str(ext_id)
    result["_phone_to_ext"] = phone_to_ext
    result["_phone_usage"]  = phone_usage

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

def collect_analytics_data(queue_ids, rc_token, days=30):
    """
    Fetches aggregated call analytics for all queues over the last N days.

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
    # timeFrom is N days ago at midnight UTC.
    now       = datetime.now(timezone.utc)
    time_from = (now - timedelta(days=days)).strftime("%Y-%m-%dT00:00:00.000Z")
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


def _make_time_range(days=30):
    """Returns timeFrom/timeTo for the last N days, ending at current moment."""
    now       = datetime.now(timezone.utc)
    time_from = (now - timedelta(days=days)).strftime("%Y-%m-%dT00:00:00.000Z")
    time_to   = now.strftime("%Y-%m-%dT%H:%M:%S.000Z")
    return time_from, time_to


def _analytics_payload(group_by, called_numbers=None, days=30):
    """Builds a standard analytics payload."""
    time_from, time_to = _make_time_range(days=days)
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


def get_top_dids(rc_token, limit=10, days=30):
    """
    Returns the top N DIDs by call volume over the last N days.
    Each entry: {id, phoneNumber, name, total_calls}
    """
    records = _analytics_post(rc_token, _analytics_payload("CompanyNumbers", days=days))

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



# ------------------------------------------------------------------
# Phase 3 — Call Flow Sankey Data (tracer-driven)
# ------------------------------------------------------------------

def collect_user_analytics_data(rc_token, days=30):
    """
    Same shape as collect_analytics_data but grouped by Users. Used when an
    IVR option or AR rule routes a DID directly to a user (e.g. a reception
    desk), or when a DID routes straight to a user with no queue in between.

    Returns a dict keyed by user extension id (str) -> {total_calls, answered,
    voicemail, abandoned, ...}, to make per-leaf lookups cheap.
    """
    now       = datetime.now(timezone.utc)
    time_from = (now - timedelta(days=days)).strftime("%Y-%m-%dT00:00:00.000Z")
    time_to   = now.strftime("%Y-%m-%dT%H:%M:%S.000Z")

    payload = {
        "grouping":     {"groupBy": "Users"},
        "timeSettings": {
            "timeZone":  "Australia/Sydney",
            "timeRange": {"timeFrom": time_from, "timeTo": time_to},
        },
        "responseOptions": {
            "counters": {
                "allCalls":        {"aggregationType": "Sum"},
                "callsByResponse": {"aggregationType": "Sum"},
                "callsByResult":   {"aggregationType": "Sum"},
            }
        }
    }

    resp = _api(
        "/analytics/calls/v1/accounts/~/aggregation/fetch",
        rc_token, method="POST", json=payload,
    )
    if not resp:
        return {}
    records = (resp.get("data") or {}).get("records", [])

    out = {}
    for item in records:
        uid = str(item.get("key", ""))
        counters = item.get("counters", {})
        total    = int(counters.get("allCalls",       {}).get("values", 0) or 0)
        answered = int(counters.get("callsByResponse", {}).get("values", {}).get("answered", 0) or 0)
        by_res   = counters.get("callsByResult", {}).get("values", {})
        voicemail = int(by_res.get("voicemail", 0) or 0)
        abandoned = int(by_res.get("abandoned", 0) or 0)
        out[uid] = {
            "total_calls": total,
            "answered":    answered,
            "voicemail":   voicemail,
            "abandoned":   abandoned,
        }
    print(f"[account_health] User analytics: {len(out)} users with data")
    return out


def _build_tracer(rc_token, min_interval=0.2):
    """
    Returns a CallFlowTracer subclass bound to the given token. Necessary
    because the tracer uses rc_api_call() which expects a Flask session —
    we're running in a background thread with no session, so we override
    the api() method to pass the token explicitly.

    Additionally, this subclass rate-limits its own API calls: the tracer
    fires config lookups back-to-back (extension info, queue detail,
    answering rules, business hours) and on large tenants easily exceeds
    RC's medium-bucket rate limit. We pace at ~5 requests/second and back
    off on 429 with Retry-After.

    min_interval: minimum seconds between requests (0.2 = 5 rps).
    """
    import requests as _requests
    from webapp.visualiser.utils import CallFlowTracer

    class TokenTracer(CallFlowTracer):
        def __init__(self, token, min_interval):
            super().__init__()
            self._token = token
            self._min_interval = min_interval
            self._last_call_at = 0.0
            # Per-discovery response cache. Lives for the life of the
            # tracer instance (single discovery run). Keyed by endpoint URL
            # with the cache-bust "_=<timestamp>" param stripped, so two
            # DIDs asking for the same answering-rule list both hit cache.
            #
            # Why this is safe: discovery runs take 2-5 minutes; a user
            # editing config mid-scan and wanting to see fresh state is not
            # a realistic workflow. The cache-busting in CallFlowTracer
            # exists for the interactive visualiser (where a user might
            # edit and immediately re-run); we don't want it here.
            self._response_cache = {}

        def _cache_key(self, endpoint):
            # Strip cache-busting timestamp param so repeated fetches of
            # the same logical endpoint collide in cache.
            import re
            return re.sub(r'[?&]_=\d+', '', endpoint)

        def api(self, endpoint):
            cache_key = self._cache_key(endpoint)
            if cache_key in self._response_cache:
                return self._response_cache[cache_key]

            # Keep the tracer's usual cache-busting behaviour on the wire
            # (so if we DO go to RC, we bypass any edge/CDN caches).
            try:
                should_bust = (
                    "answering-rule" in endpoint or
                    ("/call-queues/" in endpoint and "overflow-settings" not in endpoint)
                )
                sep = "&" if "?" in endpoint else "?"
                final_url = endpoint + f"{sep}_={int(time.time())}" if should_bust else endpoint

                # Build full URL + headers for a direct request so we can
                # see HTTP status codes (rc_api_call hides 429s).
                if final_url.startswith("/"):
                    url = f"https://platform.ringcentral.com{final_url}"
                else:
                    url = final_url

                headers = {
                    "Authorization": f"Bearer {self._token}",
                    "Accept":        "application/json",
                }

                max_retries = 3
                last_status = None
                for attempt in range(max_retries + 1):
                    # Pace the outbound requests
                    elapsed = time.time() - self._last_call_at
                    if elapsed < self._min_interval:
                        time.sleep(self._min_interval - elapsed)

                    try:
                        raw = _requests.get(url, headers=headers, timeout=30)
                    except Exception as e:
                        self._last_call_at = time.time()
                        print(f"[account_health] Tracer API exception on {endpoint}: {e}")
                        return None

                    self._last_call_at = time.time()
                    last_status = raw.status_code

                    if raw.status_code == 429:
                        if attempt >= max_retries:
                            print(f"[account_health] Tracer API 429 exhausted retries on {endpoint}")
                            return None
                        retry_after = raw.headers.get("Retry-After")
                        try:
                            wait_s = int(retry_after) if retry_after else 5
                        except ValueError:
                            wait_s = 5
                        time.sleep(wait_s)
                        continue

                    if raw.status_code != 200:
                        # Non-200, non-429 — return errorCode dict if parseable
                        # so callers like get_ext_info() can distinguish "extension
                        # not found" (errorCode CMN-102) from "network failed".
                        try:
                            body = raw.json()
                            # Cache deterministic errors (e.g. "extension not
                            # found") — they won't magically become 200 on
                            # retry, and get_ext_info() makes decisions off
                            # specific error codes.
                            if isinstance(body, dict) and body.get("errorCode"):
                                self._response_cache[cache_key] = body
                            return body
                        except Exception:
                            print(f"[account_health] Tracer API HTTP {raw.status_code} "
                                  f"on {endpoint} (unparseable body)")
                            return None

                    try:
                        body = raw.json()
                        self._response_cache[cache_key] = body
                        return body
                    except Exception:
                        print(f"[account_health] Tracer API 200 but non-JSON body on {endpoint}")
                        return None

                print(f"[account_health] Tracer API unresolved (last status {last_status}) on {endpoint}")
                return None
            except Exception as e:
                print(f"[account_health] Tracer API unexpected error on {endpoint}: {e}")
                return None

    return TokenTracer(rc_token, min_interval)


# ------------------------------------------------------------------
# Pruning the tracer graph into a Sankey skeleton
# ------------------------------------------------------------------

# Tracer node types we care about when building the Sankey skeleton
_LEAF_TYPES   = {"queue", "user", "external", "voicemail"}
_BRANCH_TYPES = {"ivr", "autoreceptionist", "site"}


def _prune_tracer_graph(tracer_graph, tracer, queue_ids_set, root_ext_id=None):
    """
    Takes a raw tracer output {nodes, edges} and produces a pruned skeleton
    that matches the user's requirements:

      - DID root (phone) is the entry point
      - Keep IVR / AR / Site as branch nodes
      - Keep queues as leaves (but follow queue→queue overflow only)
      - Keep users only when directly reached from IVR/AR (never when reached
        as a queue member — that's handled in the tracer structure already)
      - Keep external transfers and IVR-to-voicemail as terminal leaves

    Returns:
      {
        "root_nid":       "n0",                # the DID node id
        "skeleton_nodes": { nid -> node_dict },
        "skeleton_edges": [ (src_nid, tgt_nid, label) ],
        "leaf_exts":      { nid -> ext_id },   # for queues/users that have ext_ids
      }
    """
    raw_nodes = {n["data"]["id"]: n["data"] for n in tracer_graph["nodes"]}
    raw_edges = [(e["data"]["source"], e["data"]["target"], e["data"].get("label", ""))
                 for e in tracer_graph["edges"]]

    # Find the root. If caller provided root_ext_id (for extension-based
    # traces where there's no phone-type node), use that. Else look for a
    # phone-type node (traditional company-number trace).
    root_nid = None
    if root_ext_id is not None:
        # tracer.node_map maps ext_id -> graph_node_id
        root_nid = tracer.node_map.get(root_ext_id)
        # Fall through to phone lookup if the ext_id wasn't rendered
    if root_nid is None:
        for nid, nd in raw_nodes.items():
            if nd.get("type") == "phone":
                root_nid = nid
                break
    if root_nid is None:
        return None

    # Build forward adjacency: src -> [(tgt, label)]
    forward = {}
    for src, tgt, lbl in raw_edges:
        forward.setdefault(src, []).append((tgt, lbl))

    # Walk from root, keeping nodes/edges per the pruning rules.
    kept_nodes = {root_nid: raw_nodes[root_nid]}
    kept_edges = []
    leaf_exts  = {}

    # Reverse node_map from tracer: graph_node_id -> ext_id
    # tracer.node_map is ext_id -> graph_node_id; invert.
    nid_to_ext = {nid: eid for eid, nid in tracer.node_map.items()}

    def ext_of(nid):
        return nid_to_ext.get(nid)

    stack = [(root_nid, "dnst")]  # (node, "dnst" = downstream walker context)
    # dnst "queue" means "we came from another queue via overflow" — only used
    # to decide whether to follow certain edges. For simplicity, we track the
    # parent's role at the call site instead.

    visited = {root_nid}

    def _walk(nid):
        node = raw_nodes.get(nid)
        if not node:
            return
        ntype = node.get("type", "")
        children = forward.get(nid, [])

        for tgt, lbl in children:
            tgt_node = raw_nodes.get(tgt)
            if not tgt_node:
                continue
            tgt_type = tgt_node.get("type", "")

            # Rules for what to follow:
            #
            # If parent is a queue, we only follow queue→queue overflow edges.
            # We don't follow queue→user, queue→voicemail, queue→external,
            # or queue→ivr (rare) overflows, because the outcome there is
            # already covered by the queue's own analytics totals.
            if ntype == "queue":
                if tgt_type != "queue":
                    continue  # skip — don't show user/vm/external overflow
                # fallthrough: keep queue→queue edge

            # If parent is ivr/ar/site/phone, follow all meaningful children:
            # queue, user, external, voicemail, ivr, ar, site, unknown.
            #
            # We KEEP "unknown" nodes (rather than dropping them) so that when
            # the tracer's lookup fails mid-path (e.g. hit rate limit fetching
            # Site extension info), the DID still shows it routes *somewhere* —
            # just labelled as "Unknown". Dropping them would make failed
            # traces indistinguishable from genuinely unconfigured DIDs, and
            # we'd lose a lot of context on where the trace gave up.

            # Add node if new
            if tgt not in kept_nodes:
                kept_nodes[tgt] = tgt_node
                # Track ext_id if it's a leaf-analytics-eligible type.
                if tgt_type in ("queue", "user"):
                    eid = ext_of(tgt)
                    if eid:
                        leaf_exts[tgt] = eid

            kept_edges.append((nid, tgt, lbl))

            # Recurse only if we haven't already visited — prevents walking
            # the same subtree twice when a queue is shared.
            if tgt in visited:
                continue
            visited.add(tgt)

            # Recurse into branches and queues (for overflow). Don't recurse
            # into users, external, voicemail, unknown — they're terminal.
            if tgt_type in _BRANCH_TYPES or tgt_type == "queue":
                _walk(tgt)

    _walk(root_nid)

    return {
        "root_nid":       root_nid,
        "skeleton_nodes": kept_nodes,
        "skeleton_edges": kept_edges,
        "leaf_exts":      leaf_exts,
        "nid_to_ext":     nid_to_ext,
    }


# ------------------------------------------------------------------
# Attributing queue/user analytics to DIDs (with shared-leaf handling)
# ------------------------------------------------------------------

def _attribute_shared_leaves(shared_leaves, top_did_phones, rc_token, days=30):
    """
    For leaves (queues or users) that are reached by more than one top DID,
    we need to split the leaf's analytics totals proportionally by how many
    calls actually came from each DID.

    We do this by fetching the bulk inbound call log (slow but one-shot),
    then counting, for each shared leaf, how many records hit that leaf and
    also originated from each top DID.

    Returns a dict: { leaf_ext_id -> { did_phone -> fraction_of_leaf_volume } }
    where fractions sum to <=1 per leaf (the rest, if any, is from non-top
    DIDs — those calls are simply not represented on this Sankey).
    """
    if not shared_leaves:
        return {}

    print(f"[account_health] {len(shared_leaves)} shared leaves need call-log attribution")
    buckets = _call_log_bulk_inbound(rc_token, days=days)
    # buckets: {to.phoneNumber -> [records]}, where each record has legs
    # describing which extensions the call touched.

    # We want the inverse: for each (did_phone, leaf_ext_id) count how many
    # calls flowed through that leaf from that DID.
    pair_counts = {}  # (did_phone, leaf_ext_id) -> count
    leaf_totals = {}  # leaf_ext_id -> count (across our top DIDs only)

    shared_set = set(shared_leaves)
    did_set    = set(top_did_phones)

    for did_phone, records in buckets.items():
        if did_phone not in did_set:
            continue
        for rec in records:
            legs = rec.get("legs") or []
            touched = set()
            for leg in legs:
                ext = leg.get("extension") or {}
                eid = str(ext.get("id", "") or "")
                if eid in shared_set:
                    touched.add(eid)
            for eid in touched:
                pair_counts[(did_phone, eid)] = pair_counts.get((did_phone, eid), 0) + 1
                leaf_totals[eid] = leaf_totals.get(eid, 0) + 1

    # Convert to fractions
    splits = {}
    for (did_phone, eid), cnt in pair_counts.items():
        total = leaf_totals.get(eid, 0)
        if total <= 0:
            continue
        splits.setdefault(eid, {})[did_phone] = cnt / total

    print(f"[account_health] Shared-leaf attribution computed for {len(splits)} leaves")
    return splits


# ------------------------------------------------------------------
# Sankey assembly
# ------------------------------------------------------------------

# Colour-coded node types understood by the frontend.
# The existing frontend palette already handles did/ivr/queue/user. We add
# ar, external, answered, voicemail, abandoned, other, unrouted.

def build_sankey_data(rc_token, queue_ids, queue_analytics, user_analytics,
                       top_n=10, days=30, top_dids=None, did_flows_out=None,
                       phone_to_ext=None):
    """
    Builds the call-flow Sankey by tracing each top DID's config tree and
    attaching analytics outcome buckets to every queue/user leaf.

    Structural nodes (IVRs, ARs, queues, users, externals) and outcome
    buckets (Answered/Voicemail/Abandoned/Other) are SHARED across DIDs —
    if two DIDs both route through the same IVR, it appears once. Multiple
    DIDs' volumes accumulate on shared edges.

    Only the DID nodes themselves, and their per-DID "Unrouted / Other"
    shortfall bucket, remain per-DID (the shortfall genuinely is
    per-DID: "this specific DID had N calls we couldn't account for").

    queue_analytics: list of queue metric dicts from collect_analytics_data
    user_analytics:  dict of user metric dicts from collect_user_analytics_data
    top_dids: optional pre-fetched list of top DIDs. If None, fetches fresh.
    did_flows_out: optional dict to populate with {phone -> raw_graph}. Lets
        the caller reuse the traced graphs (e.g. for per-DID flow cards in the
        UI) without re-querying the RC API.
    phone_to_ext: optional dict mapping phone number to extension id. If
        a DID has an extension assigned, we trace that extension directly
        (matches how the visualiser tab works) rather than tracing via
        account-level answering rules that don't include this DID.
    """
    if top_dids is None:
        top_dids = get_top_dids(rc_token, limit=top_n, days=days)
    print(f"[account_health] Sankey: got {len(top_dids)} top DIDs")
    for d in top_dids[:3]:
        print(f"  - {d.get('phoneNumber')} ({d.get('name')}): {d.get('total_calls')} calls")
    if not top_dids:
        return {"nodes": [], "links": []}

    queue_ids_set = {str(q) for q in queue_ids}
    queue_by_id   = {q["id"]: q for q in queue_analytics}

    # ── 1. Trace each DID's config graph ───────────────────────────────
    # KEY OPTIMISATION: reuse a single tracer instance across all DIDs so
    # its extension_cache and ext_num_map warm up on DID 1 and serve
    # subsequent DIDs from memory. On a tenant where all 10 top DIDs share
    # the same IVR + queues, this cuts API calls from ~180 (18 per DID × 10)
    # to ~20 (most lookups cached after the first DID). Essential for
    # staying under RC's rate limit.
    tracer = _build_tracer(rc_token)

    def _reset_tracer_graph_state():
        """Clear per-trace graph state but KEEP the API-level caches."""
        tracer.nodes        = []
        tracer.edges        = []
        tracer.node_map     = {}
        tracer.node_counter = 0
        tracer.visited      = set()
        tracer.request_logs = []
        # PRESERVE: extension_cache, ext_num_map, schedule_cache

    skeletons = []  # list of (did_dict, pruned_skeleton)
    phone_to_ext = phone_to_ext or {}
    for did in top_dids:
        phone = did["phoneNumber"]
        _reset_tracer_graph_state()

        # Decide what to trace. If this DID is assigned to an extension,
        # trace THAT extension directly — matches how the visualiser tab
        # works. Otherwise trace via account-level answering rules with
        # the company_ prefix. This mimics the search endpoint's logic:
        # phones with extensions get bucketed under those extensions.
        assigned_ext = phone_to_ext.get(phone)
        if assigned_ext:
            trace_target = assigned_ext
            print(f"[account_health] DID {phone}: tracing via extension {assigned_ext}")
        else:
            trace_target = f"company_{phone}"

        try:
            raw_graph, _logs = tracer.generate(trace_target)
        except Exception as e:
            print(f"[account_health] Tracer failed for {phone}: {e}")
            continue

        # Diagnostic: print the raw tracer output shape so we can distinguish
        # tracer-built-nothing vs pruner-dropped-everything.
        raw_nodes = raw_graph.get("nodes", [])
        raw_edges = raw_graph.get("edges", [])
        type_counts = {}
        for n in raw_nodes:
            t = (n.get("data") or {}).get("type", "?")
            type_counts[t] = type_counts.get(t, 0) + 1
        type_summary = ", ".join(f"{t}:{c}" for t, c in sorted(type_counts.items()))
        print(f"[account_health] DID {phone}: raw tracer output "
              f"{len(raw_nodes)} nodes ({type_summary}), {len(raw_edges)} edges "
              f"(cache: {len(getattr(tracer, 'extension_cache', {}))} exts, "
              f"{len(getattr(tracer, '_response_cache', {}))} responses)")

        # Extra diagnostic: list edges coming OUT of the phone node. If this
        # is empty, the tracer is the problem — it never linked the phone to
        # any downstream node. If it's non-empty but the pruner outputs
        # 0 leaves, the pruner is dropping edges it shouldn't.
        phone_nid = None
        for n in raw_nodes:
            if (n.get("data") or {}).get("type") == "phone":
                phone_nid = n["data"]["id"]
                break
        if phone_nid:
            out_edges = []
            for e in raw_edges:
                ed = e.get("data") or {}
                if ed.get("source") == phone_nid:
                    tgt_id = ed.get("target")
                    tgt_type = "?"
                    for n in raw_nodes:
                        if (n.get("data") or {}).get("id") == tgt_id:
                            tgt_type = (n.get("data") or {}).get("type", "?")
                            tgt_label = (n.get("data") or {}).get("label", "?")
                            out_edges.append(f"{tgt_type}({tgt_label})")
                            break
            print(f"[account_health]   phone node children: {out_edges or '(none)'}")

        # If this DID came back with no routing, dump diagnostic info
        # about the account-level answering rules to help figure out why.
        if phone_nid and not [e for e in raw_edges
                               if (e.get("data") or {}).get("source") == phone_nid]:
            print(f"[account_health]   ⚠ {phone} traced to 0 edges — dumping rule match diagnostics")
            # The rules list was fetched by the tracer and should be in
            # its response cache. Look for the account rules list endpoint.
            rc_cache = getattr(tracer, '_response_cache', {})
            rules_list_body = None
            for key, body in rc_cache.items():
                if '/restapi/v1.0/account/~/answering-rule' in key and 'perPage' in key:
                    rules_list_body = body
                    break
            if rules_list_body and rules_list_body.get('records'):
                norm_did = phone.lstrip('+')
                print(f"[account_health]   {phone}: {len(rules_list_body['records'])} account rules exist")
                for stub in rules_list_body['records']:
                    rule_id = stub.get('id')
                    # Look for this rule's detail in cache
                    rule_detail = None
                    for key, body in rc_cache.items():
                        if f'/answering-rule/{rule_id}' in key:
                            rule_detail = body
                            break
                    if not rule_detail:
                        print(f"[account_health]     rule {rule_id}: (detail not in cache)")
                        continue
                    r_type = rule_detail.get('type', 'Custom')
                    r_name = rule_detail.get('name', '')
                    r_enabled = rule_detail.get('enabled', True)
                    r_action = rule_detail.get('callHandlingAction', '')
                    called_nums = rule_detail.get('calledNumbers', [])
                    called_strs = [cn.get('phoneNumber', '') for cn in called_nums]
                    matched = False
                    if r_type in ('BusinessHours', 'AfterHours'):
                        matched = True  # these always apply
                    else:
                        for cn in called_strs:
                            cn_norm = cn.lstrip('+')
                            if cn_norm == norm_did or cn_norm in phone or phone in cn:
                                matched = True; break
                    flag = '✓' if matched else '✗'
                    print(f"[account_health]     {flag} rule {rule_id} ({r_type}, "
                          f"enabled={r_enabled}, action={r_action}): "
                          f"calledNumbers={called_strs} name={r_name!r}")
            else:
                print(f"[account_health]   {phone}: rule list not in cache — unexpected")

        # Stash the raw graph for the frontend per-DID cards (same shape
        # as /api/rc/trace-flow/ returns). We store BEFORE pruning so it
        # reflects the full tracer output, including nodes the Sankey
        # pruner would otherwise drop.
        if did_flows_out is not None:
            did_flows_out[phone] = raw_graph

        pruned = _prune_tracer_graph(
            raw_graph, tracer, queue_ids_set,
            root_ext_id=assigned_ext if assigned_ext else None,
        )
        if pruned is None:
            print(f"[account_health] No config path found for DID {phone}")
            continue
        skeletons.append((did, pruned))
        print(f"[account_health] DID {phone}: pruned {len(pruned['skeleton_nodes'])} nodes, "
              f"{len(pruned['leaf_exts'])} leaves")

    if not skeletons:
        return {"nodes": [], "links": []}

    # ── 2. Shared leaves ───────────────────────────────────────────────
    # NOTE: we no longer attempt to split queue/user analytics when a leaf
    # is reached by multiple DIDs. The previous bulk-call-log approach was
    # producing over-attribution errors of 50-80% on small-volume DIDs and
    # taking 2+ minutes of API traffic. Instead, each DID gets the full
    # queue analytics total; if a queue is shared between DIDs, its volume
    # appears in full under each DID that reaches it. The top-of-DID total
    # and the per-DID outcome totals won't always conserve across the
    # entire chart for shared queues, but each DID sub-tree is internally
    # consistent, and we don't fabricate numbers.
    splits = {}  # empty — no splits applied

    # ── 3. Sankey assembly ─────────────────────────────────────────────
    nodes      = []
    links      = []
    node_index = {}       # key -> idx in nodes
    link_index = {}       # (src_key, tgt_key) -> idx in links

    def _add_node(key, label, ntype):
        if key not in node_index:
            node_index[key] = len(nodes)
            nodes.append({"id": key, "label": label, "type": ntype, "calls": 0})
        return node_index[key]

    def _add_link(src_key, tgt_key, value):
        if value <= 0:
            return
        pair = (src_key, tgt_key)
        if pair in link_index:
            # Accumulate when the same edge is emitted multiple times
            # (e.g. two DIDs both route through IVR -> Queue).
            links[link_index[pair]]["value"] += value
        else:
            link_index[pair] = len(links)
            links.append({
                "source": node_index[src_key],
                "target": node_index[tgt_key],
                "value":  value,
            })

    # Shared structural-node keys — same ext_id = same node across DIDs.
    def _structural_key(sankey_type, ext_id, nid, phone):
        """
        For nodes with a real extension id, key by ext_id so they dedupe.
        For type-only nodes (external transfers without ext_id, etc.),
        fall back to a per-DID key so they don't wrongly collapse.
        """
        if ext_id:
            return f"{sankey_type}:{ext_id}"
        # No ext_id — scope per-DID-per-tracer-nid to avoid false merges
        return f"{sankey_type}:{phone}:{nid}"

    # Global outcome-bucket keys (shared across all DIDs)
    OUTCOME_KEYS = {
        "answered":  "answered",
        "voicemail": "voicemail",
        "abandoned": "abandoned",
        "other":     "other",
    }

    tracer_node_type_to_sankey = {
        "phone":            "did",
        "ivr":              "ivr",
        "autoreceptionist": "ar",
        "site":             "ar",
        "queue":            "queue",
        "user":             "user",
        "external":         "external",
        "voicemail":        "voicemail",
        "unknown":          "unknown",   # tracer couldn't resolve this ext
    }

    # ── 4. For each DID, walk its skeleton and emit shared nodes/links ─
    for did, pruned in skeletons:
        phone      = did["phoneNumber"]
        did_total  = did["total_calls"]
        did_key    = f"did:{phone}"
        # DID node is per-DID (one per phone number)
        _add_node(did_key, did.get("name") or phone, "did")
        nodes[node_index[did_key]]["calls"] = did_total

        skel_nodes = pruned["skeleton_nodes"]
        skel_edges = pruned["skeleton_edges"]
        leaf_exts  = pruned["leaf_exts"]
        root_nid   = pruned["root_nid"]

        # Reverse tracer node_map: tracer_nid -> ext_id
        # (The tracer stores ext_id -> tracer_nid; we want the inverse.)
        # We already get this via leaf_exts for leaves, but we need it for
        # intermediate nodes too. Rebuild from the pruned skeleton.
        nid_to_ext = {}
        # The tracer wraps ext-id -> nid in its node_map, which the pruner
        # uses to populate leaf_exts. For intermediate branch nodes (IVR,
        # AR), we need to grab that mapping too. The pruner doesn't pass it
        # through directly, so the pragmatic approach is: re-peek into the
        # tracer instance carried by the skeleton. But the pruned struct
        # doesn't keep the tracer. So we iterate the underlying node_map
        # via a small reverse walk — look up each skel_node's ext_id by
        # scanning the tracer.node_map snapshot stored in pruned["nid_to_ext"]
        # if present, else try to infer from the label (for IVRs with
        # "Ext X" substring) — but that's fragile. Cleaner: have _prune
        # pass through the reverse map. We'll handle that below.
        nid_to_ext = pruned.get("nid_to_ext", {})

        # Map each tracer nid -> its Sankey key
        skel_key = {}

        # Root (DID) aliases to did_key
        skel_key[root_nid] = did_key

        for nid, nd in skel_nodes.items():
            if nid == root_nid:
                continue
            sankey_type = tracer_node_type_to_sankey.get(nd.get("type", ""), "other")
            ext_id = nid_to_ext.get(nid)
            key = _structural_key(sankey_type, ext_id, nid, phone)
            skel_key[nid] = key
            label = nd.get("label", "").split("\n")[0].strip() or nd.get("type", "?")
            _add_node(key, label, sankey_type)

        # Post-order traversal to compute "volume through each node".
        skel_children = {}
        for src, tgt, _ in skel_edges:
            skel_children.setdefault(src, []).append(tgt)

        node_volume = {}  # tracer nid -> volume (per this DID)

        def _outcomes_for_leaf(nid):
            """(total, answered, voicemail, abandoned, other) for a leaf,
            already scaled by this DID's share. None if no analytics data."""
            node = skel_nodes[nid]
            ntype = node.get("type")
            eid = leaf_exts.get(nid)

            if ntype == "queue" and eid:
                q = queue_by_id.get(eid)
                if not q:
                    return (0, 0, 0, 0, 0)
                total = q.get("total_calls", 0) or 0
                ans   = q.get("answered",    0) or 0
                vm    = q.get("voicemail",   0) or 0
                ab    = q.get("abandoned",   0) or 0
                if eid in splits:
                    frac = splits[eid].get(phone, 0)
                    total = int(round(total * frac))
                    ans   = int(round(ans   * frac))
                    vm    = int(round(vm    * frac))
                    ab    = int(round(ab    * frac))
                other = max(0, total - ans - vm - ab)
                return (total, ans, vm, ab, other)

            if ntype == "user" and eid:
                u = user_analytics.get(eid)
                if not u:
                    return (0, 0, 0, 0, 0)
                total = u.get("total_calls", 0) or 0
                ans   = u.get("answered",    0) or 0
                vm    = u.get("voicemail",   0) or 0
                ab    = u.get("abandoned",   0) or 0
                if eid in splits:
                    frac = splits[eid].get(phone, 0)
                    total = int(round(total * frac))
                    ans   = int(round(ans   * frac))
                    vm    = int(round(vm    * frac))
                    ab    = int(round(ab    * frac))
                other = max(0, total - ans - vm - ab)
                return (total, ans, vm, ab, other)

            return (None, None, None, None, None)

        attributed_volume = 0

        # Emit leaf -> outcome-bucket links (shared outcome nodes)
        for nid, eid in leaf_exts.items():
            total, ans, vm, ab, other = _outcomes_for_leaf(nid)
            if total is None:
                continue
            leaf_key = skel_key[nid]
            node_volume[nid] = total
            attributed_volume += total

            if ans > 0:
                _add_node(OUTCOME_KEYS["answered"], "Answered", "answered")
                _add_link(leaf_key, OUTCOME_KEYS["answered"], ans)
            if vm > 0:
                _add_node(OUTCOME_KEYS["voicemail"], "Voicemail", "voicemail")
                _add_link(leaf_key, OUTCOME_KEYS["voicemail"], vm)
            if ab > 0:
                _add_node(OUTCOME_KEYS["abandoned"], "Abandoned", "abandoned")
                _add_link(leaf_key, OUTCOME_KEYS["abandoned"], ab)
            if other > 0:
                _add_node(OUTCOME_KEYS["other"], "Other", "other")
                _add_link(leaf_key, OUTCOME_KEYS["other"], other)

        # Propagate volume bottom-up for intermediate nodes
        def _subtree_volume(nid):
            if nid in node_volume:
                return node_volume[nid]
            kids = skel_children.get(nid, [])
            if not kids:
                node_volume[nid] = 0
                return 0
            total = 0
            for k in kids:
                total += _subtree_volume(k)
            node_volume[nid] = total
            return total

        _subtree_volume(root_nid)

        # Emit structural edges with accumulated volumes
        seen_edges_this_did = set()
        for src_nid, tgt_nid, _lbl in skel_edges:
            s_key = skel_key.get(src_nid)
            t_key = skel_key.get(tgt_nid)
            if not s_key or not t_key or s_key == t_key:
                continue
            if (src_nid, tgt_nid) in seen_edges_this_did:
                continue
            seen_edges_this_did.add((src_nid, tgt_nid))
            vol = node_volume.get(tgt_nid, 0)
            _add_link(s_key, t_key, vol)

        # Per-DID shortfall bucket (genuinely per-DID, stays scoped).
        # If the skeleton contains any "unknown" nodes (tracer couldn't
        # resolve an extension mid-path), label the bucket accordingly so
        # the user can distinguish "tracer failure" from "genuinely no
        # routing configured".
        has_unknown = any(
            sk_node.get("type") == "unknown"
            for sk_node in skel_nodes.values()
        )
        shortfall = did_total - attributed_volume
        if shortfall > 0:
            if has_unknown:
                k = f"unk:{phone}"
                _add_node(k, "Unknown (trace failed)", "unknown")
            else:
                k = f"unr:{phone}"
                _add_node(k, "Unrouted / Other", "unrouted")
            _add_link(did_key, k, shortfall)
        elif shortfall < 0:
            print(f"[account_health] Sankey: DID {phone} over-attributed by "
                  f"{-shortfall} calls (measured {attributed_volume} > "
                  f"analytics {did_total}) — shared-leaf splits may sum high")

    # ── 5. Strip unused nodes (leaves with no outcome data) ────────────
    used = set()
    for lk in links:
        used.add(lk["source"])
        used.add(lk["target"])
    if len(used) < len(nodes):
        old_to_new = {}
        new_nodes = []
        for i, n in enumerate(nodes):
            if i in used:
                old_to_new[i] = len(new_nodes)
                new_nodes.append(n)
        new_links = [{
            "source": old_to_new[lk["source"]],
            "target": old_to_new[lk["target"]],
            "value":  lk["value"],
        } for lk in links]
        nodes, links = new_nodes, new_links

    # ── 6. Cycle removal ───────────────────────────────────────────────
    # d3-sankey refuses to render graphs that aren't strict DAGs. The
    # structural skeleton MOSTLY is a DAG, but queue-to-queue overflow
    # chains can cycle (Queue_A overflows to Queue_B which overflows back
    # to Queue_A). Detect any back-edges with a DFS and drop them, logging
    # which edges we removed so it's visible in the progress log.
    links, removed = _strip_sankey_cycles(nodes, links)
    if removed:
        for src_i, tgt_i, val in removed:
            s_label = nodes[src_i].get("label", "?")
            t_label = nodes[tgt_i].get("label", "?")
            print(f"[account_health] Sankey: dropped cycle edge "
                  f"{s_label!r} -> {t_label!r} (value={val}) to keep graph acyclic")

    print(f"[account_health] Sankey result: {len(nodes)} nodes, {len(links)} links")
    return {"nodes": nodes, "links": links}


def _strip_sankey_cycles(nodes, links):
    """
    Runs a DFS over the link graph. Any edge that closes a back-loop is
    removed. Returns (kept_links, removed_edges) where removed_edges is a
    list of (source_idx, target_idx, value) tuples.

    Strategy: iterate links in order and simulate adding each to the graph.
    If the new edge would create a path from target back to source in the
    already-accepted graph, drop it. This is O(links * nodes) worst case
    but our graph is small (<100 nodes, <200 links) so it's fine.
    """
    adjacency = {i: [] for i in range(len(nodes))}
    kept     = []
    removed  = []

    def _reachable(start, goal):
        if start == goal:
            return True
        stack = [start]
        seen  = {start}
        while stack:
            cur = stack.pop()
            for nxt in adjacency[cur]:
                if nxt == goal:
                    return True
                if nxt not in seen:
                    seen.add(nxt)
                    stack.append(nxt)
        return False

    for lk in links:
        s = lk["source"]
        t = lk["target"]
        # Would adding s->t create a cycle? A cycle exists iff we can already
        # reach s from t in the accepted graph.
        if _reachable(t, s):
            removed.append((s, t, lk["value"]))
            continue
        adjacency[s].append(t)
        kept.append(lk)

    return kept, removed


# ------------------------------------------------------------------
# Bulk call-log fetch (used only when shared leaves need attribution)
# ------------------------------------------------------------------

def _call_log_bulk_inbound(rc_token, days=30, max_records=5000):
    """
    Pulls ALL inbound voice call-log records for the account over the last N
    days, paginating through up to `max_records`, then buckets them by
    to.phoneNumber. Used by _attribute_shared_leaves to split shared-queue
    analytics proportionally across top DIDs.

    Rate-limit aware: sleeps 6s between pages, honours Retry-After on 429.
    """
    import requests as _requests

    now       = datetime.now(timezone.utc)
    date_from = (now - timedelta(days=days)).strftime("%Y-%m-%dT00:00:00.000Z")
    date_to   = now.strftime("%Y-%m-%dT%H:%M:%S.000Z")

    url = "https://platform.ringcentral.com/restapi/v1.0/account/~/call-log"
    headers = {
        "Authorization": f"Bearer {rc_token}",
        "Accept":        "application/json",
    }
    base_params = {
        "dateFrom":  date_from,
        "dateTo":    date_to,
        "direction": "Inbound",
        "type":      "Voice",
        "view":      "Detailed",
        "perPage":   250,
    }

    records = []
    page = 1
    page_sleep = 6.0
    max_429_retries = 3
    retries_on_this_page = 0

    while True:
        params = {**base_params, "page": page}
        try:
            raw = _requests.get(url, headers=headers, params=params, timeout=30)
        except Exception as e:
            print(f"[account_health] BulkCallLog page {page} exception: {e}")
            break

        if raw.status_code == 429:
            if retries_on_this_page >= max_429_retries:
                print(f"[account_health] BulkCallLog page {page}: 429 persistent, stopping")
                break
            retry_after = raw.headers.get("Retry-After")
            try:
                wait_s = int(retry_after) if retry_after else 30
            except ValueError:
                wait_s = 30
            retries_on_this_page += 1
            print(f"[account_health] BulkCallLog page {page}: 429, sleeping {wait_s}s")
            time.sleep(wait_s)
            continue

        retries_on_this_page = 0
        if raw.status_code != 200:
            print(f"[account_health] BulkCallLog page {page}: HTTP {raw.status_code} {raw.text[:200]}")
            break
        try:
            body = raw.json()
        except Exception as e:
            print(f"[account_health] BulkCallLog page {page}: bad JSON: {e}")
            break

        batch = body.get("records", [])
        records.extend(batch)
        print(f"[account_health] BulkCallLog page {page}: +{len(batch)} -> {len(records)} total")

        if len(records) >= max_records:
            records = records[:max_records]
            print(f"[account_health] BulkCallLog: hit max_records cap of {max_records}")
            break
        if not body.get("navigation", {}).get("nextPage"):
            break
        page += 1
        time.sleep(page_sleep)

    buckets = {}
    for rec in records:
        to_num = ((rec.get("to") or {}).get("phoneNumber") or "").strip()
        if not to_num:
            continue
        buckets.setdefault(to_num, []).append(rec)

    top_buckets = sorted(((n, len(r)) for n, r in buckets.items()), key=lambda x: -x[1])[:5]
    print(f"[account_health] BulkCallLog: {len(records)} records bucketed into "
          f"{len(buckets)} unique to-numbers. Top 5: {top_buckets}")
    return buckets


# ------------------------------------------------------------------
# Main entry point
# ------------------------------------------------------------------

def run_discovery(rc_token, days=30):
    """
    Runs the full account discovery — config + analytics + sankey.
    rc_token must be passed explicitly (no Flask session in background thread).

    days: time window for analytics and call log (7 or 30).
    """
    # Validate / clamp input
    if days not in (7, 30):
        print(f"[account_health] Invalid days={days}, defaulting to 30")
        days = 30
    print(f"[account_health] Starting discovery for last {days} days")

    config    = collect_config_data(rc_token)
    queue_ids = [q["id"] for q in config.get("queues", [])]
    q_analytics = collect_analytics_data(queue_ids, rc_token, days=days)

    # Merge queue analytics into the queue list (used by the UI metric tables)
    analytics_map = {a["id"]: a for a in q_analytics}
    for q in config["queues"]:
        q.update(analytics_map.get(q["id"], {}))

    # Per-user analytics (used by Sankey for direct-to-user DID leaves)
    u_analytics = collect_user_analytics_data(rc_token, days=days)

    # Strip the private extension lookup before returning to frontend.
    # (_phone_to_ext and _phone_usage are stripped AFTER build_sankey_data
    # uses them — see below.)
    config.pop("_ext_lookup", None)

    # Fetch top DIDs once — used both by Sankey and by the per-DID flow
    # cards that the frontend renders from /api/rc/trace-flow/company_<phone>.
    try:
        top_dids = get_top_dids(rc_token, limit=10, days=days)
    except Exception as e:
        print(f"[account_health] Failed to get top DIDs: {e}")
        top_dids = []
    config["top_dids"] = top_dids

    # Dict populated by build_sankey_data with each DID's raw tracer graph.
    # Reused by the frontend to render per-DID call flow cards WITHOUT
    # making fresh /api/rc/trace-flow/ calls (which would burn a second
    # round of RC rate budget and fail on large tenants).
    did_flows = {}

    try:
        config["sankey"] = build_sankey_data(
            rc_token,
            queue_ids=queue_ids,
            queue_analytics=q_analytics,
            user_analytics=u_analytics,
            top_n=10,
            days=days,
            top_dids=top_dids,
            did_flows_out=did_flows,
            phone_to_ext=config.get("_phone_to_ext", {}),
        )
    except Exception as e:
        import traceback
        print(f"[account_health] Sankey build failed: {e}")
        traceback.print_exc()
        config["sankey"] = {"nodes": [], "links": []}

    # Now that build_sankey_data is done, strip the private lookups.
    config.pop("_phone_to_ext", None)
    config.pop("_phone_usage",  None)

    config["did_flows"]           = did_flows
    config["discovery_timestamp"] = datetime.now(timezone.utc).isoformat()
    config["discovery_days"]      = days
    return config
