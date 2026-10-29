#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
emaicode.py -- temp email print karo, aur FB code aane par Termux me print kar do.

MANUAL USE:
    python emaicode.py                  # email print -> wait -> code print
    python emaicode.py -p mailtm        # mail.tm force karo
    python emaicode.py --new            # purana email chhor kar naya banao
    python emaicode.py --interval 20    # har 20 sec inbox check
    python emaicode.py --fb-only        # sirf facebook wale mails dikhao
    python emaicode.py --exit-on-code   # pehla code milte hi script band

IMPORT KE LIYE (FB register script me):
    from emaicode import new_email, wait_for_code
    mb   = new_email()
    code = wait_for_code(mb, timeout=180)
"""

import argparse
import json
import os
import random
import re
import string
import sys
import time

import requests

G = "\033[92m"   # green
Y = "\033[93m"   # yellow
R = "\033[91m"   # red
C = "\033[96m"   # cyan
B = "\033[1m"    # bold
X = "\033[0m"    # reset

YUA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
       "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36")

HERE = os.path.dirname(os.path.abspath(__file__))
STATE = os.path.join(HERE, "mailbox.json")
FB_SENDERS = ("facebook", "facebookmail", "meta.com", "fb.com")


def rnd(n):
    return "".join(random.choice(string.ascii_lowercase + string.digits) for _ in range(n))


def beep():
    try:
        sys.stdout.write("\a")
        sys.stdout.flush()
    except Exception:
        pass


def to_clipboard(text):
    """Termux me ho to email/code clipboard me daal do (optional)."""
    try:
        os.system("termux-clipboard-set '%s' >/dev/null 2>&1" % text)
    except Exception:
        pass


# ------------------------------------------------------------------ #
#  CODE EXTRACTION                                                    #
# ------------------------------------------------------------------ #
_DIGIT_GROUPS = re.compile(r"(?<!\d)(\d(?:[ \-\u00a0]\d){4,5})(?!\d)")

_SUBJ_PATS = (
    r"(\d{5,6})\s*(?:is|:|-|\u2013)\s*(?:your|the)?",
    r"(?:code|pin)\D{0,15}(\d{5,6})",
)

_BODY_PATS = (
    r"(?:confirmation|confirm|verification|verify|security|login|one[-\s]?time|access)\s*code\D{0,30}(\d{5,6})",
    r"code\D{0,20}(\d{5,6})",
    r"\b(\d{6})\b",
    r"\b(\d{5})\b",
)


def _clean(text):
    """HTML tags hatao + '1 2 3 4 5' jaisa spaced code jod do."""
    if not text:
        return ""
    t = re.sub(r"<(script|style)[^>]*>.*?</\1>", " ", text, flags=re.S | re.I)
    t = re.sub(r"<[^>]+>", " ", t)
    t = (t.replace("&nbsp;", " ").replace("&amp;", "&").replace("&#39;", "'")
          .replace("&quot;", '"').replace("&zwnj;", ""))
    t = _DIGIT_GROUPS.sub(
        lambda m: m.group(1).replace(" ", "").replace("-", "").replace("\u00a0", ""), t)
    return t


def _search(pats, text):
    for p in pats:
        m = re.search(p, text, re.I)
        if m:
            c = m.group(1)
            if not re.fullmatch(r"(19|20)\d{2}", c):   # 2024 ko code na samjho
                return c
    return None


def extract_code(subject="", body=""):
    subj = _clean(subject)
    body = _clean(body)
    c = _search(_SUBJ_PATS, subj)
    if c:
        return c
    c = _search(_BODY_PATS, body)
    if c:
        return c
    return _search(_BODY_PATS, subj)


# ------------------------------------------------------------------ #
#  BACKENDS                                                           #
# ------------------------------------------------------------------ #
class Mailbox:
    name = "?"

    def __init__(self, proxy=None):
        self.email = None
        self.proxy = proxy
        self.seen = set()
        self.s = requests.Session()
        self.s.headers.update({"User-Agent": YUA, "Accept": "application/json"})
        if proxy:
            self.s.proxies = {"http": proxy, "https": proxy}

    @property
    def address(self):
        return self.email

    def create(self):
        raise NotImplementedError

    def resume(self, state):
        return False

    def list_messages(self):
        raise NotImplementedError

    def read_message(self, mid):
        raise NotImplementedError

    def state(self):
        return {"provider": self.name, "email": self.email, "seen": list(self.seen)}


class GuerrillaMail(Mailbox):
    name = "guerrillamail"
    API = "https://api.guerrillamail.com/ajax.php"

    def __init__(self, proxy=None):
        super().__init__(proxy)
        self.sid = None

    def _call(self, **p):
        p.setdefault("ip", "127.0.0.1")
        p.setdefault("agent", YUA)
        if self.sid:
            p["sid_token"] = self.sid
        r = self.s.get(self.API, params=p, timeout=30)
        r.raise_for_status()
        return r.json()

    def create(self):
        j = self._call(f="get_email_address")
        self.sid = j.get("sid_token")
        self.email = j.get("email_addr")
        if not self.email:
            raise RuntimeError("guerrillamail: email nahi mila")
        return self.email

    def resume(self, st):
        if st.get("provider") != self.name or not st.get("sid"):
            return False
        self.sid = st["sid"]
        self.email = st.get("email")
        self.seen = set(st.get("seen", []))
        try:
            j = self._call(f="get_email_address")
        except Exception:
            return False
        new_addr = j.get("email_addr")
        if not new_addr:
            return False
        if new_addr != self.email:
            print("%s[!] purana session expire ho gaya, naya email ban gaya%s" % (Y, X))
            self.email = new_addr
            self.sid = j.get("sid_token")
        return True

    def list_messages(self):
        j = self._call(f="check_email", seq="0")
        return [{"id": m.get("mail_id"), "from": m.get("mail_from", ""),
                 "subject": m.get("mail_subject", ""), "intro": m.get("mail_excerpt", "")}
                for m in j.get("list", [])]

    def read_message(self, mid):
        j = self._call(f="fetch_email", email_id=str(mid))
        return j.get("mail_subject", ""), (j.get("mail_body") or "")

    def state(self):
        d = super().state()
        d["sid"] = self.sid
        return d


class MailTm(Mailbox):
    name = "mailtm"
    API = "https://api.mail.tm"

    def __init__(self, proxy=None):
        super().__init__(proxy)
        self.password = None
        self.token = None

    def _login(self):
        r = self.s.post(self.API + "/token",
                        json={"address": self.email, "password": self.password}, timeout=30)
        r.raise_for_status()
        self.token = r.json()["token"]
        self.s.headers["Authorization"] = "Bearer " + self.token
        return True

    def create(self):
        r = self.s.get(self.API + "/domains", timeout=30)
        r.raise_for_status()
        doms = r.json().get("hydra:member", [])
        dom = None
        for d in doms:
            if d.get("isActive") and not d.get("isPrivate"):
                dom = d["domain"]
                break
        if not dom and doms:
            dom = doms[0]["domain"]
        if not dom:
            raise RuntimeError("mail.tm: koi domain nahi mila")

        self.email = rnd(12) + "@" + dom
        self.password = rnd(12)
        r = self.s.post(self.API + "/accounts",
                        json={"address": self.email, "password": self.password}, timeout=30)
        if r.status_code not in (200, 201):
            raise RuntimeError("mail.tm account %s: %s" % (r.status_code, r.text[:120]))
        self._login()
        return self.email

    def resume(self, st):
        if st.get("provider") != self.name or not st.get("password"):
            return False
        self.email = st.get("email")
        self.password = st["password"]
        self.seen = set(st.get("seen", []))
        try:
            return self._login()
        except Exception:
            return False

    def list_messages(self):
        r = self.s.get(self.API + "/messages", timeout=30)
        r.raise_for_status()
        out = []
        for m in r.json().get("hydra:member", []):
            frm = (m.get("from") or {}).get("address", "")
            out.append({"id": m.get("id"), "from": frm,
                        "subject": m.get("subject", ""), "intro": m.get("intro", "")})
        return out

    def read_message(self, mid):
        r = self.s.get(self.API + "/messages/" + str(mid), timeout=30)
        r.raise_for_status()
        j = r.json()
        html = j.get("html")
        if isinstance(html, list):
            html = " ".join(html)
        return j.get("subject", ""), (j.get("text") or html or "")

    def state(self):
        d = super().state()
        d["password"] = self.password
        return d


PROVIDERS = {"guerrillamail": GuerrillaMail, "gm": GuerrillaMail,
             "mailtm": MailTm, "mail.tm": MailTm}


# ------------------------------------------------------------------ #
#  STATE + PUBLIC API                                                 #
# ------------------------------------------------------------------ #
def _save(mb):
    try:
        json.dump(mb.state(), open(STATE, "w"))
    except Exception:
        pass


def _load():
    if os.path.exists(STATE):
        try:
            return json.load(open(STATE))
        except Exception:
            return None
    return None


def new_email(provider="auto", proxy=None, fallback=True, verbose=False):
    """Naya temp mailbox. Fail ho to doosra provider try karo."""
    order = (["guerrillamail", "mailtm"] if provider in ("auto", "", None)
             else [provider] + (["mailtm"] if fallback and provider != "mailtm" else []))
    last = None
    for name in order:
        cls = PROVIDERS.get(name)
        if not cls:
            continue
        try:
            mb = cls(proxy=proxy)
            mb.create()
            if mb.email:
                if verbose:
                    print("%s[email] %s (%s)%s" % (G, mb.email, mb.name, X))
                return mb
        except Exception as e:
            last = e
            if verbose:
                print("%s[%s fail] %s%s" % (Y, name, e, X))
            time.sleep(1)
    raise RuntimeError("koi mail provider kaam nahi kiya: %r" % (last,))


def wait_for_code(mb, timeout=180, interval=5, only_facebook=True,
                  verbose=False, save_dir=None):
    """FB code dhoondo (FB register script isi ko call karega)."""
    end = time.time() + timeout if timeout else 0
    while True:
        try:
            msgs = mb.list_messages()
        except Exception as e:
            if verbose:
                print("[poll error]", e)
            msgs = []
        for m in msgs:
            mid = m.get("id")
            if mid in mb.seen:
                continue
            mb.seen.add(mid)
            frm = (m.get("from") or "").lower()
            if only_facebook and not any(f in frm for f in FB_SENDERS):
                continue
            try:
                subj, body = mb.read_message(mid)
            except Exception:
                subj, body = m.get("subject", ""), m.get("intro", "")
            if save_dir:
                try:
                    os.makedirs(save_dir, exist_ok=True)
                    open(os.path.join(save_dir, "mail_%s.html" % mid), "w",
                         encoding="utf-8").write(body)
                except Exception:
                    pass
            code = extract_code(subj, body)
            if verbose:
                print("[mail] %s | %r | code=%s" % (frm, (subj or "")[:50], code))
            if code:
                return code
        if end and time.time() > end:
            return None
        time.sleep(interval)


# ------------------------------------------------------------------ #
#  MANUAL MODE                                                        #
# ------------------------------------------------------------------ #
def _get_mailbox(args):
    if not args.new:
        st = _load()
        if st and st.get("email"):
            cls = PROVIDERS.get(st.get("provider"))
            if cls:
                mb = cls(proxy=args.proxy)
                try:
                    if mb.resume(st):
                        print("%s[i] purana email resume kiya%s" % (Y, X))
                        return mb
                except Exception:
                    pass
    mb = new_email(args.provider, proxy=args.proxy, verbose=True)
    _save(mb)
    return mb


def manual(args):
    os.system("clear")
    print("%s%s%s" % (C, "=" * 54, X))
    print("%s  TEMP EMAIL -> FB CODE WATCHER%s" % (B, X))
    print("%s%s%s" % (C, "=" * 54, X))
    mb = _get_mailbox(args)

    print()
    print("%s EMAIL :%s %s%s%s   (%s)" % (B, X, G, mb.email, X, mb.name))
    print("%s%s%s" % (C, "-" * 54, X))
    print(" 1) Isi email se FB id manually register karo")
    print(" 2) FB jab code bhejega, yahan apne aap print hoga")
    print(" 3) Band karne ke liye Ctrl+C")
    print("%s%s%s" % (C, "-" * 54, X))
    to_clipboard(mb.email)
    print("%s[i] email clipboard me copy kar diya (agar termux-api ho)%s\n" % (Y, X))

    start = time.time()
    last_tick = time.time()
    found = 0
    checks = 0

    while True:
        checks += 1
        try:
            msgs = mb.list_messages()
        except Exception as e:
            msgs = []
            if args.verbose:
                print("%s[poll error] %s%s" % (R, e, X))

        for m in msgs:
            mid = m.get("id")
            if mid in mb.seen:
                continue
            mb.seen.add(mid)
            frm = (m.get("from") or "").lower()
            if args.fb_only and not any(f in frm for f in FB_SENDERS):
                continue

            try:
                subj, body = mb.read_message(mid)
            except Exception:
                subj, body = m.get("subject", ""), m.get("intro", "")

            code = extract_code(subj, body)
            print("\n" + "%s%s%s" % (C, "-" * 54, X))
            print("%sFROM   :%s %s" % (B, X, m.get("from", "")))
            print("%sSUBJECT:%s %s" % (B, X, subj or m.get("subject", "")))

            if code:
                found += 1
                print()
                print("%s%s>>> CONFIRMATION CODE : %s <<<%s" % (B, G, code, X))
                print()
                beep()
                to_clipboard(code)
                _save(mb)
                if args.exit_on_code:
                    print("%s[+] code mil gaya, exit.%s" % (G, X))
                    return 0
            else:
                print("%s%s%s" % (C, "-" * 54, X))
                print("BODY   : %s" % re.sub(r"\s+", " ", _clean(body))[:250])
                print("%scode nahi mila — mail manually dekh lo%s" % (Y, X))
                try:
                    open(os.path.join(HERE, "mail_%s.html" % mid), "w",
                         encoding="utf-8").write(body)
                    print("[i] raw body: mail_%s.html" % mid)
                except Exception:
                    pass
            _save(mb)
            last_tick = time.time()

        if time.time() - last_tick >= 20:
            el = int(time.time() - start)
            print("%s[wait] %02d:%02d | inbox check #%d | codes: %d%s"
                  % (Y, el // 60, el % 60, checks, found, X))
            last_tick = time.time()

        time.sleep(args.interval)


def main():
    ap = argparse.ArgumentParser(description="Temp email + FB code watcher")
    ap.add_argument("-p", "--provider", default="auto",
                    choices=["auto", "guerrillamail", "mailtm"])
    ap.add_argument("--interval", type=int, default=5, help="inbox check interval (sec)")
    ap.add_argument("--new", action="store_true", help="naya email banao (resume na karo)")
    ap.add_argument("--fb-only", action="store_true", help="sirf facebook mails")
    ap.add_argument("--exit-on-code", action="store_true", help="pehle code par exit")
    ap.add_argument("--proxy", default=None)
    ap.add_argument("-v", "--verbose", action="store_true")
    args = ap.parse_args()

    try:
        return manual(args)
    except KeyboardInterrupt:
        print("\n%s[i] exit.%s" % (Y, X))
        return 0
    except Exception as e:
        print("%s[ERROR] %s%s" % (R, e, X))
        return 1


if __name__ == "__main__":
    sys.exit(main())
