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
}

SEND_WORDS = re.compile(
    r"(?:^|_)(?:send|sends|sent|e?mail|reply|replies|respond|post|notify|notification|webhook|"
    r"submit|publish|sms|text|message|messages|dispatch|deliver|forward|upload|tweet|call|"
    r"contact|escalate|push|broadcast|fax|page|ticket)(?:_|$)",
    re.IGNORECASE,
)


def tool_problems(tools: tuple[str, ...] | list[str]) -> list[str]:
    """Why each tool may not be granted or served: empty when every tool is read-only."""
    problems = []
    for tool in tools:
        if SEND_WORDS.search(tool):
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

# The one module allowed to use httpx (and httpx2, the Anthropic SDK's fork): every request
# through it passes the egress guard.
EGRESS_MODULE = "hall_monitor/egress.py"

# Method names that send: HTTP writes, mail and messaging client calls.
SEND_METHODS = frozenset(
    {
        "post",
        "put",
        "patch",
        "sendmail",
        "send_message",
        "send_mail",
        "send_email",
        "chat_postMessage",
        "urlopen",
    }
)
HTTP_WRITES = frozenset({"POST", "PUT", "PATCH", "DELETE"})
SHELL_CALLS = frozenset({"system", "popen", "spawnl", "spawnv", "execv", "execl", "execvp"})

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


def _scan_file(file: Path, rel: str) -> list[Finding]:
    tree = ast.parse(file.read_text(), filename=rel)
    found: list[Finding] = []

    def flag(node: ast.AST, rule: str, detail: str) -> None:
        found.append(Finding(rel, getattr(node, "lineno", 0), rule, detail))

    def check_module(node: ast.AST, module: str) -> None:
        if module.split(".")[0] in ("httpx", "httpx2"):
            if rel != EGRESS_MODULE:
                flag(node, "httpx_outside_egress", f"{module} is used only in {EGRESS_MODULE}")
        elif _banned(module) and (rel, module.split(".")[0]) not in ALLOWED:
            flag(node, "banned_import", module)

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                check_module(node, alias.name)
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            check_module(node, node.module)
            if not _banned(node.module):
                for alias in node.names:
                    if (node.module, alias.name) in BANNED_FROM:
                        flag(node, "banned_import", f"{node.module}.{alias.name}")
        elif isinstance(node, ast.Call):
            func = node.func
            name = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", None)
            if name in ("__import__", "import_module"):
                flag(node, "dynamic_import", f"{name}() can load a sending module")
            elif isinstance(func, ast.Attribute) and name in SEND_METHODS:
                flag(node, "send_method", f".{name}()")
            elif isinstance(func, ast.Attribute) and name == "request" and node.args:
                first = node.args[0]
                if not (isinstance(first, ast.Constant) and first.value == "GET"):
                    flag(node, "send_method", ".request() with a method other than GET")
            elif (
                isinstance(func, ast.Attribute)
                and name in SHELL_CALLS
                and isinstance(func.value, ast.Name)
                and func.value.id == "os"
            ):
                flag(node, "shell_command", f"os.{name}()")
    return found


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
