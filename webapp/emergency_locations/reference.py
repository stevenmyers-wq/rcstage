# webapp/emergency_locations/reference.py
"""Baked-in reference data + friendly-value resolution for Emergency Locations.

``reference_data.json`` is a committed snapshot of RingCentral's dictionaries
(``/dictionary/country``, ``/dictionary/state``, ``/dictionary/address-formats``),
captured via the tool's snapshot debug. It lets the tool resolve friendly values
an operator types (country/state/street-type by name) into the codes RC needs,
pick the right address format per country, and validate a row against RC's own
format spec BEFORE writing — so a bad row fails with a clear message instead of
RC silently dropping fields.

To refresh: run the "Generate reference snapshot" debug and replace
``reference_data.json`` with its output (same shape).
"""
import json
import os
import re
import threading

_REF = None
_LOCK = threading.Lock()


def _load():
    global _REF
    if _REF is None:
        with _LOCK:
            if _REF is None:
                path = os.path.join(os.path.dirname(__file__), "reference_data.json")
                with open(path, encoding="utf-8") as fh:
                    _REF = json.load(fh)
    return _REF


def loaded():
    """True if the reference data is present and non-empty."""
    try:
        return bool(_load().get("formats"))
    except Exception:
        return False


def _norm(value):
    return str(value if value is not None else "").strip().lower()


# ---------------------------------------------------------------------------
# Lookups
# ---------------------------------------------------------------------------

def resolve_country(value):
    """Match a country by internal id, ISO code, or name → country dict or None."""
    v = _norm(value)
    if not v:
        return None
    for c in _load().get("countries", []):
        if v in (_norm(c.get("id")), _norm(c.get("isoCode")), _norm(c.get("name"))):
            return c
    return None


def formats_for_country(country_id):
    return [f for f in _load().get("formats", []) if str(f.get("countryId")) == str(country_id)]


def primary_format_for_country(country_id):
    """The current (primary) emergency address format for a country, if any."""
    fmts = formats_for_country(country_id)
    for f in fmts:
        if f.get("primary"):
            return f
    return fmts[0] if fmts else None


def get_format(format_id):
    for f in _load().get("formats", []):
        if str(f.get("id")) == str(format_id):
            return f
    return None


def resolve_state(country_id, value):
    """Match a state by id, ISO code, or name within a country → state dict or None."""
    v = _norm(value)
    if not v:
        return None
    for s in _load().get("states", {}).get(str(country_id), []):
        if v in (_norm(s.get("id")), _norm(s.get("isoCode")), _norm(s.get("name"))):
            return s
    return None


def _field(fmt, field_id):
    for fl in (fmt.get("fields") or []):
        if fl.get("id") == field_id:
            return fl
    return None


def resolve_option(fmt, field_id, value):
    """For a field with an options enum, match value against key OR label → key."""
    fl = _field(fmt, field_id)
    if not fl or not fl.get("options"):
        return None
    v = _norm(value)
    for o in fl["options"]:
        if v in (_norm(o.get("key")), _norm(o.get("label"))):
            return o.get("key")
    return None


# ---------------------------------------------------------------------------
# Prepare + validate an address for a write
# ---------------------------------------------------------------------------

def prepare_address(address, format_id=None, country_hint=None, require_mandatory=True):
    """Resolve friendly values and validate an address against its RC format.

    ``address`` is the dict built from the sheet's ``Address.*`` columns (values
    may be friendly names). Returns ``(address2, format_id2, errors, notes)``:

      address2   — a copy with option fields normalised to their codes
                   (streetType "Drive"→"Dr"), state enriched with
                   stateId/stateName, and country/countryId filled in.
      format_id2 — the chosen addressFormatId (given one, else the country's
                   primary format).
      errors     — human-readable problems that would make RC reject/drop the
                   write (missing mandatory field, invalid option, regexp/length).
      notes      — friendly→code resolutions applied, for reporting.

    If the reference data can't identify a format (unknown country, or no
    reference loaded), the address is returned unchanged with no errors — the
    raw ``Address.*`` path still works exactly as before.
    """
    addr = dict(address or {})
    notes, errors = [], []

    if not loaded():
        return addr, format_id, errors, notes

    # 1. Resolve country from the address or a hint.
    country = (resolve_country(addr.get("country"))
               or resolve_country(addr.get("countryId"))
               or resolve_country(country_hint))
    if country:
        if _norm(addr.get("country")) != _norm(country.get("isoCode")):
            if addr.get("country"):
                notes.append(f"country {addr.get('country')!r}→{country.get('isoCode')}")
        addr["country"] = country.get("isoCode")
        addr["countryId"] = country.get("id")

    # 2. Pick the format: explicit id wins, else the country's primary format.
    fmt = get_format(format_id) if format_id else None
    if not fmt and country:
        fmt = primary_format_for_country(country.get("id"))
        if fmt:
            notes.append(f"addressFormatId→{fmt.get('id')} (primary for {country.get('isoCode')})")
    format_id2 = fmt.get("id") if fmt else format_id

    if not fmt:
        # Nothing to resolve/validate against — leave the raw address as-is.
        return addr, format_id2, errors, notes

    # 3. Normalise option fields (streetType, state, …) to their codes.
    for fl in (fmt.get("fields") or []):
        fid = fl.get("id")
        if not fl.get("options") or not addr.get(fid):
            continue
        key = resolve_option(fmt, fid, addr.get(fid))
        if key and _norm(key) != _norm(addr.get(fid)):
            notes.append(f"{fid} {addr.get(fid)!r}→{key}")
        if key:
            addr[fid] = key

    # 4. Enrich state with stateId / stateName from the dictionary.
    if country and addr.get("state"):
        st = resolve_state(country.get("id"), addr.get("state"))
        if st:
            addr["state"] = st.get("isoCode") or addr["state"]
            addr.setdefault("stateName", st.get("name"))
            addr["stateId"] = st.get("id")

    # 5. Validate against the format spec.
    errors = validate_address(fmt, addr, require_mandatory=require_mandatory)
    return addr, format_id2, errors, notes


def validate_address(fmt, address, require_mandatory=True):
    """List the ways ``address`` violates the format spec (empty list = valid)."""
    errs = []
    for fl in (fmt.get("fields") or []):
        fid = fl.get("id")
        val = str(address.get(fid, "") or "").strip()
        if not val:
            if require_mandatory and fl.get("mandatory"):
                errs.append(f"{fid} is required for addressFormatId {fmt.get('id')}")
            continue
        opts = fl.get("options") or []
        if opts and not any(_norm(o.get("key")) == _norm(val) for o in opts):
            sample = ", ".join(o.get("key") for o in opts[:12])
            errs.append(f"{fid}={val!r} is not a valid option (expected one of: {sample}…)")
        rx = fl.get("regexp")
        if rx:
            try:
                if not re.match(rx, val):
                    errs.append(f"{fid}={val!r} does not match required format {rx}")
            except re.error:
                pass
        ml = fl.get("maxLength")
        if ml and len(val) > ml:
            errs.append(f"{fid} is longer than the {ml}-char limit")
    return errs
