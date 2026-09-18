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

    These become the matrix columns. Only assignable permissions are included –
    read-only/system permissions cannot be granted on a custom role, so exposing
    them as editable columns would be misleading.
    """
    records = _get_all_records("/restapi/v1.0/dictionary/permission")
    ids = []
    for perm in records:
        pid = perm.get('id')
        if not pid:
            continue
        # 'assignable' is the flag RC uses for "an admin may grant this on a role".
        if perm.get('assignable', True):
            ids.append(pid)
    return sorted(set(ids))


def _role_permission_ids(detail):
    """Extract the set of permission IDs held by a role detail object."""
    held = set()
    for perm in (detail.get('permissions') or []):
        pid = perm.get('id')
        if pid:
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
        # The detail call is what carries the permissions array.
        detail = rc_api_call(f"/restapi/v1.0/account/~/user-role/{role_id}") or {}
        if 'errorCode' in detail or not detail:
            detail = summary  # fall back to the summary so the row still appears

        is_custom = bool(detail.get('custom', summary.get('custom', False)))
        held = _role_permission_ids(detail)

        row = {
            "Action": "",  # operator sets NEW or MODIFY
            "RoleID": role_id or "",
            "DisplayName": detail.get('displayName', summary.get('displayName', '')),
            "Description": detail.get('description', summary.get('description', '')),
            "Scope": detail.get('scope', summary.get('scope', '')),
            "Custom": "true" if is_custom else "false",
            "Editable": "Yes" if is_custom else "No (predefined)",
            "PermissionCount": len(held),
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
    """Build the RC permissions array from the ticked matrix columns of a row."""
    permissions = []
    for pid in permission_columns:
        value = str(record.get(pid, "")).strip().lower()
        if value in ("x", "true", "1", "yes", "y", "✓"):
            permissions.append({"id": pid})
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


def apply_roles_from_records(records, permission_columns, task_id=None):
    """Stream create/update of custom roles, one chunk per record.

    Only rows whose Action is NEW or MODIFY are acted on. Predefined
    (non-custom) roles are refused for MODIFY – they are read-only in RC.

      NEW    -> POST /restapi/v1.0/account/~/user-role
      MODIFY -> PUT  /restapi/v1.0/account/~/user-role/{roleId}
    """
    total = len(records)
    yield {"type": "start", "total": total,
           "message": f"Applying {total} role change{'' if total == 1 else 's'}…"}
    results = []

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

            if action == "MODIFY":
                if not role_id:
                    raise ValueError("RoleID is required to MODIFY a role.")
                if str(record.get("Custom", "")).strip().lower() == "false":
                    raise ValueError("Predefined roles are read-only and cannot be modified.")
                endpoint = f"/restapi/v1.0/account/~/user-role/{role_id}"
                response = rc_api_call(endpoint, method="PUT", json=body, return_response=True)
            else:  # NEW
                endpoint = "/restapi/v1.0/account/~/user-role"
                response = rc_api_call(endpoint, method="POST", json=body, return_response=True)

            if response is not None and getattr(response, 'ok', False):
                item = {"name": name, "status": "success",
                        "message": f"{action} succeeded ({len(body['permissions'])} permissions)."}
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
