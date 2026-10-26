"""Thread-confined connection pools; no sharing of upstream cookies across users."""
import threading
import requests
from requests.adapters import HTTPAdapter

_local = threading.local()


def media_session():
    session = getattr(_local, 'media_session', None)
    if session is None:
        session = requests.Session()
        session.mount('https://', HTTPAdapter(pool_connections=16, pool_maxsize=2, max_retries=0))
        _local.media_session = session
    session.cookies.clear()
    return session
