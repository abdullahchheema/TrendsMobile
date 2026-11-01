"""Validate the explicit local/LAN main-CRM route without exposing server secrets."""
import ipaddress


def enabled(env):
    flag=env.get('COVISON_MAIN_CRM_ENABLED','0')
    if flag not in ('0','1'):raise ValueError('COVISON_MAIN_CRM_ENABLED must be 0 or 1.')
    if flag=='0':return False
    address=ipaddress.ip_address(env.get('COVISON_BIND_ADDRESS',''))
    if address.version!=4 or not address.is_private or address.is_loopback or address.is_unspecified or address.is_multicast:
        raise ValueError('Main CRM needs a specific private IPv4 LAN address distinct from loopback.')
    port=int(env.get('COVISON_MAIN_PORT','8080'))
    if not 1<=port<=65535 or port in (8081,int(env.get('COVISON_API_PORT','8765'))):raise ValueError('Choose a distinct valid main CRM port.')
    return True
