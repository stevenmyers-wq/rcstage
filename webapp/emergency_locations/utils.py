# webapp/emergency_locations/utils.py
"""Business logic for the Emergency Locations (ERL) tool.

Audit, create, update and delete RingCentral account Emergency Response
Locations (the regulated E911 / emergency addresses attached to an account) via
a flat spreadsheet — one row per location.

RingCentral Emergency Locations API (account-level ERL registry):
  GET    /restapi/v1.0/account/~/emergency-locations            list ERLs
  GET    /restapi/v1.0/account/~/emergency-locations/{id}       read one ERL
  POST   /restapi/v1.0/account/~/emergency-locations            create an ERL
  PUT    /restapi/v1.0/account/~/emergency-locations/{id}       update an ERL
  DELETE /restapi/v1.0/account/~/emergency-locations/{id}       delete an ERL
  GET    /restapi/v1.0/account/~/sites                          site list (labels)

Format-agnostic by design
--------------------------
Non-US / international ERLs use a different `address` shape than US ones. Rather
than hard-code a US schema, the audit *discovers* address columns from live data:
every key seen under any location's ``address`` object becomes an ``Address.<key>``
column. Create/update then rebuilds the ``address`` object from whichever
``Address.*`` columns are present on the row. A US and a non-US account therefore
export (and re-import) with whatever fields RC actually returns for each, with no
code change. The ``/raw`` debug endpoint (see routes.py) dumps untouched JSON so
an operator can inspect the real international shape.
"""
from webapp.rc_api import rc_api_call
from webapp import task_control

ERL_ENDPOINT = "/restapi/v1.0/account/~/emergency-locations"

# Fixed leading columns of the sheet. Everything AFTER these that starts with
# the address prefix is treated as an address field. Both this module and the
# frontend agree on this set so an uploaded sheet can be split back into
# metadata vs. address columns.
#
# AddressFormatId is the linchpin of the international ("special") format: it is
# a top-level field (NOT under address) whose value depends on the country
# (verified against live data: US=19, AU=33, NZ=174) and determines which
# address sub-fields RC expects. It must round-trip and be sent on create/update
# so non-US locations validate. AddressFormatStatus (Actual/Outdated) is the
# RC-managed audit companion.
META_COLUMNS = [
    "Action", "LocationId", "Name", "Visibility", "SiteId", "SiteName",
    "AddressFormatId", "AddressFormatStatus", "UsageStatus", "AddressStatus",
]

# Address columns are namespaced so they never collide with a meta column and so
# the frontend can identify them without a hard-coded list.
ADDR_PREFIX = "Address."

# Columns that reflect RC-managed state — shown for audit context but never sent
# back on a create/update (RC rejects or ignores operator-supplied values here).
READ_ONLY_META = {"UsageStatus", "AddressStatus", "AddressFormatStatus", "SiteName"}

# Accepted "granted/true" style tokens are not needed here (no matrix ticks), but
# visibility must be one of RC's enum values when supplied.
VISIBILITY_VALUES = ["Private", "Public"]


def _get_all_records(base_endpoint):
    """Fetch every record across all pages for a paginated RC list endpoint."""
    all_records = []
    page = 1
    per_page = 1000
    while True:
        separator = '&' if '?' in base_endpoint else '?'
        endpoint = f"{base_endpoint}{separator}page={page}&perPage={per_page}"
        response = rc_api_call(endpoint)
        if not response or 'records' not in response:
            break
        all_records.extend(response['records'])

        navigation = response.get('navigation', {})
        if 'nextPage' in navigation:
            page += 1
            continue
        paging = response.get('paging', {})
        if page < paging.get('totalPages', 1):
            page += 1
        else:
            break
    return all_records


def _site_name_lookup():
    """Map siteId -> site name so the audit can label each ERL's site.

    Best-effort: if the sites call fails the audit still runs (names blank).
    """
    lookup = {}
    try:
        for site in _get_all_records("/restapi/v1.0/account/~/sites"):
            sid = str(site.get('id', '')).strip()
            if sid:
                lookup[sid] = site.get('name', '')
    except Exception as e:
        print(f"Emergency Locations: could not load sites for labelling: {e}")
    return lookup


def _flatten_address(address):
    """Return {'Address.<key>': value} for every scalar key in an address object.

    Nested objects (RC sometimes nests state/country as {id,name}) are flattened
    one level deeper as ``Address.state.name`` etc. so no information is lost and
    the exact intl shape round-trips.
    """
    flat = {}
    if not isinstance(address, dict):
        return flat
    for key, value in address.items():
        col = f"{ADDR_PREFIX}{key}"
        if isinstance(value, dict):
            for sub_key, sub_val in value.items():
                if not isinstance(sub_val, (dict, list)):
                    flat[f"{col}.{sub_key}"] = sub_val
        elif isinstance(value, list):
            continue  # addresses have no list fields today; skip defensively
        else:
            flat[col] = value
    return flat


# ===============================================================
# AUDIT
# ===============================================================

def fetch_locations(site_id=None):
    """Stream every emergency location as a flat row, with live progress.

    ``site_id`` optionally scopes the audit to a single site.

    Emits NDJSON-friendly chunks:
      {"type": "start",   "message": ...}
      {"type": "columns", "address": [ ...Address.* columns... ]}
      {"type": "total",   "total": N}
      {"type": "progress","current": i, "total": N, "name": ...}
      {"type": "done",    "data": [ ...rows... ], "address": [...]}
    """
    yield {"type": "start", "message": "Loading sites…"}
    sites = _site_name_lookup()

    yield {"type": "start", "message": "Loading emergency locations…"}
    endpoint = ERL_ENDPOINT
    if site_id:
        endpoint = f"{ERL_ENDPOINT}?siteId={site_id}"
    summaries = _get_all_records(endpoint)

    total = len(summaries)
    yield {"type": "total", "total": total}

    if not summaries:
        yield {"type": "done", "data": [], "address": []}
        return

    rows = []
    address_columns = []          # ordered, de-duplicated across all rows
    seen_addr = set()

    for i, summary in enumerate(summaries):
        loc_id = summary.get('id')
        # The list resource already carries the full location for most accounts,
        # but read the detail so any fields only present on the single-resource
        # response (and the true intl shape) are captured.
        detail = rc_api_call(f"{ERL_ENDPOINT}/{loc_id}") or {}
        if not detail or 'errorCode' in detail:
            detail = summary  # fall back so the row still appears

        site = detail.get('site') or {}
        site_id_val = str(site.get('id', '') or '').strip()
        addr_flat = _flatten_address(detail.get('address'))

        for col in addr_flat:
            if col not in seen_addr:
                seen_addr.add(col)
                address_columns.append(col)

        row = {
            "Action": "",  # operator sets NEW / MODIFY / DELETE
            "LocationId": loc_id or "",
            "Name": detail.get('name', summary.get('name', '')),
            "Visibility": detail.get('visibility', summary.get('visibility', '')),
            "SiteId": site_id_val,
            "SiteName": site.get('name') or sites.get(site_id_val, ''),
            # Top-level (not under address); country-specific — see META_COLUMNS.
            "AddressFormatId": detail.get('addressFormatId', ''),
            "AddressFormatStatus": detail.get('addressFormatStatus', ''),
            "UsageStatus": detail.get('usageStatus', ''),
            "AddressStatus": detail.get('addressStatus', ''),
        }
        row.update(addr_flat)
        rows.append(row)

        yield {
            "type": "progress",
            "current": i + 1,
            "total": total,
            "name": row["Name"] or loc_id,
        }

    # Keep a stable, readable order for the address columns.
    address_columns = sorted(address_columns)
    yield {"type": "columns", "address": address_columns}

    # Backfill missing address cells so every row has every discovered column
    # (json_to_sheet on the frontend needs consistent keys).
    for row in rows:
        for col in address_columns:
            row.setdefault(col, "")

    yield {"type": "done", "data": rows, "address": address_columns}


# ===============================================================
# DEBUG — raw example (international format inspection)
# ===============================================================

def fetch_raw_examples(location_id=None, limit=3):
    """Return raw ERL JSON, untouched, for format inspection.

    - ``location_id`` given → the single-resource GET response for that ERL.
    - otherwise → the raw list response plus the detail of the first ``limit``
      locations, so an operator can compare the list shape with the per-id shape
      (and capture a real non-US address structure).
    """
    if location_id:
        detail = rc_api_call(f"{ERL_ENDPOINT}/{location_id}", return_response=True)
        body = None
        try:
            body = detail.json()
        except Exception:
            body = {"error": getattr(detail, 'text', 'no body')}
        return {
            "mode": "single",
            "locationId": location_id,
            "status": getattr(detail, 'status_code', None),
            "location": body,
        }

    list_resp = rc_api_call(ERL_ENDPOINT, return_response=True)
    try:
        list_body = list_resp.json()
    except Exception:
        list_body = {"error": getattr(list_resp, 'text', 'no body')}

    examples = []
    records = (list_body or {}).get('records', []) if isinstance(list_body, dict) else []
    for rec in records[:max(0, int(limit or 0))]:
        rid = rec.get('id')
        if not rid:
            continue
        detail = rc_api_call(f"{ERL_ENDPOINT}/{rid}")
        examples.append({"id": rid, "detail": detail})

    return {
        "mode": "list",
        "status": getattr(list_resp, 'status_code', None),
        "list": list_body,
        "details": examples,
    }


# ===============================================================
# CREATE / UPDATE / DELETE
# ===============================================================

def _address_from_row(record, address_columns):
    """Rebuild the RC ``address`` object from the row's ``Address.*`` columns.

    Format-agnostic: whatever ``Address.<key>`` columns exist on the sheet are
    written straight back under ``address``. Nested keys (``Address.state.name``)
    are re-nested. Blank cells are dropped so we don't overwrite a stored value
    with an empty string on MODIFY.
    """
    address = {}
    for col in address_columns:
        if not col.startswith(ADDR_PREFIX):
            continue
        raw = record.get(col, "")
        value = str(raw).strip() if raw is not None else ""
        if value == "":
            continue
        path = col[len(ADDR_PREFIX):]  # e.g. "street" or "state.name"
        parts = path.split(".")
        cursor = address
        for part in parts[:-1]:
            cursor = cursor.setdefault(part, {})
            if not isinstance(cursor, dict):
                # Conflicting flat + nested column for the same key — keep flat.
                cursor = {}
        cursor[parts[-1]] = value
    return address


def _build_location_body(record, address_columns):
    """Construct the create/update request body from a sheet row."""
    body = {"name": str(record.get("Name", "")).strip()}

    visibility = str(record.get("Visibility", "")).strip()
    if visibility:
        body["visibility"] = visibility

    site_id = str(record.get("SiteId", "")).strip()
    if site_id:
        body["site"] = {"id": site_id}

    # Top-level, country-specific format id — required for non-US ("special
    # format") locations to validate. Send it whenever the row carries one.
    address_format_id = str(record.get("AddressFormatId", "")).strip()
    if address_format_id:
        body["addressFormatId"] = address_format_id

    address = _address_from_row(record, address_columns)
    if address:
        body["address"] = address
    return body


def _flatten_scalars(obj, prefix=""):
    """Flatten a dict of (possibly nested) scalars to {dotted_key: str_value}."""
    flat = {}
    if not isinstance(obj, dict):
        return flat
    for key, value in obj.items():
        dotted = f"{prefix}{key}"
        if isinstance(value, dict):
            flat.update(_flatten_scalars(value, prefix=f"{dotted}."))
        elif not isinstance(value, list):
            flat[dotted] = "" if value is None else str(value)
    return flat


def _stored_state(response, loc_id):
    """The location as RingCentral stored it after a write.

    Prefers the write response body; falls back to a fresh GET so we always
    compare against ground truth even when POST/PUT returns no body.
    """
    body = None
    try:
        body = response.json() if response is not None else None
    except Exception:
        body = None
    if not isinstance(body, dict) or 'address' not in body:
        rid = (body or {}).get('id') if isinstance(body, dict) else None
        rid = rid or loc_id
        if rid:
            fresh = rc_api_call(f"{ERL_ENDPOINT}/{rid}")
            if isinstance(fresh, dict):
                body = fresh
    return body if isinstance(body, dict) else None


def _write_diff(sent_body, stored):
    """Compare what we sent vs what RC stored; flag fields that didn't stick.

    Returns (message, dropped, changed):
      dropped — fields we sent that RC did not store (empty/absent) — e.g. a
                structured-only field like ``address.streetType`` sent against a
                flat/outdated ``addressFormatId``. This is the "didn't stick" case.
      changed — fields RC stored with a different (non-empty) value than sent,
                i.e. RC normalised/validated the input (not a failure).
    """
    if not isinstance(stored, dict):
        return "written (RC returned no body to verify).", [], []

    sent = {}
    if isinstance(sent_body.get('address'), dict):
        sent.update(_flatten_scalars(sent_body['address'], prefix="address."))
    if sent_body.get('addressFormatId'):
        sent['addressFormatId'] = str(sent_body['addressFormatId'])

    stored_flat = {}
    if isinstance(stored.get('address'), dict):
        stored_flat.update(_flatten_scalars(stored['address'], prefix="address."))
    if stored.get('addressFormatId') is not None:
        stored_flat['addressFormatId'] = str(stored['addressFormatId'])

    dropped, changed = [], []
    for key, sent_val in sent.items():
        stored_val = stored_flat.get(key, "")
        if stored_val.strip() == "":
            dropped.append(f"{key}={sent_val!r}")
        elif stored_val.strip().lower() != sent_val.strip().lower():
            changed.append(f"{key}: sent {sent_val!r} → stored {stored_val!r}")

    if dropped:
        fmt = stored_flat.get('addressFormatId', '?')
        msg = (f"applied, but {len(dropped)} field(s) did NOT stick "
               f"(RC dropped them for addressFormatId {fmt}): " + "; ".join(dropped))
        if changed:
            msg += f". RC also normalised: {'; '.join(changed)}"
        return msg, dropped, changed
    if changed:
        return "applied (RC normalised some values): " + "; ".join(changed), dropped, changed
    return "applied — all sent fields stored as-is.", dropped, changed


def apply_locations_from_records(records, address_columns, task_id=None):
    """Stream create/update/delete of ERLs, one chunk per record.

    Only rows whose Action is NEW, MODIFY or DELETE are acted on.
      NEW    -> POST   /restapi/v1.0/account/~/emergency-locations
      MODIFY -> PUT    /restapi/v1.0/account/~/emergency-locations/{id}
      DELETE -> DELETE /restapi/v1.0/account/~/emergency-locations/{id}
    """
    address_columns = address_columns or []
    total = len(records)
    yield {"type": "start", "total": total,
           "message": f"Applying {total} location change{'' if total == 1 else 's'}…"}
    results = []

    for i, record in enumerate(records):
        # Cooperative stop: locations already written stand; the rest are skipped.
        if task_control.is_stopped(task_id):
            item = {"name": "—", "status": "cancelled",
                    "message": "Stopped by user — remaining locations were skipped."}
            results.append(item)
            yield {"type": "progress", "current": i, "total": total, "result": item}
            break

        action = str(record.get("Action", "")).strip().upper()
        name = str(record.get("Name", "")).strip()
        loc_id = str(record.get("LocationId", "")).strip()
        label = name or loc_id or f"Row {i + 1}"

        if action not in ("NEW", "MODIFY", "DELETE"):
            # Advance the bar even for untouched rows so it tracks true position.
            yield {"type": "progress", "current": i + 1, "total": total}
            continue

        try:
            if action == "DELETE":
                if not loc_id:
                    raise ValueError("LocationId is required to DELETE a location.")
                response = rc_api_call(f"{ERL_ENDPOINT}/{loc_id}",
                                       method="DELETE", return_response=True)
                verb = "DELETE"
            elif action == "MODIFY":
                if not loc_id:
                    raise ValueError("LocationId is required to MODIFY a location.")
                body = _build_location_body(record, address_columns)
                if not body.get("name"):
                    raise ValueError("Name is required.")
                response = rc_api_call(f"{ERL_ENDPOINT}/{loc_id}",
                                       method="PUT", json=body, return_response=True)
                verb = "MODIFY"
            else:  # NEW
                body = _build_location_body(record, address_columns)
                if not body.get("name"):
                    raise ValueError("Name is required.")
                if not body.get("address"):
                    raise ValueError("At least one Address.* field is required to create a location.")
                response = rc_api_call(ERL_ENDPOINT,
                                       method="POST", json=body, return_response=True)
                verb = "NEW"

            if response is not None and getattr(response, 'ok', False):
                if verb == "DELETE":
                    item = {"name": label, "status": "success",
                            "message": "DELETE succeeded.", "locationId": loc_id}
                else:
                    stored = _stored_state(response, loc_id)
                    new_id = (stored or {}).get('id') or loc_id
                    diff_msg, dropped, changed = _write_diff(body, stored)
                    prefix = (f"NEW succeeded (location {new_id}) — " if verb == "NEW"
                              else "MODIFY succeeded — ")
                    # A dropped field means the write "didn't stick" — surface it as
                    # a warning so the operator sees it in the log and results file.
                    status = "warning" if dropped else "success"
                    item = {"name": label, "status": status,
                            "message": prefix + diff_msg, "locationId": new_id,
                            "dropped": dropped, "changed": changed}
            else:
                detail = _error_detail(response)
                item = {"name": label, "status": "error",
                        "message": f"{verb} failed: {detail}"}
        except Exception as e:
            print(f"ERROR processing location '{label}': {e}")
            item = {"name": label, "status": "error", "message": str(e)}

        results.append(item)
        yield {"type": "progress", "current": i + 1, "total": total, "result": item}

    yield {"type": "done", "results": results}


def _error_detail(response):
    """Pull a human-readable message out of an RC error response."""
    if response is None:
        return "no response from RingCentral."
    try:
        data = response.json()
    except Exception:
        return (getattr(response, 'text', '') or 'unknown error').strip()[:400]
    if isinstance(data, dict):
        if data.get('errors'):
            parts = [e.get('message', '') for e in data['errors'] if isinstance(e, dict)]
            joined = "; ".join(p for p in parts if p)
            if joined:
                return joined
        if data.get('message'):
            return data['message']
    return str(data)[:400]
