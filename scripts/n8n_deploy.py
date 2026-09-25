"""Create or update the ShelfSight workflows in n8n from the templates in n8n/.

  uv run --env-file .env python scripts/n8n_deploy.py

Reads from the environment (put these in the git-ignored .env):
  N8N_API_KEY              n8n → Settings → n8n API
  SHELFSIGHT_BRIDGE_TOKEN  shared with `shelfsight serve`
  SHELFSIGHT_REPORT_TO     address the reports and alerts go to (also the SMTP sender)
  N8N_URL                  default http://localhost:5679
  SHELFSIGHT_WORKSPACE     default dotandkey

Secrets never go into the templates. The bridge token becomes an encrypted n8n credential.
The SMTP credential "ShelfSight Gmail SMTP" is yours to create in the n8n UI; this script links it
by name once it exists (re-run the script after creating it).
"""
import json
import os
import sys
from pathlib import Path

import httpx

TEMPLATES = Path("n8n")
BRIDGE_CRED = "ShelfSight bridge token"
SMTP_CRED = "ShelfSight Gmail SMTP"
ERROR_WORKFLOW = "error_alert.json"
SCHEDULED = ("daily_run.json", "weekly_digest.json")


def env(name: str, default: str | None = None) -> str:
    value = os.environ.get(name, default)
    if not value:
        sys.exit(f"error: {name} is not set (add it to .env)")
    return value


def render(template: dict, values: dict[str, str], smtp_id: str | None) -> dict:
    """Fill placeholders; drop the SMTP credential link when that credential doesn't exist yet."""
    text = json.dumps(template)
    for key, value in values.items():
        text = text.replace(key, value)
    wf = json.loads(text)
    for node in wf["nodes"]:
        creds = node.get("credentials", {})
        if "smtp" in creds and not smtp_id:
            del creds["smtp"]
        elif "smtp" in creds:
            creds["smtp"]["id"] = smtp_id
        if not creds:
            node.pop("credentials", None)
    if not wf["settings"].get("errorWorkflow"):
        wf["settings"].pop("errorWorkflow", None)
    return wf


def main() -> int:
    api = httpx.Client(base_url=env("N8N_URL", "http://localhost:5679") + "/api/v1",
                       headers={"X-N8N-API-KEY": env("N8N_API_KEY")}, timeout=30)
    token, report_to = env("SHELFSIGHT_BRIDGE_TOKEN"), env("SHELFSIGHT_REPORT_TO")
    workspace = env("SHELFSIGHT_WORKSPACE", "dotandkey")

    creds = {c["name"]: c for c in api.get("/credentials").raise_for_status().json()["data"]}
    if BRIDGE_CRED in creds:
        bridge_id = creds[BRIDGE_CRED]["id"]
        print(f"credential '{BRIDGE_CRED}' exists (delete it in n8n to rotate the token)")
    else:
        bridge_id = api.post("/credentials", json={"name": BRIDGE_CRED, "type": "httpHeaderAuth",
                                                   "data": {"name": "X-ShelfSight-Token", "value": token}}
                             ).raise_for_status().json()["id"]
        print(f"created credential '{BRIDGE_CRED}'")
    smtp = [c for c in creds.values() if c["type"] == "smtp"]
    chosen = creds.get(SMTP_CRED) or (smtp[0] if len(smtp) == 1 else None)  # the name, else the only SMTP one
    smtp_id = chosen["id"] if chosen else None
    if chosen:
        print(f"SMTP credential: linked '{chosen['name']}'")
    else:
        print(f"SMTP credential: NOT FOUND ({len(smtp)} SMTP credentials; name one '{SMTP_CRED}') - email nodes left unlinked")

    existing = {w["name"]: w for w in api.get("/workflows").raise_for_status().json()["data"]}
    # laptop: n8n in Docker reaches the bridge on the host; EC2: the bridge is a sibling container
    bridge_url = env("SHELFSIGHT_BRIDGE_URL", "http://host.docker.internal:8765")
    values = {"__BRIDGE_CREDENTIAL_ID__": bridge_id, "__SHELFSIGHT_REPORT_TO__": report_to,
              "__SHELFSIGHT_WORKSPACE__": workspace, "__BRIDGE_URL__": bridge_url, "__ERROR_WORKFLOW_ID__": ""}
    print(f"bridge url: {bridge_url}")
    ids = {}
    for name in (ERROR_WORKFLOW, *SCHEDULED):  # error workflow first: the others point at its id
        wf = render(json.loads((TEMPLATES / name).read_text(encoding="utf-8")), values, smtp_id)
        body = {k: wf[k] for k in ("name", "nodes", "connections", "settings")}
        if wf["name"] in existing:
            wid = existing[wf["name"]]["id"]
            api.put(f"/workflows/{wid}", json=body).raise_for_status()
            print(f"updated  {wf['name']} ({wid})")
        else:
            wid = api.post("/workflows", json=body).raise_for_status().json()["id"]
            print(f"created  {wf['name']} ({wid})")
        ids[name] = wid
        values["__ERROR_WORKFLOW_ID__"] = ids[ERROR_WORKFLOW]
    for name in (ERROR_WORKFLOW, *SCHEDULED):  # n8n 2.x won't run an error workflow that isn't active
        r = api.post(f"/workflows/{ids[name]}/activate")
        print(f"activate {name}: {'ok' if r.is_success else f'FAILED {r.status_code} {r.text[:300]}'}")
    print(f"\nOpen n8n: {env('N8N_URL', 'http://localhost:5679')}/home/workflows")
    return 0


if __name__ == "__main__":
    sys.exit(main())
