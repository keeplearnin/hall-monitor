"""The only outbound HTTP path, and the guard on it (HM-25).

Every request the complaint desk makes passes GuardedTransport, which checks it against an
EgressPolicy before anything is sent: the scheme (https only), the exact host, the default port,
the method and the path. Anything else raises EgressRefused, and the refusal is recorded (method,
host, policy and a hash of the URL; never a body). Redirects are not followed, and if a caller
asks to follow one, the next hop is checked again like any request.

Two policies exist, and no others:

- CFPB_POLICY: GET on the public complaint API, read-only. The data path.
- MODEL_API_POLICY: POST to the Claude API's messages endpoint, for the desk's model client.
  It carries the redacted complaint to the model, never to a consumer, company or CFPB.

The static check (hall_monitor/no_send.py) fails the build if httpx is used anywhere else, so a
new outbound path can't be added without changing this file, in review.
"""

from __future__ import annotations

import hashlib
import posixpath
from collections.abc import Callable
from dataclasses import dataclass

import anthropic
import httpx
import httpx2

CFPB_API_HOST = "www.consumerfinance.gov"
CFPB_API_PATH = "/data-research/consumer-complaints/search/api/v1/"
MODEL_API_HOST = "api.anthropic.com"


class EgressRefused(PermissionError):
    """A request the egress policy doesn't allow. Raised before anything leaves the process."""


@dataclass(frozen=True)
class Rule:
    method: str
    host: str
    path_prefix: str


@dataclass(frozen=True)
class EgressPolicy:
    name: str
    rules: tuple[Rule, ...]

    def refusal(self, request: httpx.Request) -> str | None:
        """Why the request is refused (a short code), or None if a rule allows it."""
        url = request.url
        if url.scheme != "https":
            return "not_https"
        if url.userinfo:
            return "credentials_in_url"
        if url.port not in (None, 443):
            return "not_default_port"
        path = url.path
        # A path that normalises differently (../, //, /./) could leave the allowed prefix.
        if posixpath.normpath(path) + ("/" if path.endswith("/") else "") != path:
            return "path_not_normalised"
        for rule in self.rules:
            if (
                request.method == rule.method
                and url.host == rule.host
                and path.startswith(rule.path_prefix)
            ):
                return None
        return "method_host_or_path_not_allowed"


CFPB_POLICY = EgressPolicy("cfpb_complaint_api", (Rule("GET", CFPB_API_HOST, CFPB_API_PATH),))
MODEL_API_POLICY = EgressPolicy("model_api", (Rule("POST", MODEL_API_HOST, "/v1/messages"),))

OnRefused = Callable[[dict], None]


def _guarded_transport_class(lib):  # lib: httpx or httpx2
    """The guard as a transport for an httpx-style library: httpx for data, httpx2 for the
    Anthropic SDK (which uses that fork)."""

    class Guarded(lib.BaseTransport):
        """Checks every request against the policy, then hands it to the real transport."""

        def __init__(self, policy: EgressPolicy, inner=None, on_refused: OnRefused | None = None):
            self.policy = policy
            self._inner = inner or lib.HTTPTransport(retries=0)
            self._on_refused = on_refused

        def handle_request(self, request):
            reason = self.policy.refusal(request)
            if reason is not None:
                if self._on_refused is not None:
                    self._on_refused(
                        {
                            "policy": self.policy.name,
                            "method": request.method,
                            "host": request.url.host,
                            "url_sha256": hashlib.sha256(str(request.url).encode()).hexdigest(),
                            "reason": reason,
                        }
                    )
                raise EgressRefused(
                    f"egress refused by {self.policy.name}: "
                    f"{request.method} {request.url.host} ({reason})"
                )
            return self._inner.handle_request(request)

        def close(self) -> None:
            self._inner.close()

    Guarded.__name__ = Guarded.__qualname__ = f"GuardedTransport[{lib.__name__}]"
    return Guarded


GuardedTransport = _guarded_transport_class(httpx)
_GuardedModelTransport = _guarded_transport_class(httpx2)


def guarded_client(
    policy: EgressPolicy,
    *,
    transport: httpx.BaseTransport | None = None,
    on_refused: OnRefused | None = None,
    timeout: float = 30.0,
) -> httpx.Client:
    """An httpx client that can reach only what the policy allows. transport replaces the real
    network transport (tests pass a mock); the guard is always in front of it."""
    return httpx.Client(
        transport=GuardedTransport(policy, transport, on_refused),
        follow_redirects=False,
        timeout=timeout,
        headers={"user-agent": "hall-monitor-complaint-desk (read-only)"},
    )


def model_client(
    *,
    api_key: str | None = None,
    transport: httpx2.BaseTransport | None = None,
    on_refused: OnRefused | None = None,
    max_retries: int = 0,
) -> anthropic.Anthropic:
    """The complaint desk's Claude client: it can reach only the messages endpoint. Retries are
    off by default, so a failure stops the complaint and sends it to a person (HM-08). The SDK
    uses httpx2, so transport (a test's mock) is an httpx2 transport."""
    http = httpx2.Client(
        transport=_GuardedModelTransport(MODEL_API_POLICY, transport, on_refused),
        follow_redirects=False,
        timeout=120.0,
    )
    return anthropic.Anthropic(api_key=api_key, http_client=http, max_retries=max_retries)


def audit_refusals(audit) -> OnRefused:
    """Record each refusal in the audit log, under the scope "egress", as the pipeline."""

    def record(refusal: dict) -> None:
        audit.append("egress", "egress_refused", "pipeline", refusal)

    return record
