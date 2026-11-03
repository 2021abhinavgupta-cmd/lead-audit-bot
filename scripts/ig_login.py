"""One-time hand login for the spare Instagram account used by the Social tab.

Opens a HEADED Chromium window on instagram.com using the persistent profile
in IG_SESSION_DIR (default data/ig_session, gitignored). Log in yourself,
including any "is this you?" checkpoint or 2FA code. The script waits until
Instagram sets the sessionid cookie, then closes the window; the login stays
in that folder and scrapers/social/instagram_web.py reuses it headless with
the same browser fingerprint.

Use a SPARE account, never the agency's main one. The reader is read only
(it never likes, follows, DMs or posts), but any automated reading carries a
risk of the account being checkpointed. Re-run this script whenever the Social
tab says the spare session needs re-login.

Run:  python scripts/ig_login.py            (waits up to 10 minutes)
      python scripts/ig_login.py --timeout 900
"""
import argparse
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from scrapers.social import instagram_web as igw  # noqa: E402


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--timeout", type=int, default=600, help="seconds to wait for the login (default 600)")
    args = ap.parse_args(argv)

    from playwright.sync_api import sync_playwright

    user_data_dir = igw.session_dir()
    os.makedirs(user_data_dir, exist_ok=True)
    print(f"[ig_login] session folder: {user_data_dir}")

    with sync_playwright() as p:
        ctx = p.chromium.launch_persistent_context(
            user_data_dir, headless=False, args=igw._LAUNCH_ARGS, **igw.browser_identity(p))  # noqa: SLF001
        try:
            if igw.has_session_cookie(ctx.cookies("https://www.instagram.com")):
                ctx.storage_state(path=os.path.join(user_data_dir, igw.STATE_FILE))
                print("[ig_login] already logged in (cookie copy refreshed). Delete the folder above to start over.")
                return 0
            page = ctx.pages[0] if ctx.pages else ctx.new_page()
            page.goto("https://www.instagram.com/accounts/login/", wait_until="domcontentloaded")
            print(f"[ig_login] log in by hand in the window (up to {args.timeout // 60} minutes)...")
            ok = igw.wait_for_session_cookie(lambda: ctx.cookies("https://www.instagram.com"),
                                             timeout=args.timeout, poll=2)
            if not ok:
                print("[ig_login] timed out without a login, nothing saved. Run it again.")
                return 1
            # give Instagram a moment to finish setting the remaining cookies
            page.wait_for_timeout(3000)
            # Plain copy of the cookies next to the profile: the profile's own
            # cookie store is OS-encrypted, so the Linux Docker container
            # cannot read a login made here on Windows (see instagram_web).
            ctx.storage_state(path=os.path.join(user_data_dir, igw.STATE_FILE))
            print("[ig_login] logged in, session saved. The Social tab will now use it.")
            print("[ig_login] the folder holds a live login cookie: keep it out of git and backups you share.")
            return 0
        finally:
            ctx.close()


if __name__ == "__main__":
    sys.exit(main())
