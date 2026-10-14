"""Fixtures for SponsorPay direct-mode tests."""

import json

import pytest

GEN = 10**18
DAY = 86_400
FEE = 2 * GEN
REQS = ["Discloses the sponsorship with #ad", "Mentions the product name Acme Bottle", "Links to acme.example"]
HOSTS = json.dumps(["social.example"])
URL = "https://social.example/@carol/post/1"
POST = "Hiking with my new Acme Bottle, keeps water cold all day. acme.example #ad sponsorpay-0"
LLM = r"sponsored social media post"


def to_hex(addr) -> str:
    if hasattr(addr, "as_hex"):
        return addr.as_hex
    from genlayer.py.types import Address

    return Address(addr).as_hex


class Ledger:
    def __init__(self):
        self.transfers = []

    def paid_to(self, addr) -> int:
        h = to_hex(addr).lower()
        return sum(v for a, v in self.transfers if a.lower() == h)


@pytest.fixture
def ledger():
    import gltest.direct.wasi_mock as wm

    book = Ledger()
    original = wm._handle_gl_call

    def spy(vm, request):
        if isinstance(request, dict) and "EthSend" in request:
            r = request["EthSend"]
            book.transfers.append((to_hex(r["address"]), int(r["value"])))
        return original(vm, request)

    wm._handle_gl_call = spy
    yield book
    wm._handle_gl_call = original


def chain_balance(vm, amount):
    """The mock never moves value, so tests set the contract's balance as the chain would show it."""
    vm.deal(vm._contract_address, amount)


@pytest.fixture
def sp(direct_vm, direct_deploy, direct_alice, direct_bob):
    """Campaign 0: brand Alice, creator Bob, 7 days to submit, 14 days live."""
    direct_vm.warp("2030-01-01T10:00:00Z")
    c = direct_deploy("contracts/sponsor_pay.py")
    direct_vm.sender = direct_alice
    direct_vm.value = FEE
    c.create_campaign(to_hex(direct_bob), "Acme Bottle launch", "Summer hiking post.", json.dumps(REQS), HOSTS, 7, 14)
    direct_vm.value = 0
    chain_balance(direct_vm, FEE)
    return c


def post(vm, met=(True, True, True), body=POST, status=200):
    vm.clear_mocks()
    vm.mock_web(r"social\.example", {"status": status, "body": body})
    vm.mock_llm(LLM, json.dumps({"met": list(met), "note": "checked"}))


def submit(vm, c, who, url=URL, **kw):
    post(vm, **kw)
    vm.sender = who
    return c.submit_post(0, url)


@pytest.fixture
def live(sp, direct_vm, direct_bob):
    submit(direct_vm, sp, direct_bob)
    chain_balance(direct_vm, FEE // 2)
    return sp
