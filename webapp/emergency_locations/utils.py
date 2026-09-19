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
from . import reference

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
# DEBUG — reference snapshot (bake the coded values into the repo)
# ===============================================================
#
# The real RC dictionaries (confirmed from the Emergency Locations OpenAPI schema):
#   GET /restapi/v1.0/dictionary/country                         countries
#   GET /restapi/v1.0/dictionary/state?countryId={id}           states/provinces
#   GET /restapi/v1.0/dictionary/address-formats?addressType=Emergency
#                                                                emergency address formats
#   GET /restapi/v1.0/dictionary/address-formats/{id}           one format incl. fields+options
#
# build_reference_snapshot() assembles all of these into one JSON blob in the
# exact shape we commit as reference_data.json — run it once (debug button),
# drop the output in misc/1234, and it becomes the baked-in reference the
# template + backend friendly-value matching read from. Re-run to refresh.

COUNTRY_ENDPOINT = "/restapi/v1.0/dictionary/country"
STATE_ENDPOINT = "/restapi/v1.0/dictionary/state"
ADDRESS_FORMATS_ENDPOINT = "/restapi/v1.0/dictionary/address-formats"


def _json_or_text(response):
    """Best-effort JSON body from an RC response, falling back to trimmed text."""
    if response is None:
        return {"error": "no response"}
    try:
        return response.json()
    except Exception:
        return {"raw": (getattr(response, 'text', '') or '')[:2000]}


def _safe_rc_path(path):
    """Constrain a free-form path to the RingCentral REST API.

    Prevents the passthrough from being pointed at an arbitrary host: only
    ``/restapi/...`` relative paths are allowed (absolute URLs are rejected).
    """
    path = (path or "").strip()
    if not path or path.lower().startswith(("http://", "https://")):
        return None
    if not path.startswith("/"):
        path = "/" + path
    return path if path.startswith("/restapi/") else None


def _dict_records(endpoint):
    """Page through an RC dictionary endpoint, returning trimmed id/code/name rows."""
    rows = []
    for rec in _get_all_records(endpoint):
        rows.append({
            "id": rec.get("id"),
            "isoCode": rec.get("isoCode"),
            "name": rec.get("name"),
        })
    return rows


def _trim_format(detail):
    """Reduce an address-format resource to the fields the tool needs.

    Keeps each field's id/name/type, the metadata that governs validation
    (mandatory, regexp, maxLength, order, defaultValue, hidden) and — crucially —
    the allowed ``options`` (e.g. the streetType {label, key} enum). Also keeps
    the format's country, primary/allowed flags, validation version and the
    MandatoryGroup dependencies that explain why partial rows are rejected.
    """
    country = detail.get("country") or {}
    fields = []
    for fld in (detail.get("fields") or []):
        md = fld.get("metadata") or {}
        fields.append({
            "id": fld.get("id"),
            "name": fld.get("name"),
            "type": fld.get("type"),
            "mandatory": md.get("mandatory"),
            "regexp": md.get("regexp"),
            "maxLength": md.get("maxLength"),
            "order": md.get("order"),
            "defaultValue": md.get("defaultValue"),
            "hidden": md.get("hidden"),
            "options": [{"label": o.get("label"), "key": o.get("key")}
                        for o in (fld.get("options") or [])],
        })
    return {
        "id": detail.get("id"),
        "countryId": country.get("id"),
        "countryIso": country.get("isoCode"),
        "countryName": country.get("name"),
        "addressFormatImposed": country.get("addressFormatImposed"),
        "validationVersion": detail.get("validationVersion"),
        "primary": detail.get("primary"),
        # NB: schema says `allowed` means "is the format deprecated".
        "allowed": detail.get("allowed"),
        "applicableToAddressTypes": detail.get("applicableToAddressTypes"),
        "dependencies": detail.get("dependencies"),
        "fields": fields,
    }


def build_reference_snapshot():
    """Assemble the full ERL reference (countries, states, formats) as one JSON.

    Output is the exact shape committed as reference_data.json:
      { schemaVersion, countries[], states{countryId: [...]}, formats[] }
    States are fetched only for countries that have an emergency address format
    (the relevant subset), each format is expanded via its detail endpoint so we
    capture fields + streetType options.
    """
    countries = _dict_records(COUNTRY_ENDPOINT)

    formats = []
    country_ids = set()
    for fmt in _get_all_records(f"{ADDRESS_FORMATS_ENDPOINT}?addressType=Emergency"):
        fid = fmt.get("id")
        detail = rc_api_call(f"{ADDRESS_FORMATS_ENDPOINT}/{fid}") if fid else None
        trimmed = _trim_format(detail if isinstance(detail, dict) and detail.get("fields") else fmt)
        formats.append(trimmed)
        if trimmed.get("countryId"):
            country_ids.add(str(trimmed["countryId"]))

    states = {}
    for cid in sorted(country_ids, key=lambda x: int(x) if str(x).isdigit() else 0):
        states[cid] = _dict_records(f"{STATE_ENDPOINT}?countryId={cid}")

    return {
        "schemaVersion": 1,
        "counts": {"countries": len(countries),
                   "formats": len(formats),
                   "statesForCountries": len(states)},
        "countries": countries,
        "states": states,
        "formats": formats,
    }


def explore_dictionary(kind=None, country_id=None, path=None, format_id=None):
    """Look up the coded values behind ERL fields.

    kinds:
      snapshot — assemble the full reference (countries + states + formats incl.
                 fields/options) in the shape committed as reference_data.json.
      formats  — /dictionary/address-formats?addressType=Emergency (optionally
                 &countryId=…): the emergency address formats.
      format   — /dictionary/address-formats/{id}: one format incl. fields+options.
      (path)   — raw passthrough of any /restapi/… path.
    """
    if path:
        safe = _safe_rc_path(path)
        if not safe:
            return {"error": "Path must be a RingCentral API path starting with /restapi/."}
        resp = rc_api_call(safe, return_response=True)
        return {"mode": "path", "path": safe,
                "status": getattr(resp, 'status_code', None), "body": _json_or_text(resp)}

    kind = (kind or "").lower()

    if kind == "snapshot":
        return {"mode": "snapshot", **build_reference_snapshot()}

    if kind == "formats":
        ep = f"{ADDRESS_FORMATS_ENDPOINT}?addressType=Emergency"
        if country_id:
            ep += f"&countryId={country_id}"
        return {"mode": "formats", "records": _get_all_records(ep)}

    if kind == "format":
        if not format_id:
            return {"error": "format_id is required for a single address format."}
        return {"mode": "format", "id": str(format_id),
                "format": rc_api_call(f"{ADDRESS_FORMATS_ENDPOINT}/{format_id}")}

    return {"error": "Unknown dictionary kind. Use snapshot, formats, format, or a path."}


# ===============================================================
# DEBUG — raw test write (iterate on the exact body that sticks)
# ===============================================================

def test_write(body, location_id=None):
    """Send one exact body to the ERL endpoint and report the full round-trip.

    Bounded to the emergency-locations resource: POST to create when
    ``location_id`` is omitted, otherwise PUT to that id. Returns the request
    sent, RC's raw status + response body, and a fresh GET of the resulting
    record — so an operator can pin down precisely which body makes structured
    fields (buildingNumber / streetType) persist, and see any validation error
    or normalisation RC applies. Read-heavy debugging tool; it does write, so it
    is only reachable behind the same auth as the rest of the tool.
    """
    if not isinstance(body, dict) or not body:
        return {"error": "A non-empty JSON body is required."}

    if location_id:
        resp = rc_api_call(f"{ERL_ENDPOINT}/{location_id}", method="PUT",
                           json=body, return_response=True)
        verb = "PUT"
    else:
        resp = rc_api_call(ERL_ENDPOINT, method="POST", json=body, return_response=True)
        verb = "POST"

    resp_body = _json_or_text(resp)
    result_id = location_id
    if not result_id and isinstance(resp_body, dict):
        result_id = resp_body.get('id')

    stored = None
    if result_id:
        stored = rc_api_call(f"{ERL_ENDPOINT}/{result_id}")

    return {
        "verb": verb,
        "endpoint": ERL_ENDPOINT + (f"/{location_id}" if location_id else ""),
        "requestBody": body,
        "status": getattr(resp, 'status_code', None),
        "ok": getattr(resp, 'ok', False),
        "response": resp_body,
        "storedAfter": stored,
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


def _account_sites_by_name():
    """{lower site name: site id} for resolving a friendly SiteId on upload.

    Also maps the id to itself and the literal 'main-site' so a row that already
    carries an id (or the company-main sentinel) passes through unchanged.
    """
    mapping = {}
    try:
        for s in _get_all_records("/restapi/v1.0/account/~/sites"):
            sid = s.get("id")
            if sid is None:
                continue
            sid = str(sid)
            mapping[sid.lower()] = sid
            name = s.get("name")
            if name:
                mapping[str(name).strip().lower()] = sid
    except Exception as e:
        print(f"Emergency Locations: could not load sites for name resolution: {e}")
    mapping.setdefault("main-site", "main-site")
    return mapping


def _resolve_site_id(value, sites_by_name):
    """Turn a SiteId cell (an id, 'main-site', or a site NAME) into a site id."""
    raw = str(value or "").strip()
    if not raw:
        return raw
    return sites_by_name.get(raw.lower(), raw)


def _build_location_body(record, address_columns, require_mandatory=True, sites_by_name=None):
    """Construct the create/update request body from a sheet row.

    Returns ``(body, errors, notes)``. Friendly values (country/state/street-type
    by name) are resolved to RC codes via the baked-in reference, the country's
    primary address format is chosen when none is given, and the address is
    validated against RC's format spec. ``errors`` (missing mandatory field,
    invalid option, bad regexp/length) let the caller fail the row BEFORE the
    write instead of RC silently dropping fields; ``notes`` records what was
    auto-resolved. If the reference can't identify a format, the raw address is
    used unchanged and there are no errors (raw ``Address.*`` still works).
    """
    body = {"name": str(record.get("Name", "")).strip()}

    visibility = str(record.get("Visibility", "")).strip()
    if visibility:
        body["visibility"] = visibility

    site_id = str(record.get("SiteId", "")).strip()
    if site_id:
        # SiteId may be a friendly site NAME — resolve it to the id RC needs.
        if sites_by_name:
            site_id = _resolve_site_id(site_id, sites_by_name)
        body["site"] = {"id": site_id}

    address_format_id = str(record.get("AddressFormatId", "")).strip() or None
    address = _address_from_row(record, address_columns)

    # A "Country" meta column (friendly) can seed resolution when the address
    # itself doesn't carry a country.
    country_hint = str(record.get("Country", "")).strip() or None

    errors, notes = [], []
    if address:
        address, address_format_id, errors, notes = reference.prepare_address(
            address, format_id=address_format_id, country_hint=country_hint,
            require_mandatory=require_mandatory)

    if address_format_id:
        body["addressFormatId"] = address_format_id
    if address:
        body["address"] = address
    return body, errors, notes


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

    # Stored top-level context — the likely reason a structured field is dropped
    # is the address failing validation (addressStatus) or the format not being
    # switched (addressFormatId/Status). Surface it alongside the dropped list.
    ctx = (f"stored addressFormatId={stored.get('addressFormatId')}, "
           f"addressFormatStatus={stored.get('addressFormatStatus')}, "
           f"addressStatus={stored.get('addressStatus')}")

    if dropped:
        msg = (f"applied, but {len(dropped)} field(s) did NOT stick "
               f"({ctx}): " + "; ".join(dropped))
        if changed:
            msg += f". RC also normalised: {'; '.join(changed)}"
        return msg, dropped, changed
    if changed:
        return f"applied ({ctx}); RC normalised: " + "; ".join(changed), dropped, changed
    return f"applied — all sent fields stored as-is ({ctx}).", dropped, changed


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
    # Resolve friendly site names → ids once for the whole run.
    sites_by_name = _account_sites_by_name()
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
        notes = []

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
                # On MODIFY only validate the fields that are supplied (a partial
                # update is legitimate), so don't enforce mandatory-presence.
                body, errs, notes = _build_location_body(record, address_columns,
                                                         require_mandatory=False,
                                                         sites_by_name=sites_by_name)
                if not body.get("name"):
                    raise ValueError("Name is required.")
                if errs:
                    raise ValueError("Address rejected before sending — " + "; ".join(errs))
                response = rc_api_call(f"{ERL_ENDPOINT}/{loc_id}",
                                       method="PUT", json=body, return_response=True)
                verb = "MODIFY"
            else:  # NEW
                body, errs, notes = _build_location_body(record, address_columns,
                                                         require_mandatory=True,
                                                         sites_by_name=sites_by_name)
                if not body.get("name"):
                    raise ValueError("Name is required.")
                if not body.get("address"):
                    raise ValueError("At least one Address.* field is required to create a location.")
                if errs:
                    # Pre-flight: fail clearly instead of letting RC silently drop fields.
                    raise ValueError("Address rejected before sending — " + "; ".join(errs))
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
                    msg = prefix + diff_msg
                    if notes:
                        msg += " | auto-resolved: " + "; ".join(notes)
                    item = {"name": label, "status": status,
                            "message": msg, "locationId": new_id,
                            "dropped": dropped, "changed": changed, "notes": notes}
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
