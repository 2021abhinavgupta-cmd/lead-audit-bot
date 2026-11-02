from analyzer.ai_audit import AIAuditor

S = AIAuditor._subject_with_site


def test_puts_the_leads_own_domain_in_front():
    assert S("7 Broken Links Are Sending Visitors Nowhere", "https://www.acme-dental.com/home?x=1") == (
        "acme-dental.com: 7 Broken Links Are Sending Visitors Nowhere"
    )


def test_bare_host_without_scheme_works():
    assert S("Slow Site", "acme.in") == "acme.in: Slow Site"


def test_already_present_is_left_alone():
    assert S("Acme.com Loads In 9 Seconds", "https://acme.com") == "Acme.com Loads In 9 Seconds"


def test_unknown_or_invalid_host_changes_nothing():
    assert S("Slow Site", "") == "Slow Site"
    assert S("Slow Site", None) == "Slow Site"
    assert S("Slow Site", "localhost") == "Slow Site"


def test_empty_subject_stays_empty():
    assert S("", "https://acme.com") == ""
