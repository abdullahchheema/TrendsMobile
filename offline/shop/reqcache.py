"""A tiny per-request memo for read-only look-ups (the chart of accounts, shop settings) that one page asks for many times.

Only page views (GET/HEAD) switch it on, so a save never reads a stale copy. Outside a request (tests, management commands,
posting code) there is no memo and every call goes to the database as before."""
from contextvars import ContextVar

_memo=ContextVar('covison_request_memo',default=None)


def start():return _memo.set({})


def stop(token):_memo.reset(token)


def remember(key,build):
    memo=_memo.get()
    if memo is None:return build()
    if key not in memo:memo[key]=build()
    return memo[key]
