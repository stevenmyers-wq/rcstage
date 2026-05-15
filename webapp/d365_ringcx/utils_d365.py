import time
import requests
import logging

logger = logging.getLogger(__name__)

# In-memory D365 token cache: {(tenant_id, client_id): {token, expires_at}}
_d365_token_cache: dict = {}

RINGCX_BASE_URL = 'https://ringcx.ringcentral.com'
ENGAGE_BASE_URL = 'https://engage.ringcentral.com'


def get_ringcx_campaigns(ringcx_token: str, account_id: str) -> list:
    """
    Fetches outbound campaigns for a RingCX account.
    Campaigns are nested under dial groups:
      1. GET /voice/api/v1/admin/accounts/{accountId}/dialGroups
      2. GET /voice/api/v1/admin/accounts/{accountId}/dialGroups/{dialGroupId}/campaigns
    Returns a flat list of campaigns with _dialGroupName and _dialGroupId added.
    """
    headers = {'Authorization': f'Bearer {ringcx_token}'}

    # Step 1: fetch dial groups
    dg_url = f'{ENGAGE_BASE_URL}/voice/api/v1/admin/accounts/{account_id}/dialGroups'
    try:
        dg_resp = requests.get(dg_url, headers=headers)
        dg_resp.raise_for_status()
        dial_groups = dg_resp.json()
        logger.info(f'RingCX dial groups: {str(dial_groups)[:300]}')
    except requests.exceptions.HTTPError as e:
        body = e.response.text[:200] if e.response is not None else 'no body'
        logger.error(f'RingCX dial groups fetch failed: {e} — {body}')
        raise Exception(f'Could not fetch RingCX dial groups: {body}')
    except Exception as e:
        logger.error(f'RingCX dial groups fetch error: {e}')
        raise Exception(f'Could not fetch RingCX dial groups: {e}')

    if not isinstance(dial_groups, list):
        dial_groups = dial_groups.get('dialGroups', dial_groups.get('data', []))

    # Step 2: fetch campaigns for each dial group
    all_campaigns = []
    for dg in dial_groups:
        dg_id = dg.get('dialGroupId') or dg.get('id')
        dg_name = dg.get('dialGroupName') or dg.get('name', '')
        if not dg_id:
            continue
        camp_url = f'{ENGAGE_BASE_URL}/voice/api/v1/admin/accounts/{account_id}/dialGroups/{dg_id}/campaigns'
        try:
            camp_resp = requests.get(camp_url, headers=headers)
            camp_resp.raise_for_status()
            campaigns = camp_resp.json()
            if not isinstance(campaigns, list):
                campaigns = campaigns.get('campaigns', campaigns.get('data', []))
            for c in campaigns:
                c['_dialGroupName'] = dg_name
                c['_dialGroupId'] = dg_id
            all_campaigns.extend(campaigns)
        except Exception as e:
            logger.warning(f'RingCX campaigns for dialGroup {dg_id} ({dg_name}) failed: {e}')

    return all_campaigns


def push_leads_to_ringcx(ringcx_token: str, account_id: str, campaign_id: str, leads: list) -> dict:
    """
    Pushes a batch of leads into a RingCX campaign in a single upload (one email per batch).

    Discovered via HAR analysis of a working manual UI upload:
      Step 1: POST /leadLoader/preview  -- multipart/form-data CSV -> returns transactionId
      Step 2: POST /leadLoader/process  -- JSON with transactionId + column mappings -> 202 Accepted

    Key settings (all confirmed from working HAR + listLog analysis):
      phoneNumbersI18nEnabled:   true
      internationalNumberFormat: false
      numberOriginCountry:       "e164"  -- literal string, NOT a country code;
                                           tells the API phones are already E.164
      timeZoneOption:            "EXPLICIT" -- per-lead timezone in col 5 of the CSV
      fileContainsHeaders:       false (server default) -- do NOT include a header row;
                                 the server treats every row as a lead, so a header row
                                 fails phone validation and counts as a rejected lead.

    Each entry in leads must have:
        firstName, lastName, phone1 (E.164 e.g. +61412345678), externId (D365 leadId),
        leadTimezone (RingCX timezone code: EAST / CENTRAL / WEST)

    Returns dict with at least {'transactionId': '...'}.
    """
    if not leads:
        raise ValueError('push_leads_to_ringcx: leads list is empty')

    base_url = f'{RINGCX_BASE_URL}/voice/api/v1/admin/accounts/{account_id}/campaigns/{campaign_id}'
    auth_headers = {'Authorization': f'Bearer {ringcx_token}'}

    def _safe(val):
        return (str(val or '')).replace('"', '').replace(',', ' ').strip()

    # Build multi-row CSV (no header row)
    # Columns (1-indexed):
    #   1=firstName, 2=lastName, 3=phone1, 4=externalId, 5=timezone,
    #   6=webscore (AUX_DATA1), 7=moveType (AUX_DATA2),
    #   8=origin (AUX_DATA3), 9=destination (AUX_DATA4)
    rows = []
    for ld in leads:
        rows.append(','.join([
            _safe(ld.get('firstName')),
            _safe(ld.get('lastName')),
            _safe(ld.get('phone1')),
            _safe(ld.get('externId')),
            _safe(ld.get('leadTimezone', 'EAST')),
            _safe(ld.get('webscore', '')),
            _safe(ld.get('movetype', '')),
            _safe(ld.get('origin', '')),
            _safe(ld.get('destination', '')),
        ]))
    csv_content = '\n'.join(rows) + '\n'

    # ------------------------------------------------------------------ Step 1
    try:
        logger.warning(
            f'RingCX push_leads step1/preview campaign={campaign_id} count={len(leads)}'
        )
        preview_resp = requests.post(
            f'{base_url}/leadLoader/preview',
            headers=auth_headers,
            files={
                'file':                      ('leads.csv', csv_content.encode('utf-8'), 'text/csv'),
                'fileType':                  (None, 'COMMA'),
                'internationalNumberFormat': (None, 'false'),
                'phoneNumbersI18nEnabled':   (None, 'true'),
            },
        )
        logger.warning(
            f'RingCX preview status={preview_resp.status_code} '
            f'body={preview_resp.text[:500]}'
        )
        preview_resp.raise_for_status()
        transaction_id = preview_resp.json()['transactionId']
    except requests.exceptions.HTTPError as e:
        body = e.response.text if e.response is not None else 'no body'
        logger.error(f'RingCX push_leads preview failed: {e} -- {body}')
        raise Exception(f'RingCX leadLoader preview failed: {body}')
    except Exception as e:
        logger.error(f'RingCX push_leads preview error: {e}')
        raise

    # ------------------------------------------------------------------ Step 2
    process_payload = {
        'listState':                 'ACTIVE',
        'fileType':                  'COMMA',
        'duplicateHandling':         'RETAIN_ALL',
        'timeZoneOption':            'EXPLICIT',
        'phoneNumbersI18nEnabled':   True,
        'internationalNumberFormat': False,
        'numberOriginCountry':       'e164',
        'scheduleArchiveDts':        '',
        'description':               'Demo lead',
        'transactionId':             transaction_id,
        'pageColumnMappings': {
            'FIRST_NAME':    1,
            'LAST_NAME':     2,
            'LEAD_PHONE':    3,
            'EXTERN_ID':     4,
            'LEAD_TIMEZONE': 5,
            'AUX_DATA1':     6,   # webscore
            'AUX_DATA2':     7,   # move type
            'AUX_DATA3':     8,   # origin suburb
            'AUX_DATA4':     9,   # destination suburb
        },
        'dncTags':                  [],
        'pageNumber':               1,
        'extendedLeadDataMappings': {},
    }

    try:
        logger.warning(
            f'RingCX push_leads step2/process campaign={campaign_id} '
            f'transactionId={transaction_id} count={len(leads)}'
        )
        process_resp = requests.post(
            f'{base_url}/leadLoader/process',
            headers={**auth_headers, 'Content-Type': 'application/json'},
            json=process_payload,
        )
        logger.warning(
            f'RingCX process status={process_resp.status_code} '
            f'body={process_resp.text[:500]}'
        )
        process_resp.raise_for_status()
        try:
            result = process_resp.json()
        except Exception:
            result = {}
        result['transactionId'] = transaction_id
        logger.warning(
            f'RingCX push_leads complete transactionId={transaction_id} count={len(leads)}'
        )
        return result
    except requests.exceptions.HTTPError as e:
        body = e.response.text if e.response is not None else 'no body'
        logger.error(f'RingCX push_leads process failed: {e} -- {body}')
        raise Exception(f'RingCX leadLoader process failed: {body}')
    except Exception as e:
        logger.error(f'RingCX push_leads process error: {e}')
        raise


def delete_lead_from_ringcx(ringcx_token: str, account_id: str, campaign_id: str, ringcx_lead_id: str) -> None:
    """
    Removes a lead from a RingCX campaign by its internal RingCX lead ID.
    Called during demo cleanup to remove uncalled leads from campaigns.
    """
    url = f'{ENGAGE_BASE_URL}/voice/api/v1/admin/accounts/{account_id}/campaigns/{campaign_id}/leads/{ringcx_lead_id}'
    headers = {'Authorization': f'Bearer {ringcx_token}'}
    try:
        response = requests.delete(url, headers=headers)
        response.raise_for_status()
    except requests.exceptions.HTTPError as e:
        body = e.response.text if e.response is not None else 'no body'
        logger.error(f'RingCX delete_lead {ringcx_lead_id} failed: {e} — {body}')
        raise Exception(f'RingCX delete lead failed: {body}')
    except Exception as e:
        logger.error(f'RingCX delete_lead error: {e}')
        raise


D365_API_VERSION = 'v9.2'

# Fields fetched on every lead query.
# crd67_* fields are the custom columns created in Power Apps (publisher prefix crd67_).
LEAD_SELECT_FIELDS = ','.join([
    'leadid', 'fullname', 'firstname', 'lastname',
    'emailaddress1', 'mobilephone', 'telephone1',
    'subject', 'description', 'statecode', 'statuscode',
    'address1_city', 'address1_stateorprovince', 'address1_postalcode',
    'createdon', 'modifiedon', 'donotphone',
    'crd67_webscore', 'crd67_movetype', 'crd67_movedate',
    'crd67_originsuburb', 'crd67_destinationsuburb',
    'crd67_campaignassigned', 'crd67_rcdisposition',
])


def _normalise_url(env_url: str) -> str:
    """Ensures env_url has https:// and no trailing slash."""
    env_url = env_url.strip().rstrip('/')
    if not env_url.startswith('https://'):
        env_url = f'https://{env_url}'
    return env_url


def _api_url(env_url: str, path: str) -> str:
    return f'{_normalise_url(env_url)}/api/data/{D365_API_VERSION}/{path}'


def _headers(token: str, prefer_representation: bool = False) -> dict:
    h = {
        'Authorization': f'Bearer {token}',
        'Content-Type': 'application/json',
        'OData-MaxVersion': '4.0',
        'OData-Version': '4.0',
        'Accept': 'application/json',
    }
    if prefer_representation:
        h['Prefer'] = 'return=representation'
    return h


# ---------------------------------------------------------------------------
# Auth
# ---------------------------------------------------------------------------

def get_d365_token(tenant_id: str, client_id: str, client_secret: str, env_url: str) -> str:
    """
    Obtains a D365 access token via client credentials flow.
    scope is derived from env_url, e.g. https://org.crm.dynamics.com/.default
    """
    cache_key = (tenant_id, client_id)
    cached = _d365_token_cache.get(cache_key)
    if cached and cached['expires_at'] > time.time() + 300:
        return cached['token']

    env_url = _normalise_url(env_url)
    token_url = f'https://login.microsoftonline.com/{tenant_id}/oauth2/v2.0/token'

    try:
        response = requests.post(token_url, data={
            'client_id':     client_id,
            'client_secret': client_secret,
            'scope':         f'{env_url}/.default',
            'grant_type':    'client_credentials',
        })
        response.raise_for_status()
        payload = response.json()
        token = payload.get('access_token')
        if not token:
            raise Exception('No access_token in response')
        expires_in = payload.get('expires_in', 3600)
        _d365_token_cache[cache_key] = {
            'token':      token,
            'expires_at': time.time() + expires_in,
        }
        return token
    except requests.exceptions.HTTPError as e:
        body = e.response.text if e.response is not None else 'no body'
        logger.error(f'D365 get_d365_token failed: {e} — {body}')
        raise Exception(f'D365 auth failed: {body}')
    except Exception as e:
        logger.error(f'D365 get_d365_token error: {e}')
        raise


# ---------------------------------------------------------------------------
# Connectivity check
# ---------------------------------------------------------------------------

def whoami(token: str, env_url: str) -> dict:
    """
    Calls the D365 WhoAmI endpoint to verify the token works.
    Returns {'UserId': ..., 'OrganizationId': ...} on success.
    """
    url = _api_url(env_url, 'WhoAmI')
    try:
        response = requests.get(url, headers=_headers(token))
        response.raise_for_status()
        return response.json()
    except requests.exceptions.HTTPError as e:
        body = e.response.text if e.response is not None else 'no body'
        logger.error(f'D365 whoami failed: {e} — {body}')
        raise Exception(f'D365 WhoAmI failed: {body}')
    except Exception as e:
        logger.error(f'D365 whoami error: {e}')
        raise


# ---------------------------------------------------------------------------
# Lead operations
# ---------------------------------------------------------------------------

def get_lead(token: str, env_url: str, leadid: str) -> dict:
    """Fetches a single lead by ID. Returns the full lead dict."""
    url = _api_url(env_url, f'leads({leadid})')
    try:
        response = requests.get(url, headers=_headers(token))
        response.raise_for_status()
        return response.json()
    except requests.exceptions.HTTPError as e:
        body = e.response.text if e.response is not None else 'no body'
        logger.error(f'D365 get_lead({leadid}) failed: {e} — {body}')
        raise Exception(f'D365 get lead failed: {body}')
    except Exception as e:
        logger.error(f'D365 get_lead error: {e}')
        raise


def get_leads(token: str, env_url: str, filter_expr: str = None, limit: int = 100) -> list:
    """
    Fetches leads from D365, newest first.
    filter_expr: optional OData $filter, e.g. "statecode eq 0"
    """
    params = {
        '$orderby': 'modifiedon desc',
        '$top':     limit,
    }
    if filter_expr:
        params['$filter'] = filter_expr

    url = _api_url(env_url, 'leads')

    try:
        response = requests.get(url, headers=_headers(token), params=params)
        response.raise_for_status()
        return response.json().get('value', [])
    except requests.exceptions.HTTPError as e:
        body = e.response.text if e.response is not None else 'no body'
        logger.error(f'D365 get_leads failed: {e} — {body}')
        raise Exception(f'D365 get leads failed: {body}')
    except Exception as e:
        logger.error(f'D365 get_leads error: {e}')
        raise


def create_lead(token: str, env_url: str, lead_data: dict) -> dict:
    """
    Creates a D365 lead and returns the created record (including leadid).

    Expected lead_data keys (all optional except subject):
        firstname, lastname, mobilephone, emailaddress1, subject,
        description, address1_city, address1_stateorprovince,
        address1_postcode, leadsourcecode (10 = Web),
        rc_originsuburb, rc_destinationsuburb, rc_movedate,
        rc_movetype, rc_webscore

    Note on rc_movetype: if you created this as a Choice field in D365,
    the API requires the integer option-set value, not a string.
    Use the D365 metadata API to discover values if needed:
      GET /api/data/v9.2/EntityDefinitions(LogicalName='lead')/Attributes(LogicalName='rc_movetype')
          /Microsoft.Dynamics.CRM.PicklistAttributeMetadata?$expand=OptionSet
    If you recreated it as Single Line of Text, pass strings directly.
    """
    url = _api_url(env_url, 'leads')

    try:
        response = requests.post(url, headers=_headers(token, prefer_representation=True), json=lead_data)
        response.raise_for_status()
        return response.json()
    except requests.exceptions.HTTPError as e:
        body = e.response.text if e.response is not None else 'no body'
        logger.error(f'D365 create_lead failed: {e} — {body}')
        raise Exception(f'D365 create lead failed: {body}')
    except Exception as e:
        logger.error(f'D365 create_lead error: {e}')
        raise


def update_lead(token: str, env_url: str, leadid: str, fields: dict) -> None:
    """
    PATCHes specific fields on a D365 lead. Returns nothing (D365 sends 204).
    """
    url = _api_url(env_url, f'leads({leadid})')

    try:
        response = requests.patch(url, headers=_headers(token), json=fields)
        response.raise_for_status()
    except requests.exceptions.HTTPError as e:
        body = e.response.text if e.response is not None else 'no body'
        logger.error(f'D365 update_lead({leadid}) failed: {e} — {body}')
        raise Exception(f'D365 update lead failed: {body}')
    except Exception as e:
        logger.error(f'D365 update_lead error: {e}')
        raise


def delete_lead(token: str, env_url: str, leadid: str) -> None:
    """Deletes a D365 lead. Returns nothing (D365 sends 204)."""
    url = _api_url(env_url, f'leads({leadid})')

    try:
        response = requests.delete(url, headers=_headers(token))
        response.raise_for_status()
    except requests.exceptions.HTTPError as e:
        body = e.response.text if e.response is not None else 'no body'
        logger.error(f'D365 delete_lead({leadid}) failed: {e} — {body}')
        raise Exception(f'D365 delete lead failed: {body}')
    except Exception as e:
        logger.error(f'D365 delete_lead error: {e}')
        raise


def create_phone_call_activity(token: str, env_url: str, leadid: str, call_data: dict) -> None:
    """
    Creates a Phone Call activity linked to a lead in D365.
    Used for AI agent calls only — native RingCX integration handles human agent calls.

    call_data keys:
        queue, disposition, notes, recording_url,
        summary, transcript_url, phone, duration_minutes
    """
    url = _api_url(env_url, 'phonecalls')
    headers = _headers(token)

    payload = {
        'subject': f"Outbound Call - {call_data.get('disposition', 'Unknown')}",
        'description': (
            f"Call queue: {call_data.get('queue', '')}\n"
            f"Disposition: {call_data.get('disposition', '')}\n"
            f"Note: {call_data.get('notes', '')}\n"
            f"Call recording: {call_data.get('recording_url', '')}\n"
            f"Summary: {call_data.get('summary', '')}\n"
            f"Transcript url: {call_data.get('transcript_url', '')}"
        ),
        'phonenumber':           call_data.get('phone', ''),
        'directioncode':         True,
        'actualdurationminutes': call_data.get('duration_minutes', 1),
        'statuscode':            2,
        'regardingobjectid_lead@odata.bind': f'/leads({leadid})',
    }

    try:
        response = requests.post(url, headers=headers, json=payload)
        response.raise_for_status()
    except requests.exceptions.HTTPError as e:
        body = e.response.text if e.response is not None else 'no body'
        logger.error(f'D365 create_phone_call_activity({leadid}) failed: {e} — {body}')
        raise Exception(f'D365 create phone call activity failed: {body}')
    except Exception as e:
        logger.error(f'D365 create_phone_call_activity error: {e}')
        raise
