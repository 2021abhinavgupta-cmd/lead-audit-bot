"""
Turn a SocialProfile into (1) a ranked list of concrete issues and (2)
channel-specific outreach copy.

audit_profile is deterministic: code decides the issues, the AI (in
generate_social_outreach) only writes copy about the issues code already
found. Same split as analyzer/flaws.py for websites.

Wording rules for every `detail` string: plain language, no jargon, and no
hyphens or dashes of any kind (the outreach model mirrors the style it is
shown, and the rest of this codebase's generated copy forbids dashes).
"""

import json
import re as _re
from dataclasses import dataclass
from statistics import median

from analyzer import social_link
from scrapers.social.base import SocialProfile

_SEVERITY_RANK = {"high": 0, "medium": 1, "low": 2}

# Tie break inside one severity: issues a business owner can check for
# themselves in ten seconds (tap the link, look at the dates) come first, so
# the outreach leads with the most provable problem.
_PROOF_RANK = {
    "Link in bio is broken": 0,
    "Inactive account": 1,
    "No link in the bio": 2,
    "No call to action in the bio": 3,
    "Uneven posting schedule": 4,
    "Not using short video": 5,
}
_PROOF_DEFAULT = 10


@dataclass
class SocialIssue:
    severity: str   # "high" | "medium" | "low"
    label: str
    detail: str


def _issue_rank(issue: SocialIssue) -> tuple[int, int]:
    return _SEVERITY_RANK.get(issue.severity, 3), _PROOF_RANK.get(issue.label, _PROOF_DEFAULT)


# ---------------------------------------------------------------------------
# word lists (lower case, matched on word boundaries)
# ---------------------------------------------------------------------------

def _word_re(words) -> "_re.Pattern":
    alts = sorted((_re.escape(w) for w in words), key=len, reverse=True)
    return _re.compile(r"(?<![a-z0-9])(?:" + "|".join(alts) + r")(?![a-z0-9])", _re.I)


_BIO_CTA = _word_re((
    "book", "booking", "bookings", "book now", "call", "call us", "calls", "dm", "dms", "dm us",
    "message", "message us", "msg", "inbox", "text us", "order", "orders", "ordering", "pre order",
    "whatsapp", "whats app", "wa", "email", "e mail", "mail", "mail us", "contact", "contact us",
    "enquire", "enquiry", "enquiries", "inquire", "inquiry", "inquiries", "appointment", "appointments",
    "reserve", "reservation", "reservations", "shop", "shop now", "buy", "visit", "click", "tap",
    "link", "link below", "below", "get in touch", "reach us", "reach out", "write to", "hire",
    "work with us", "sign up", "register", "join", "download", "quote", "subscribe", "apply",
))
_EMAIL_RE = _re.compile(r"[\w.+]+@[\w-]+\.[\w.-]+")
_PHONE_RE = _re.compile(r"\+?\d[\d ()]{8,}\d")
_CTA_EMOJI = ("\U0001F447", "⬇", "\U0001F4DE", "☎", "\U0001F4E9", "\U0001F4E7", "✉",
              "\U0001F4F2", "\U0001F6D2", "\U0001F449")

_LOCATION_WORDS = _word_re((
    "based in", "located", "location", "locations", "address", "hq", "headquarters", "near",
    "visit us", "outlet", "outlets", "branch", "branches", "store", "stores", "worldwide",
    "pan india", "all india", "across india", "india", "online", "delivery", "delivering",
    "we deliver", "ships", "shipping", "nationwide", "city",
    "mumbai", "delhi", "new delhi", "bangalore", "bengaluru", "hyderabad", "chennai", "kolkata",
    "pune", "ahmedabad", "surat", "jaipur", "lucknow", "kanpur", "nagpur", "indore", "bhopal",
    "goa", "chandigarh", "kochi", "gurgaon", "gurugram", "noida", "thane", "navi mumbai",
    "vadodara", "coimbatore", "mysore", "mysuru", "nashik", "ludhiana", "dubai", "london",
    "new york", "singapore",
))
_LOCATION_EMOJI = ("\U0001F4CD", "\U0001F30D", "\U0001F30E", "\U0001F30F", "\U0001F3E0", "\U0001F3EA",
                   "\U0001F3E2", "\U0001F5FA")
# "Bakery in Pune", "Studio at Bandra": a capitalised place after in / at.
_IN_PLACE_RE = _re.compile(r"\b(?:in|at|from)\s+[A-Z][a-z]{2,}")
_PINCODE_RE = _re.compile(r"\b\d{6}\b")

_SERVICE_WORDS = _word_re((
    "we", "our", "us", "make", "makes", "made", "making", "sell", "selling", "offer", "offering",
    "provide", "providing", "design", "designs", "designer", "studio", "shop", "store", "boutique",
    "salon", "spa", "clinic", "cafe", "café", "restaurant", "bakery", "bakes", "bakehouse",
    "kitchen", "agency", "service", "services", "brand", "handmade", "homemade", "products",
    "consulting", "consultant", "coach", "coaching", "classes", "academy", "school", "institute",
    "gym", "fitness", "yoga", "photography", "photographer", "makeup", "artist", "catering",
    "caterer", "events", "wedding", "weddings", "interior", "interiors", "realty", "real estate",
    "jewellery", "jewelry", "fashion", "clothing", "apparel", "wear", "skincare", "dental",
    "dentist", "doctor", "dr", "hospital", "pharmacy", "repair", "repairs", "travel", "tours",
    "hotel", "homestay", "resort", "food", "cakes", "coffee", "tea", "organic", "decor",
    "furniture", "printing", "marketing", "software", "solutions", "official", "company",
    "pvt", "ltd", "llp", "since", "founder", "founded", "est", "manufacturer", "manufacturing",
    "wholesale", "retail", "supplier", "exporter", "delivery", "custom", "bespoke",
))

_CAPTION_CTA = _word_re((
    "link in bio", "link", "dm", "dm us", "message", "message us", "book", "booking", "order",
    "order now", "call", "whatsapp", "shop", "shop now", "buy", "visit", "comment", "comments",
    "tag", "share", "save", "follow", "click", "tap", "swipe", "register", "sign up", "join",
    "enquire", "enquiry", "inquire", "contact", "reach out", "email", "mail", "learn more",
    "check out", "get yours", "grab", "try", "reply", "tell us", "let us know", "drop", "apply",
    "subscribe", "download", "reserve", "hurry", "limited",
))
_HASHTAG_RE = _re.compile(r"#\w+")


def _bio_has_cta(bio: str) -> bool:
    return bool(_BIO_CTA.search(bio) or _EMAIL_RE.search(bio) or _PHONE_RE.search(bio)
                or "wa.me" in bio.lower() or any(e in bio for e in _CTA_EMOJI))


def _bio_says_where(bio: str) -> bool:
    return bool(_LOCATION_WORDS.search(bio) or _IN_PLACE_RE.search(bio) or _PINCODE_RE.search(bio)
                or any(e in bio for e in _LOCATION_EMOJI))


def _bio_says_what(bio: str) -> bool:
    return bool(_SERVICE_WORDS.search(bio))


# ---------------------------------------------------------------------------
# helpers over recent_posts
# ---------------------------------------------------------------------------

def _post_dates_in_order(posts: list[dict]) -> list[int] | None:
    """Post timestamps with pinned posts left out, or None when any date is
    missing. A pinned post sits at the top of the grid out of date order (up
    to three), so a top post older than one below it is treated as pinned."""
    ts = [x.get("ts") for x in posts]
    if not ts or any(not isinstance(t, (int, float)) for t in ts):
        return None
    keep = []
    for i, t in enumerate(ts):
        if i < 3 and any(t < later for later in ts[i + 1:]):
            continue
        keep.append(t)
    return keep


def _uneven_posting(posts: list[dict]) -> str | None:
    """'gap' when the largest gap between two posts is over 21 days,
    'burst' when more than half the posts landed in one 7 day window of a
    span of at least four weeks, else None."""
    ts = _post_dates_in_order(posts)
    if ts is None or len(ts) < 4:
        return None
    days = sorted(t / 86400 for t in ts)
    if max(b - a for a, b in zip(days, days[1:])) > 21:
        return "gap"
    span = days[-1] - days[0]
    if span >= 28:
        most = max(sum(1 for d in days if start <= d < start + 7) for start in days)
        if most > len(days) / 2:
            return "burst"
    return None


def _link_issue(p: SocialProfile, website: str, link_check: dict | None) -> SocialIssue | None:
    """At most one issue about where the bio link goes."""
    if not p.has_link_in_bio or not p.link_url:
        return None
    lc = link_check or {}
    if lc.get("verdict") == "broken":
        return SocialIssue(
            "high", "Link in bio is broken",
            "The link in the bio does not open, so anyone who taps it to buy or book lands on an error instead.",
        )
    final = lc.get("final_url") if lc.get("verdict") == "ok" else ""
    if social_link.is_link_aggregator(p.link_url) or (final and social_link.is_link_aggregator(final)):
        return SocialIssue(
            "low", "Link in bio is a link list page",
            "The bio link opens a page of more links rather than the business's own site, "
            "which adds one more tap before a visitor can buy or book.",
        )
    if not website:
        return None
    target = final or p.link_url
    if not final and social_link.is_url_shortener(p.link_url):
        return None   # a short link whose target we did not see
    if social_link.is_whatsapp_link(target):
        return None   # a deliberate contact route, not a wrong site
    own = social_link.registered_domain(website)
    if own and social_link.registered_domain(target) != own:
        return SocialIssue(
            "low", "Link in bio skips the website",
            "The bio link points somewhere other than the business website, "
            "so people coming from Instagram never land on the main site.",
        )
    return None


def audit_profile(profile: SocialProfile, *, website: str = "", link_check: dict | None = None) -> list[SocialIssue]:
    """Ranked issues for a profile. Empty when the profile could not be
    analyzed or when nothing is wrong.

    website: the business's own site when known (enables the "bio link goes
    elsewhere" check). link_check: social_link.check_bio_link's verdict for
    the bio link, fetched by the caller (only "broken" yields the broken
    link issue; None / "unknown" never does)."""
    if not profile.analyzed:
        return []

    issues: list[SocialIssue] = []
    p = profile
    plat = p.platform
    posts = p.recent_posts or []
    inactive = False

    # Activity checks only run when the adapter actually saw the posting
    # activity. On a header-only read (activity_known=False) the activity
    # fields sit at their zero defaults, and treating those as facts would put
    # a false "Inactive account" on every such profile.
    if p.activity_known:
        inactive = p.posts_last_30_days == 0 or (
            p.last_post_age_days is not None and p.last_post_age_days > 45
        )
        if inactive:
            issues.append(SocialIssue(
                "high", "Inactive account",
                "The account has gone quiet, so anyone who checks it sees a page that looks abandoned.",
            ))

        if not inactive and p.posting_frequency in ("irregular", "weekly") and p.followers > 1000:
            issues.append(SocialIssue(
                "medium", "Posting has slowed",
                "Posts are going out only now and then, which is not enough to stay in front of followers.",
            ))


        if not inactive and _uneven_posting(posts):
            issues.append(SocialIssue(
                "medium", "Uneven posting schedule",
                "Posts come in bursts with long quiet stretches in between, "
                "so followers see the brand on and off rather than steadily.",
            ))

    # Short video: ONE issue. When the post types were read, judge from them
    # (works on a public read without dates too); otherwise fall back to the
    # adapter's uses_video, which is only meaningful when activity was seen.
    if p.formats_known and len(posts) >= 3:
        if not any(x.get("type") == "video" for x in posts):
            issues.append(SocialIssue(
                "medium", "Not using short video",
                "The recent posts are all photos with no reels or short videos, "
                "and short video is the format the platform pushes hardest right now.",
            ))
    elif not p.formats_known and p.activity_known and not p.uses_video:
        issues.append(SocialIssue(
            "medium", "Not using short video",
            "There are no reels or short videos, and that is the format the platform pushes hardest right now.",
        ))

    if plat in ("instagram", "facebook") and not p.has_link_in_bio:
        issues.append(SocialIssue(
            "medium", "No link in the bio",
            "There is no clickable link, so a visitor who wants to buy or book has nowhere to go.",
        ))

    link = _link_issue(p, website, link_check) if plat in ("instagram", "facebook") else None
    if link:
        issues.append(link)

    bio = p.bio.strip()
    if len(bio) < 20:
        issues.append(SocialIssue(
            "low", "Thin or empty bio",
            "The bio barely says what the business does or why someone should follow.",
        ))
    elif not _bio_says_what(bio) and not _bio_says_where(bio):
        # merged with the thin bio check: a short bio gets that one only
        issues.append(SocialIssue(
            "low", "Bio does not say what or where",
            "The bio does not say what the business offers or where it is, "
            "so a new visitor cannot tell at a glance whether it is for them.",
        ))

    if plat in ("instagram", "facebook") and not _bio_has_cta(bio):
        issues.append(SocialIssue(
            "medium", "No call to action in the bio",
            "The bio never tells a visitor what to do next, such as book, call, order or send a message, "
            "so interested people are left to work it out on their own.",
        ))

    if p.captions_known and len(posts) >= 3:
        issues.extend(_caption_issues(posts))

    # Engagement checks: only with likes AND comments actually read (the
    # logged in session reader). A public read never sees them.
    if p.engagement_known and plat in ("instagram", "facebook"):
        issues.extend(_engagement_issues(p, posts))

    if plat == "instagram" and p.following > p.followers and p.followers < 2000:
        issues.append(SocialIssue(
            "low", "Following more than followers",
            "The account follows more people than follow it back, which reads as a brand that is still finding its feet.",
        ))

    # On a header-only read a 0 means "count not read", not "no posts".
    post_count_known = p.activity_known or p.posts_count > 0
    if post_count_known and p.posts_count < 9:
        issues.append(SocialIssue(
            "low", "Thin content history",
            "There are only a few posts in total, so the page has little for a new visitor to look through.",
        ))

    if post_count_known and p.posts_count >= 50 and p.followers / p.posts_count < 8:
        issues.append(SocialIssue(
            "low", "Audience not growing with the posts",
            "The account has been posting a lot without the audience growing with it, "
            "so the effort going into the page is not turning into new followers.",
        ))

    issues.sort(key=_issue_rank)
    return issues


# Engagement (likes + comments per post, as a share of followers) falls as an
# account grows, and published benchmarks disagree widely (roughly 0.45% to
# 2.5% for "average"). So: coarse size bands, and a flag only when the rate is
# far below even the cautious end for the band. These floors are ours, never
# stated as a benchmark in any copy.
_ENGAGEMENT_BANDS = (
    (1_000, "under 1k", 1.0),
    (10_000, "1k to 10k", 0.5),
    (50_000, "10k to 50k", 0.3),
    (None, "50k plus", 0.15),
)


def engagement_band(followers: int) -> tuple[str, float]:
    """(band name, the rate below which engagement is clearly low)."""
    for upper, name, floor in _ENGAGEMENT_BANDS:
        if upper is None or followers < upper:
            return name, floor
    return _ENGAGEMENT_BANDS[-1][1], _ENGAGEMENT_BANDS[-1][2]


def _avg(values):
    return sum(values) / len(values) if values else 0.0


def _engagement_issues(p: SocialProfile, posts: list[dict]) -> list[SocialIssue]:
    out: list[SocialIssue] = []
    _, floor = engagement_band(p.followers)
    low = p.followers > 500 and 0 < p.avg_engagement_rate < floor
    if low:
        out.append(SocialIssue(
            "medium", "Low engagement",
            "Very few followers like or comment on the posts, well below what is typical for an account of this size.",
        ))

    counted = [x for x in posts if x.get("likes") is not None and x.get("comments") is not None]
    if not low and p.followers > 1000 and len(counted) >= 3 and len(counted) == len(posts):
        if _avg([x["comments"] for x in counted]) < 1:
            out.append(SocialIssue(
                "low", "Very few comments",
                "The posts get almost no comments, so there is little conversation happening on the page.",
            ))

    if p.formats_known and len(counted) == len(posts):
        def eng(kind):
            vals = [x["likes"] + x["comments"] for x in counted if x.get("type") == kind]
            return (_avg(vals), len(vals))
        img_avg, img_n = eng("image")
        better = []
        if img_n >= 3:
            for kind, word in (("video", "video"), ("carousel", "carousel")):
                avg, n = eng(kind)
                if n >= 3 and avg > 0 and avg >= 1.5 * img_avg:
                    better.append(word)
        if better:
            which = " and ".join(better)
            out.append(SocialIssue(
                "low", f"Image posts underperform {which} posts on this account",
                f"On this account the {which} posts get clearly more likes and comments than the single image posts, "
                f"yet single images are still part of the mix.",
            ))
    return out


def _caption_issues(posts: list[dict]) -> list[SocialIssue]:
    """Caption checks over the recent posts (captions known, at least 3 posts)."""
    out: list[SocialIssue] = []
    captions = [(x.get("caption") or "").strip() for x in posts]
    filled = [c for c in captions if c]
    if len(captions) - len(filled) >= len(captions) / 2:
        out.append(SocialIssue(
            "low", "Many posts have no caption",
            "Many of the recent posts go out with no caption, so nothing tells people what they are "
            "looking at or what to do next.",
        ))
    if len(filled) >= 3:
        if not any(_CAPTION_CTA.search(c) for c in filled):
            out.append(SocialIssue(
                "low", "Captions never ask for action",
                "None of the recent captions ask people to do anything, like book, order, message or visit, "
                "so a post that catches someone's eye gives them no next step.",
            ))
        tags = [len(_HASHTAG_RE.findall(c)) for c in filled]
        if sum(tags) == 0:
            out.append(SocialIssue(
                "low", "No hashtags on recent posts",
                "None of the recent posts use hashtags, so they miss one of the simple ways people "
                "browsing a topic can come across them.",
            ))
        elif sum(tags) / len(filled) > 15:
            out.append(SocialIssue(
                "low", "Too many hashtags",
                "The recent posts carry long walls of hashtags, which looks spammy and buries the message.",
            ))
    return out


# ---------------------------------------------------------------------------
# Lead score
# ---------------------------------------------------------------------------

_ISSUE_POINTS = {"high": 15, "medium": 10, "low": 5}
# analyzer/audience_quality.py label -> lead score change. "unknown" and
# "mixed" change nothing.
_AUDIENCE_POINTS = {"suspect": -15, "healthy": 5}
_AUDIENCE_REASON = {
    "suspect": "The audience looks less engaged than its size suggests, so it is worth checking before pitching.",
    "healthy": "Engagement looks healthy for the size of the audience.",
}


def lead_score(profile: SocialProfile | None, issues: list[SocialIssue], audience: dict | None = None) -> dict:
    """How worth pitching this profile is, from known data only:
    {"score": 0..100, "label": "strong" | "maybe" | "weak", "reasons": [...]}.

    Stronger: posting recently AND a real audience AND fixable issues.
    Weak: abandoned, private, or not analyzed. Unknown posting activity caps
    the lead at "maybe" (we cannot say anyone is running the account).
    audience: analyzer.audience_quality's result; "suspect" lowers the score
    by 15, "healthy" raises it by 5, anything else changes nothing.
    Deterministic; reasons are plain language for the Social tab, never fed
    to the outreach model."""
    if profile is None or not profile.analyzed:
        note = (getattr(profile, "note", "") or "").lower()
        if "private" in note:
            reason = "The account is private, so there is nothing visible to pitch on."
        else:
            reason = "The profile could not be read, so there is nothing to pitch on yet."
        return {"score": 0, "label": "weak", "reasons": [reason]}

    p = profile
    score = 0
    reasons: list[str] = []
    cap = 100
    age = p.last_post_age_days

    if p.activity_known and (age is None or age > 90):
        reasons.append("No posts in over three months, so the account looks abandoned." if age is not None
                       else "There are no posts to look at, so the account looks unused.")
        cap = 20
    elif p.activity_known and age <= 30:
        score += 35
        reasons.append("Posted within the last month, so someone is still running the account.")
    elif p.activity_known:
        score += 10
        reasons.append("Posting has gone quiet recently but the account is not abandoned.")
    else:
        score += 15
        reasons.append("Posting activity could not be read, so it is unclear whether anyone runs the account.")
        cap = 69

    if p.followers >= 500:
        score += 30
        reasons.append(f"Has a real audience of {p.followers:,} followers.")
    elif p.followers >= 100:
        score += 10
        reasons.append(f"Small audience of {p.followers:,} followers.")
    else:
        reasons.append(f"Very small audience of {p.followers:,} followers.")

    if issues:
        top = sorted(issues, key=_issue_rank)[0]
        score += min(35, sum(_ISSUE_POINTS.get(i.severity, 0) for i in issues))
        n = len(issues)
        reasons.append(f"{n} fixable issue{'s' if n != 1 else ''} found, the biggest being {top.label.lower()}.")
    else:
        reasons.append("No clear issues found, so there is little to pitch.")

    aq_label = (audience or {}).get("label")
    if aq_label in _AUDIENCE_POINTS:
        score += _AUDIENCE_POINTS[aq_label]
        reasons.append(_AUDIENCE_REASON[aq_label])

    score = max(0, min(score, cap, 100))
    label = "strong" if score >= 70 else "maybe" if score >= 40 else "weak"
    return {"score": score, "label": label, "reasons": reasons}


# ---------------------------------------------------------------------------
# Outreach copy generation
# ---------------------------------------------------------------------------

# Instructions are spelled out in words, so every digit in the prompt comes
# from the business name, the handle or the issue list (tested).
_CHANNEL_SHAPE = {
    "email": (
        "Write a cold SALES email of one hundred and eighty to two hundred and fifty words, in short paragraphs of one to three "
        "sentences. Voice: confident and direct, like a sharp salesperson who has studied this exact account, not a template. "
        "Structure: "
        "First paragraph: state the TOP ISSUE as customers, bookings or sales being lost, in plain terms. "
        "Second paragraph: if MEASURED FACTS include engagement, quote those numbers exactly as given. If the issue list contains "
        "Low engagement or Posts are not reaching the audience, use the numbers to show the gap between audience size and likes and "
        "comments and what it costs the business. Otherwise use them as proof that the audience is real and responds, which makes the "
        "other problems more expensive to leave alone, and never call the engagement weak. "
        "Third paragraph: walk through every other issue in one short sentence each, saying what it costs the business in "
        "customers, never inventing a number. "
        "Fourth paragraph: start with 'Here is what I would do' and describe the fixes in a short sequence of sentences, one per issue. "
        "Fifth paragraph: one sentence introducing MMGA as a small agency that fixes exactly this, then the PROOF LINE if one is given. "
        "Never claim MMGA experience, clients, results, studies or time spent beyond the PROOF LINE. "
        "Last paragraph: offer to send a one page plan for this account, free, within a day, and make the cost of waiting felt "
        "without pressure or threats. "
        "Return a subject line of at most fifty characters that is specific and creates curiosity about the problem."
    ),
    "dm": "Write a short Instagram DM. No subject. Two or three sentences, casual, one issue, one soft ask.",
    "whatsapp": "Write a short WhatsApp message. No subject. Two or three sentences, casual and friendly, light on emoji.",
}


def _proof_line() -> str:
    import config
    return (getattr(config, "SOCIAL_PROOF_LINE", "") or "").strip()


def _fmt_num(n: float) -> str:
    if n >= 10:
        return f"{int(round(n)):,}"
    return f"{n:.1f}".removesuffix(".0")


def engagement_summary(profile) -> dict | None:
    """The typical recent post: {"median_likes", "median_comments",
    "posts_read"} over the posts whose likes AND comments were read. The
    median, not the mean, so one viral or collab post cannot inflate it.
    None unless engagement is known and at least four posts were counted."""
    if profile is None or not getattr(profile, "engagement_known", False):
        return None
    read = [p for p in (getattr(profile, "recent_posts", None) or [])
            if isinstance(p, dict) and p.get("likes") is not None and p.get("comments") is not None]
    if len(read) < 4:
        return None
    return {
        "median_likes": median(p["likes"] for p in read),
        "median_comments": median(p["comments"] for p in read),
        "posts_read": len(read),
    }


def measured_facts(profile) -> tuple[list[str], set[str]]:
    """Real numbers read from the account, as plain sentences the email may
    quote, plus the set of digit strings those sentences contain. Engagement
    facts only when at least four recent posts were actually counted, and
    they describe the TYPICAL post (median), never an average."""
    facts: list[str] = []
    if profile.followers and profile.followers > 0:
        facts.append(f"The account has {profile.followers:,} followers")
    summary = engagement_summary(profile)
    if summary:
        facts.append(f"Across its last {summary['posts_read']} posts, a typical post gets about "
                     f"{_fmt_num(summary['median_likes'])} likes and "
                     f"{_fmt_num(summary['median_comments'])} comments")
        if profile.avg_engagement_rate and profile.avg_engagement_rate > 0:
            facts.append(f"For a typical post that is an engagement rate of about "
                         f"{profile.avg_engagement_rate:.1f} percent of its followers")
    nums = {m.replace(",", "") for f in facts for m in _re.findall(r"\d[\d,]*\.?\d*", f)}
    return facts, nums


def _numbers_in(text: str) -> set[str]:
    return {m.replace(",", "").rstrip(".") for m in _re.findall(r"\d[\d,]*\.?\d*", text or "")}


def _disallowed_numbers(body: str, profile, issues, company: str) -> set[str]:
    """Digits in the email that are not measured facts, the business name, the
    handle, the issue wording or the proof line."""
    _facts, nums = measured_facts(profile)
    allowed = set(nums)
    for source in (company, profile.handle, _proof_line(),
                   " ".join(f"{i.label} {i.detail}" for i in issues)):
        allowed |= _numbers_in(source)
    return {n for n in _numbers_in(body) if n not in allowed}


def _build_outreach_prompt(company, profile, issues, channel, your_name):
    # Strongest PROVEN issue first: severity, then how easily the owner can
    # verify it (a broken link before a judgement call). Stable for ties.
    ordered = sorted(issues, key=_issue_rank)
    if ordered:
        top, rest = ordered[0], ordered[1:]
        issue_lines = f"TOP ISSUE (open with this one):\n- {top.label}: {top.detail}"
        if rest:
            issue_lines += "\nOTHER ISSUES:\n" + "\n".join(f"- {i.label}: {i.detail}" for i in rest)
    else:
        issue_lines = "- (no specific issues found)"
    shape = _CHANNEL_SHAPE.get(channel, _CHANNEL_SHAPE["email"])
    facts_block = ""
    if channel == "email":
        facts, _nums = measured_facts(profile)
        if facts:
            facts_block = ("MEASURED FACTS (real numbers read from the account, you may quote these exactly and no other number):\n"
                           + "\n".join(f"- {f}" for f in facts) + "\n\n")
        proof = _proof_line()
        if proof:
            facts_block += f"PROOF LINE (use as one sentence, word for word):\n- {proof}\n\n"
    return (
        f"You write outreach for MMGA, a small agency that fixes social media presence for local businesses.\n\n"
        f"Business: {company}\n"
        f"Platform: {profile.platform}\n"
        f"Handle: @{profile.handle}\n\n"
        f"ISSUES FOUND (these are the ONLY facts you may use, do not invent any number, metric or claim beyond this list"
        f"{' and the measured facts' if facts_block else ''}):\n"
        f"{issue_lines}\n\n"
        f"{facts_block}"
        f"{shape}\n\n"
        f"Rules:\n"
        f"- Open with the top issue stated as a business problem, not a compliment or pleasantry.\n"
        f"- Plain language a busy owner understands. No marketing jargon.\n"
        f"- Do NOT use hyphens or dashes of any kind anywhere in your output.\n"
        f"- Do NOT use generic AI phrases like 'I noticed', 'In today's competitive landscape', 'I hope this finds you well'.\n"
        f"- One low pressure call to action (offer to send a short list, not demand a meeting).\n"
        f"- Sign off as {your_name}.\n\n"
        f"Return ONLY JSON: {{\"subject\": \"...\", \"body\": \"...\"}}. "
        f"For a DM or WhatsApp message set subject to an empty string."
    )


def _parse_outreach_json(text: str) -> dict | None:
    if not text:
        return None
    fenced = _re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, _re.DOTALL)
    raw = fenced.group(1) if fenced else text
    start = raw.find("{")
    end = raw.rfind("}")
    if start == -1 or end == -1 or end <= start:
        return None
    try:
        obj = json.loads(raw[start:end + 1])
    except (ValueError, TypeError):
        return None
    if not isinstance(obj, dict) or "body" not in obj:
        return None
    return obj


def _default_ai_call(prompt: str) -> tuple[str, float]:
    """Real provider path — reuse AIAuditor's simple text-in/JSON-out
    fallback chain (the same three providers, in the same order, that the
    History tab's follow-up generator uses: Claude Haiku, then Gemini, then
    GPT-4o-mini). Each returns (raw_text, cost) or None."""
    from analyzer.ai_audit import AIAuditor
    auditor = AIAuditor()
    for call_fn in (
        auditor._call_followup_anthropic,   # noqa: SLF001
        auditor._call_followup_gemini,      # noqa: SLF001
        auditor._call_followup_openai,      # noqa: SLF001
    ):
        result = call_fn(prompt)
        if result is not None:
            return result
    return "", 0.0


def generate_social_outreach(company, profile, issues, channel, your_name="Kshitij", ai_call=None) -> dict:
    """One AI call producing outreach copy for *channel*, grounded entirely in
    *issues*. Returns {"subject", "body", "cost"}; body is "" if the model's
    reply could not be parsed (caller surfaces that, never crashes)."""
    call = ai_call or _default_ai_call
    prompt = _build_outreach_prompt(company, profile, issues, channel, your_name)
    try:
        text, cost = call(prompt)
    except Exception as e:  # noqa: BLE001
        print(f"[SocialOutreach] provider call failed: {e}")
        return {"subject": "", "body": "", "cost": 0.0}
    parsed = _parse_outreach_json(text) or {}
    subject = "" if channel in ("dm", "whatsapp") else str(parsed.get("subject", ""))
    body = str(parsed.get("body", ""))
    total_cost = float(cost or 0.0)

    if channel == "email" and body:
        bad = _disallowed_numbers(f"{subject} {body}", profile, issues, company)
        if bad:
            # One retry: the email used a number we did not measure.
            retry_prompt = (prompt + "\n\nYour previous draft used numbers that are not in the measured facts: "
                            + ", ".join(sorted(bad)) + ". Rewrite it using only the measured numbers, or no numbers.")
            try:
                text2, cost2 = call(retry_prompt)
                total_cost += float(cost2 or 0.0)
                parsed2 = _parse_outreach_json(text2) or {}
                subject2 = str(parsed2.get("subject", ""))
                body2 = str(parsed2.get("body", ""))
                if body2 and not _disallowed_numbers(f"{subject2} {body2}", profile, issues, company):
                    subject, body = subject2, body2
                else:
                    subject, body = "", ""
            except Exception as e:  # noqa: BLE001
                print(f"[SocialOutreach] retry failed: {e}")
                subject, body = "", ""
    return {"subject": subject, "body": body, "cost": total_cost}
