"""Campaign lifecycle: create, submit, live period, completion and refunds."""

import json

import pytest

from tests.direct.conftest import FEE, HOSTS, POST, REQS, URL, chain_balance, post, submit, to_hex


def test_create(sp, direct_alice, direct_bob):
    c = sp.get_campaign(0)
    assert c["brand"].lower() == to_hex(direct_alice).lower() and c["creator"].lower() == to_hex(direct_bob).lower()
    assert c["status"] == "OPEN" and c["amount"] == FEE and c["code"] == "sponsorpay-0"
    assert c["requirements"] == REQS and c["attempts_left"] == 3
    assert sp.get_accounting()["unaccounted"] == 0


@pytest.mark.parametrize(
    "creator_is_brand, value, reqs, hosts, days, msg",
    [
        (True, FEE, REQS, HOSTS, (7, 14), "another address"),
        (False, 0, REQS, HOSTS, (7, 14), "escrow the fee"),
        (False, FEE, [], HOSTS, (7, 14), "1-8 requirements"),
        (False, FEE, ["x" * 9] * 9, HOSTS, (7, 14), "1-8 requirements"),
        (False, FEE, ["ok"], HOSTS, (7, 14), "3-200"),
        (False, FEE, REQS, json.dumps(["https://social.example"]), (7, 14), "hostnames"),
        (False, FEE, REQS, HOSTS, (0, 14), "submit_days"),
        (False, FEE, REQS, HOSTS, (7, 91), "live_days"),
    ],
)
def test_create_validation(sp, direct_vm, direct_alice, direct_bob, creator_is_brand, value, reqs, hosts, days, msg):
    direct_vm.sender = direct_alice
    direct_vm.value = value
    with direct_vm.expect_revert(msg):
        sp.create_campaign(to_hex(direct_alice if creator_is_brand else direct_bob), "Title", "", json.dumps(reqs), hosts, *days)


def test_compliant_post_pays_half(sp, direct_vm, direct_bob, ledger):
    r = submit(direct_vm, sp, direct_bob)
    assert r["status"] == "LIVE" and r["met"] == [True, True, True]
    c = sp.get_campaign(0)
    assert c["paid"] == FEE // 2 and c["live_until"] - c["checked_at"] == 14 * 86_400 and len(c["post_sha256"]) == 64
    assert ledger.paid_to(direct_bob) == FEE // 2


def test_failed_attempts_then_refund(sp, direct_vm, direct_alice, direct_bob, ledger):
    r = submit(direct_vm, sp, direct_bob, met=(False, True, True))
    assert r["status"] == "OPEN" and r["code"] == "not_met"
    assert sp.get_campaign(0)["met"] == [False, True, True]
    submit(direct_vm, sp, direct_bob, body=POST.replace("sponsorpay-0", ""))
    assert sp.get_campaign(0)["note"] == "code_missing"
    assert submit(direct_vm, sp, direct_bob, status=404)["status"] == "EXPIRED"
    assert ledger.paid_to(direct_alice) == FEE and ledger.paid_to(direct_bob) == 0
    with direct_vm.expect_revert("not taking submissions"):
        submit(direct_vm, sp, direct_bob)


def test_submit_guards(sp, direct_vm, direct_alice, direct_bob):
    with direct_vm.expect_revert("not authorised"):
        submit(direct_vm, sp, direct_alice)
    for url in ("http://social.example/p", "https://evil.example/p", "https://social.example.evil.io/p"):
        with direct_vm.expect_revert("campaign hosts"):
            submit(direct_vm, sp, direct_bob, url=url)
    assert submit(direct_vm, sp, direct_bob, url="https://m.social.example/p/1")["status"] == "LIVE"
    with direct_vm.expect_revert("not taking submissions"):
        submit(direct_vm, sp, direct_bob)


def test_deadline_and_expire(sp, direct_vm, direct_alice, direct_bob, direct_charlie, ledger):
    direct_vm.sender = direct_charlie
    with direct_vm.expect_revert("still open"):
        sp.expire(0)
    direct_vm.warp("2030-01-08T10:00:01Z")
    with direct_vm.expect_revert("not taking submissions"):
        submit(direct_vm, sp, direct_bob)
    direct_vm.sender = direct_charlie
    sp.expire(0)
    assert sp.get_campaign(0)["status"] == "EXPIRED" and ledger.paid_to(direct_alice) == FEE


def test_recheck_unchanged_during_live_period(live, direct_vm, direct_charlie, ledger):
    post(direct_vm, met=(False, False, False))  # an LLM call would fail it
    direct_vm.warp("2030-01-05T10:00:00Z")
    direct_vm.sender = direct_charlie
    r = live.recheck(0)
    assert r["status"] == "LIVE" and r["code"] == "unchanged"
    assert live.get_campaign(0)["checks"] == 2 and ledger.transfers == []


def test_completion_pays_the_rest(live, direct_vm, direct_bob, ledger):
    direct_vm.warp("2030-01-15T10:00:01Z")
    post(direct_vm)
    direct_vm.sender = direct_bob
    assert live.recheck(0)["status"] == "COMPLETED"
    assert ledger.paid_to(direct_bob) == FEE // 2 and live.get_campaign(0)["paid"] == FEE
    with direct_vm.expect_revert("not live"):
        live.recheck(0)


def test_edited_post_still_compliant(live, direct_vm, direct_charlie):
    old = live.get_campaign(0)["post_sha256"]
    post(direct_vm, body=POST + " Edit: typo")
    direct_vm.sender = direct_charlie
    assert live.recheck(0)["code"] == "ok"
    assert live.get_campaign(0)["post_sha256"] != old


@pytest.mark.parametrize(
    "kw, code",
    [
        ({"status": 404}, "removed"),
        ({"status": 410}, "removed"),
        ({"body": POST.replace(" #ad", "")[:-len(" sponsorpay-0")] + " acme"}, "code_missing"),
        ({"body": POST.replace(" #ad", ""), "met": (False, True, True)}, "not_met"),
    ],
)
def test_broken_post_refunds_the_rest(live, direct_vm, direct_alice, direct_charlie, ledger, kw, code):
    post(direct_vm, **kw)
    direct_vm.warp("2030-01-06T10:00:00Z")
    direct_vm.sender = direct_charlie
    r = live.recheck(0)
    assert r["status"] == "REFUNDED" and r["code"] == code
    assert ledger.paid_to(direct_alice) == FEE // 2 and live.get_campaign(0)["paid"] == FEE


@pytest.mark.parametrize("status", [503, 429, 403])
def test_outage_changes_nothing(live, direct_vm, direct_charlie, ledger, status):
    post(direct_vm, status=status)
    direct_vm.sender = direct_charlie
    with direct_vm.expect_revert(f"[EXTERNAL] post returned HTTP {status}"):
        live.recheck(0)
    assert live.get_campaign(0)["status"] == "LIVE" and ledger.transfers == []


def test_recheck_needs_a_live_campaign(sp, direct_vm, direct_charlie):
    direct_vm.sender = direct_charlie
    with direct_vm.expect_revert("not live"):
        sp.recheck(0)
    with direct_vm.expect_revert("campaign not found"):
        sp.get_campaign(3)


# ── Each payout settles from its own transfer ───────────────────────────────

OTHER = "0x" + "77" * 20


def test_delivered_fee_settles(live, direct_vm, direct_charlie, chain):
    tx, fin = chain.pay(live, 0, "submit_post", [0, URL])
    direct_vm.sender = direct_charlie
    assert live.settle_payout(0, tx, fin) == "DELIVERED"
    assert live.get_payout(0)["tx"] == tx
    assert direct_vm.run_validator(leader_result="failed") is False
    with direct_vm.expect_revert("already settled"):
        live.settle_payout(0, tx, fin)
    with direct_vm.expect_revert("payout not found"):
        live.settle_payout(5, tx, fin)


def test_failed_fee_is_recovered_by_the_creator_to_another_address(live, direct_vm, direct_bob, chain, ledger):
    tx, fin = chain.pay(live, 0, "submit_post", [0, URL], delivered=False)
    assert live.settle_payout(0, tx, fin) == "FAILED"
    assert live.get_recoverable(to_hex(direct_bob)) == FEE // 2
    assert live.get_accounting()["expected_balance"] == FEE
    direct_vm.warp("2030-01-01T10:00:05Z")
    direct_vm.sender = direct_bob
    assert live.recover(OTHER) == FEE // 2
    assert ledger.paid_to(OTHER) == FEE // 2
    with direct_vm.expect_revert("nothing to recover"):
        live.recover(OTHER)
    # The recovery is a payout too, and a failed one is recoverable again by Bob.
    tx2, fin2 = chain.pay(live, 1, "recover", [OTHER], delivered=False)
    assert live.settle_payout(1, tx2, fin2) == "FAILED"
    assert live.get_recoverable(to_hex(direct_bob)) == FEE // 2 and live.get_recoverable(OTHER) == 0


def test_failed_refund_is_recoverable_by_the_brand(live, direct_vm, direct_alice, direct_charlie, chain):
    post(direct_vm, status=404)
    direct_vm.warp("2030-01-06T10:00:00Z")
    direct_vm.sender = direct_charlie
    live.recheck(0)
    tx, fin = chain.pay(live, 1, "recheck", [0], delivered=False)
    assert live.settle_payout(1, tx, fin) == "FAILED"
    assert live.get_recoverable(to_hex(direct_alice)) == FEE // 2
    assert live.get_recoverable(to_hex(direct_charlie)) == 0


def test_expire_payout_settles(sp, direct_vm, direct_charlie, chain):
    direct_vm.warp("2030-01-09T10:00:00Z")
    direct_vm.sender = direct_charlie
    sp.expire(0)
    tx, fin = chain.pay(sp, 0, "expire", [0])
    assert sp.settle_payout(0, tx, fin) == "DELIVERED"


@pytest.mark.parametrize(
    "kw,err",
    [
        ({"method": "expire"}, "did not create this payout"),  # a fee is not made by expire
        ({"args": [1, URL]}, "did not create this payout"),
        ({"sender": "0x" + "99" * 20}, "did not create this payout"),
        ({"at": 1}, "did not create this payout"),
        ({"result": 2}, "did not create this payout"),
        ({"status": 5}, "not finalized yet"),
        # same sender, second, method and id, but it sent nothing (a call that did not pay)
        ({"delivered": None}, "did not create this payout"),
    ],
)
def test_settle_rejects_a_transaction_that_is_not_this_payout(kw, err, live, direct_vm, chain):
    method, args = kw.pop("method", "submit_post"), kw.pop("args", [0, URL])
    tx, fin = chain.pay(live, 0, method, args, **{"delivered": False, **kw})
    with direct_vm.expect_revert(err):
        live.settle_payout(0, tx, fin)
    assert live.get_payout(0)["status"] == "SENT"


def test_a_paying_transaction_settles_one_payout(live, direct_vm, direct_bob, chain):
    tx, fin = chain.pay(live, 0, "submit_post", [0, URL])
    live.settle_payout(0, tx, fin)
    direct_vm.sender = direct_bob
    post(direct_vm, status=404)
    direct_vm.warp("2030-01-06T10:00:00Z")
    live.recheck(0)
    with direct_vm.expect_revert("already settled a payout"):
        live.settle_payout(1, tx, fin)


def test_settle_needs_the_finalization_of_that_transaction(live, direct_vm, chain):
    tx, _ = chain.pay(live, 0, "submit_post", [0, URL], delivered=False)
    _, other_fin = chain.pay(live, 0, "submit_post", [0, URL])
    with direct_vm.expect_revert("not the finalization"):
        live.settle_payout(0, tx, other_fin)
    with direct_vm.expect_revert("not found"):
        live.settle_payout(0, tx, "0x" + "ab" * 32)
    with direct_vm.expect_revert("0x + 64 hex"):
        live.settle_payout(0, tx, "latest")
    chain.down = True
    with direct_vm.expect_revert("RPC unavailable"):
        live.settle_payout(0, tx, other_fin)


def test_one_payment_per_sender_per_second(live, direct_vm, direct_bob, direct_charlie):
    post(direct_vm, status=404)  # Bob's fee was paid at this second
    direct_vm.sender = direct_bob
    with direct_vm.expect_revert("one payment per sender per second"):
        live.recheck(0)
    direct_vm.sender = direct_charlie  # another sender is a different key
    live.recheck(0)
    assert live.get_payout(1)["sender"].lower() == to_hex(direct_charlie).lower()
