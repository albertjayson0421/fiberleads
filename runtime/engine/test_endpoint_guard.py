import endpoint_guard as eg

P = F = 0

def check(name, condition):
    global P,F
    if condition:
        P += 1
        print("  PASS", name)
    else:
        F += 1
        print("  FAIL", name)


def run():
    original_time = eg.time.time
    clock = [1000.0]
    eg.time.time = lambda: clock[0]

    try:
        print("[single-lane circuit]")
        name = "offline-test-single"
        eg.reset(name)

        for _ in range(eg.TRIP_AFTER):
            eg.record_failure(name)

        s = eg.state(name)
        check("six failures open single-lane circuit", s["open"])
        check("single-lane mode is open", s["mode"] == "open")

        blocked = False
        try:
            eg.before_attempt(name)
        except eg.EndpointDown:
            blocked = True
        check("open circuit refuses another attempt", blocked)

        eg.record_success(name)
        s = eg.state(name)
        check("success resets single-lane circuit", not s["open"])
        check("success clears consecutive failures",
              s["consecutive_failures"] == 0)

        print("[multi-lane isolation and soft throttle]")
        name = "offline-test-multi"
        eg.reset(name)

        eg.record_success(name, lane="lane-2")
        for _ in range(eg.TRIP_AFTER):
            eg.record_failure(name, lane="lane-1")

        s = eg.state(name)
        check("one failing lane does not open multi-lane circuit",
              not s["open"])

        eg.record_success(name, lane="lane-2")
        s = eg.state(name)

        check("success from another lane clears failure streak",
              s["consecutive_failures"] == 0)
        check("success from another lane clears down-lane evidence",
              s["lanes_down"] == 0)

        for i in range(6):
            eg.record_failure(name, lane="lane-%d" % i)

        eg.before_attempt(name, lane="lane-0")
        s = eg.state(name)
        check("multi-lane outage enters soft mode", s["mode"] == "soft")

        blocked = False
        try:
            eg.before_attempt(name, lane="lane-1")
        except eg.EndpointDown:
            blocked = True
        check("soft mode throttles immediate second probe", blocked)

        eg.record_failure(name, lane="lane-0")
        
        clock[0] += eg.SOFT_PROBE_INTERVAL
        allowed = True
        try:
            eg.before_attempt(name, lane="lane-1")
        except eg.EndpointDown:
            allowed = False
        check("soft mode permits probe after interval", allowed)

        clock[0] += eg.SILENCE_SECONDS
        blocked = False
        try:
            eg.before_attempt(name, lane="lane-2")
        except eg.EndpointDown:
            blocked = True

        s = eg.state(name)
        check("sustained multi-lane silence hard-opens circuit",
              s["mode"] == "open" and s["open"])
        check("hard-open circuit refuses further attempts", blocked)

        eg.record_success(name, lane="lane-5")
        s = eg.state(name)
        check("success from any lane closes multi-lane circuit",
              s["mode"] == "closed" and not s["open"])
        check("success clears down lanes", s["lanes_down"] == 0)

    finally:
        eg.reset("offline-test-single")
        eg.reset("offline-test-multi")
        eg.time.time = original_time

if __name__ == "__main__":
    run()
    print("\nRESULT: %d passed, %d failed" % (P, F))
    raise SystemExit(1 if F else 0)