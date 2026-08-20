"""The two things every outbound call from this project has to get right.

Both were found the hard way, on real services, and both are one line each:

* **Say who you are.** Groq's edge answers `Python-urllib/3.11` with `403
  Forbidden`, and answers a request carrying a real `User-Agent` with `200` -
  the same key, the same second. On a POST it does not even bother with the 403
  and drops the connection, which surfaces as `EOF occurred in violation of
  protocol` and reads exactly like a broken TLS stack. LRCLIB asks for a
  `User-Agent` in its documentation for gentler reasons.
* **Trust the machine's certificates.** This machine runs TLS inspection: an
  antivirus re-signs HTTPS traffic and the bundle Python ships with rejects the
  chain with "self-signed certificate in certificate chain". The Windows store
  already trusts that root, so `truststore` is the fix, not disabling
  verification. Optional on purpose - the Linux container has no inspection and
  should not carry the dependency.
"""

import logging

log = logging.getLogger("karuki.net")

# Identifies the project rather than the library, which is what the services ask
# for and what makes a request from here recognisable in somebody's logs.
USER_AGENT = "karuki/0.1.0 (https://github.com/sarah94087-bot/Karaoke)"

_injected = False


def trust_system_certificates() -> None:
    """Use the operating system's certificate store, if `truststore` is here.

    Idempotent: it is called before every request rather than once at import,
    because a module that only works when it happens to be imported first is a
    module that breaks in a different entry point.
    """
    global _injected
    if _injected:
        return
    try:
        import truststore
    except ImportError:
        _injected = True
        return
    truststore.inject_into_ssl()
    _injected = True
    log.debug("using the system certificate store")
