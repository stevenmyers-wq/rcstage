import requests
import logging

logger = logging.getLogger(__name__)

RINGCX_BASE_URL = 'https://ringcx.ringcentral.com'


def exchange_rc_token_for_ringcx(rc_token):
    """
    Exchanges a RingCentral (RingEX) bearer token for a RingCX access token.
    Returns dict with accessToken, refreshToken, and accountId.
    Raises Exception on failure.
    """
    url = f'{RINGCX_BASE_URL}/api/auth/login/rc/accesstoken?includeRefresh=true'
    headers = {'Content-Type': 'application/x-www-form-urlencoded'}
    data = {
        'rcAccessToken': rc_token,
        'rcTokenType': 'Bearer'
    }

    try:
        response = requests.post(url, headers=headers, data=data)
        response.raise_for_status()
        payload = response.json()

        # Extract accountId from first agentDetails entry
        agent_details = payload.get('agentDetails', [])
        account_id = agent_details[0].get('accountId') if agent_details else None

        return {
            'accessToken': payload.get('accessToken'),
            'refreshToken': payload.get('refreshToken'),
            'accountId': account_id,
            'agentDetails': agent_details
        }

    except requests.exceptions.HTTPError as e:
        error_body = e.response.text if e.response else 'No response body'
        logger.error(f'RingCX token exchange failed: {e} — {error_body}')
        raise Exception(f'RingCX token exchange failed: {error_body}')
    except Exception as e:
        logger.error(f'RingCX token exchange error: {e}')
        raise


def refresh_ringcx_token(refresh_token):
    """
    Refreshes a RingCX access token using the stored refresh token.
    Returns dict with new accessToken and refreshToken.
    Raises Exception on failure.
    """
    url = f'{RINGCX_BASE_URL}/api/auth/token/refresh'
    headers = {'Content-Type': 'application/x-www-form-urlencoded'}
    data = {
        'refresh_token': refresh_token,
        'rcTokenType': 'Bearer'
    }

    try:
        response = requests.post(url, headers=headers, data=data)
        response.raise_for_status()
        payload = response.json()

        return {
            'accessToken': payload.get('accessToken'),
            'refreshToken': payload.get('refreshToken')
        }

    except requests.exceptions.HTTPError as e:
        error_body = e.response.text if e.response else 'No response body'
        logger.error(f'RingCX token refresh failed: {e} — {error_body}')
        raise Exception(f'RingCX token refresh failed: {error_body}')
    except Exception as e:
        logger.error(f'RingCX token refresh error: {e}')
        raise


def get_ringcx_accounts(ringcx_token):
    """
    Fetches the list of RingCX accounts accessible to the authenticated user.
    Returns list of account dicts.
    Raises Exception on failure.
    """
    url = f'{RINGCX_BASE_URL}/voice/api/v1/admin/accounts'
    headers = {'Authorization': f'Bearer {ringcx_token}'}

    try:
        response = requests.get(url, headers=headers)
        response.raise_for_status()
        return response.json()

    except requests.exceptions.HTTPError as e:
        error_body = e.response.text if e.response else 'No response body'
        logger.error(f'RingCX get accounts failed: {e} — {error_body}')
        raise Exception(f'RingCX get accounts failed: {error_body}')
    except Exception as e:
        logger.error(f'RingCX get accounts error: {e}')
        raise
