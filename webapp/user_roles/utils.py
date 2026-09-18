# webapp/user_roles/utils.py
"""Business logic for the User Roles tool.

Audit, create and update RingCentral custom user roles via a permission-matrix
XLSX (one row per role, one column per assignable permission, "X" = granted).

RingCentral Role Management API (verified against RingCentral's public SDKs):
  GET    /restapi/v1.0/account/~/user-role            list roles
  GET    /restapi/v1.0/account/~/user-role/{roleId}   read a role (incl. permissions)
  POST   /restapi/v1.0/account/~/user-role            create a custom role
  PUT    /restapi/v1.0/account/~/user-role/{roleId}   update a custom role
  GET    /restapi/v1.0/dictionary/permission          full assignable permission set

Predefined (non-custom) roles are read-only and are skipped on write.
"""
from webapp.rc_api import rc_api_call
from webapp import task_control

# Fixed leading columns of the matrix sheet. Everything AFTER these is treated
# as a permission-id column. Both this module and the frontend agree on this set
# so an uploaded sheet can be split back into metadata vs. permission columns.
META_COLUMNS = [
    "Action", "RoleID", "DisplayName", "Description",
    "Scope", "Custom", "Editable", "PermissionCount",
]

# Valid values for the RoleResource.scope enum (used to hint the operator).
SCOPE_VALUES = [
    "Account", "AllExtensions", "Federation", "Group",
    "NonUserExtensions", "RoleBased", "Self", "UserExtensions",
]

# Process-level cache of permission flags from /dictionary/permission.
_PERM_META_CACHE = None


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


def _assignable_permission_ids():
    """Return the sorted list of assignable permission IDs from the RC dictionary.

    These become the matrix columns. Only *assignable* permissions are included
    ("can be assigned by the account administrator"); including non-assignable
    permissions in a create/update body makes RingCentral reject or ignore the
    permission set.
    """
    return sorted(_permission_metadata()['assignable'])


def _permission_metadata():
    """Fetch permission flags from the RC dictionary.

    Returns {'assignable': set(ids), 'readonly': set(ids), 'all': set(ids)}.
    Cached per process since the catalog is account-independent and stable
    within a session.
    """
    global _PERM_META_CACHE
    if _PERM_META_CACHE is not None:
        return _PERM_META_CACHE

    records = _get_all_records("/restapi/v1.0/dictionary/permission")
    assignable, readonly, every = set(), set(), set()
    for perm in records:
        pid = perm.get('id')
        if not pid:
            continue
        every.add(pid)
        if bool(perm.get('readOnly', False)):
            readonly.add(pid)
        # 'assignable' = an admin may put this permission on a role.
        if bool(perm.get('assignable', True)):
            assignable.add(pid)
    _PERM_META_CACHE = {'assignable': assignable, 'readonly': readonly, 'all': every}
    return _PERM_META_CACHE


def _permission_is_granted(perm):
    """Whether a role's permission entry is actually granted.

    With ``advancedPermissions=true`` each entry carries a
    ``permissionsCapabilities`` object whose ``enabled`` flag is the real grant;
    an entry can be present but disabled. Without capabilities (simple view) the
    entry's mere presence means granted.
    """
    caps = perm.get('permissionsCapabilities')
    if isinstance(caps, dict) and 'enabled' in caps:
        return bool(caps.get('enabled'))
    return True


def _role_permission_ids(detail):
    """Set of permission IDs a role actually grants (enabled)."""
    held = set()
    for perm in (detail.get('permissions') or []):
        pid = perm.get('id')
        if pid and _permission_is_granted(perm):
            held.add(pid)
    return held


# ===============================================================
# AUDIT
# ===============================================================

def fetch_roles(category='all'):
    """Stream every user role as a flat matrix row, with live progress.

    ``category`` selects which roles to include:
      'all'    – every role (predefined + custom)
      'custom' – custom roles only

    Emits NDJSON-friendly chunks:
      {"type": "start",   "message": ...}
      {"type": "columns", "permissions": [ ...permission ids... ]}
      {"type": "total",   "total": N}
      {"type": "progress","current": i, "total": N, "name": ...}
      {"type": "done",    "data": [ ...rows... ], "permissions": [...]}
    """
    category = (category or 'all').lower()

    yield {"type": "start", "message": "Loading permission dictionary…"}
    permission_columns = _assignable_permission_ids()
    yield {"type": "columns", "permissions": permission_columns}

    label = "custom user roles" if category == 'custom' else "account user roles"
    yield {"type": "start", "message": f"Loading {label}…"}
    role_summaries = _get_all_records("/restapi/v1.0/account/~/user-role")

    if category == 'custom':
        # The collection resource carries the `custom` flag, so we can filter
        # before spending a detail call per role.
        role_summaries = [r for r in role_summaries if r.get('custom')]

    total = len(role_summaries)
    yield {"type": "total", "total": total}

    if not role_summaries:
        yield {"type": "done", "data": [], "permissions": permission_columns}
        return

    rows = []
    for i, summary in enumerate(role_summaries):
        role_id = summary.get('id')
        # The detail call carries the permissions array; advancedPermissions=true
        # makes RC include permissionsCapabilities so we can read the enabled flag.
        detail = rc_api_call(
            f"/restapi/v1.0/account/~/user-role/{role_id}?advancedPermissions=true"
        ) or {}
        if 'errorCode' in detail or not detail:
            detail = summary  # fall back to the summary so the row still appears

        is_custom = bool(detail.get('custom', summary.get('custom', False)))
        held = _role_permission_ids(detail)
        # Count only editable perms so the number matches the visible X columns
        # (RC-managed read-only baseline perms aren't shown as columns).
        held_editable = held & set(permission_columns)

        row = {
            "Action": "",  # operator sets NEW or MODIFY
            "RoleID": role_id or "",
            "DisplayName": detail.get('displayName', summary.get('displayName', '')),
            "Description": detail.get('description', summary.get('description', '')),
            "Scope": detail.get('scope', summary.get('scope', '')),
            "Custom": "true" if is_custom else "false",
            "Editable": "Yes" if is_custom else "No (predefined)",
            "PermissionCount": len(held_editable),
        }
        for pid in permission_columns:
            row[pid] = "X" if pid in held else ""

        rows.append(row)
        yield {
            "type": "progress",
            "current": i + 1,
            "total": total,
            "name": row["DisplayName"] or role_id,
        }

    yield {"type": "done", "data": rows, "permissions": permission_columns}


# ===============================================================
# CREATE / UPDATE
# ===============================================================

def _permissions_from_row(record, permission_columns):
    """Build the RC permissions array from the ticked matrix columns of a row.

    Each granted permission is sent with a ``permissionsCapabilities`` object —
    RingCentral's role model treats ``enabled`` as the actual grant flag, and a
    bare ``{"id": ...}`` is ignored (the role falls back to a default template).
    A ticked cell means "this role has this permission", so we enable it fully.

    Only *assignable* permissions are sent. Non-assignable permissions are
    managed by RingCentral automatically; including them in the body causes RC
    to reject or ignore the whole permission set, so we drop them here even if
    an older template ticked them.
    """
    assignable = _permission_metadata()['assignable']
    permissions = []
    for pid in permission_columns:
        if pid not in assignable:
            continue
        value = str(record.get(pid, "")).strip().lower()
        if value in ("x", "true", "1", "yes", "y", "✓"):
            # Only set `enabled` — it is the grant flag. manageEnabled/grantEnabled
            # are not valid on every permission (e.g. a permission that cannot be
            # granted onward), and setting them makes RC reject the whole array
            # with "permissionCapabilities value is invalid". RC applies its own
            # defaults for the manage/grant sub-capabilities.
            permissions.append({
                "id": pid,
                "permissionsCapabilities": {"enabled": True},
            })
    return permissions


def _build_role_body(record, permission_columns):
    """Construct the create/update request body from a matrix row."""
    body = {
        "displayName": str(record.get("DisplayName", "")).strip(),
        "description": str(record.get("Description", "")).strip(),
        "permissions": _permissions_from_row(record, permission_columns),
    }
    scope = str(record.get("Scope", "")).strip()
    if scope:
        body["scope"] = scope
    return body


def _stored_permission_ids(response):
    """Set of granted permission IDs RingCentral stored, from a role response."""
    try:
        data = response.json()
    except Exception:
        return None
    if isinstance(data, dict) and isinstance(data.get('permissions'), list):
        return {p.get('id') for p in data['permissions']
                if isinstance(p, dict) and p.get('id') and _permission_is_granted(p)}
    return None


def _create_role(body):
    """Create a custom role, then enforce the requested permission set.

    RingCentral's POST create initialises a new custom role from a default
    permission template and does NOT reliably apply the ``permissions`` array
    from the POST body (the role comes back looking like a copy of a predefined
    role such as "Standard (International)"). So we POST to create the role,
    then immediately PUT the full desired state — including permissions — onto
    the returned role id. The PUT is a no-op if the POST already applied them.

    Returns (final_response, stored_permission_ids | None).
    """
    adv = {"advancedPermissions": "true"}
    create_resp = rc_api_call("/restapi/v1.0/account/~/user-role", params=adv,
                              method="POST", json=body, return_response=True)
    if create_resp is None or not getattr(create_resp, 'ok', False):
        return create_resp, None

    new_id = None
    try:
        new_id = (create_resp.json() or {}).get('id')
    except Exception:
        new_id = None

    # No permissions requested, or we couldn't read the new id — nothing to enforce.
    if not new_id or not body.get('permissions'):
        return create_resp, _stored_permission_ids(create_resp)

    put_resp = rc_api_call(f"/restapi/v1.0/account/~/user-role/{new_id}", params=adv,
                           method="PUT", json=body, return_response=True)
    if put_resp is not None and getattr(put_resp, 'ok', False):
        return put_resp, _stored_permission_ids(put_resp)
    # The role was created but enforcing permissions failed — surface that.
    return put_resp, None


def _diff_message(action, sent_ids, stored_ids, assignable_universe):
    """Human-readable diagnostic comparing what we sent vs what RC stored.

    Restricts the applied/dropped comparison to assignable permissions (RC also
    manages a baseline of non-assignable permissions in the stored set).
    """
    sent = set(sent_ids)
    if stored_ids is None:
        return f"{action} succeeded — sent {len(sent)} permissions (RC did not return the stored set)."
    stored_assignable = stored_ids & assignable_universe
    applied = sent & stored_ids
    dropped = sent - stored_ids
    added = stored_assignable - sent
    msg = (f"{action} succeeded — sent {len(sent)}, RC kept {len(applied)}, "
           f"dropped {len(dropped)}, added {len(added)} assignable "
           f"(+{len(stored_ids) - len(stored_assignable)} baseline).")
    if dropped:
        sample = ", ".join(sorted(dropped)[:8])
        msg += f" Dropped: {sample}{'…' if len(dropped) > 8 else ''}."
    return msg


def apply_roles_from_records(records, permission_columns, task_id=None):
    """Stream create/update of custom roles, one chunk per record.

    Only rows whose Action is NEW or MODIFY are acted on. Predefined
    (non-custom) roles are refused for MODIFY – they are read-only in RC.

      NEW    -> POST /restapi/v1.0/account/~/user-role, then PUT to enforce
                the requested permission set (see _create_role).
      MODIFY -> PUT  /restapi/v1.0/account/~/user-role/{roleId}
    """
    total = len(records)
    yield {"type": "start", "total": total,
           "message": f"Applying {total} role change{'' if total == 1 else 's'}…"}
    results = []
    assignable_universe = _permission_metadata()['assignable']

    for i, record in enumerate(records):
        # Cooperative stop: roles already written stand; the rest are skipped.
        if task_control.is_stopped(task_id):
            item = {"name": "—", "status": "cancelled",
                    "message": "Stopped by user — remaining roles were skipped."}
            results.append(item)
            yield {"type": "progress", "current": i, "total": total, "result": item}
            break

        action = str(record.get("Action", "")).strip().upper()
        display_name = str(record.get("DisplayName", "")).strip()
        role_id = str(record.get("RoleID", "")).strip()
        name = display_name or role_id or f"Row {i + 1}"

        if action not in ("NEW", "MODIFY"):
            # Advance the bar even for untouched rows so it tracks true position.
            yield {"type": "progress", "current": i + 1, "total": total}
            continue

        try:
            body = _build_role_body(record, permission_columns)
            if not body["displayName"]:
                raise ValueError("DisplayName is required.")
            sent_ids = [p["id"] for p in body["permissions"]]

            if action == "MODIFY":
                if not role_id:
                    raise ValueError("RoleID is required to MODIFY a role.")
                if str(record.get("Custom", "")).strip().lower() == "false":
                    raise ValueError("Predefined roles are read-only and cannot be modified.")
                endpoint = f"/restapi/v1.0/account/~/user-role/{role_id}"
                response = rc_api_call(endpoint, params={"advancedPermissions": "true"},
                                       method="PUT", json=body, return_response=True)
                stored = _stored_permission_ids(response) if getattr(response, 'ok', False) else None
            else:  # NEW
                response, stored = _create_role(body)

            if response is not None and getattr(response, 'ok', False):
                item = {"name": name, "status": "success",
                        "message": _diff_message(action, sent_ids, stored, assignable_universe)}
                if stored is not None:
                    # Attach the id sets for the downloadable results detail.
                    item["dropped"] = sorted(set(sent_ids) - stored)
                    item["added"] = sorted((stored & assignable_universe) - set(sent_ids))
            else:
                detail = _error_detail(response)
                item = {"name": name, "status": "error",
                        "message": f"{action} failed: {detail}"}
        except Exception as e:
            print(f"ERROR processing role '{name}': {e}")
            item = {"name": name, "status": "error", "message": str(e)}

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
