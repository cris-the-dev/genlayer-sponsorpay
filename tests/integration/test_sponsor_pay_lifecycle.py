"""The rest of the lifecycle on the deployed contract (Testnet Bradbury):

    DEMO_UPLOAD='ssh <host> "cat > /var/www/<site>/demo/{name}"' \
        gltest test_sponsor_pay_lifecycle.py -v -s --network testnet_bradbury

- campaign 0 (from test_sponsor_pay.py): once its live period is over, a
  recheck completes it (skipped until then);
- a new campaign goes live, passes an unchanged recheck, then the post is
  edited to drop #ad: the recheck refunds the brand;
- a new campaign gets three non-compliant posts: expired, brand refunded in full.

The posts are demo/post-good.html and demo/post-noad.html with `__CODE__`
replaced, published under sponsorpay.cristhedev.com/demo/ by DEMO_UPLOAD
(a shell command; `{name}` is the file name, the page comes on stdin).

SETTLE="<payout id>:<paying tx>,..." settles payouts left by an interrupted run.
"""

import json
import os
import subprocess
import time
from pathlib import Path

import pytest
from gltest import get_accounts

from .bradbury import attach, ok, reverted, send, settle, view

ADDRESS = "0xBB7C6c404A3D415CD61b7c6bc3168F1DDb928509"
FEE = 2 * 10**15
REQS = ["Discloses the sponsorship with #ad", "Mentions the GenLayer Builder Kit", "Points readers to genlayer.com"]
HOSTS = json.dumps(["sponsorpay.cristhedev.com"])
BASE = "https://sponsorpay.cristhedev.com/demo/"
DEMO = Path(__file__).parents[2] / "demo"


def publish(template, name, campaign_id):
    page = (DEMO / template).read_text().replace("__CODE__", f"sponsorpay-{campaign_id}")
    subprocess.run(os.environ["DEMO_UPLOAD"].format(name=name), shell=True, check=True, input=page.encode())
    return BASE + name


def wait(make):
    return send(lambda: make().transact(wait_interval=10_000, wait_retries=60))


def last_payout(c, kind, to, amount):
    pid = view(c, "get_accounting")["payout_count"] - 1
    p = view(c, "get_payout", [pid])
    assert p["kind"] == kind and p["to"].lower() == to.lower() and p["amount"] == amount, p
    return pid


@pytest.mark.integration
def test_complete():
    """Campaign 0 once its live period (a day) is over: a passing recheck pays the creator the rest."""
    c = attach("SponsorPay", "sponsor_pay.py", ADDRESS)
    camp = view(c, "get_campaign", [0])
    if camp["status"] != "LIVE" or time.time() < camp["live_until"]:
        pytest.skip(f"campaign 0 is {camp['status']}, live until {camp['live_until']}")
    tx = wait(lambda: c.connect(get_accounts()[2]).recheck(args=[0]))
    camp = view(c, "get_campaign", [0])
    assert camp["status"] == "COMPLETED" and camp["paid"] == camp["amount"], camp
    settle(c, last_payout(c, "fee", camp["creator"], camp["amount"] - camp["amount"] // 2), tx)


@pytest.mark.integration
def test_refund_expire():
    c = attach("SponsorPay", "sponsor_pay.py", ADDRESS)
    brand, creator, anyone = get_accounts()[:3]
    paid = []  # (payout id, paying tx)

    # A live post edited out of compliance refunds the brand.
    cid = view(c, "get_count")
    ok(c.connect(brand).create_campaign(args=[creator.address, "Builder Kit weekend", "One post about the kit.", json.dumps(REQS), HOSTS, 7, 1]).transact(value=FEE))
    url = publish("post-good.html", f"post-{cid}.html", cid)
    assert reverted(c.connect(brand).submit_post(args=[cid, url]).transact())  # only the named creator
    tx = wait(lambda: c.connect(creator).submit_post(args=[cid, url]))
    assert view(c, "get_campaign", [cid])["status"] == "LIVE"
    paid.append((last_payout(c, "fee", creator.address, FEE // 2), tx))
    wait(lambda: c.connect(anyone).recheck(args=[cid]))  # unchanged, still inside the live period
    camp = view(c, "get_campaign", [cid])
    assert camp["status"] == "LIVE" and camp["paid"] == FEE // 2, camp
    publish("post-noad.html", f"post-{cid}.html", cid)
    tx = wait(lambda: c.connect(anyone).recheck(args=[cid]))
    camp = view(c, "get_campaign", [cid])
    assert camp["status"] == "REFUNDED" and camp["met"][0] is False, camp
    paid.append((last_payout(c, "refund", brand.address, FEE - FEE // 2), tx))
    assert reverted(c.connect(anyone).recheck(args=[cid]).transact())

    # Three non-compliant posts: expired, the brand gets the whole fee back.
    cid = view(c, "get_count")
    ok(c.connect(brand).create_campaign(args=[creator.address, "Builder Kit teaser", "One post about the kit.", json.dumps(REQS), HOSTS, 7, 1]).transact(value=FEE))
    url = publish("post-noad.html", f"post-{cid}.html", cid)
    for attempt in range(3):
        tx = wait(lambda: c.connect(creator).submit_post(args=[cid, url]))
        print("  attempt", attempt + 1, view(c, "get_campaign", [cid])["status"])
    camp = view(c, "get_campaign", [cid])
    assert camp["status"] == "EXPIRED" and camp["paid"] == FEE, camp
    paid.append((last_payout(c, "refund", brand.address, FEE), tx))
    assert reverted(c.connect(creator).submit_post(args=[cid, url]).transact())

    for pid, tx in paid:
        settle(c, pid, tx)
    assert view(c, "get_accounting")["unaccounted"] == 0


@pytest.mark.integration
@pytest.mark.skipif(not os.environ.get("SETTLE"), reason="SETTLE not set")
def test_settle_earlier():
    c = attach("SponsorPay", "sponsor_pay.py", ADDRESS)
    for item in os.environ["SETTLE"].split(","):
        pid, tx = item.split(":")
        settle(c, int(pid), tx)
