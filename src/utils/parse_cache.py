"""Bounded per-worker cache/coalescing for the public mini-program endpoint only."""
import copy
import hashlib
import threading
import time
from collections import OrderedDict


class BusyError(Exception):
    pass


class WaitTimeout(Exception):
    pass


class ParseCache:
    def __init__(self, ttl=120, capacity=128, concurrent=2):
        self.ttl = ttl
        self.capacity = capacity
        self.lock = threading.Lock()
        self.slots = threading.BoundedSemaphore(concurrent)
        self.admissions = threading.BoundedSemaphore(concurrent * 2)
        self.cache = OrderedDict()
        self.pending = {}

    def run(self, url, compute, wait_seconds=45, refresh=False):
        if not self.admissions.acquire(blocking=False):
            raise BusyError()
        try:
            return self._run(url, compute, wait_seconds, refresh)
        finally:
            self.admissions.release()

    def _run(self, url, compute, wait_seconds, refresh):
        key = hashlib.sha256(url.encode()).digest()
        with self.lock:
            cached = self.cache.get(key)
            if cached and not refresh and cached[0] > time.monotonic():
                self.cache.move_to_end(key)
                return copy.deepcopy(cached[1]), 'hit'
            self.cache.pop(key, None)
            pending = self.pending.get(key)
            leader = pending is None
            if leader:
                if not self.slots.acquire(blocking=False):
                    raise BusyError()
                pending = {'event': threading.Event()}
                self.pending[key] = pending
        if not leader:
            if not pending['event'].wait(wait_seconds):
                raise WaitTimeout()
            if 'error' in pending:
                raise pending['error']
            return copy.deepcopy(pending['result']), 'shared'
        try:
            result = compute()
            pending['result'] = copy.deepcopy(result)
            body, status = result
            if status == 200 and body.get('succ'):
                with self.lock:
                    self.cache[key] = (time.monotonic() + self.ttl, copy.deepcopy(result))
                    while len(self.cache) > self.capacity:
                        self.cache.popitem(last=False)
            return result, 'miss'
        except BaseException as exc:
            pending['error'] = exc
            raise
        finally:
            with self.lock:
                self.pending.pop(key, None)
                pending['event'].set()
                self.slots.release()
