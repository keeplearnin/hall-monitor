"""The CIO, CISO and auditor briefing is the three use cases, run, not a hand-kept page."""

from hall_monitor.briefing import CLAIM_IDS, DOC, render
from hall_monitor.claims.data import DATA_DIR, load_claim, load_ground_truth
from hall_monitor.complaints.ingest import PINNED_NAME, load_pinned
from hall_monitor.complaints.routing import HIGH_REASONS, code_reasons
from hall_monitor.no_send import scan


def test_the_briefing_is_what_the_three_use_cases_just_did():
    assert DOC.read_text() == render()


def test_the_briefing_shows_each_audience_the_control_and_its_limit():
    text = DOC.read_text()
    assert "claim_001" in text and "Route: `auto`" in text
    assert "claim_004" in text and "Route: `adjuster`" in text
    assert "lr_001" in text and "`local_manager`" in text
    assert "lr_004" in text and "`hr_business_partner`" in text
    assert "lr_009" in text and "`hr_director`" in text
    assert "urgency to low" in text and "Urgency: `high`" in text
    assert "The static no-send check reports 0 findings." in text
    assert scan() == []
    assert "answer key" in text
    assert "The leave rows are the FMLA rules. No leave agent runs." in text
    assert "public demo keys" in text
    assert "Partner claims are not in this run" in text


def test_the_briefing_does_not_print_case_text():
    """Names, policy numbers, claim documents and a complaint narrative stay off the page."""
    text = render()
    for claim_id in CLAIM_IDS:
        claim = load_claim(DATA_DIR / "claims" / f"{claim_id}.json")
        truth = load_ground_truth(claim_id)
        assert truth.extraction.claimant_name not in text
        assert truth.extraction.policy_number not in text
        assert claim.narrative not in text
        for attachment in claim.attachments:
            assert attachment.text not in text
    for complaint in load_pinned(PINNED_NAME):
        if code_reasons(complaint) & HIGH_REASONS:
            if complaint.narrative:
                assert complaint.narrative not in text
            break
