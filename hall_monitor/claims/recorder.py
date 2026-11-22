"""The claims triage's audit events: what TriageRecorder writes for each step of a claim.

The log and the agent- and tool-call events every example shares are in
hall_monitor.governance.audit (AuditLog, EventRecorder); the claim's own events, and the keyed
hash of its documents that finds the same receipts filed again, are claims code.
"""

from __future__ import annotations

from hall_monitor.claims.models import policy_numbers_in
from hall_monitor.governance.audit import AuditLog, EventRecorder, canonical, sha256


class TriageRecorder(EventRecorder):
    """Writes one claim's triage events to the log as they happen (pipeline.triage's on_event).

    Payloads hold IDs, hashes, decision fields and costs, never claim text: critic issues and
    the claim itself are hashed. Agent and tool calls are written by EventRecorder, which keys
    their hashes (HMAC): the recommendation agent's input has few possible values, so a plain
    hash could be confirmed by guessing.
    """

    def __init__(self, log: AuditLog, claim_id: str):
        super().__init__(log, claim_id)
        self._documents: str | None = None

    def documents_hmac(self, claim) -> str | None:
        """A keyed hash of the claim's documents, normalised (case, spacing and their order
        don't count), scoped by the policy numbers in the claim as sent (code finds them; an
        agent's extraction isn't used, so a claim stopped before intake, or with no policy
        number, is hashed too: with none, the documents alone). None if the claim sent no
        documents. Keyed, so the log holds neither the documents nor the policy number, nor a
        hash a guess could confirm."""
        documents = sorted(" ".join(a.text.split()).casefold() for a in claim.attachments)
        if not any(documents):
            return None
        policies = policy_numbers_in(claim)
        return self.log.keyed_hash(canonical({"policies": policies, "documents": documents}))

    def documents_seen(self, claim) -> bool:
        """Whether another claim ID's triage result recorded the same documents_hmac: the
        same documents filed again with the same policy numbers (a restarted triage of this
        claim doesn't count). Triage results only, so a claim counts once its triage has
        finished, stopped or not."""
        digest = self.documents_hmac(claim)
        return digest is not None and self.log.documents_recorded(digest, self.claim_id)

    def _claim_submitted(self, item: dict) -> tuple[str, dict]:
        claim = item["claim"]
        self._documents = self.documents_hmac(claim)
        return f"channel:{claim.channel}", {
            "claim_sha256": sha256(claim.model_dump_json()),
            "submitted_at": claim.submitted_at.isoformat(),
            "attachments": len(claim.attachments),
            "identifiers_redacted": item["redactions"],
            "injection_signals": item["injection_signals"],
        }

    def _critic_review(self, review) -> tuple[str, dict]:
        # Issue text can quote the claim, so only its keyed hash is kept here.
        return "critic", {
            "issues": [
                {
                    "stage": i.stage.value,
                    "problem_hmac": self.log.keyed_hash(i.problem + i.evidence),
                }
                for i in review.issues
            ]
        }

    @staticmethod
    def _supervisor_step(step) -> tuple[str, dict]:
        # Codes, counts and amounts only (SupervisorStep has no free-text field).
        return "claims_supervisor", step.model_dump(mode="json")

    @staticmethod
    def _revision(record) -> tuple[str, dict]:
        return record.stage.value, {
            "round": record.round,
            "changed": {
                f: [record.before.get(f), record.after.get(f)] for f in record.changed_fields
            },
        }

    @staticmethod
    def _code_correction(correction: str) -> tuple[str, dict]:
        return "pipeline", {"correction": correction}

    def _triage_result(self, result) -> tuple[str, dict]:
        return "pipeline", {
            # From the claim as sent, for every result, stopped or not (review 38b).
            "documents_hmac": self._documents,
            "result_sha256": sha256(result.model_dump_json()),
            "route": result.routing.route.value,
            "routing_reasons": result.routing.reasons,
            "recommended_next_step": result.recommendation.next_step.value,
            "covered_amount": result.coverage.covered_amount if result.coverage else None,
            "stopped_reason": result.stopped_reason,
            "cost_usd": result.cost_usd,
            "models": sorted({f"{c.agent_id}:{c.model}" for c in result.agent_calls}),
        }
