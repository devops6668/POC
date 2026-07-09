#!/usr/bin/env python3
"""Argo Workflow Duration Collector - K8s CronJob (no kubectl needed)."""
import json, urllib.request, os, ssl
from datetime import datetime, timezone

# ES config
ES_URL = os.environ.get("ES_URL", "https://elastic-cluster-es-http.elastic-system.svc:9200")
ES_USER = os.environ.get("ES_USER", "elastic")
ES_PASS = os.environ.get("ES_PASS", "eBqox3y3pLFz8TxeoN5oL4XG")
INDEX = "argo-workflow-durations"

# K8s API config - use service account token
K8S_HOST = "https://kubernetes.default.svc"
TOKEN_FILE = "/var/run/secrets/kubernetes.io/serviceaccount/token"
CA_FILE = "/var/run/secrets/kubernetes.io/serviceaccount/ca.crt"

def k8s_get(path):
    """Call Kubernetes API."""
    with open(TOKEN_FILE) as f:
        token = f.read().strip()
    ctx = ssl.create_default_context(cafile=CA_FILE) if os.path.exists(CA_FILE) else ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    req = urllib.request.Request(f"{K8S_HOST}{path}",
        headers={"Authorization": f"Bearer {token}", "Accept": "application/json"})
    with urllib.request.urlopen(req, context=ctx, timeout=30) as resp:
        return json.loads(resp.read())

def es_req(method, path, body=None):
    """Call Elasticsearch API."""
    auth = __import__('base64').b64encode(f"{ES_USER}:{ES_PASS}".encode()).decode()
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    url = f"{ES_URL}/{path}"
    data = json.dumps(body).encode() if body else None
    req = urllib.request.Request(url, data=data, method=method,
        headers={"Content-Type": "application/json", "Authorization": f"Basic {auth}"})
    try:
        with urllib.request.urlopen(req, context=ctx, timeout=30) as resp:
            return json.loads(resp.read())
    except Exception as e:
        return {"error": str(e)[:200]}

# Ensure index
r = es_req("HEAD", INDEX)
if isinstance(r, dict) and "error" in r:
    es_req("PUT", INDEX, {
        "mappings": {"properties": {
            "@timestamp": {"type": "date"},
            "name": {"type": "keyword"},
            "namespace": {"type": "keyword"},
            "template": {"type": "keyword"},
            "app": {"type": "keyword"},
            "status": {"type": "keyword"},
            "duration_seconds": {"type": "float"},
            "started_at": {"type": "date"},
            "finished_at": {"type": "date"}
        }}
    })

# Get workflows via K8s API
data = k8s_get("/apis/argoproj.io/v1alpha1/workflows?limit=500")
print(f"Total: {len(data.get('items',[]))}")

docs = []
for wf in data.get("items", []):
    st = wf.get("status", {})
    phase = st.get("phase", "")
    if phase not in ("Succeeded", "Failed", "Error"):
        continue
    started = st.get("startedAt", "")
    finished = st.get("finishedAt", "")
    if not started or not finished:
        continue
    try:
        fmt = "%Y-%m-%dT%H:%M:%SZ"
        s = datetime.strptime(started, fmt).replace(tzinfo=timezone.utc)
        f = datetime.strptime(finished, fmt).replace(tzinfo=timezone.utc)
        dur = (f - s).total_seconds()
    except:
        continue
    meta = wf["metadata"]
    tmpl = wf.get("spec", {}).get("workflowTemplateRef", {}).get("name", "") or "inline"
    labels = meta.get("labels", {})
    docs.append({
        "@timestamp": finished,
        "name": meta["name"],
        "namespace": meta.get("namespace", ""),
        "template": tmpl,
        "app": labels.get("app", ""),
        "status": phase,
        "duration_seconds": dur,
        "started_at": started,
        "finished_at": finished
    })

print(f"Completed: {len(docs)}")
if not docs:
    exit(0)

# Get existing
existing = set()
resp = es_req("POST", f"{INDEX}/_search?size=5000", {
    "_source": ["namespace", "name"],
    "query": {"match_all": {}}
})
for hit in resp.get("hits", {}).get("hits", []):
    src = hit.get("_source", {})
    existing.add(f"{src.get('namespace','')}/{src.get('name','')}")

# Push new
pushed = 0
for d in docs:
    key = f"{d['namespace']}/{d['name']}"
    if key in existing:
        continue
    r = es_req("POST", f"{INDEX}/_doc", d)
    print(f"  [{r.get('result','?')}] {key}: {d['duration_seconds']:.0f}s")
    pushed += 1

# GC: keep 10 per template
aggs = es_req("POST", f"{INDEX}/_search?size=0", {
    "aggs": {"templates": {"terms": {"field": "template", "size": 100}}}
})
for b in aggs.get("aggregations", {}).get("templates", {}).get("buckets", []):
    tmpl, total = b["key"], b["doc_count"]
    if total <= 10:
        continue
    hits = es_req("POST", f"{INDEX}/_search?size={total}", {
        "sort": [{"@timestamp": "desc"}],
        "_source": False,
        "query": {"term": {"template": tmpl}}
    }).get("hits", {}).get("hits", [])
    for h in hits[10:]:
        es_req("DELETE", f"{INDEX}/_doc/{h['_id']}")
    print(f"  GC {tmpl}: kept 10, deleted {len(hits[10:])}")

print(f"Done! Pushed {pushed} new workflows")
