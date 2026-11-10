#!/usr/bin/env python3
"""GET-only probes. No credentials or upstream response bodies are printed."""
import argparse
import json
import os
import re
import sys
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def config(expected_ref, env=os.environ):
    url = env.get("SUPABASE_URL", "").rstrip("/")
    parts = urlsplit(url)
    if not re.fullmatch(r"[a-z0-9]{10,40}", expected_ref):
        raise ValueError("An explicit expected project reference is required.")
    if url != f"https://{expected_ref}.supabase.co" or parts.username or parts.password:
        raise ValueError("SUPABASE_URL must exactly match the expected HTTPS project origin.")
    secret = env.get("SUPABASE_SECRET_KEY") or env.get("SUPABASE_SERVICE_ROLE_KEY")
    public = env.get("SUPABASE_PUBLISHABLE_KEY", "")
    if not secret or any(c.isspace() for c in secret):
        raise ValueError("A server database key is required in the environment.")
    if not public.startswith("sb_publishable_"):
        raise ValueError("A current publishable key is required for anonymous-access probes.")
    return url, secret, public


def probe(opener, url, key, path, query):
    headers = {"apikey": key, "Accept": "application/json"}
    if not key.startswith(("sb_secret_", "sb_publishable_")):
        headers["Authorization"] = f"Bearer {key}"
    request = Request(url + path + "?" + urlencode(query), headers=headers, method="GET")
    try:
        with opener.open(request, timeout=12) as response:
            # Deliberately do not read or display rows, errors, tokens or keys.
            return response.status
    except HTTPError as exc:
        return exc.code
    except (URLError, TimeoutError, OSError):
        return 0


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--expected-ref", required=True)
    args = parser.parse_args()
    try:
        url, secret, public = config(args.expected_ref)
    except ValueError as exc:
        print(json.dumps({"ready": False, "configuration_error": str(exc)}))
        return 2
    opener = build_opener(NoRedirect())
    checks = []
    # Otherwise an invalid public key could masquerade as successful RLS denial.
    public_key_status = probe(opener, url, public, "/auth/v1/settings", {})
    checks.append({"check": "publishable_key_accepted", "http_status": public_key_status, "ok": public_key_status == 200})
    projections = {
        "predictions": "id,ts,model_version,task,features,prediction,confidence,horizon_s,cohort,meta,received_at",
        "outcomes": "prediction_id,resolved_ts,realized,meta,received_at",
        "registry": "version,task,config,created_ts,status,autonomy",
        "registry_scores": "version,recorded_ts,score",
    }
    for table, columns in projections.items():
        query = {"select": columns, "limit": 0}
        server_status = probe(opener, url, secret, f"/rest/v1/{table}", query)
        public_status = probe(opener, url, public, f"/rest/v1/{table}", query)
        checks.append({"check": f"server_{table}_shape", "http_status": server_status, "ok": server_status == 200})
        # This candidate revokes SELECT grants, so anonymous requests must fail,
        # not merely return an empty list filtered by RLS.
        checks.append({"check": f"anonymous_{table}_denied", "http_status": public_status, "ok": public_status in (401, 403)})
    joined = probe(opener, url, secret, "/rest/v1/predictions", {
        "select": "id,outcomes!inner(resolved_ts,realized)", "order": "received_at.desc,id.asc", "limit": 0,
    })
    checks.append({"check": "terminal_join_and_release_order", "http_status": joined, "ok": joined == 200})
    status = probe(opener, url, secret, "/rest/v1/rpc/selflearn_status", {})
    anon_status = probe(opener, url, public, "/rest/v1/rpc/selflearn_status", {})
    checks.extend([
        {"check": "server_status_rpc", "http_status": status, "ok": status == 200},
        {"check": "anonymous_status_denied", "http_status": anon_status, "ok": anon_status in (401, 403)},
    ])
    ok = all(check["ok"] for check in checks)
    print(json.dumps({"read_only_probes_passed": ok, "checks": checks,
        "limits": "Does not prove write grants, append-only behavior, Auth owner identity, migration history, billing, broker connectivity, or trading readiness."}, indent=2))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
