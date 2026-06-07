"""Load / end-to-end stress test — fire N concurrent appeals at a running server.

Each appeal detonates a 3-agent swarm, so N=100 exercises ~300 concurrent call
agents through the full pipeline (Unsiloed -> PAVO route -> Moss -> reconcile ->
letter) plus the WebSocket broadcast, the shared router, the audit log, and the
cost ledger — all at once. This is the "does it survive the stage" test.

Usage (server must be running: python -m backstop.server):
    python tests/load_test.py            # 100 appeals
    python tests/load_test.py 250        # custom count

Verifies every appeal reaches letter.ready (the denial overturned) with a real
cost ratio, reports throughput, and fails loudly on any error.
"""
from __future__ import annotations

import asyncio
import sys
import time

import httpx

BASE = "http://localhost:8000"


async def _one(client: httpx.AsyncClient, i: int) -> dict:
    try:
        r = await client.post(f"{BASE}/appeals", timeout=20)
        appeal_id = r.json()["appeal_id"]
    except Exception as exc:
        return {"ok": False, "stage": "post", "error": repr(exc)}

    # poll the per-appeal event log until done (each appeal has its own log)
    deadline = time.time() + 45
    while time.time() < deadline:
        try:
            a = (await client.get(f"{BASE}/appeals/{appeal_id}", timeout=20)).json()
        except Exception as exc:
            return {"ok": False, "stage": "poll", "id": appeal_id, "error": repr(exc)}
        if a.get("status") in ("done", "error"):
            types = {e["type"] for e in a.get("events", [])}
            cost = next((e for e in reversed(a["events"]) if e["type"] == "cost.tick"), {})
            return {
                "ok": a["status"] == "done" and "letter.ready" in types,
                "id": appeal_id,
                "status": a["status"],
                "has_letter": "letter.ready" in types,
                "has_moss": "moss.hit" in types,
                "has_reconcile": "reconcile.found" in types,
                "ratio": cost.get("ratio"),
                "events": len(a.get("events", [])),
            }
        await asyncio.sleep(0.25)
    return {"ok": False, "stage": "timeout", "id": appeal_id}


async def main(n: int) -> int:
    # confirm server is up
    async with httpx.AsyncClient() as client:
        try:
            h = (await client.get(f"{BASE}/healthz", timeout=5)).json()
        except Exception:
            print(f"ERROR: server not reachable at {BASE} — start it: python -m backstop.server")
            return 2
        print(f"server ok · PAVO params {h['pavo_params']} · firing {n} concurrent appeals "
              f"(~{n*3} agents)…\n")

        t0 = time.time()
        results = await asyncio.gather(*[_one(client, i) for i in range(n)])
        dt = time.time() - t0

    ok = [r for r in results if r.get("ok")]
    bad = [r for r in results if not r.get("ok")]
    ratios = [r["ratio"] for r in ok if r.get("ratio")]
    avg_ratio = sum(ratios) / len(ratios) if ratios else 0
    with_letter = sum(1 for r in results if r.get("has_letter"))
    with_moss = sum(1 for r in results if r.get("has_moss"))
    with_recon = sum(1 for r in results if r.get("has_reconcile"))

    print(f"  appeals:            {n}")
    print(f"  succeeded:          {len(ok)}/{n}")
    print(f"  reached letter:     {with_letter}/{n}")
    print(f"  moss retrieval hit: {with_moss}/{n}")
    print(f"  contradiction found:{with_recon}/{n}")
    print(f"  avg cost ratio:     {avg_ratio:.2f}x")
    print(f"  wall time:          {dt:.1f}s  ({n/dt:.1f} appeals/s)")
    if bad:
        print(f"\n  FAILURES ({len(bad)}):")
        for r in bad[:8]:
            print("   ", r)

    passed = len(ok) == n and avg_ratio >= 3.0
    print("\n" + ("STRESS TEST PASSED ✓" if passed else "STRESS TEST FAILED ✗"))
    return 0 if passed else 1


if __name__ == "__main__":
    count = int(sys.argv[1]) if len(sys.argv) > 1 else 100
    sys.exit(asyncio.run(main(count)))
