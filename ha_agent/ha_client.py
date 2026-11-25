from urllib.parse import quote

import requests

from .config import Settings


def _headers(settings: Settings):
    return {"Authorization": f"Bearer {settings.ha_token}"}


def get_states(settings: Settings):
    response = requests.get(
        f"{settings.ha_url}/api/states",
        headers=_headers(settings),
        timeout=settings.ha_timeout,
    )
    response.raise_for_status()
    return response.json()


def get_state(settings: Settings, entity_id):
    response = requests.get(
        f"{settings.ha_url}/api/states/{quote(entity_id, safe='')}",
        headers=_headers(settings),
        timeout=settings.ha_timeout,
    )
    if response.status_code == 404:
        return None
    response.raise_for_status()
    return response.json()


def get_history(settings: Settings, entity_id, start, end):
    response = requests.get(
        f"{settings.ha_url}/api/history/period/{quote(start.isoformat(), safe='')}",
        headers=_headers(settings),
        params={
            "filter_entity_id": entity_id,
            "end_time": end.isoformat(),
            "significant_changes_only": 0,
        },
        timeout=settings.ha_history_timeout,
    )
    response.raise_for_status()
    return response.json()


def call_service(
    settings: Settings,
    domain,
    service,
    data,
    return_response=False,
):
    params = {"return_response": ""} if return_response else None
    response = requests.post(
        f"{settings.ha_url}/api/services/{quote(domain, safe='')}/{quote(service, safe='')}",
        headers=_headers(settings),
        json=data,
        params=params,
        timeout=(
            settings.ha_history_timeout
            if return_response
            else settings.ha_timeout
        ),
    )
    response.raise_for_status()
    if not response.content:
        return None
    return response.json()
