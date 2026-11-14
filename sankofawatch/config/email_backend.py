"""SMTP backend that connects to a fixed IP while still verifying TLS against EMAIL_HOST.

Used only when EMAIL_CONNECT_IP is set. Handy when a local network resolves the
SMTP hostname to an address that drops connections: the TCP connection goes to the
pinned IP, but smtplib keeps the hostname for the TLS handshake and certificate check.
"""
import smtplib
import socket

from django.conf import settings
from django.core.mail.backends.smtp import EmailBackend


class _PinnedIPSMTP(smtplib.SMTP):
    def _get_socket(self, host, port, timeout):
        ip = getattr(settings, 'EMAIL_CONNECT_IP', '') or host
        return socket.create_connection((ip, port), timeout, self.source_address)


class PinnedIPEmailBackend(EmailBackend):
    @property
    def connection_class(self):
        return _PinnedIPSMTP
