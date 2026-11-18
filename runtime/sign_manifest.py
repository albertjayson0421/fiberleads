#!/usr/bin/env python3
"""Re-sign production_manifest.json from the CURRENT bytes of the 5 hash-gated modules.

WHY THIS EXISTS: on startup, fleet_scheduler.gate_hashes() refuses to run any of these
5 modules unless its bytes match production_manifest.json. That stops an unreviewed edit
from silently running in production. So after an OWNER-REVIEWED, intentional change to one
of these modules is merged, the manifest must be regenerated (re-signed) or production will
refuse to start.

RUN THIS ONLY after reviewing and merging an intentional change to a gated module.
    python3 runtime/sign_manifest.py            # from the repo root
    python3 sign_manifest.py                     # from inside runtime/
Then commit the updated manifest with a signed commit:  git commit -S runtime/production_manifest.json

It regenerates from whatever the files currently are — it does NOT judge whether the change
was correct. That judgement is the PR review you already did.
"""
import hashlib, json, os, sys

HERE = os.path.dirname(os.path.abspath(__file__))  # the runtime/ directory
GATED = {
    "carrier_uqual":   os.path.join(HERE, "engine", "carrier_uqual.py"),
    "fleet_actor_v2":  os.path.join(HERE, "fleet_actor_v2.py"),
    "product_stage":   os.path.join(HERE, "product_stage.py"),
    "billing_mapping": os.path.join(HERE, "engine", "billing_mapping.py"),
    "fleet_pipeline":  os.path.join(HERE, "fleet_pipeline.py"),
}

def main():
    manifest, out_lines = {}, []
    for name, path in GATED.items():
        if not os.path.exists(path):
            sys.exit(f"REFUSED: gated module '{name}' not found at {path}")
        digest = hashlib.sha256(open(path, "rb").read()).hexdigest()
        manifest[name] = digest
        out_lines.append(f"  {name:<16} {digest[:16]}…")
    out = os.path.join(HERE, "production_manifest.json")
    prev = json.load(open(out)) if os.path.exists(out) else {}
    changed = [n for n in manifest if manifest[n] != prev.get(n)]
    with open(out, "w") as fh:
        json.dump(manifest, fh, indent=1)
    print("Re-signed production_manifest.json from current module bytes:")
    print("\n".join(out_lines))
    if changed:
        print(f"\nCHANGED since last manifest: {', '.join(changed)}")
    else:
        print("\n(no hashes changed — manifest already matched the files)")
    print(f"\nWrote {out}")
    print("Next: commit it with a signed commit so the reviewed change can run in prod:")
    print("  git commit -S runtime/production_manifest.json -m 'Re-sign manifest after <change>'")

if __name__ == "__main__":
    main()
