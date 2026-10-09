"""Generate an importable, read-only GPT Action schema for an actual public URL."""
import argparse
import json
from pathlib import Path
from urllib.parse import urlsplit

parser = argparse.ArgumentParser()
parser.add_argument("public_url", help="Full HTTPS latest.json URL")
parser.add_argument("--output", default="public/openapi.json")
args = parser.parse_args()
url = urlsplit(args.public_url)
if url.scheme != "https" or url.username or url.password or not url.path.endswith("/latest.json") or url.query or url.fragment:
    raise SystemExit("Use a public HTTPS URL ending in /latest.json, without credentials or query.")
server = args.public_url.rsplit("/", 1)[0]
schema = json.loads((Path(__file__).parents[1] / "schemas/snapshot.schema.json").read_text(encoding="utf-8"))
schema.pop("$schema", None)
document = {
    "openapi": "3.1.0",
    "info": {"title": "A股指数14:30行情", "version": "1.0.0", "description": "Read-only validated snapshots; recompute freshness at read time."},
    "servers": [{"url": server}],
    "paths": {
        "/latest.json": {"get": {"operationId": "getIndexSnapshot", "summary": "Read latest 14:30 snapshot and reject stale or unavailable data", "x-openai-isConsequential": False, "responses": {"200": {"description": "Snapshot; status=ok only means valid at collection. Inspect source timestamps and expiry.", "content": {"application/json": {"schema": schema}, "text/plain": {"schema": schema}}}}}},
        "/verification.json": {"get": {"operationId": "getEndpointVerification", "summary": "Read endpoint deployment verification", "x-openai-isConsequential": False, "responses": {"200": {"description": "Transport and schema verification; not a substitute for checking quote freshness.", "content": {"application/json": {"schema": {"type": "object", "properties": {"public_https_verified": {"type": "boolean"}, "checked_at": {"type": "string"}, "live_usable_at_read": {"type": "boolean"}, "chatgpt_action_verified": {"type": "boolean"}, "reasons": {"type": "array", "items": {"type": "string"}}}}}}}}}}
    }
}
destination = Path(args.output)
destination.parent.mkdir(parents=True, exist_ok=True)
destination.write_text(json.dumps(document, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
print("OpenAPI schema generated; ChatGPT Action has not yet been tested.")
