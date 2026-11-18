"""Never hammer an endpoint that is not answering. Not once, not ever again.

WHAT THIS PREVENTS
------------------
On 2026-08-27 the proxy gateway stopped accepting connections mid-run. 200
workers each retried independently, forever: measured at 16 failed TCP connects
per second, sustained for roughly twenty minutes, from one IP. That is
indistinguishable from an attack, and the endpoint refused us afterwards --
instantly, on every port. Bandwidth was fine and the account was active. We did
it to ourselves.

The failure has a precise shape and it is worth naming, because it is not
"retrying is bad":

    every worker's retry policy was individually reasonable, and there was
    nothing anywhere that could see all of them at once.

A per-worker backoff cannot fix this. Twenty workers each politely retrying
every two seconds is still ten requests a second at the far end. The only thing
that helps is a counter the whole process shares, so the two-hundredth failure
knows about the first.

WHY THIS FILE CHANGED (2026-09-03)
----------------------------------
The original circuit counted CONSECUTIVE transport failures against ONE endpoint
name and opened at six -- regardless of WHICH exit lane produced them. That was
correct for a single direct connection, but the carrier is now reached through
~10 residential exit lanes (dataimpulse_p1..p10, all fronted by the shared
gateway gw.dataimpulse.com). A single TRANSIENT ConnectionError blip on that
shared gateway -- the kind that clears in a second on a fresh session -- landed
as a burst of transport failures across several lanes at once. At COLD START, no
success had yet been recorded to reset the streak, so six of those transient
blips in a row opened the circuit and PARKED THE ENTIRE FLEET at 0/s for a 30 s
cooldown that then doubled. A momentary blip on one gateway froze a whole run.

THE RULE, RESTATED FOR LANES
----------------------------
  * A transient transport error on ONE lane is not evidence the endpoint is
    down -- it is evidence that ONE exit is unhappy. It is counted PER LANE and
    the request is retried on a FRESH lane (carrier.py rotates the exit). It
    does NOT, on its own, refuse the fleet.
  * The process-wide circuit opens only on SUSTAINED SILENCE ACROSS ALL LANES:
    many distinct lanes failing, with NO successful answer from ANY lane for a
    sustained window. That -- and only that -- is a dead endpoint or a dead
    shared gateway, and it is exactly the 2026-08-27 shape. When it is seen the
    circuit opens hard, with the same cooldowns, the same single probe, and the
    same PATIENCE abandonment as before.
  * ANY success from ANY lane clears the down-lane evidence and the silence
    timer completely. So a partial outage where some lanes still answer never
    trips: we keep serving through the lanes that work.

A middle SOFT-THROTTLE state bounds the damage while silence is still being
confirmed: once many lanes are failing but the silence window has not yet
elapsed, the fleet is throttled to one probe every couple of seconds instead of
hammering -- and a single successful probe (the blip clearing) restores full
speed immediately, with no 30 s freeze.

SINGLE-LANE / NO-LANE CALLERS ARE UNCHANGED. Endpoints reached on one connection
(the pin geocoders, the retail carrier, any caller that passes no lane) keep the
original behaviour byte-for-byte: six consecutive failures open the circuit,
full stop. The lane-aware logic engages only once more than one distinct lane
has been seen for an endpoint.

WHY IT RAISES INSTEAD OF WAITING FOREVER
----------------------------------------
A pull that cannot reach the carrier has no result to report, and the run that
discovered this defect proved why that matters: it was on course to spend six
hours producing a header-only CSV that downstream code would have read as "this
market has nobody in it". So once the circuit has stayed open past the patience
limit, the caller is told the endpoint is down and the run stops with the work
untouched and resumable. Silence is not a finding.
"""

from __future__ import annotations

import os
import threading
import time


class EndpointDown(RuntimeError):
    """The endpoint is not answering and we have stopped asking.

    Raised instead of continuing to retry. Whatever was in flight is unfinished,
    not empty -- callers must treat it as work still to do, never as a result.
    """


def _envf(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, "") or default)
    except (TypeError, ValueError):
        return default


def _envi(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, "") or default)
    except (TypeError, ValueError):
        return default


#: Consecutive failures before the circuit opens. Deliberately small: the point
#: is to stop long before a firewall notices us, and a genuinely healthy
#: endpoint does not fail six times in a row.
TRIP_AFTER = 6

#: First cooldown once open. Doubles on each failed probe, to a ceiling.
COOLDOWN_START = 30.0
COOLDOWN_MAX = 900.0

#: How long the circuit may stay open before the run is abandoned rather than
#: left spinning. Half an hour of a dead endpoint is not a blip.
PATIENCE = 1800.0

#: --- Lane-aware (multi-lane) trip parameters. Only consulted once an endpoint
#: has been seen on more than one distinct lane. ---
#:
#: How long EVERY lane must stay silent -- no success from any of them -- before
#: the hard circuit opens. Long enough to ride out a transient gateway blip
#: (measured blips clear in ~1-2 s); short enough that a truly dead endpoint is
#: caught quickly. During this window the fleet is soft-throttled, NOT hammering.
SILENCE_SECONDS = _envf("FL_CIRCUIT_SILENCE_S", 12.0)

#: How many DISTINCT lanes must be failing (with no success among them) for the
#: outage to count as fleet-wide rather than one bad exit. A single wedged lane
#: can never reach this, so it can never trip the fleet. Capped at the number of
#: lanes actually seen, so a run that only ever uses a few lanes still trips when
#: all of them are silent.
MIN_DOWN_LANES = _envi("FL_CIRCUIT_MIN_DOWN_LANES", 6)

#: Probe spacing while silence is still being confirmed (soft-throttle). One
#: request every this-many seconds, fleet-wide -- a hard cap on socket rate that
#: still lets a recovering blip prove itself on the very next probe.
SOFT_PROBE_INTERVAL = _envf("FL_CIRCUIT_SOFT_PROBE_S", 2.0)

#: Sentinel lane key for callers that pass no lane (single-connection callers).
_NO_LANE = "\x00default"


class _Circuit:
    __slots__ = ("fails", "opened_at", "cooldown", "probing", "first_open",
                 "seen", "down", "last_ok", "soft_probe_at")

    def __init__(self):
        self.fails = 0
        self.opened_at = 0.0
        self.cooldown = COOLDOWN_START
        self.probing = False
        self.first_open = 0.0
        #: Distinct lanes ever observed (success or failure) for this endpoint.
        self.seen: set[str] = set()
        #: Distinct lanes that have failed since the last success from ANY lane.
        #: Cleared whole on any success -- that is what makes "all lanes silent"
        #: mean literally no lane answered.
        self.down: set[str] = set()
        #: Wall-clock of the last success from any lane. 0 = none yet.
        self.last_ok = 0.0
        #: Wall-clock the most recent soft-probe was released.
        self.soft_probe_at = 0.0


_LOCK = threading.Lock()
_CIRCUITS: dict[str, _Circuit] = {}


def _circuit(name: str) -> _Circuit:
    c = _CIRCUITS.get(name)
    if c is None:
        c = _CIRCUITS[name] = _Circuit()
    return c


def _lane_key(lane) -> str:
    if lane is None or lane == "":
        return _NO_LANE
    return str(lane)


def _silence(c: _Circuit, now: float) -> float:
    """Seconds since the endpoint last produced ANY answer.

    Measured from the last success, or -- if it has never answered (cold start)
    -- from when it first went fully down. 0 before it is fully down.
    """
    base = c.last_ok or c.first_open
    return (now - base) if base else 0.0


def before_attempt(name: str, lane=None, log=None) -> None:
    """Call immediately before touching the endpoint. Raises if it is down.

    Refuses locally when the circuit is open, so no socket is opened and the far
    end sees nothing at all. Exactly one caller per cooldown is let through to
    probe; everyone else is refused without touching the network.

    `lane` identifies the exit/lane this attempt will go out through. When more
    than one lane has been seen, the circuit refuses ONLY on sustained silence
    across the lanes (see the module docstring); a single lane's transient
    failure never refuses the fleet.
    """
    now = time.time()
    with _LOCK:
        c = _circuit(name)

        # ---- SINGLE-LANE / NO-LANE: original behaviour, byte-for-byte. ----
        if len(c.seen) <= 1:
            if c.fails < TRIP_AFTER:
                return
            waited = now - c.opened_at
            if waited < c.cooldown:
                raise EndpointDown(
                    f"{name} is not answering — {c.fails} consecutive failures, "
                    f"holding off {c.cooldown - waited:.0f}s more. No request was "
                    f"sent. (Hammering a dead endpoint is what got this machine "
                    f"refused on 2026-08-27.)")
            if c.probing:
                raise EndpointDown(
                    f"{name} is not answering — another worker is probing it. "
                    f"No request was sent.")
            if now - c.first_open > PATIENCE:
                raise EndpointDown(
                    f"{name} has been unreachable for "
                    f"{(now - c.first_open)/60:.0f} minutes. Stopping rather than "
                    f"retrying: nothing was written for the work in flight, so it "
                    f"is unfinished, NOT empty, and re-running resumes it.")
            c.probing = True
            if log:
                log(f"    {name}: probing once after {c.cooldown:.0f}s quiet "
                    f"(circuit open, {c.fails} consecutive failures)")
            return

        # ---- MULTI-LANE: open only on sustained silence across all lanes. ----
        need = min(MIN_DOWN_LANES, len(c.seen))
        strong = c.fails >= TRIP_AFTER and len(c.down) >= need
        if not strong:
            # Healthy lanes still exist, or not enough evidence. Never refuse:
            # a transient one-lane blip is retried on a fresh lane, not by
            # freezing the fleet.
            return
        if not c.first_open:
            c.first_open = now
        silence = _silence(c, now)

        if silence >= SILENCE_SECONDS:
            # SUSTAINED silence across the lanes == dead endpoint / dead gateway.
            # Full protection: one probe per cooldown, cooldown doubles on a
            # failed probe, abandon after PATIENCE.
            if not c.opened_at:
                c.opened_at = now
            waited = now - c.opened_at
            if waited < c.cooldown:
                raise EndpointDown(
                    f"{name} is not answering — {c.fails} consecutive failures "
                    f"across {len(c.down)} lanes, silent {silence:.0f}s. Holding "
                    f"off {c.cooldown - waited:.0f}s more. No request was sent. "
                    f"(Hammering a dead endpoint is what got this machine refused "
                    f"on 2026-08-27.)")
            if c.probing:
                raise EndpointDown(
                    f"{name} is not answering — another worker is probing it. "
                    f"No request was sent.")
            if now - c.first_open > PATIENCE:
                raise EndpointDown(
                    f"{name} has been unreachable across all lanes for "
                    f"{(now - c.first_open)/60:.0f} minutes. Stopping rather than "
                    f"retrying: the work in flight is unfinished, NOT empty, and "
                    f"re-running resumes it.")
            c.probing = True
            if log:
                log(f"    {name}: probing once after {c.cooldown:.0f}s quiet "
                    f"(all lanes silent {silence:.0f}s, {c.fails} failures)")
            return

        # SOFT-THROTTLE. Many lanes failing but silence not yet sustained: this
        # may be a transient blip clearing. Do not hammer -- one probe every
        # SOFT_PROBE_INTERVAL -- but do not freeze either: a success on the next
        # probe restores full speed at once.
        if c.probing:
            raise EndpointDown(
                f"{name}: all lanes quiet — a probe is already outstanding. "
                f"No request was sent.")
        if now - c.soft_probe_at < SOFT_PROBE_INTERVAL:
            raise EndpointDown(
                f"{name}: all lanes quiet {silence:.0f}s — throttled to one "
                f"probe per {SOFT_PROBE_INTERVAL:.0f}s while it clears. No "
                f"request was sent.")
        c.probing = True
        c.soft_probe_at = now
        if log:
            log(f"    {name}: soft-throttle probe ({len(c.down)} lanes quiet "
                f"{silence:.0f}s; not yet confirmed down)")


def record_success(name: str, lane=None) -> None:
    """A real answer. Closes the circuit and forgets the streak.

    A success from ANY lane clears the whole down-lane set and the silence
    timer: the endpoint is demonstrably answering, so whatever failures other
    lanes saw are not sustained silence.
    """
    with _LOCK:
        c = _circuit(name)
        c.seen.add(_lane_key(lane))
        c.fails = 0
        c.probing = False
        c.cooldown = COOLDOWN_START
        c.opened_at = 0.0
        c.first_open = 0.0
        c.last_ok = time.time()
        c.down = set()
        c.soft_probe_at = 0.0


def record_failure(name: str, lane=None, log=None) -> None:
    """A connect/transport failure. Never call this for a real HTTP answer.

    A 429 or a 500 is the endpoint TALKING to us; only silence counts here.
    Counting real answers as failures would trip the circuit on a healthy
    service and stop a pull that was working.

    The failure is attributed to `lane`. In multi-lane mode it advances the
    fleet toward a trip only in concert with failures on the OTHER lanes and an
    absence of any success -- one lane failing on its own never trips.
    """
    now = time.time()
    with _LOCK:
        c = _circuit(name)
        key = _lane_key(lane)
        c.seen.add(key)
        c.down.add(key)
        was_probing = c.probing
        c.probing = False
        c.fails += 1

        # ---- SINGLE-LANE / NO-LANE: original behaviour, byte-for-byte. ----
        if len(c.seen) <= 1:
            if c.fails == TRIP_AFTER:
                c.opened_at = now
                c.first_open = now
                if log:
                    log(f"    {name}: {TRIP_AFTER} consecutive failures — "
                        f"STOPPING requests to it. Nothing further will be sent "
                        f"for {c.cooldown:.0f}s.")
            elif was_probing:
                c.opened_at = now
                c.cooldown = min(c.cooldown * 2, COOLDOWN_MAX)
                if log:
                    log(f"    {name}: still down; next probe in {c.cooldown:.0f}s")
            return

        # ---- MULTI-LANE. A failed HARD probe (we were already in sustained
        # silence) doubles the cooldown, exactly as the single-lane path does. A
        # failed SOFT probe just leaves soft_probe_at set, so the next probe is
        # spaced by SOFT_PROBE_INTERVAL; nothing else to do. ----
        if was_probing and c.opened_at and _silence(c, now) >= SILENCE_SECONDS:
            c.opened_at = now
            c.cooldown = min((c.cooldown or COOLDOWN_START) * 2, COOLDOWN_MAX)
            if log:
                log(f"    {name}: still down across lanes; next probe in "
                    f"{c.cooldown:.0f}s")


def _mode(c: _Circuit, now: float) -> str:
    if len(c.seen) <= 1:
        return "open" if c.fails >= TRIP_AFTER else "closed"
    need = min(MIN_DOWN_LANES, len(c.seen))
    if not (c.fails >= TRIP_AFTER and len(c.down) >= need):
        return "closed"
    return "open" if _silence(c, now) >= SILENCE_SECONDS else "soft"


def state(name: str) -> dict:
    now = time.time()
    with _LOCK:
        c = _circuit(name)
        mode = _mode(c, now)
        # `cooldown` is the effective near-term wait a refused caller should
        # honour: the real (doubling) cooldown when hard-open, the short probe
        # interval when only soft-throttling. CircuitGate reads this to decide
        # how long to park -- returning the 30 s cooldown during a soft-throttle
        # would reintroduce exactly the freeze this change removes.
        if mode == "soft":
            cool = SOFT_PROBE_INTERVAL
        else:
            cool = c.cooldown
        return {"endpoint": name,
                "consecutive_failures": c.fails,
                "open": mode == "open",
                "mode": mode,
                "cooldown": round(cool, 1),
                "lanes_seen": len(c.seen),
                "lanes_down": len(c.down),
                "silence_s": round(_silence(c, now), 1)}


def reset(name: str | None = None) -> None:
    """Test hook. Never call this to 'get past' an open circuit in a real run."""
    with _LOCK:
        if name is None:
            _CIRCUITS.clear()
        else:
            _CIRCUITS.pop(name, None)
