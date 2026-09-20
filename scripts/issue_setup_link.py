"""Mint a one-time link for an existing user to choose their first password.

Run inside the api container, e.g.
    docker compose exec -T api python scripts/issue_setup_link.py owner@example.com

Prints a URL. The link expires in an hour and stops working the moment it is
used, because it is bound to the account's current password state. Nothing
here sets or learns a password.
"""
import sys

from app.auth import issue_setup_token
from app.persistence import Persistence

PORTAL = "https://portal.techdeva.net"


def main(email: str) -> int:
    user = Persistence().find_user_by_email(email)
    if not user:
        print("no such user: %s" % email)
        return 1
    token = issue_setup_token(email, user.get("password_hash"))
    state = "reset (account already has a password)" if user.get("password_hash") \
        else "first password"
    print("%s\nuser:  %s (%s)\nvalid: 1 hour, single use\n\n%s/set-password?token=%s"
          % (state, user["email"], user["role"], PORTAL, token))
    return 0


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print(__doc__)
        raise SystemExit(2)
    raise SystemExit(main(sys.argv[1]))
