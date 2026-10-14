"""Integration test against Testnet Bradbury, on the packed code that is deployed:

    python deploy/shrink.py contracts/sponsor_pay.py build/sponsor_pay.py --pack
    cd tests/integration && gltest . -v -s --network testnet_bradbury   # config in this folder

Uses the demo posts at sponsorpay.cristhedev.com/demo/ (served with
`__CODE__` = sponsorpay-0). The live period is at least a day, so completion
is the recorded Bradbury run in the README.
"""

import json

import pytest
from gltest import get_accounts, get_contract_factory
from gltest.assertions import tx_execution_failed, tx_execution_succeeded

FEE = 2 * 10**15
REQS = ["Discloses the sponsorship with #ad", "Mentions the GenLayer Builder Kit", "Points readers to genlayer.com"]
BASE = "https://sponsorpay.cristhedev.com/demo/"


@pytest.mark.integration
def test_submit_non_compliant_then_compliant():
    c = get_contract_factory("SponsorPay").deploy(args=[])
    creator = get_accounts()[1]
    args = [creator.address, "Builder Kit launch", "One post about the kit.", json.dumps(REQS), json.dumps(["sponsorpay.cristhedev.com"]), 7, 1]
    assert tx_execution_succeeded(c.create_campaign(args=args).transact(value=FEE))
    as_creator = c.connect(creator)
    assert tx_execution_failed(as_creator.submit_post(args=[0, "https://example.com/post"]).transact())

    assert tx_execution_succeeded(as_creator.submit_post(args=[0, BASE + "post-noad.html"]).transact())
    camp = c.get_campaign(args=[0]).call()
    assert camp["status"] == "OPEN" and camp["met"][0] is False and camp["paid"] == 0

    assert tx_execution_succeeded(as_creator.submit_post(args=[0, BASE + "post-good.html"]).transact())
    camp = c.get_campaign(args=[0]).call()
    assert camp["status"] == "LIVE" and camp["paid"] == FEE // 2
    assert c.get_accounting(args=[]).call()["unaccounted"] == 0
