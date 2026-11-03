"""
The two Social tab PDFs, as self-contained HTML for reporting.report.render_pdf.

  * SMALL (internal): mirrors the website draft's small PDF. Details, the
    measured profile numbers, the lead score, the issue list, the email
    exactly as it stands in the editor, and a clearly labelled "Internal"
    audience quality block.
  * LONG (client facing, safe to attach to an email to the prospect): drawn in
    the website design review's theme (reporting/theme.py) so the combined PDF
    reads as one document: a dark cover, "The short version" with the top three
    fixes and the measured profile numbers, one numbered finding per issue (what
    we found, why it matters, what we would do), "What we'd do first" and the
    "Want us to do it?" box. Footer page numbers start after start_page (the
    website part's page count in the combined PDF). It NEVER carries the lead
    score, the audience quality, the reader's notes or the outreach email.

Every value from the request is html escaped. The copy owned here (FIX_TEXT,
WHY_TEXT and the fixed sentences) follows the project's copy rules: plain
words, no dashes of any kind, no numbers, no comparison with other accounts,
no claim about MMGA's experience or results. Every issue label that
analyzer/social_audit.py can emit has an entry (test_social_pdf.py reads the
labels from that file's source, so a new label without copy fails the test).
"""

from html import escape

from reporting import theme

_SEVERITY_RANK = {"high": 0, "medium": 1, "low": 2}
_TAG = {"high": "Fix first", "medium": "Next", "low": "Worth doing"}

# label -> what we would do. Prefix keys (see _PREFIX_KEYS) cover labels that
# analyzer/social_audit.py builds with an f string.
FIX_TEXT = {
    "Link in bio is broken": (
        "Replace the bio link with a working page, ideally the one where people buy or book, "
        "and test it from a phone after every change."),
    "Link in bio is a link list page": (
        "Point the bio link straight at the page most visitors want, such as the shop or the booking page, "
        "and keep a link list only if it truly helps."),
    "Link in bio skips the website": (
        "Point the bio link at the business website, or at the one page on it where people buy, book or get in touch."),
    "Inactive account": (
        "Restart with a simple, steady plan built from work the business already has, so the page looks alive again."),
    "Posting has slowed": (
        "Set a small, fixed posting rhythm that is easy to keep up, and plan the posts a couple of weeks ahead "
        "so the gaps stop."),
    "Uneven posting schedule": (
        "Spread the posts out evenly by planning and scheduling them ahead, so followers see the brand "
        "regularly rather than in bursts."),
    "Not using short video": (
        "Add short reels made from everyday work, such as behind the scenes clips, before and after shots "
        "or quick tips, alongside the photos."),
    "No link in the bio": (
        "Add one clear link to the bio that goes to the page where people can buy, book or get in touch."),
    "Thin or empty bio": (
        "Rewrite the bio in a couple of short lines: what the business offers, who it is for, where it is, "
        "and what to do next."),
    "Bio does not say what or where": (
        "Add what the business offers and where it serves to the bio, in plain words a new visitor "
        "understands at a glance."),
    "No call to action in the bio": (
        "End the bio with one clear next step, such as book now, message us or order through the link."),
    "Following more than followers": (
        "Trim the following list to accounts that matter to the business, so the profile reads as an "
        "established brand."),
    "Thin content history": (
        "Build up the grid with a run of useful posts that show the products, the work and the people "
        "behind it, so new visitors have something to look through."),
    "Audience not growing with the posts": (
        "Shift some effort from posting more to posting what new people find and pass on, such as reels, "
        "collaborations and posts that answer common questions."),
    "Low engagement": (
        "Post more of the content that already gets a response, ask a simple question in captions, and "
        "reply to every comment so conversations start."),
    "Very few comments": (
        "End captions with an easy question or an invitation to reply, and answer every comment quickly "
        "so people see it is worth talking."),
    "Image posts underperform": (
        "Make more of the formats that already do better on this account and fewer single image posts."),
    "Many posts have no caption": (
        "Give every post a short caption that says what it shows and what to do next."),
    "Captions never ask for action": (
        "Close each caption with one clear next step, such as message us to order, book through the link "
        "in the bio or visit the shop."),
    "No hashtags on recent posts": (
        "Add a few relevant hashtags to each post, mixing the topic of the business with the local area, "
        "so people browsing those topics can find it."),
    "Too many hashtags": (
        "Cut each post down to a few relevant hashtags and let the caption carry the message."),
    "Posts are not reaching the audience": (
        "Focus on posts people want to pass on, like reels and useful tips, and try different posting "
        "times to see when followers respond."),
}

WHY_TEXT = {
    "Link in bio is broken": (
        "People who tap the link are ready to act, and an error page loses them at the last step."),
    "Link in bio is a link list page": (
        "Every extra tap is a chance for an interested visitor to give up before buying or booking."),
    "Link in bio skips the website": (
        "The website is where the business explains itself and takes enquiries, so visitors from "
        "Instagram miss it."),
    "Inactive account": (
        "Many people check Instagram before they buy, and a quiet page can make them wonder whether "
        "the business is still open."),
    "Posting has slowed": (
        "Followers forget a brand they rarely see, so fewer of them think of it when they are ready to buy."),
    "Uneven posting schedule": (
        "Long quiet stretches make the page look neglected to anyone who visits during one, and followers "
        "lose the habit of seeing the brand."),
    "Not using short video": (
        "Short video is the format the platform puts in front of new people most, so leaving it out "
        "limits how many new people find the account."),
    "No link in the bio": (
        "Without a link, an interested visitor has to search for the business elsewhere, and many will not bother."),
    "Thin or empty bio": (
        "The bio is the first thing a new visitor reads, and it decides whether they follow or leave."),
    "Bio does not say what or where": (
        "Visitors who cannot tell what is offered or where usually move on rather than ask."),
    "No call to action in the bio": (
        "Interested people still need to be told what to do next, or they leave without acting."),
    "Following more than followers": (
        "New visitors judge a brand by its profile at a glance, and this makes it look smaller than it is."),
    "Thin content history": (
        "A new visitor looks through the grid to decide whether to trust the business, and a short "
        "history gives them little to go on."),
    "Audience not growing with the posts": (
        "Time spent on posts that do not bring new followers is time that is not growing the business."),
    "Low engagement": (
        "Posts that few people respond to do little to bring in enquiries, so the effort put into them "
        "is not paying back."),
    "Very few comments": (
        "Comments are where questions and orders often start, and a quiet comment section gives visitors "
        "no sign that others buy here."),
    "Image posts underperform": (
        "The account's own posts show which formats people respond to, so leaning on the weaker one "
        "leaves attention unused."),
    "Many posts have no caption": (
        "A post without words leaves people guessing what they are looking at and gives them no reason to act."),
    "Captions never ask for action": (
        "A post that catches someone's eye but gives no next step lets that interest fade."),
    "No hashtags on recent posts": (
        "Hashtags are one of the simple ways new people browsing a topic come across a business."),
    "Too many hashtags": (
        "A wall of hashtags looks spammy and buries the message the post is meant to carry."),
    "Posts are not reaching the audience": (
        "Followers who never see the posts cannot become customers, so the audience the business built "
        "is not working for it."),
}

GENERIC_FIX = ("Fix this as part of a short, prioritised plan for the account, starting with whatever "
               "stops people from getting in touch.")
GENERIC_WHY = "Left as it is, this makes it harder for interested people to become customers."

# Labels built with an f string in analyzer/social_audit.py, matched by prefix.
_PREFIX_KEYS = ("Image posts underperform",)

_PLATFORM_NAME = {"instagram": "Instagram", "facebook": "Facebook", "youtube": "YouTube", "linkedin": "LinkedIn"}


def _key(label: str) -> str:
    label = (label or "").strip()
    if label in FIX_TEXT:
        return label
    for prefix in _PREFIX_KEYS:
        if label.startswith(prefix):
            return prefix
    return ""


def fix_for(label: str) -> str:
    return FIX_TEXT.get(_key(label), GENERIC_FIX)


def why_for(label: str) -> str:
    return WHY_TEXT.get(_key(label), GENERIC_WHY)


def _e(v) -> str:
    return escape("" if v is None else str(v))


def _num(n) -> str:
    if n is None:
        return ""
    n = float(n)
    if n >= 10 or n == int(n):
        return f"{int(round(n)):,}"
    return f"{n:.1f}"


def _ordered(issues: list[dict]) -> list[dict]:
    return sorted((i for i in issues or [] if (i.get("label") or "").strip()),
                  key=lambda i: _SEVERITY_RANK.get(i.get("severity"), 3))


def _typical(profile: dict) -> tuple[str, str] | None:
    """("about N likes and M comments", posts read) when the median numbers
    were measured from at least four posts, else None."""
    likes, comments, read = profile.get("median_likes"), profile.get("median_comments"), profile.get("posts_read")
    if likes is None or comments is None or not read or read < 4:
        return None
    return f"about {_num(likes)} likes and {_num(comments)} comments", str(read)


def _snapshot_rows(profile: dict, *, internal: bool) -> list[tuple[str, str]]:
    rows = []
    for key, name in (("followers", "Followers"), ("following", "Following"), ("posts_count", "Posts")):
        if profile.get(key) is not None:
            rows.append((name, _num(profile[key])))
    typical = _typical(profile)
    if typical:
        rows.append(("Typical post", f"{typical[0]} (from the {typical[1]} most recent posts we read)"))
        rate = profile.get("engagement_rate")
        if rate:
            rows.append(("Typical engagement", f"about {float(rate):.1f}% of followers per post"))
    if profile.get("link_in_bio") is not None:
        rows.append(("Link in bio", "Yes" if profile["link_in_bio"] else "No"))
    if internal:
        if profile.get("bio"):
            rows.append(("Bio", profile["bio"]))
        if profile.get("note"):
            rows.append(("Reader note", profile["note"]))
    return rows


def has_content(data: dict) -> bool:
    """False when the requested PDF would be empty (the route answers 400)."""
    profile = data.get("profile") or {}
    has_profile = any(profile.get(k) for k in ("followers", "following", "posts_count"))
    has_issues = bool(_ordered(data.get("issues") or []))
    if data.get("mode") == "long":
        return has_profile or has_issues
    email = data.get("email") or {}
    return has_profile or has_issues or bool((email.get("subject") or "").strip() or (email.get("body") or "").strip())


_CSS = (
    '@page{size:A4;margin:0}*{box-sizing:border-box}'
    'body{font-family:Arial,Helvetica,sans-serif;color:#1e293b;margin:0;background:#fff}'
    '.top{background:#0f172a;color:#fff;padding:26px 48px}.top small{color:#94a3b8;letter-spacing:.08em;font-size:11px}'
    '.top h1{margin:6px 0 0;font-size:22px;line-height:1.3}.top .sub{color:#cbd5e1;font-size:13px;margin-top:6px}'
    '.in{padding:28px 48px}h2{font-size:13px;letter-spacing:.06em;text-transform:uppercase;color:#64748b;margin:26px 0 8px}'
    'p{font-size:13.5px;line-height:1.6;margin:0 0 10px}'
    'table{border-collapse:collapse;width:100%}td{padding:5px 0;vertical-align:top;font-size:13px;border-bottom:1px solid #f1f5f9}'
    '.k{color:#64748b;width:150px}'
    '.mail{border:1px solid #e2e8f0;border-radius:8px;padding:20px 24px;background:#f8fafc}'
    '.subj{font-weight:700;font-size:15px;margin-bottom:14px}'
    '.body{white-space:pre-wrap;font-size:14px;line-height:1.65}'
    '.iss li{font-size:13px;line-height:1.5;margin:0 0 6px}.sev{font-weight:700}'
    '.sev.high{color:#dc2626}.sev.medium{color:#d97706}.sev.low{color:#64748b}'
    '.internal{border:2px dashed #f59e0b;border-radius:8px;padding:14px 18px;background:#fffbeb;margin-top:26px}'
    '.internal h2{color:#b45309;margin-top:0}'
    '.score{display:inline-block;font-weight:700;font-size:13px;padding:3px 10px;border-radius:999px;background:#0f172a;color:#fff}'
    '.card{border:1px solid #e2e8f0;border-radius:10px;padding:16px 20px;margin:0 0 14px;break-inside:avoid}'
    '.card h3{font-size:16px;margin:6px 0 10px}'
    '.tag{display:inline-block;font-size:10px;font-weight:700;letter-spacing:.08em;text-transform:uppercase;'
    'padding:2px 9px;border-radius:999px;background:#e2e8f0;color:#334155}'
    '.tag.high{background:#dc2626;color:#fff}.tag.medium{background:#f59e0b;color:#fff}'
    '.card h4{font-size:10.5px;letter-spacing:.08em;text-transform:uppercase;color:#64748b;margin:8px 0 3px}'
    '.card p{margin:0 0 4px}'
    '.plan{padding-left:20px}.plan li{font-size:13.5px;line-height:1.55;margin:0 0 6px}'
    '.next{background:#0f172a;color:#fff;border-radius:10px;padding:18px 22px;margin-top:22px;break-inside:avoid}'
    '.next h2{color:#94a3b8;margin-top:0}.next p{color:#fff}'
    '.mail,table,.internal{break-inside:avoid}h2{break-after:avoid}'
)


def _doc(title: str, inner: str) -> str:
    return (f'<!doctype html><html lang="en"><head><meta charset="utf-8"><title>{_e(title)}</title>'
            f'<style>{_CSS}</style></head><body>{inner}</body></html>')


def _rows(rows) -> str:
    return "".join(f'<tr><td class="k">{_e(k)}</td><td>{_e(v)}</td></tr>' for k, v in rows if v not in (None, ""))


def _platform(data: dict) -> str:
    p = (data.get("platform") or "").strip()
    return _PLATFORM_NAME.get(p.lower(), p[:1].upper() + p[1:] if p else "Social")


def _handle(data: dict) -> str:
    h = (data.get("handle") or "").strip().lstrip("@")
    return f"@{h}" if h else ""


def build_small_html(data: dict) -> str:
    profile = data.get("profile") or {}
    email = data.get("email") or {}
    issues = _ordered(data.get("issues") or [])
    company = (data.get("company") or "").strip()
    heading = (email.get("subject") or "").strip() or company or _handle(data) or "Social audit"

    details = _rows((
        ("Company", company), ("Platform", _platform(data)), ("Handle", _handle(data)),
        ("Profile", data.get("profile_url")), ("Website", data.get("website")),
        ("Audited", data.get("timestamp")),
    ))
    snapshot = _rows(_snapshot_rows(profile, internal=True))

    ls = data.get("lead_score") or {}
    lead = ""
    if ls.get("label") or ls.get("score") is not None:
        reasons = "".join(f"<li>{_e(r)}</li>" for r in ls.get("reasons") or [] if r)
        lead = (f'<h2>Lead score</h2><p><span class="score">{_e(ls.get("label") or "")} lead · '
                f'{_e(ls.get("score") if ls.get("score") is not None else "")}</span></p>'
                + (f'<ul class="iss">{reasons}</ul>' if reasons else ""))

    issue_html = ""
    if issues:
        items = "".join(
            f'<li><span class="sev {_e(i.get("severity"))}">{_e(i.get("label"))}</span>: {_e(i.get("detail"))}</li>'
            for i in issues)
        issue_html = f'<h2>Issues found</h2><ul class="iss">{items}</ul>'

    mail = ""
    if (email.get("subject") or "").strip() or (email.get("body") or "").strip():
        mail = (f'<h2>The email</h2><div class="mail"><div class="subj">{_e(email.get("subject"))}</div>'
                f'<div class="body">{_e(email.get("body"))}</div></div>')

    aq = data.get("audience_quality") or {}
    internal = ""
    if aq.get("label") or aq.get("signals"):
        signals = "".join(f"<li>{_e(s.get('detail'))}</li>" for s in aq.get("signals") or [] if s.get("detail"))
        score = f" · {_e(aq['score'])}" if aq.get("score") is not None else ""
        internal = (
            '<div class="internal"><h2>Internal only: audience quality</h2>'
            '<p>For our own judgement. Never shown to the prospect and not used in the email.</p>'
            f'<p><b>{_e(aq.get("label") or "unknown")}{score}</b></p>'
            + (f'<ul class="iss">{signals}</ul>' if signals else '<p>No signals found.</p>')
            + '</div>')

    inner = (
        f'<div class="top"><small>SOCIAL DRAFT</small><h1>{_e(heading)}</h1></div><div class="in">'
        f'<h2>Details</h2><table>{details}</table>'
        + (f'<h2>Profile snapshot</h2><table>{snapshot}</table>' if snapshot else "")
        + lead + issue_html + mail + internal + '</div>'
    )
    return _doc(f"Social draft for {company or _handle(data)}", inner)


# The long audit is drawn in the website report's theme (reporting/theme.py), so
# the combined PDF reads as one document. Full bleed A4 pages: print with margin 0.
LONG_MARGIN_MM = 0

# issue severity -> the website report's impact class (t-serious is the orange tag)
_IMPACT = {"high": "serious", "medium": "moderate", "low": "minor"}

_LONG_CSS = (
    f'.stats{{list-style:none;padding:0;margin:0}}'
    f'.stats li{{font-size:10pt;line-height:1.5;padding:2.4mm 0;border-bottom:1px solid {theme.PANEL}}}'
    '.stats li.n{display:flex;justify-content:space-between;align-items:baseline;gap:5mm}'
    '.stats .k{display:block;font-size:8pt;letter-spacing:1.5px;text-transform:uppercase;color:#8a7a68}'
    '.stats li.t .k{margin-bottom:1mm}'
    f'.stats b{{font-size:17pt;color:{theme.ACCENT};line-height:1.1}}'
    '.finding .cols p + h4{margin-top:4mm}'
)

# snapshot rows shown as a big orange number
_BIG_STATS = ("Followers", "Following", "Posts")


def _stats(rows) -> str:
    """Counts as a big orange number on the right; sentences on a line of their own."""
    items = []
    for k, v in rows:
        if v in (None, ""):
            continue
        if k in _BIG_STATS:
            items.append(f'<li class="n"><span class="k">{_e(k)}</span><b>{_e(v)}</b></li>')
        else:
            items.append(f'<li class="t"><span class="k">{_e(k)}</span>{_e(v)}</li>')
    return f'<ul class="stats">{"".join(items)}</ul>' if items else ""


# Room for findings on one page (A4 297 mm less the 18 mm top padding and the
# footer band), and a rough height of one finding block in the theme's type:
# about 44 characters a line in a half width column, 5.8 mm a line.
_PAGE_BODY_MM = 245


def _finding_mm(issue: dict) -> float:
    def lines(text: str, per_line: int = 44) -> int:
        return max(1, -(-len(text or "") // per_line))
    label = issue.get("label") or ""
    left = (lines(issue.get("detail")) + lines(why_for(label))) * 5.8 + 18
    right = lines(fix_for(label)) * 5.8 + 6
    head = max(13, 6 + lines(label, 30) * 7.3) + 4
    return head + max(left, right) + 9


def build_long_html(data: dict, *, sender_name: str = "", sender_email: str = "", start_page: int = 1) -> str:
    """The client facing audit. start_page is the page number of the cover: 1 on
    its own, the website report's page count plus one inside the combined PDF."""
    profile = data.get("profile") or {}
    issues = _ordered(data.get("issues") or [])
    platform = _platform(data)
    company = (data.get("company") or "").strip() or _handle(data) or "this account"
    handle = _handle(data)
    when = (data.get("timestamp") or "").strip()
    who = company + (f" ({handle})" if handle and handle != company else "")
    n = max(1, int(start_page or 1))
    pages: list[str] = []

    pages.append(theme.cover(
        kicker=f"{platform} audit" + (f" · {when}" if when else ""), name=company, subline=handle,
        intro=(f"What a new visitor sees on your {platform} profile, what makes them trust you, "
               "and what stands between them and getting in touch."),
        footer_right=f"{len(issues)} finding{'s' if len(issues) != 1 else ''}"))
    n += 1

    fixes = [(i.get("label") or "", fix_for(i.get("label"))) for i in issues]
    top = fixes[:3]
    firsts = theme.first_list(top or [("Nothing urgent", "We found nothing that clearly needs fixing on this profile.")])
    stats = _stats(_snapshot_rows(profile, internal=False))
    snap = (f'<div><h4>Profile snapshot</h4>{stats}'
            '<p class="cap">Numbers as read from the profile on the day of this audit.</p></div>') if stats else ""
    pages.append(theme.page(
        '<p class="kicker">The short version</p>'
        f'<h1>{len(issues)} thing{"s" if len(issues) != 1 else ""} worth fixing</h1>'
        f'<p style="max-width:150mm">We looked at the {_e(platform)} profile of {_e(who)} the way a new visitor '
        'would and read the numbers on it. Every point below rests on what the profile shows, not on opinion.</p>'
        f'<div class="cols" style="margin-top:8mm"><div class="box"><h4>Do these first</h4>{firsts}</div>{snap}</div>'
        + theme.footer(theme.BRAND, n)))
    n += 1

    blocks = [theme.finding_block(
        number=k, impact=_IMPACT.get(i.get("severity"), "minor"),
        tag=_TAG.get(i.get("severity"), "Worth doing"), title=i.get("label") or "",
        columns=[[("What we found", i.get("detail") or ""), ("Why it matters", why_for(i.get("label")))],
                 [("What we would do", fix_for(i.get("label")))]])
        for k, i in enumerate(issues, 1)]
    # Two findings a page, like the website report, unless together they would
    # run past the footer (pages are fixed A4 with overflow hidden).
    queue, used = [], 0.0
    for block, issue in zip(blocks, issues):
        h = _finding_mm(issue)
        if queue and (len(queue) == 2 or used + h > _PAGE_BODY_MM):
            pages.append(theme.page("".join(queue) + theme.footer(theme.BRAND, n)))
            n += 1
            queue, used = [], 0.0
        queue.append(block)
        used += h
    if queue:
        pages.append(theme.page("".join(queue) + theme.footer(theme.BRAND, n)))
        n += 1

    pages.append(theme.closing_page(top, contact_email=(sender_email or "").strip(), number=n,
                                    sign=(sender_name or "").strip()))
    return theme.document(f"{platform} audit for {company}", pages, extra_css=_LONG_CSS)
