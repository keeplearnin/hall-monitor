"""HM-25: the system never replies to, contacts or sends anything to anyone.

Two halves, both in code:

- **The tool catalogue.** Every MCP tool the system may expose or grant is listed in
  READ_ONLY_TOOLS with what it reads. The registry refuses at run time any manifest that lists a
  tool outside it, or one whose name reads as sending (SEND_WORDS); approvals refuse to approve
  one; the tool gateway refuses to start on a server that exposes one.
- **The static check.** ``python -m hall_monitor.no_send check`` reads the syntax tree of every
  module in hall_monitor/ and the dependencies in pyproject.toml, and fails on anything that
  could send: mail, messaging and raw network libraries, httpx outside hall_monitor/egress.py
  (the one guarded outbound path), HTTP write methods, dynamic imports that could load any of
  those, and shell commands. CI runs it on every pull request, and so do the tests.

The check reads this repository's own code. Third-party libraries it depends on (the Anthropic
SDK, which posts model requests; Langfuse, which posts traces when its keys are set) are not
scanned: what they may reach is stated in the controls register (HM-25 gaps).
"""

from __future__ import annotations

import argparse
import ast
import re
import sys
import tomllib
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

# Every tool the system may expose or grant, and what it reads. Read-only, all of them; adding a
# tool here is a reviewed change, and a name that reads as sending is refused even here.
READ_ONLY_TOOLS: dict[str, str] = {
    "policy_lookup": "reads a synthetic travel policy record",
    "claims_history": "reads the insured's earlier synthetic claims",
    "cfpb_complaint_search": "reads public CFPB complaints with GET (complaints/cfpb.py)",
}

# Words that mean sending wherever they appear in a name ("emailcustomer", "replyto").
_SEND_ANYWHERE = (
    r"send|sent|e?mail|reply|replies|respond|notif|webhook|submit|publish|sms|dispatch|deliver|"
    r"forward|upload|tweet|broadcast|escalat|outreach|contact"
)
# Words that mean sending only as a whole word ("post", not "postal" or "postcode").
_SEND_WORDS = r"post|text|message|messages|call|push|fax|page|ticket"
SEND_WORDS = re.compile(rf"(?:{_SEND_ANYWHERE})|(?:^|_)(?:{_SEND_WORDS})(?:_|$)", re.IGNORECASE)


def _words(name: str) -> str:
    """A tool name in snake case whatever style it was written in: sendEmail and send-email
    both become send_email."""
    return re.sub(r"[^A-Za-z0-9]+", "_", re.sub(r"(?<=[a-z0-9])(?=[A-Z])", "_", name)).lower()


def tool_problems(tools: tuple[str, ...] | list[str]) -> list[str]:
    """Why each tool may not be granted or served: empty when every tool is read-only."""
    problems = []
    for tool in tools:
        if SEND_WORDS.search(_words(tool)):
            problems.append(f"{tool!r} reads as a sending tool")
        elif tool not in READ_ONLY_TOOLS:
            problems.append(f"{tool!r} is not in the read-only tool catalogue")
    return problems


# --- The static check ---------------------------------------------------------------------

# Modules that send mail or messages, open raw connections or reach other hosts. A module is
# banned with everything under it (email.mime bans email.mime.text).
BANNED_MODULES = frozenset(
    {
        "smtplib",
        "smtpd",
        "aiosmtplib",
        "imaplib",
        "poplib",
        "email.mime",
        "ftplib",
        "telnetlib",
        "socket",
        "socketserver",
        "asyncio.streams",
        "ssl",
        "urllib.request",
        "http.client",
        "xmlrpc",
        "webbrowser",
        "subprocess",
        "pty",
        "requests",
        "urllib3",
        "aiohttp",
        "pycurl",
        "websocket",
        "websockets",
        "grpc",
        "paramiko",
        "twilio",
        "sendgrid",
        "slack_sdk",
        "slack",
        "discord",
        "telegram",
        "boto3",
        "botocore",
        "mailgun",
        "postmarker",
        "yagmail",
        "redmail",
        "exchangelib",
        "O365",
        "pywhatkit",
        "pusher",
        "zulip",
        "github",
    }
)
# `from urllib import request` and the like: a banned module imported by its last part.
BANNED_FROM = {("urllib", "request"), ("http", "client"), ("email", "mime")}

# Files allowed one banned module, and why. Each is a fixed local command, read in review.
ALLOWED = {
    ("hall_monitor/evals/meta.py", "subprocess"): "runs `git rev-parse` and `git status` locally",
}

# The one module allowed to make an MCP client, for its own in-process server.
GATEWAY_MODULE = "hall_monitor/tools/gateway.py"
# fastmcp names that reach another server: its client, its client transports and its proxies.
MCP_REMOTE_NAMES = frozenset({"Client", "create_proxy", "as_proxy", "FastMCPProxy", "ProxyClient"})

# The one module allowed to use httpx (and httpx2, the Anthropic SDK's fork): every request
# through it passes the egress guard.
EGRESS_MODULE = "hall_monitor/egress.py"

# Method names that send: HTTP writes, mail and messaging client calls.
SEND_METHODS = frozenset(
    {
        "post",
        "put",
        "patch",
        "delete",
        "sendmail",
        "send_message",
        "send_mail",
        "send_email",
        "chat_postMessage",
        "urlopen",
    }
)
# Calls that take the HTTP method as their first argument or method=: allowed only with "GET".
METHOD_FIRST = frozenset({"request", "stream", "build_request"})
# Calls that open a raw connection, whatever module they come from (asyncio, an event loop).
RAW_CONNECTIONS = frozenset(
    {
        "open_connection",
        "open_unix_connection",
        "create_connection",
        "create_unix_connection",
        "sock_connect",
        "create_datagram_endpoint",
    }
)
# os functions that run another program, which could send.
SHELL_CALL = re.compile(r"^(?:system|popen|posix_spawnp?|spawn\w*|exec\w*|startfile)$")
# What other modules may import from egress.py. Its httpx, its policy and rule types and its
# transport stay inside it, so a new outbound policy can't be built elsewhere.
EGRESS_PUBLIC = frozenset(
    {
        "CFPB_API_HOST",
        "CFPB_API_PATH",
        "CFPB_URL",
        "CFPB_POLICY",
        "MODEL_API_HOST",
        "MODEL_API_POLICY",
        "EgressRefused",
        "OnRefused",
        "guarded_client",
        "model_client",
        "audit_refusals",
        "CfpbClient",
        "CfpbError",
    }
)
EGRESS_INTERNALS = frozenset({"httpx", "httpx2", "Rule", "EgressPolicy", "GuardedTransport"})

# Packages that exist to send: mail, SMS, chat, webhooks, or general HTTP clients beside httpx.
SENDING_PACKAGES = frozenset(
    {
        "requests",
        "aiohttp",
        "urllib3",
        "pycurl",
        "twilio",
        "sendgrid",
        "slack-sdk",
        "slack-bolt",
        "slackclient",
        "discord-py",
        "python-telegram-bot",
        "boto3",
        "botocore",
        "aiosmtplib",
        "yagmail",
        "redmail",
        "mailgun",
        "postmarker",
        "exchangelib",
        "o365",
        "pywhatkit",
        "pusher",
        "zulip",
        "pygithub",
        "websocket-client",
        "websockets",
        "grpcio",
        "paramiko",
    }
)


@dataclass(frozen=True)
class Finding:
    path: str
    line: int
    rule: str
    detail: str

    def __str__(self) -> str:
        return f"{self.path}:{self.line}: {self.rule}: {self.detail}"


def _banned(module: str) -> bool:
    return any(module == b or module.startswith(b + ".") for b in BANNED_MODULES)


def _method_arg(call: ast.Call) -> ast.expr | None:
    """The HTTP method a request-style call names: its first argument or method=."""
    for keyword in call.keywords:
        if keyword.arg == "method":
            return keyword.value
    return call.args[0] if call.args else None


def _scan_file(file: Path, rel: str) -> list[Finding]:
    tree = ast.parse(file.read_text(), filename=rel)
    found: list[Finding] = []
    in_egress = rel == EGRESS_MODULE
    in_gateway = rel == GATEWAY_MODULE
    # Names this file binds to the os module, to egress.py and to fastmcp.
    os_names, egress_names, fastmcp_names = {"os"}, {"egress"}, {"fastmcp"}

    def flag(node: ast.AST, rule: str, detail: str) -> None:
        found.append(Finding(rel, getattr(node, "lineno", 0), rule, detail))

    def check_module(node: ast.AST, module: str) -> None:
        if module.split(".")[0] in ("httpx", "httpx2"):
            if not in_egress:
                flag(node, "httpx_outside_egress", f"{module} is used only in {EGRESS_MODULE}")
        elif module.startswith(("fastmcp.client", "fastmcp.server.providers.proxy")):
            flag(node, "remote_mcp", f"{module}: an MCP client can reach a remote server")
        elif _banned(module) and (rel, module.split(".")[0]) not in ALLOWED:
            flag(node, "banned_import", module)

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                check_module(node, alias.name)
                if alias.name == "os":
                    os_names.add(alias.asname or "os")
                if alias.name == "hall_monitor.egress" and alias.asname:
                    egress_names.add(alias.asname)
                if alias.name == "fastmcp":
                    fastmcp_names.add(alias.asname or "fastmcp")
        elif isinstance(node, ast.ImportFrom) and node.level > 0 and not in_egress:
            # Relative imports of egress.py, or of its internals, from inside the package.
            names = {alias.name for alias in node.names}
            if (node.module or "").split(".")[-1] == "egress":
                for name in sorted(names - EGRESS_PUBLIC):
                    flag(node, "egress_internals", f"{name} stays inside {EGRESS_MODULE}")
            elif "egress" in names:
                egress_names |= {a.asname or a.name for a in node.names if a.name == "egress"}
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            check_module(node, node.module)
            names = {alias.name for alias in node.names}
            if not _banned(node.module):
                for name in sorted(names):
                    if (node.module, name) in BANNED_FROM:
                        flag(node, "banned_import", f"{node.module}.{name}")
            if node.module == "importlib" and "import_module" in names:
                flag(node, "dynamic_import", "import_module can load a sending module")
            if node.module == "os":
                for name in sorted(n for n in names if SHELL_CALL.match(n)):
                    flag(node, "shell_command", f"os.{name}")
            if node.module == "hall_monitor.egress" and not in_egress:
                for name in sorted(names - EGRESS_PUBLIC):
                    flag(node, "egress_internals", f"{name} stays inside {EGRESS_MODULE}")
            if node.module == "hall_monitor":
                egress_names |= {a.asname or a.name for a in node.names if a.name == "egress"}
            if node.module.split(".")[0] == "fastmcp" and not in_gateway:
                for name in sorted(names & MCP_REMOTE_NAMES):
                    flag(node, "remote_mcp", f"fastmcp's {name} can reach a remote server")
        elif isinstance(node, ast.Attribute) and not in_egress and node.attr in EGRESS_INTERNALS:
            if _dotted(node.value) in egress_names | {"hall_monitor.egress"}:
                flag(node, "egress_internals", f"{node.attr} stays inside {EGRESS_MODULE}")
        elif isinstance(node, ast.Attribute) and not in_gateway and node.attr in MCP_REMOTE_NAMES:
            if _dotted(node.value).split(".")[0] in fastmcp_names:
                flag(node, "remote_mcp", f"fastmcp's {node.attr} can reach a remote server")
        elif isinstance(node, ast.Call):
            _check_call(node, flag, os_names)
    return found


def _dotted(node: ast.AST) -> str:
    """a.b.c for a chain of names and attributes; "" for anything else."""
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        inner = _dotted(node.value)
        return f"{inner}.{node.attr}" if inner else ""
    return ""


def _check_call(node: ast.Call, flag, os_names: set[str]) -> None:
    func = node.func
    name = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", None)
    if name in ("__import__", "import_module"):
        flag(node, "dynamic_import", f"{name}() can load a sending module")
    elif name == "getattr" and len(node.args) >= 2:
        attr = node.args[1]
        if isinstance(attr, ast.Constant) and attr.value in SEND_METHODS | METHOD_FIRST:
            flag(node, "send_method", "getattr() can reach a sending method")
    elif isinstance(func, ast.Attribute) and name in SEND_METHODS:
        flag(node, "send_method", f".{name}()")
    elif isinstance(func, ast.Attribute) and name in METHOD_FIRST:
        method = _method_arg(node)
        # stream() with no arguments reads a request body (Starlette); it sends nothing.
        if method is not None and not (isinstance(method, ast.Constant) and method.value == "GET"):
            flag(node, "send_method", f".{name}() with a method other than GET")
    elif name in RAW_CONNECTIONS:
        flag(node, "raw_connection", f"{name}() opens a network connection")
    elif (
        isinstance(func, ast.Attribute)
        and SHELL_CALL.match(func.attr)
        and isinstance(func.value, ast.Name)
        and func.value.id in os_names
    ):
        flag(node, "shell_command", f"os.{func.attr}()")


def _dependency_name(spec: str) -> str:
    name = re.split(r"[\s<>=!~;\[@]", spec.strip(), maxsplit=1)[0]
    return re.sub(r"[-_.]+", "-", name).lower()


def _scan_dependencies(pyproject: Path) -> list[Finding]:
    if not pyproject.exists():
        return [Finding("pyproject.toml", 0, "sending_dependency", "pyproject.toml is missing")]
    data = tomllib.loads(pyproject.read_text())
    specs = list(data.get("project", {}).get("dependencies", []))
    for group in data.get("project", {}).get("optional-dependencies", {}).values():
        specs += group
    for group in data.get("dependency-groups", {}).values():
        specs += [s for s in group if isinstance(s, str)]
    return [
        Finding("pyproject.toml", 0, "sending_dependency", spec)
        for spec in specs
        if _dependency_name(spec) in SENDING_PACKAGES
    ]


def scan(root: Path = ROOT) -> list[Finding]:
    """Every sending capability found in root's hall_monitor/ package and pyproject.toml."""
    findings: list[Finding] = []
    for file in sorted((root / "hall_monitor").rglob("*.py")):
        findings += _scan_file(file, file.relative_to(root).as_posix())
    return findings + _scan_dependencies(root / "pyproject.toml")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m hall_monitor.no_send")
    sub = parser.add_subparsers(dest="command", required=True)
    check = sub.add_parser("check", help="fail if any sending capability is in the code")
    check.add_argument("--root", type=Path, default=ROOT)
    args = parser.parse_args(argv)
    findings = scan(args.root)
    for finding in findings:
        print(finding)
    if findings:
        print(f"{len(findings)} sending capabilities found: the system must never send (HM-25).")
        return 1
    print("No sending capability found (HM-25).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
