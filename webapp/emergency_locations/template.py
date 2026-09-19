# webapp/emergency_locations/template.py
"""Server-side generator for the ERL "new location" template workbook.

Produces a single .xlsx (all countries, no per-country pick) with real Excel
data-validation dropdowns:
  - Action / Visibility: fixed lists
  - Country: the full ISO list (from the baked reference)
  - Site: the account's existing site NAMES (fetched live; resolved to site ids
    on upload)
  - Address.state / Address.streetType: DEPENDENT dropdowns that change with the
    Country cell, via INDIRECT + per-country named ranges

AddressFormatId is intentionally NOT a column — the upload resolver discerns the
country's primary format. Required fields vary by country, so they are noted on
the Ref Countries sheet (per country) and enforced by the upload validator.

SheetJS (the CDN build used in the browser) can't write data validations, so the
workbook is built here with openpyxl (already a dependency). Values are friendly
(country ISO, site/state name, street-type label); the upload resolver converts
them to RC codes and validates before sending.
"""
import io

from openpyxl import Workbook
from openpyxl.worksheet.datavalidation import DataValidation
from openpyxl.workbook.defined_name import DefinedName
from openpyxl.comments import Comment
from openpyxl.utils import get_column_letter

from webapp.rc_api import rc_api_call
from . import reference

# Universal friendly column set. No AddressFormatId (backend discerns it).
HEADERS = [
    "Action", "LocationId", "Name", "Visibility", "SiteId", "Country",
    "Address.customerName", "Address.buildingName", "Address.buildingNumber",
    "Address.street", "Address.streetType", "Address.city", "Address.state",
    "Address.zip", "Address.street2",
]
_COL = {name: get_column_letter(i + 1) for i, name in enumerate(HEADERS)}

# Location-level fields that are always required (independent of country).
ALWAYS_REQUIRED = ["Action", "Name", "SiteId", "Country"]

MAX_ROWS = 500  # rows the dropdowns cover


def _sanitize_key(iso):
    """A defined-name-safe token from an ISO code (letters/digits only)."""
    return "".join(ch for ch in str(iso or "") if ch.isalnum()).upper()


def _account_sites():
    """Existing account sites as [{id, name}] (best-effort; empty on failure)."""
    from .utils import _get_all_records
    out = []
    try:
        for s in _get_all_records("/restapi/v1.0/account/~/sites"):
            sid = s.get("id")
            if sid is not None:
                out.append({"id": str(sid), "name": s.get("name") or str(sid)})
    except Exception as e:
        print(f"ERL template: could not load sites: {e}")
    return out


def build_template_workbook():
    ref = reference._load()
    countries = reference.ui_countries()          # createable, with primaryFormatId
    states_by_country = ref.get("states", {})
    fmt_by_id = {str(f.get("id")): f for f in ref.get("formats", [])}
    sites = _account_sites()

    wb = Workbook()
    ws = wb.active
    ws.title = "Emergency Locations"
    ws.append(HEADERS)

    # Note required-ness on the header cells.
    for name in HEADERS:
        cell = ws[f"{_COL[name]}1"]
        if name in ALWAYS_REQUIRED:
            cell.comment = Comment("Required.", "RCAU")
    ws[f"{_COL['Address.state']}1"].comment = Comment(
        "State/province — pick from the dropdown (it follows the Country cell). "
        "Some countries have no states (e.g. New Zealand): leave this blank and "
        "put the city/locality (Auckland, etc.) in the City column.", "RCAU")
    ws[f"{_COL['Address.streetType']}1"].comment = Comment(
        "Required only for structured formats (e.g. AU). Dropdown follows Country.", "RCAU")
    ws[f"{_COL['SiteId']}1"].comment = Comment(
        "Pick an existing site by name; it is resolved to the site id on upload.", "RCAU")

    # Example row (AU): valid-shaped, friendly, ready to edit.
    example_site = sites[0]["name"] if sites else "main-site"
    ws.append([
        "NEW", "", "Example site - rename me", "Public", example_site, "Australia",
        "Example site", "", "190",
        "Henty", "Drive", "Redbank Plains", "Queensland",
        "4301", "",
    ])

    # --- Reference (visible): all createable countries + their required fields ---
    ws_ctry = wb.create_sheet("Ref Countries")
    ws_ctry.append(["ISO", "Name", "CountryId", "PrimaryFormatId", "RequiredFields"])
    iso_list = []
    for c in countries:
        iso = c.get("isoCode")
        fmt = fmt_by_id.get(str(c.get("primaryFormatId")))
        req = list(ALWAYS_REQUIRED)
        if fmt:
            req += [f"Address.{fld['id']}" for fld in (fmt.get("fields") or [])
                    if fld.get("mandatory")]
        ws_ctry.append([iso or "", c.get("name"), c.get("id"),
                        c.get("primaryFormatId") or "", ", ".join(req)])
        if iso:
            iso_list.append((iso, c))

    # --- Reference (visible): account sites the operator can choose ---
    ws_sites = wb.create_sheet("Ref Sites")
    ws_sites.append(["Site Name", "Site Id"])
    for s in sites:
        ws_sites.append([s["name"], s["id"]])
    site_name_range = (f"'Ref Sites'!$A$2:$A${len(sites) + 1}") if sites else None

    # --- Hidden helper sheets that back the dependent dropdowns ---
    ws_st = wb.create_sheet("Ref States")
    ws_st.append(["The State dropdown reads from here based on the Country cell."])
    ws_sty = wb.create_sheet("Ref Street Types")
    ws_sty.append(["The Street Type dropdown reads from here based on the Country cell."])
    ws_st["E1"] = ""
    ws_sty["E1"] = ""
    empty_ref_st = "'Ref States'!$E$1"
    empty_ref_sty = "'Ref Street Types'!$E$1"

    def _add_name(name, ref_text):
        try:
            wb.defined_names.add(DefinedName(name=name, attr_text=ref_text))
        except Exception:
            wb.defined_names[name] = DefinedName(name=name, attr_text=ref_text)

    st_row = 3
    sty_row = 3
    for iso, c in iso_list:
        key = _sanitize_key(iso)
        if not key:
            continue

        fmt = fmt_by_id.get(str(c.get("primaryFormatId")))
        state_field = None
        if fmt:
            for fld in (fmt.get("fields") or []):
                if fld.get("id") == "state":
                    state_field = fld
                    break

        # State values: prefer the format's own state options (authoritative for
        # this country — e.g. AU's 9 states). Otherwise fall back to the state
        # dictionary, dropping the pseudo "national" row (name == country name,
        # e.g. the lone "New Zealand"/"Australia" entry) that isn't a real state.
        if state_field and state_field.get("options"):
            names = [o.get("label") or o.get("key") for o in state_field["options"]]
        else:
            cname = (c.get("name") or "").strip().lower()
            names = [s.get("name") for s in states_by_country.get(str(c.get("id")), [])
                     if s.get("name") and s.get("name").strip().lower() != cname]
        if names:
            start = st_row
            for nm in names:
                ws_st.cell(row=st_row, column=1, value=iso)
                ws_st.cell(row=st_row, column=2, value=nm)
                st_row += 1
            _add_name(f"st_{key}", f"'Ref States'!$B${start}:$B${st_row - 1}")
        else:
            _add_name(f"st_{key}", empty_ref_st)

        opts = []
        if fmt:
            for fld in (fmt.get("fields") or []):
                if fld.get("id") == "streetType":
                    opts = [o.get("label") or o.get("key") for o in (fld.get("options") or [])]
                    break
        if opts:
            start = sty_row
            for lbl in opts:
                ws_sty.cell(row=sty_row, column=1, value=iso)
                ws_sty.cell(row=sty_row, column=2, value=lbl)
                sty_row += 1
            _add_name(f"sty_{key}", f"'Ref Street Types'!$B${start}:$B${sty_row - 1}")
        else:
            _add_name(f"sty_{key}", empty_ref_sty)

    ws_st.sheet_state = "hidden"
    ws_sty.sheet_state = "hidden"

    n = len(countries) + 1
    # Country dropdown shows full NAMES (col B). The dependent state/street-type
    # dropdowns key off the ISO code, so convert the selected name → ISO inline
    # with INDEX/MATCH against Ref Countries (name col B → iso col A). No hidden
    # helper column needed. (The upload resolver also accepts the full name.)
    name_range = f"'Ref Countries'!$B$2:$B${n}"
    cc = _COL["Country"]
    iso_lookup = (f"INDEX('Ref Countries'!$A$2:$A${n},"
                  f"MATCH(${cc}2,'Ref Countries'!$B$2:$B${n},0))")

    # --- Data validation dropdowns ---
    def _dv(formula, allow_blank=True):
        dv = DataValidation(type="list", formula1=formula, allow_blank=allow_blank)
        dv.showDropDown = False  # Excel quirk: False => dropdown IS shown
        return dv

    validations = []
    dv_action = _dv('"NEW,MODIFY,DELETE"'); dv_action.add(f"{_COL['Action']}2:{_COL['Action']}{MAX_ROWS}"); validations.append(dv_action)
    dv_vis = _dv('"Public"'); dv_vis.add(f"{_COL['Visibility']}2:{_COL['Visibility']}{MAX_ROWS}"); validations.append(dv_vis)
    dv_ctry = _dv(name_range); dv_ctry.add(f"{cc}2:{cc}{MAX_ROWS}"); validations.append(dv_ctry)
    if site_name_range:
        dv_site = _dv(site_name_range); dv_site.add(f"{_COL['SiteId']}2:{_COL['SiteId']}{MAX_ROWS}"); validations.append(dv_site)

    dv_state = _dv(f'=INDIRECT("st_"&{iso_lookup})'); dv_state.add(f"{_COL['Address.state']}2:{_COL['Address.state']}{MAX_ROWS}"); validations.append(dv_state)
    dv_stype = _dv(f'=INDIRECT("sty_"&{iso_lookup})'); dv_stype.add(f"{_COL['Address.streetType']}2:{_COL['Address.streetType']}{MAX_ROWS}"); validations.append(dv_stype)

    for dv in validations:
        ws.add_data_validation(dv)

    for name in HEADERS:
        ws.column_dimensions[_COL[name]].width = max(12, min(len(name) + 4, 26))
    ws_ctry.column_dimensions["B"].width = 32
    ws_ctry.column_dimensions["E"].width = 60
    ws_sites.column_dimensions["A"].width = 32

    bio = io.BytesIO()
    wb.save(bio)
    bio.seek(0)
    return bio.getvalue()
