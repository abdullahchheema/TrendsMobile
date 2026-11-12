"""Tell the website its catalogue changed, the moment stock, prices, photos or orders change in the ERP.

The website reads the catalogue straight from the ERP's database views, so it can never hold a wrong number for long; this only makes it
*instant*: after a change is committed the ERP calls the website's /api/revalidate with the shared key and the website drops its cached copy.
It is best effort by design: if the website is down or slow the change is already safe in the database and the website's own cache expires
within a minute anyway, so nothing is ever lost or duplicated (the call carries no data, it only says 'look again')."""
import logging
import time
import urllib.request
from django.conf import settings
from django.db import transaction

log=logging.getLogger('covison.website_sync')
_last=[0.0]
failures=[0]            # how many calls failed since the server started (visible in the logs; the cache expiry covers the gap)


def configured():
    return bool(getattr(settings,'WEBSITE_REVALIDATE_URL','') and getattr(settings,'ONLINE_API_KEY',''))


def _send():
    now=time.monotonic()
    if now-_last[0]<1.0:return                    # a burst of changes (one sale touches several rows) is one call
    _last[0]=now
    try:
        request=urllib.request.Request(settings.WEBSITE_REVALIDATE_URL,data=b'{}',method='POST',headers={'Content-Type':'application/json','X-API-Key':settings.ONLINE_API_KEY})
        urllib.request.urlopen(request,timeout=1.5).read()
    except Exception as error:
        failures[0]+=1;log.warning('Website revalidation failed (%s). The website refreshes by itself within a minute.',error)


def notify(**kwargs):
    """Signal handler: runs after the surrounding transaction commits, so the website never looks at half-written data."""
    if configured():transaction.on_commit(_send)
