"""
Audience quality: does the audience behind a social profile look like real,
interested followers? Deterministic, from KNOWN data only. INTERNAL: shown in
the Social tab, never passed to the outreach model (the one exception is
outreach_issue below, a soft, accusation free "posts are not reaching the
audience" line).

audience_quality(profile) -> {"score": int 0..100 | None, "label":
"healthy" | "mixed" | "suspect" | "unknown", "signals": [{"key",
"severity", "detail"}]}

Data rules
  * Engagement signals use only posts whose likes AND comments were read
    (not None); posts with unknown counts are ignored, never counted as 0.
    They need at least MIN_POSTS such posts and a follower count.
  * Without engagement data (and without comment text) the label is never
    "healthy" or "suspect": it is "unknown" unless public signals pull the
    score below the healthy line, then "mixed". An unknown label has score None.

Signals and weights (score = 100 minus the weights of the signals found):
  likes_low_for_audience  35 high    median likes per post, as a share of
                                     followers, below the Low engagement floor
                                     for the size band (social_audit
                                     .engagement_band: under 1k 1.0%, 1k to 10k
                                     0.5%, 10k to 50k 0.3%, 50k plus 0.15%);
                                     only over 500 followers. The floors are
                                     ours and never quoted as a benchmark.
  likes_no_comments       20 medium  5+ posts, median likes 10+, and no
                                     comment at all on 80% or more of them.
  comment_ratio_low       15 medium  comments under 1 per 500 likes, with
                                     1000+ likes seen (skipped when
                                     likes_no_comments fired).
  comment_ratio_high      15 medium  comments more than half the likes, with
                                     20+ comments seen.
  engagement_spike        10 low     5+ posts: one post has 5x or more the
                                     median likes of the others (and 30+ more),
                                     while the others are flat (none above
                                     twice their median).
  follow_for_follow       25 medium  following 500+ and at least twice the
                                     followers, followers under 5000. Works on
                                     public data.
  round_follower_count     5 low     exact follower count (followers_exact)
                                     above 5000 that is a multiple of 1000.
                                     Weak; never decides a label alone.
  generic_comments        20 medium  comment texts (only when a reader
                                     supplies recent_posts[].comment_texts;
                                     none does today), 8+ seen and 60%+ emoji
                                     only or generic ("nice", "great pic",
                                     "follow back").

Labels: healthy 80+, mixed 55 to 79, suspect under 55.

Wording: plain, no dashes, never "fake" / "bot" or any accusation as fact;
"looks", "usually means", "worth checking".
"""

import re
from statistics import median

from analyzer.social_audit import SocialIssue, engagement_band

MIN_POSTS = 4
HEALTHY_AT = 80
MIXED_AT = 55

WEIGHTS = {
    "likes_low_for_audience": 35,
    "likes_no_comments": 20,
    "comment_ratio_low": 15,
    "comment_ratio_high": 15,
    "engagement_spike": 10,
    "follow_for_follow": 25,
    "round_follower_count": 5,
    "generic_comments": 20,
}

_SEVERITY = {
    "likes_low_for_audience": "high",
    "likes_no_comments": "medium",
    "comment_ratio_low": "medium",
    "comment_ratio_high": "medium",
    "engagement_spike": "low",
    "follow_for_follow": "medium",
    "round_follower_count": "low",
    "generic_comments": "medium",
}

_DETAIL = {
    "likes_low_for_audience": (
        "Posts get very few likes for the size of the audience, which usually means many followers "
        "are not seeing the posts or are no longer active. Worth checking."),
    "likes_no_comments": (
        "Posts collect likes but almost never get a comment, which is worth checking because an "
        "engaged audience usually talks back now and then."),
    "comment_ratio_low": (
        "Comments are extremely rare compared with the likes, a pattern that is worth checking."),
    "comment_ratio_high": (
        "Comments are unusually high compared with the likes, which often comes from giveaways or "
        "comment swaps rather than everyday followers. Worth checking."),
    "engagement_spike": (
        "One recent post got far more likes than all the others while the rest stay flat. That can be "
        "a post that took off or likes that were pushed, so it is worth checking."),
    "follow_for_follow": (
        "The account follows far more people than follow it, which usually comes from follow back "
        "growth, so many followers may not be interested in the business."),
    "round_follower_count": (
        "The follower count is an exact round number, which on its own means very little and is only "
        "worth a second look alongside other signs."),
    "generic_comments": (
        "Most comments seen are emoji only or short generic lines like nice or great pic, which "
        "usually means little real conversation. Worth checking."),
}

_GENERIC = {
    "nice", "great", "wow", "awesome", "amazing", "cool", "superb", "beautiful", "lovely", "perfect",
    "good", "so good", "very nice", "too good", "love it", "love this", "nice pic", "great pic",
    "nice post", "great post", "nice one", "great work", "nice work", "good work", "keep it up",
    "keep going", "great content", "nice content", "awesome post", "amazing post", "awesome pic",
    "amazing pic", "follow back", "follow me", "followback", "check my page", "check my profile",
    "check dm", "dm me", "dm", "nice feed", "great feed", "stunning", "fantastic", "excellent",
    "gorgeous", "cute", "nice shot", "great shot", "wonderful", "fire", "lit", "goals",
}
_NON_WORD = re.compile(r"[^a-z0-9 ]+")
_SPACES = re.compile(r"\s+")


def _unknown(signals=None) -> dict:
    return {"score": None, "label": "unknown", "signals": signals or []}


def _signal(key: str) -> dict:
    return {"key": key, "severity": _SEVERITY[key], "detail": _DETAIL[key]}


def _known_posts(posts) -> list[dict]:
    def ok(v):
        return isinstance(v, int) and not isinstance(v, bool) and v >= 0
    return [x for x in posts or [] if isinstance(x, dict) and ok(x.get("likes")) and ok(x.get("comments"))]


def _is_generic(text: str) -> bool:
    low = (text or "").lower()
    if not re.search(r"[a-z0-9]", low):
        return True   # emoji / punctuation only
    norm = _SPACES.sub(" ", _NON_WORD.sub(" ", low)).strip()
    norm = re.sub(r"(.)\1{2,}", r"\1", norm)   # "niceee" -> "nice"
    return norm in _GENERIC


def _engagement_signals(followers: int, posts: list[dict]) -> list[str]:
    out: list[str] = []
    likes = [x["likes"] for x in posts]
    comments = [x["comments"] for x in posts]
    n = len(posts)

    _, floor = engagement_band(followers)
    if followers > 500 and median(likes) / followers * 100 < floor:
        out.append("likes_low_for_audience")

    no_comments = False
    if n >= 5 and median(likes) >= 10 and sum(1 for c in comments if c == 0) >= 0.8 * n:
        no_comments = True
        out.append("likes_no_comments")

    total_likes, total_comments = sum(likes), sum(comments)
    if not no_comments and total_likes >= 1000 and total_comments / total_likes < 0.002:
        out.append("comment_ratio_low")
    if total_comments >= 20 and total_likes > 0 and total_comments / total_likes > 0.5:
        out.append("comment_ratio_high")

    if n >= 5:
        ordered = sorted(likes)
        top, others = ordered[-1], ordered[:-1]
        mid = median(others)
        if mid > 0 and top >= 5 * mid and top - mid >= 30 and max(others) <= 2 * mid:
            out.append("engagement_spike")
    return out


def audience_quality(profile) -> dict:
    """See the module docstring. Never raises on a partial profile."""
    if profile is None or not getattr(profile, "analyzed", False):
        return _unknown()

    followers = int(getattr(profile, "followers", 0) or 0)
    following = int(getattr(profile, "following", 0) or 0)
    posts = _known_posts(getattr(profile, "recent_posts", None))
    keys: list[str] = []

    engagement_data = len(posts) >= MIN_POSTS and followers > 0
    if engagement_data:
        keys += _engagement_signals(followers, posts)

    texts = []
    for x in getattr(profile, "recent_posts", None) or []:
        if isinstance(x, dict) and isinstance(x.get("comment_texts"), list):
            texts += [t for t in x["comment_texts"] if isinstance(t, str) and t.strip()]
    texts_data = len(texts) >= 8
    if texts_data and sum(1 for t in texts if _is_generic(t)) >= 0.6 * len(texts):
        keys.append("generic_comments")

    if followers < 5000 and following >= 500 and following >= 2 * followers:
        keys.append("follow_for_follow")
    if getattr(profile, "followers_exact", False) and followers > 5000 and followers % 1000 == 0:
        keys.append("round_follower_count")

    signals = [_signal(k) for k in keys]
    score = max(0, 100 - sum(WEIGHTS[k] for k in keys))
    if not (engagement_data or texts_data):
        if score >= HEALTHY_AT:
            return _unknown(signals)
        return {"score": score, "label": "mixed", "signals": signals}
    label = "healthy" if score >= HEALTHY_AT else "mixed" if score >= MIXED_AT else "suspect"
    return {"score": score, "label": label, "signals": signals}


OUTREACH_LABEL = "Posts are not reaching the audience"
OUTREACH_DETAIL = ("Likes are low for the size of the audience, which usually means posts are not "
                   "reaching the people who follow the account.")


def outreach_issue(quality: dict | None) -> SocialIssue | None:
    """The ONLY audience quality fact allowed into the outreach prompt: a
    soft, accusation free line when likes are low for the audience size."""
    keys = {s.get("key") for s in (quality or {}).get("signals") or []}
    if "likes_low_for_audience" not in keys:
        return None
    return SocialIssue("low", OUTREACH_LABEL, OUTREACH_DETAIL)
