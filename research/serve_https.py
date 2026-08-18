"""HTTPS static server for the phone test (T-0.2.5).

AudioWorklet only exists in a secure context. Over plain http:// to a LAN IP the
phone gets `ctx.audioWorklet === undefined` and the page cannot run at all, so the
phone test needs TLS even though everything is local.
"""

import http.server
import os
import socket
import ssl

PORT = 8443
# Serve the project root, not this script's directory: the prototype pages under
# research/prototype/ reach up to ../../output and ../../transcripts.
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class Handler(http.server.SimpleHTTPRequestHandler):
    def __init__(self, *a, **kw):
        super().__init__(*a, directory=ROOT, **kw)

    def end_headers(self):
        # no caching, so re-testing after an edit measures the new code
        self.send_header("Cache-Control", "no-store")
        super().end_headers()

    def log_message(self, fmt, *args):
        print("  %s" % (fmt % args))


def lan_ip():
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("8.8.8.8", 80))
        return s.getsockname()[0]
    except Exception:
        return "127.0.0.1"
    finally:
        s.close()


ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
ctx.load_cert_chain(os.path.join(ROOT, "certs", "cert.pem"), os.path.join(ROOT, "certs", "key.pem"))

httpd = http.server.HTTPServer(("0.0.0.0", PORT), Handler)
httpd.socket = ctx.wrap_socket(httpd.socket, server_side=True)

ip = lan_ip()
print("=" * 58)
print("  HTTPS server running")
print("  On the phone open:")
print("     https://%s:%d/research/prototype/mobile.html" % (ip, PORT))
print()
print("  The certificate is self-signed, so the phone will warn once.")
print("  Choose Advanced -> Proceed. This is your own machine.")
print("=" * 58)
httpd.serve_forever()
