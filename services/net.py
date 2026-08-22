"""
Shared networking helpers.

Why this exists: on panel hosts like Wispbyte the container's default DNS
resolver occasionally can't reach anything ("Temporary failure in name
resolution"), which used to make every HTTP call in this project fail, get
retried against 5 more hosts/servers that were never going to work either,
and dump a full Python traceback for each one. That's most of what made the
bot feel slow and the console feel spammy.

This module gives every HTTP call in the project:
  1. A resolver that falls back to public DNS (1.1.1.1 / 8.8.8.8) when
     aiodns is installed, so a flaky container resolver doesn't take the
     whole bot down.
  2. A cheap way to recognize "this is a DNS failure" so a caller can stop
     looping through hosts/servers immediately instead of repeating the
     same failure 5 more times.
"""

import logging

import aiohttp

logger = logging.getLogger("genshin_userbot")

try:
    import aiodns  # noqa: F401
    _HAS_AIODNS = True
except ImportError:
    _HAS_AIODNS = False

_FALLBACK_NAMESERVERS = ["1.1.1.1", "1.0.0.1", "8.8.8.8", "8.8.4.4"]

_warned_no_aiodns = False

# Some aiodns/pycares releases changed the signature of
# Channel.getaddrinfo() in a way older aiohttp versions don't call it with,
# which raises a TypeError on *every* lookup (not at resolver-construction
# time, so the try/except in build_connector() below can't catch it). If
# that happens, stop using the custom resolver for the rest of this run
# instead of every request failing forever. The real fix is pinning
# compatible versions in requirements.txt; this is just a safety net.
_disable_custom_resolver = False
_RESOLVER_BUG_SIGNATURE = "getaddrinfo() takes"


def _disable_broken_resolver(reason):
    global _disable_custom_resolver
    if _disable_custom_resolver:
        return
    _disable_custom_resolver = True
    logger.error(
        "net: installed aiodns/pycares version is incompatible with aiohttp's resolver "
        "(%s). Falling back to the container's default DNS resolver for the rest of this "
        "run. To fix permanently, pin 'pycares<4.4.0' in requirements.txt and redeploy.",
        reason,
    )


def build_connector():
    global _warned_no_aiodns
    if _HAS_AIODNS and not _disable_custom_resolver:
        try:
            resolver = aiohttp.AsyncResolver(nameservers=_FALLBACK_NAMESERVERS)
            return aiohttp.TCPConnector(resolver=resolver, ttl_dns_cache=300)
        except Exception as error:
            logger.warning("net: custom DNS resolver unavailable, using system default: %s", error)
    elif not _HAS_AIODNS and not _warned_no_aiodns:
        _warned_no_aiodns = True
        logger.warning(
            "net: 'aiodns' not installed, using the container's default DNS resolver. "
            "If you keep seeing 'Temporary failure in name resolution', "
            "add 'aiodns' to requirements.txt and redeploy."
        )
    return aiohttp.TCPConnector(ttl_dns_cache=300)


def new_session(headers=None, timeout_seconds=10):
    timeout = aiohttp.ClientTimeout(total=timeout_seconds)
    return aiohttp.ClientSession(connector=build_connector(), headers=headers, timeout=timeout)


def is_dns_error(error):
    """True if `error` looks like a name-resolution failure rather than a
    normal HTTP-level problem (404, 503, etc). Also self-heals the
    aiodns/pycares version-mismatch bug described above if it's detected."""
    if isinstance(error, (aiohttp.ClientConnectorDNSError, aiohttp.ClientConnectorError)):
        return True
    text = str(error)
    if _RESOLVER_BUG_SIGNATURE in text:
        _disable_broken_resolver(text)
        return True
    return "Temporary failure in name resolution" in text or "nodename nor servname" in text


class DNSFailure(Exception):
    """Raised internally to unwind host/server retry loops in one shot once
    we know DNS is down, instead of repeating the same failure and its
    traceback for every remaining combination."""
