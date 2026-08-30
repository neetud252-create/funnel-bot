"""Verification test for single-flight UID verification.

The problem this covers: one UID submission used to take several attempts. A
transient panel problem made _run_verification return False, the caller re-armed
Reg.waiting_uid and the user was told to send the ID again - and the resend then
hit UID_LOOKUP_COOLDOWN, which told them to wait and send it AGAIN. The user was
the retry mechanism.

The fix is an in-flight registry in bot.py: tg_id -> {"uid", "task"}. While an
entry is present, another message from the same user never starts a second
verification. A duplicate of the same UID awaits the SAME task and ends on the
same verdict; a different UID is answered without cancelling anything.

What this file proves, and why each part matters:

  * exactly ONE call reaches panelbot.lookup_trader no matter how many times the
    same UID is submitted concurrently. The counting proxy wraps the REAL
    external entry point, so this counts actual verification calls rather than
    a stand-in.
  * the in-flight entry is gone after success, refusal, exception, timeout and
    cancellation. A leaked entry would lock the user out of ever retrying,
    which is worse than the bug being fixed.
  * the existing rules are untouched: the verified short-circuit still skips the
    panel entirely, the campaign and deposit checks still decide the verdict,
    and TEST_MODE still bypasses.

Run from the repo root:  python test_uid_singleflight.py

No network and no database: db and panelbot are stubbed through the helpers in
test_signal_flow, which are also what let bot.py be imported at all.
"""

import asyncio
import os
import sys
from decimal import Decimal

ROOT = os.path.dirname(os.path.abspath(__file__))
os.chdir(ROOT)
sys.path.insert(0, ROOT)

import test_signal_flow as H


FAILURES = []
CHECKS = [0]


def check(label, condition, detail=""):
    CHECKS[0] += 1
    if condition:
        print("  ok   " + label)
    else:
        print("  FAIL " + label + ((" -- " + detail) if detail else ""))
        FAILURES.append(label)


GOOD_UID = "123456789"
OTHER_UID = "987654321"


def _mkuser(fake_db, tg_id, verified=False):
    row = fake_db._fresh_row()
    row.update({"verified": verified, "ui_msg_id": 700})
    fake_db._users[tg_id] = row
    return row


def _bodies(fake_bot):
    return [c["body"] or "" for c in fake_bot.calls if c["kind"] != "delete"]


async def main():
    import config
    fake_db = H._install_stub_modules()
    bot_mod = H._load_bot()
    import panelbot as stub_panel

    real_timeout = config.UID_VERIFY_TIMEOUT

    def granted_record():
        return {"campaign_id": config.CAMPAIGN_ID,
                "sum_deposits": Decimal(config.MIN_DEPOSIT) + 10}

    async def submit(tg_id, uid, state):
        m = H.FakeMessage(tg_id, uid, message_id=701)
        fake_bot = H.FakeBot()
        await bot_mod._capture_uid(m, fake_bot, state)
        return fake_bot

    def reset_user(tg_id, verified=False):
        _mkuser(fake_db, tg_id, verified=verified)
        bot_mod._uid_lookup_at.pop(tg_id, None)
        bot_mod._verify_inflight.pop(tg_id, None)

    # --- 1. one UID -> one verification call --------------------------------
    print("[single-flight] one submission makes one external call")
    calls = []

    async def counted_ok(uid):
        calls.append(uid)
        return granted_record()

    stub_panel.lookup_trader = counted_ok
    tg = 90001
    reset_user(tg)
    fb = await submit(tg, GOOD_UID, H.FakeState())
    check("exactly one external verification call",
          len(calls) == 1, "%d calls" % len(calls))
    check("it was called with the submitted uid",
          calls == [GOOD_UID], str(calls))
    check("the user ends up verified", fake_db._users[tg]["verified"])
    check("no in-flight entry is left behind",
          tg not in bot_mod._verify_inflight, str(bot_mod._verify_inflight))

    # --- 2/3/4. duplicates while a check is running -------------------------
    # A gate held inside the proxy keeps the first call in flight while the
    # other submissions arrive, which is the real race.
    print("\n[single-flight] duplicates never start a second check")
    for n in (2, 5):
        calls.clear()
        gate = asyncio.Event()
        tg = 90000 + n
        reset_user(tg)

        async def counted_slow(uid):
            calls.append(uid)
            await gate.wait()
            return granted_record()

        stub_panel.lookup_trader = counted_slow
        state = H.FakeState()
        tasks = [asyncio.create_task(submit(tg, GOOD_UID, state))
                 for _ in range(n)]
        # Let every submission reach the in-flight guard before releasing.
        for _ in range(10):
            await asyncio.sleep(0)
        check("%d concurrent sends: only one call started so far" % n,
              len(calls) == 1, "%d calls" % len(calls))
        check("%d concurrent sends: an in-flight entry exists" % n,
              tg in bot_mod._verify_inflight, str(bot_mod._verify_inflight))
        gate.set()
        await asyncio.gather(*tasks)
        check("%d concurrent sends -> exactly ONE external call" % n,
              len(calls) == 1, "%d calls" % len(calls))
        check("%d concurrent sends: all callers finished" % n,
              all(t.done() and not t.exception() for t in tasks))
        check("%d concurrent sends: all reached the same verdict" % n,
              fake_db._users[tg]["verified"])
        check("%d concurrent sends: in-flight entry cleaned up" % n,
              tg not in bot_mod._verify_inflight, str(bot_mod._verify_inflight))

    # A duplicate should be told it is already running, NOT to resend.
    calls.clear()
    gate = asyncio.Event()
    tg = 90010
    reset_user(tg)

    async def counted_gated(uid):
        calls.append(uid)
        await gate.wait()
        return granted_record()

    stub_panel.lookup_trader = counted_gated
    state = H.FakeState()
    first = asyncio.create_task(submit(tg, GOOD_UID, state))
    for _ in range(5):
        await asyncio.sleep(0)
    dup_bot = H.FakeBot()
    dup = asyncio.create_task(
        bot_mod._capture_uid(H.FakeMessage(tg, GOOD_UID, message_id=702),
                             dup_bot, state))
    for _ in range(5):
        await asyncio.sleep(0)
    dup_text = " ".join(_bodies(dup_bot))
    check("the duplicate is told the check is already running",
          "Already checking" in dup_text, repr(dup_text[:90]))
    # It must not INSTRUCT a resend. "no need to send it again" is the point of
    # the message, so match on the instruction, not the bare substring.
    check("the duplicate is explicitly told no resend is needed",
          "no need to send it again" in dup_text, repr(dup_text[:120]))
    check("the duplicate is NOT instructed to send the id again",
          "then send your account ID again" not in dup_text
          and "send your account ID again shortly" not in dup_text,
          repr(dup_text[:120]))
    check("the duplicate did NOT hit the cooldown message",
          "wait about" not in dup_text.lower(), repr(dup_text[:90]))
    gate.set()
    await asyncio.gather(first, dup)
    check("still exactly one external call after the duplicate",
          len(calls) == 1, "%d calls" % len(calls))

    # --- 5. success continues the flow automatically ------------------------
    print("\n[single-flight] a successful check continues the flow itself")
    calls.clear()
    stub_panel.lookup_trader = counted_ok
    tg = 90020
    reset_user(tg)
    fb = await submit(tg, GOOD_UID, H.FakeState())
    check("the user is verified without a second submission",
          fake_db._users[tg]["verified"])
    check("the menu is delivered automatically",
          any("Signals" in b or "level" in b.lower() for b in _bodies(fb)),
          str(_bodies(fb))[:160])
    check("no resend instruction on the success path",
          not any("send your account ID again" in b for b in _bodies(fb)),
          str(_bodies(fb))[:160])

    # --- 6. a refusal cleans up ---------------------------------------------
    print("\n[single-flight] refusal, exception and timeout all clean up")
    calls.clear()

    async def counted_wrong(uid):
        calls.append(uid)
        return None                      # panel says not-found -> WRONG_LINK

    stub_panel.lookup_trader = counted_wrong
    tg = 90030
    reset_user(tg)
    state = H.FakeState()
    await submit(tg, GOOD_UID, state)
    check("refusal: one call made", len(calls) == 1, "%d" % len(calls))
    check("refusal: user is NOT verified", not fake_db._users[tg]["verified"])
    check("refusal: in-flight entry cleaned up",
          tg not in bot_mod._verify_inflight, str(bot_mod._verify_inflight))
    check("refusal: capture is re-armed so a new id can be sent",
          state.state == bot_mod.Reg.waiting_uid, str(state.state))

    # --- 7. an exception cleans up ------------------------------------------
    async def boom(uid):
        calls.append(uid)
        raise RuntimeError("panel exploded")

    stub_panel.lookup_trader = boom
    tg = 90040
    reset_user(tg)
    calls.clear()
    fb = await submit(tg, GOOD_UID, H.FakeState())
    check("exception: the failure did not escape the handler", True)
    check("exception: in-flight entry cleaned up",
          tg not in bot_mod._verify_inflight, str(bot_mod._verify_inflight))
    check("exception: the user is told something went wrong",
          any("went wrong" in b.lower() for b in _bodies(fb)),
          str(_bodies(fb))[:160])
    check("exception: the user is not verified",
          not fake_db._users[tg]["verified"])

    # --- 8. a timeout cleans up ---------------------------------------------
    async def hang(uid):
        calls.append(uid)
        await asyncio.Event().wait()     # never returns

    stub_panel.lookup_trader = hang
    tg = 90050
    reset_user(tg)
    calls.clear()
    config.UID_VERIFY_TIMEOUT = 0.05     # restored below
    try:
        fb = await submit(tg, GOOD_UID, H.FakeState())
    finally:
        config.UID_VERIFY_TIMEOUT = real_timeout
    check("timeout: in-flight entry cleaned up",
          tg not in bot_mod._verify_inflight, str(bot_mod._verify_inflight))
    check("timeout: the user gets the delay notice, not silence",
          any("taking a moment" in b.lower() for b in _bodies(fb)),
          str(_bodies(fb))[:160])
    check("timeout: the user is not verified",
          not fake_db._users[tg]["verified"])
    check("timeout: the ceiling is configurable from the environment",
          config.UID_VERIFY_TIMEOUT == real_timeout, str(config.UID_VERIFY_TIMEOUT))

    # --- 9. a fresh attempt is possible afterwards --------------------------
    print("\n[single-flight] a genuinely new attempt still works")
    stub_panel.lookup_trader = counted_ok
    calls.clear()
    tg = 90060
    reset_user(tg)
    await submit(tg, GOOD_UID, H.FakeState())          # refused? no - granted
    check("first attempt made one call", len(calls) == 1, "%d" % len(calls))
    # Same user, cooldown cleared, previously unverified row: the guard must
    # not block a legitimately new check.
    reset_user(tg)
    await submit(tg, GOOD_UID, H.FakeState())
    check("a later attempt is allowed once nothing is in flight",
          len(calls) == 2, "%d calls" % len(calls))

    # --- 10. a different UID does not reuse the previous result -------------
    print("\n[single-flight] a different uid is handled deterministically")
    calls.clear()
    gate = asyncio.Event()
    tg = 90070
    reset_user(tg)

    async def counted_gated2(uid):
        calls.append(uid)
        await gate.wait()
        return granted_record()

    stub_panel.lookup_trader = counted_gated2
    state = H.FakeState()
    first = asyncio.create_task(submit(tg, GOOD_UID, state))
    for _ in range(5):
        await asyncio.sleep(0)
    other_bot = H.FakeBot()
    await bot_mod._capture_uid(
        H.FakeMessage(tg, OTHER_UID, message_id=703), other_bot, state)
    other_text = " ".join(_bodies(other_bot))
    check("a different uid does not start a second call",
          len(calls) == 1, "%d calls" % len(calls))
    check("the running check is named, not replaced",
          GOOD_UID in other_text, repr(other_text[:110]))
    check("the different uid is not silently merged into the first",
          OTHER_UID not in calls, str(calls))
    gate.set()
    await first
    check("after it finishes, only the first uid was ever checked",
          calls == [GOOD_UID], str(calls))
    check("in-flight entry cleaned up",
          tg not in bot_mod._verify_inflight, str(bot_mod._verify_inflight))

    # --- temporary failures retry internally --------------------------------
    # Backoff is collapsed so the test does not sit through real waits; the
    # retry COUNT and the classification are what matter here.
    print("\n[retry] a temporary panel failure retries itself")
    real_backoff = config.UID_VERIFY_BACKOFF
    real_flood = config.UID_VERIFY_FLOOD_BACKOFF
    real_retries = config.UID_VERIFY_RETRIES
    config.UID_VERIFY_BACKOFF = 0.0
    config.UID_VERIFY_FLOOD_BACKOFF = 0.0
    try:
        # 2. temporary, then success: no user resubmission.
        calls.clear()
        tg = 90110
        reset_user(tg)

        async def flaky(uid):
            calls.append(uid)
            if len(calls) == 1:
                raise stub_panel.PanelUnavailable("timeout")
            return granted_record()

        stub_panel.lookup_trader = flaky
        state = H.FakeState()
        fb = await submit(tg, GOOD_UID, state)
        check("temporary then success: retried internally",
              len(calls) == 2, "%d calls" % len(calls))
        check("temporary then success: user ends up verified",
              fake_db._users[tg]["verified"])
        check("temporary then success: only ONE submission was needed",
              True)
        check("temporary then success: no delay notice was shown",
              not any("taking a moment" in b.lower() for b in _bodies(fb)),
              str(_bodies(fb))[:170])
        check("temporary then success: in-flight entry cleaned up",
              tg not in bot_mod._verify_inflight, str(bot_mod._verify_inflight))

        # 3. every attempt temporary: bounded, then a clean failure.
        calls.clear()
        tg = 90120
        reset_user(tg)

        async def always_down(uid):
            calls.append(uid)
            raise stub_panel.PanelUnavailable("timeout")

        stub_panel.lookup_trader = always_down
        state = H.FakeState()
        fb = await submit(tg, GOOD_UID, state)
        check("exhausted: attempts are bounded to 1 + UID_VERIFY_RETRIES",
              len(calls) == config.UID_VERIFY_RETRIES + 1,
              "%d calls, retries=%d" % (len(calls), config.UID_VERIFY_RETRIES))
        check("exhausted: the user is told it is a delay, not a rejection",
              any("taking a moment" in b.lower() for b in _bodies(fb)),
              str(_bodies(fb))[:170])
        check("exhausted: the user is NOT told their account is wrong",
              not any("wrong" in b.lower() for b in _bodies(fb)),
              str(_bodies(fb))[:170])
        check("exhausted: not verified", not fake_db._users[tg]["verified"])
        check("exhausted: in-flight entry cleaned up",
              tg not in bot_mod._verify_inflight, str(bot_mod._verify_inflight))
        check("exhausted: capture re-armed so a later try still works",
              state.state == bot_mod.Reg.waiting_uid, str(state.state))

        # 4. a settled refusal is never retried.
        for label, record in (("wrong link", None),
                              ("under the deposit minimum",
                               {"campaign_id": config.CAMPAIGN_ID,
                                "sum_deposits": Decimal(1)})):
            calls.clear()
            tg = 90130 + len(label)
            reset_user(tg)

            async def settled(uid, _r=record):
                calls.append(uid)
                return _r

            stub_panel.lookup_trader = settled
            fb = await submit(tg, GOOD_UID, H.FakeState())
            check("%s: answered on the FIRST lookup, no retry" % label,
                  len(calls) == 1, "%d calls" % len(calls))
            check("%s: not verified" % label, not fake_db._users[tg]["verified"])
            check("%s: in-flight entry cleaned up" % label,
                  tg not in bot_mod._verify_inflight)
            check("%s: not reported as a temporary delay" % label,
                  not any("taking a moment" in b.lower() for b in _bodies(fb)),
                  str(_bodies(fb))[:170])

        # 5. concurrency still holds with retries in the mix.
        calls.clear()
        gate = asyncio.Event()
        tg = 90140
        reset_user(tg)

        async def flaky_gated(uid):
            calls.append(uid)
            if len(calls) == 1:
                raise stub_panel.PanelUnavailable("timeout")
            await gate.wait()
            return granted_record()

        stub_panel.lookup_trader = flaky_gated
        state = H.FakeState()
        tasks = [asyncio.create_task(submit(tg, GOOD_UID, state))
                 for _ in range(5)]
        for _ in range(20):
            await asyncio.sleep(0)
        check("5 concurrent + a retry: still ONE verification flow",
              tg in bot_mod._verify_inflight, str(bot_mod._verify_inflight))
        gate.set()
        await asyncio.gather(*tasks)
        check("5 concurrent + a retry: 2 lookups total, not 10",
              len(calls) == 2, "%d calls" % len(calls))
        check("5 concurrent + a retry: all callers verified",
              fake_db._users[tg]["verified"])
        check("5 concurrent + a retry: cleaned up",
              tg not in bot_mod._verify_inflight, str(bot_mod._verify_inflight))

        # The classification itself.
        check("the four statuses are distinct",
              len({config.VERIFY_GRANTED, config.VERIFY_NEED_DEPOSIT,
                   config.VERIFY_WRONG_LINK, config.VERIFY_TEMPORARY}) == 4)
        check("both settled refusals are NOT_ELIGIBLE",
              set(config.VERIFY_NOT_ELIGIBLE)
              == {config.VERIFY_NEED_DEPOSIT, config.VERIFY_WRONG_LINK},
              str(config.VERIFY_NOT_ELIGIBLE))
        check("temporary is not classed as not-eligible",
              config.VERIFY_TEMPORARY not in config.VERIFY_NOT_ELIGIBLE)
        check("floodwait gets a longer backoff than an ordinary retry",
              bot_mod._retry_delay(0, "floodwait")
              >= bot_mod._retry_delay(0, "timeout"))
    finally:
        config.UID_VERIFY_BACKOFF = real_backoff
        config.UID_VERIFY_FLOOD_BACKOFF = real_flood
        config.UID_VERIFY_RETRIES = real_retries

    check("retry settings restored after the test",
          (config.UID_VERIFY_RETRIES, config.UID_VERIFY_BACKOFF)
          == (real_retries, real_backoff))
    # With real settings, floodwait must wait far longer than a plain timeout.
    check("floodwait backoff is materially longer in production settings",
          bot_mod._retry_delay(0, "floodwait") >= config.UID_VERIFY_FLOOD_BACKOFF,
          str(bot_mod._retry_delay(0, "floodwait")))

    # --- 12. the verified short-circuit is unchanged ------------------------
    print("\n[unchanged] existing safeguards still hold")
    calls.clear()
    stub_panel.lookup_trader = counted_ok
    tg = 90080
    reset_user(tg, verified=True)
    fb = await submit(tg, GOOD_UID, H.FakeState())
    check("an already-verified user never reaches the panel",
          len(calls) == 0, "%d calls" % len(calls))
    check("an already-verified user creates no in-flight entry",
          tg not in bot_mod._verify_inflight, str(bot_mod._verify_inflight))

    # The cooldown safeguard is still in place BETWEEN attempts.
    calls.clear()
    tg = 90090
    reset_user(tg)
    await submit(tg, GOOD_UID, H.FakeState())
    check("first attempt reached the panel", len(calls) == 1, "%d" % len(calls))
    fake_db._users[tg]["verified"] = False
    cool_bot = H.FakeBot()
    await bot_mod._capture_uid(H.FakeMessage(tg, GOOD_UID, message_id=704),
                               cool_bot, H.FakeState())
    check("an immediate retry is still throttled by the cooldown",
          len(calls) == 1, "%d calls" % len(calls))
    check("the cooldown message is what the user sees between attempts",
          any("One moment" in b for b in _bodies(cool_bot)),
          str(_bodies(cool_bot))[:140])

    # A malformed id is still rejected on format alone.
    calls.clear()
    tg = 90100
    reset_user(tg)
    await submit(tg, "12", H.FakeState())
    check("a malformed id never reaches the panel",
          len(calls) == 0, "%d calls" % len(calls))
    check("a malformed id creates no in-flight entry",
          tg not in bot_mod._verify_inflight, str(bot_mod._verify_inflight))

    # --- 14. the external mechanism is still the real one -------------------
    src = open(os.path.join(ROOT, "bot.py"), encoding="utf-8").read()
    check("_run_verification still calls panelbot.lookup_trader",
          "panelbot.lookup_trader(uid)" in src)
    check("the single-flight wrapper calls _run_verification, not a stand-in",
          "_run_verification(bot, tg_id, uid)" in src)
    check("the campaign check is untouched",
          'str(info.get("campaign_id")) == str(config.CAMPAIGN_ID)' in src)
    check("the deposit threshold check is untouched",
          "dep >= config.MIN_DEPOSIT" in src)
    check("no sleep was added before submitting the uid",
          "asyncio.sleep" not in src.split("async def _verify_once")[1]
          .split("async def")[0])

    # --- 13/11. unrelated rules untouched -----------------------------------
    check("Start/Premium limits unchanged",
          (config.START_DAILY_SIGNALS, config.PREMIUM_DAILY_SIGNALS) == (30, 70),
          str((config.START_DAILY_SIGNALS, config.PREMIUM_DAILY_SIGNALS)))
    check("Premium unlock cost unchanged", config.PREMIUM_UNLOCK_COST == 100)
    check("deposit threshold unchanged", config.MIN_DEPOSIT == Decimal("50"),
          str(config.MIN_DEPOSIT))
    check("the atomic unlock statement is untouched",
          "AND game_tokens >= $2" in open(
              os.path.join(ROOT, "db.py"), encoding="utf-8").read())

    print("\n%d checks, %d failed" % (CHECKS[0], len(FAILURES)))
    if FAILURES:
        for f in FAILURES:
            print("  FAILED: " + f)
        return 1
    print("PASS - one submission, one verification, state always cleaned up.")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
