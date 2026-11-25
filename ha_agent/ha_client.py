from urllib.parse import quote

import requests

from .config import Settings


class HomeAssistantUnavailable(RuntimeError):
    pass


def _headers(settings: Settings):
    return {"Authorization": f"Bearer {settings.ha_token}"}


def _request(request_function, *args, **kwargs):
    try:
        return request_function(*args, **kwargs)
    except (requests.ConnectionError, requests.Timeout) as exc:
        raise HomeAssistantUnavailable(
            "Немає зв'язку з Home Assistant."
        ) from exc


def _raise_for_status(response):
    try:
        response.raise_for_status()
    except requests.HTTPError as exc:
        if response.status_code in {502, 503, 504}:
            raise HomeAssistantUnavailable(
                "Home Assistant тимчасово недоступний."
            ) from exc
        raise


def get_states(settings: Settings):
    response = _request(
        requests.get,
        f"{settings.ha_url}/api/states",
        headers=_headers(settings),
        timeout=settings.ha_timeout,
    )
    _raise_for_status(response)
    return response.json()


def check_api_connection(settings: Settings):
    response = _request(
        requests.get,
        f"{settings.ha_url}/api/",
        headers=_headers(settings),
        timeout=settings.ha_timeout,
    )
    _raise_for_status(response)


def get_state(settings: Settings, entity_id):
    response = _request(
        requests.get,
        f"{settings.ha_url}/api/states/{quote(entity_id, safe='')}",
        headers=_headers(settings),
        timeout=settings.ha_timeout,
    )
    if response.status_code == 404:
        return None
    _raise_for_status(response)
    return response.json()


def get_history(settings: Settings, entity_id, start, end):
    response = _request(
        requests.get,
        f"{settings.ha_url}/api/history/period/{quote(start.isoformat(), safe='')}",
        headers=_headers(settings),
        params={
            "filter_entity_id": entity_id,
            "end_time": end.isoformat(),
            "significant_changes_only": 0,
        },
        timeout=settings.ha_history_timeout,
    )
    _raise_for_status(response)
    return response.json()


def call_service(
    settings: Settings,
    domain,
    service,
    data,
    return_response=False,
):
    params = {"return_response": ""} if return_response else None
    response = _request(
        requests.post,
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
    _raise_for_status(response)
    if not response.content:
        return None
    return response.json()
