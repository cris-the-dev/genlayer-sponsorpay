"""Integration test against Testnet Bradbury, on the packed code that is deployed:

    python deploy/shrink.py contracts/sponsor_pay.py build/sponsor_pay.py --pack
    cd tests/integration && gltest . -v -s --network testnet_bradbury   # config in this folder

Uses the demo posts at sponsorpay.cristhedev.com/demo/ (served with
`__CODE__` = sponsorpay-0). The live period is at least a day, so completion
is the recorded Bradbury run in the README. The first half of the fee is
settled from its own transfer once the paying transaction finalizes.
"""

import json

import pytest
from gltest import get_accounts

from .bradbury import deploy, ok, reverted, send, settle, view

FEE = 2 * 10**15
REQS = ["Discloses the sponsorship with #ad", "Mentions the GenLayer Builder Kit", "Points readers to genlayer.com"]
BASE = "https://sponsorpay.cristhedev.com/demo/"


@pytest.mark.integration
def test_submit_non_compliant_then_compliant():
    c = deploy("SponsorPay", "sponsor_pay.py")
    creator = get_accounts()[1]
    args = [creator.address, "Builder Kit launch", "One post about the kit.", json.dumps(REQS), json.dumps(["sponsorpay.cristhedev.com"]), 7, 1]
    ok(c.create_campaign(args=args).transact(value=FEE))
    as_creator = c.connect(creator)
    assert reverted(as_creator.submit_post(args=[0, "https://example.com/post"]).transact())

    send(lambda: as_creator.submit_post(args=[0, BASE + "post-noad.html"]).transact(wait_interval=10_000, wait_retries=60))
    camp = view(c, "get_campaign", [0])
    assert camp["status"] == "OPEN" and camp["met"][0] is False and camp["paid"] == 0, camp

    tx = send(lambda: as_creator.submit_post(args=[0, BASE + "post-good.html"]).transact(wait_interval=10_000, wait_retries=60))
    camp = view(c, "get_campaign", [0])
    assert camp["status"] == "LIVE" and camp["paid"] == FEE // 2, camp
    p = view(c, "get_payout", [0])
    assert p["kind"] == "fee" and p["to"].lower() == creator.address.lower() and p["amount"] == FEE // 2, p
    settle(c, 0, tx)
    # Once the transfers have left, the balance matches what the contract expects to hold.
    assert view(c, "get_accounting")["unaccounted"] == 0
