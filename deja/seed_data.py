"""Synthetic but realistic incident history for a mid-size e-commerce platform,
plus live alert scenarios for the demo. Names, services and error strings are
modelled on real-world postmortems."""
from __future__ import annotations

TEAM = ["Priya Raman", "Marcus Chen", "Aisha Bello", "Tomás Ortega", "Kenji Watanabe", "Sara Lindqvist"]

HISTORY: list[dict] = [
    {
        "id": "INC-2031", "service": "checkout-api", "severity": "SEV2", "started_at": "2026-04-08T02:07:00Z",
        "title": "Checkout p99 latency 6s, HikariPool connection timeouts",
        "symptoms": "p99 latency jumped from 280ms to 6.1s, 5xx rate 9%. Logs: 'HikariPool-1 - Connection is not available, request timed out after 30000ms'. Postgres primary CPU 97%.",
        "root_cause": "The nightly analytics `bulk-export` cron (02:00 UTC) was pointed at the Postgres PRIMARY instead of the read replica after a config change; its long-running scans starved checkout of connections.",
        "fix": "Killed the bulk-export job (`kubectl -n analytics delete job bulk-export-<date>`), repointed it at the read replica (PR #4412). Latency recovered within 3 minutes.",
        "failed": ["Raised Hikari maximumPoolSize 20 -> 50: made it worse, Postgres hit max_connections", "Rolling restart of checkout pods: relief for ~10 minutes, then relapse"],
        "resolved_by": "Priya Raman", "ttr_minutes": 94,
    },
    {
        "id": "INC-2047", "service": "payments-gateway", "severity": "SEV1", "started_at": "2026-04-22T14:31:00Z",
        "title": "Stripe webhooks failing signature verification",
        "symptoms": "100% of Stripe webhooks rejected with 'No signatures found matching the expected signature for payload'. Orders stuck in PAYMENT_PENDING.",
        "root_cause": "Stripe webhook signing secret was rotated in the Stripe dashboard but STRIPE_WEBHOOK_SECRET in Vault was not updated.",
        "fix": "Updated `secret/payments/stripe_webhook` in Vault, restarted payments-gateway, replayed failed events from the Stripe dashboard.",
        "failed": ["Rolled back the 14:05 payments-gateway deploy: no effect, the deploy was unrelated"],
        "resolved_by": "Marcus Chen", "ttr_minutes": 41,
    },
    {
        "id": "INC-2058", "service": "image-resizer", "severity": "SEV3", "started_at": "2026-05-03T09:12:00Z",
        "title": "image-resizer pods OOMKilled / CrashLoopBackOff",
        "symptoms": "Pods OOMKilled every 2-4 minutes, CrashLoopBackOff, product thumbnails returning 503.",
        "root_cause": "libvips operation cache is unbounded by default; tenant `pixelforge` bulk-uploaded 60MP TIFFs which blew the cache.",
        "fix": "Set env VIPS_CACHE_MAX=100 and VIPS_CONCURRENCY=2, added 25MP upload limit at the API edge.",
        "failed": ["Raised memory limit 1Gi -> 2Gi: only delayed the OOM by ~6 minutes"],
        "resolved_by": "Aisha Bello", "ttr_minutes": 130,
    },
    {
        "id": "INC-2064", "service": "checkout-api", "severity": "SEV2", "started_at": "2026-05-14T02:04:00Z",
        "title": "Checkout connection pool exhaustion (again), escalated to SEV1 by DB failover",
        "symptoms": "Same HikariPool 'Connection is not available' errors, p99 5.4s, starting just after 02:00 UTC.",
        "root_cause": "bulk-export regressed onto the PRIMARY again: a Helm values override in the `analytics-jobs` chart reset DB_HOST.",
        "fix": "Killed bulk-export job, pinned DB_HOST to the replica in the analytics-jobs chart and added a CI check that fails if it targets the primary.",
        "failed": ["Manual Postgres primary failover: caused a 4-minute FULL checkout outage (escalated to SEV1) and did not fix anything"],
        "resolved_by": "Tomás Ortega", "ttr_minutes": 71,
    },
    {
        "id": "INC-2079", "service": "search-indexer", "severity": "SEV3", "started_at": "2026-06-02T11:40:00Z",
        "title": "Kafka consumer lag 2.3M on catalog-updates",
        "symptoms": "Consumer group `search-indexer` lag climbed to 2.3M messages; constant 'Attempt to heartbeat failed since group is rebalancing'.",
        "root_cause": "Slow enrichment batches exceeded max.poll.interval.ms (300s) causing a rebalance storm.",
        "fix": "Reduced max.poll.records 500 -> 100; lag drained in 25 minutes.",
        "failed": ["Scaled consumers 6 -> 12: more rebalancing, lag grew faster"],
        "resolved_by": "Kenji Watanabe", "ttr_minutes": 58,
    },
    {
        "id": "INC-2085", "service": "auth-service", "severity": "SEV1", "started_at": "2026-06-11T07:55:00Z",
        "title": "38% of logins failing: x509 certificate has expired",
        "symptoms": "Login error rate 38%. Logs: 'x509: certificate has expired or is not yet valid' on mTLS calls to identity-provider.",
        "root_cause": "cert-manager renewal for `auth-idp-client-cert` failed silently because the ClusterIssuer hit ACME rate limits.",
        "fix": "`cmctl renew auth-idp-client-cert -n auth`, then added an alert on certificates expiring in < 14 days.",
        "failed": [],
        "resolved_by": "Sara Lindqvist", "ttr_minutes": 47,
    },
    {
        "id": "INC-2093", "service": "notification-worker", "severity": "SEV3", "started_at": "2026-06-25T18:20:00Z",
        "title": "Customers receiving duplicate order emails",
        "symptoms": "Duplicate order-confirmation emails (up to 4x). Redis `evicted_keys` spiking.",
        "root_cause": "Redis maxmemory-policy allkeys-lru was evicting idempotency keys under memory pressure.",
        "fix": "Moved idempotency keys to a dedicated Redis with maxmemory-policy noeviction.",
        "failed": ["Flushing the email queue: lost 3,000 legitimate emails that had to be re-sent"],
        "resolved_by": "Marcus Chen", "ttr_minutes": 88,
    },
    {
        "id": "INC-2101", "service": "checkout-api", "severity": "SEV3", "started_at": "2026-07-09T16:02:00Z",
        "title": "Intermittent 502s from ingress after node pool upgrade",
        "symptoms": "0.8% of checkout requests return 502 from the ALB, no errors in app logs.",
        "root_cause": "Keepalive mismatch: ALB idle timeout 60s vs nginx-ingress upstream keepalive 75s, so ALB reused closed connections.",
        "fix": "Set nginx-ingress `upstream-keepalive-timeout: 55`.",
        "failed": ["Rolled back the node pool: no effect"],
        "resolved_by": "Tomás Ortega", "ttr_minutes": 63,
    },
    {
        "id": "INC-2110", "service": "payments-gateway", "severity": "SEV2", "started_at": "2026-07-21T19:45:00Z",
        "title": "Timeouts calling acquiring bank under peak load",
        "symptoms": "p99 to `api.acquirer.internal` 8s, 'dial tcp: lookup api.acquirer.internal: i/o timeout'. CoreDNS CPU saturated.",
        "root_cause": "Pod dnsConfig ndots:5 made every external lookup try 5 search domains first, overloading CoreDNS at peak.",
        "fix": "Set `dnsConfig.options ndots: 2` on payments-gateway and used FQDNs with trailing dot.",
        "failed": ["Scaled payments-gateway 8 -> 16 pods: doubled DNS load, made it worse"],
        "resolved_by": "Kenji Watanabe", "ttr_minutes": 102,
    },
    {
        "id": "INC-2118", "service": "image-resizer", "severity": "SEV3", "started_at": "2026-08-05T10:03:00Z",
        "title": "image-resizer OOMKilled again",
        "symptoms": "OOMKilled pods, CrashLoopBackOff, thumbnails 503.",
        "root_cause": "VIPS_CACHE_MAX was dropped during a Dockerfile refactor (PR #5120); pixelforge uploads again.",
        "fix": "Restored VIPS_CACHE_MAX=100, moved it into the Helm chart so it cannot be lost in image refactors.",
        "failed": [],
        "resolved_by": "Aisha Bello", "ttr_minutes": 19,
    },
    {
        "id": "INC-2124", "service": "search-indexer", "severity": "SEV2", "started_at": "2026-08-19T03:30:00Z",
        "title": "Elasticsearch indices read-only, flood-stage watermark",
        "symptoms": "'cluster_block_exception: index [catalog-v42] blocked by: [TOO_MANY_REQUESTS/12/disk usage exceeded flood-stage watermark]'.",
        "root_cause": "Old catalog-v* indices never deleted; disk 95%.",
        "fix": "Deleted catalog-v30..v39, cleared `index.blocks.read_only_allow_delete`, added ILM policy keeping last 3 indices.",
        "failed": [],
        "resolved_by": "Sara Lindqvist", "ttr_minutes": 76,
    },
    {
        "id": "INC-2131", "service": "checkout-api", "severity": "SEV2", "started_at": "2026-09-02T13:15:00Z",
        "title": "Checkout latency after enabling recs_v2_inline flag",
        "symptoms": "p99 latency 3.9s right after the `recs_v2_inline` LaunchDarkly flag went to 100%.",
        "root_cause": "recs_v2_inline made N+1 calls to recommendations-svc per cart item.",
        "fix": "Turned the flag off in LaunchDarkly; latency recovered in 60s.",
        "failed": [],
        "resolved_by": "Priya Raman", "ttr_minutes": 17,
    },
]

TEAM_RULES = [
    ("No primary failover for pool exhaustion",
     "Never recommend a Postgres primary failover for connection-pool exhaustion. It caused a full checkout outage in INC-2064."),
]


def postmortem_text(inc: dict) -> str:
    failed = "; ".join(inc.get("failed") or []) or "none recorded"
    return (
        f"Incident {inc['id']} ({inc['severity']}) on service {inc['service']} started {inc['started_at']}: {inc['title']}.\n"
        f"Symptoms: {inc['symptoms']}\n"
        f"Root cause: {inc['root_cause']}\n"
        f"Fix that worked: {inc['fix']}\n"
        f"Attempts that did NOT work: {failed}\n"
        f"Resolved by {inc['resolved_by']} in {inc['ttr_minutes']} minutes."
    )


def postmortem_metadata(inc: dict) -> dict[str, str]:
    return {
        "incident_id": inc["id"], "service": inc["service"], "severity": inc["severity"],
        "root_cause": inc["root_cause"], "fix": inc["fix"],
        "failed": " | ".join(inc.get("failed") or []), "resolved_by": inc["resolved_by"],
        "ttr_minutes": str(inc["ttr_minutes"]), "title": inc["title"],
    }


# Live scenarios. `resolution` pre-fills the resolve form so a demo takes seconds.
SCENARIOS: list[dict] = [
    {
        "key": "checkout-pool",
        "label": "Checkout latency at 02:06 UTC",
        "service": "checkout-api", "severity": "SEV2", "hour": "02:06",
        "title": "checkout-api p99 latency 5.8s, 5xx 11%",
        "alert": "[PagerDuty] checkout-api: p99 latency 5.8s (SLO 400ms), 5xx rate 11%. Started 02:06 UTC.",
        "logs": [
            "02:06:12 WARN  HikariPool-1 - Connection is not available, request timed out after 30000ms",
            "02:06:13 ERROR o.h.e.j.s.SqlExceptionHelper - Unable to acquire JDBC Connection",
            "02:06:40 INFO  pg_stat_activity: 212 active, 41 state='active' query LIKE 'COPY (SELECT%'",
        ],
        "resolution": {
            "root_cause": "bulk-export cron hit the Postgres primary again: new `analytics-jobs-v2` chart doesn't inherit the CI guard on DB_HOST.",
            "fix": "Killed bulk-export job, pinned DB_HOST to replica in analytics-jobs-v2, extended the CI check to the new chart.",
            "failed": "", "resolved_by": "Priya Raman", "ttr_minutes": 9,
        },
    },
    {
        "key": "resizer-oom",
        "label": "image-resizer crash loop",
        "service": "image-resizer", "severity": "SEV3", "hour": "10:40",
        "title": "image-resizer CrashLoopBackOff, OOMKilled",
        "alert": "[Alertmanager] KubePodCrashLooping image-resizer-7c9f (OOMKilled, exit 137). Thumbnail 503 rate 22%.",
        "logs": [
            "Last State: Terminated  Reason: OOMKilled  Exit Code: 137",
            "vips: resize 7952x7952 tiff from tenant=pixelforge",
            "container memory 1.98Gi / limit 2Gi",
        ],
        "resolution": {
            "root_cause": "New tenant `lumagrid` uploads 80MP PNGs; the 25MP edge limit only covered JPEG/TIFF.",
            "fix": "Extended the 25MP limit to all formats at the API edge; VIPS_CACHE_MAX was still in place.",
            "failed": "", "resolved_by": "Aisha Bello", "ttr_minutes": 14,
        },
    },
    {
        "key": "payments-dns",
        "label": "Payments timeouts at peak",
        "service": "payments-gateway", "severity": "SEV2", "hour": "20:10",
        "title": "payments-gateway timeouts to acquirer",
        "alert": "[Datadog] payments-gateway: authorization p99 7.2s, 3.1% of card payments timing out.",
        "logs": [
            "dial tcp: lookup api.acquirer.internal on 10.96.0.10:53: read udp: i/o timeout",
            "coredns-5d78c9869d CPU throttled 94%",
            "HPA scaled payments-gateway 8 -> 14",
        ],
        "resolution": {
            "root_cause": "New `fraud-score` sidecar was deployed without the ndots:2 dnsConfig, recreating the CoreDNS overload.",
            "fix": "Added ndots:2 to the pod template (covers sidecars too), scaled HPA back down.",
            "failed": "HPA scale-out made DNS load worse, again", "resolved_by": "Kenji Watanabe", "ttr_minutes": 21,
        },
    },
    {
        "key": "ledger-wal",
        "label": "Brand-new failure: ledger WAL disk",
        "service": "ledger-service", "severity": "SEV2", "hour": "04:22",
        "title": "ledger-service writes failing, disk full",
        "alert": "[PagerDuty] ledger-service: write error rate 100%. Postgres 'No space left on device'.",
        "logs": [
            "PANIC: could not write to file \"pg_wal/xlogtemp.1123\": No space left on device",
            "replication slot 'debezium_ledger' inactive since 2026-09-27 21:14, restart_lsn 3A/9F000028",
            "/var/lib/postgresql 100% used",
        ],
        "resolution": {
            "root_cause": "Inactive Debezium replication slot `debezium_ledger` retained WAL after the CDC connector crashed at 21:14, filling the disk.",
            "fix": "Restarted the Debezium connector so the slot advanced, then set max_slot_wal_keep_size=20GB and alerted on inactive slots.",
            "failed": "Expanding the PVC: took 25 min and only bought time",
            "resolved_by": "Sara Lindqvist", "ttr_minutes": 66,
        },
    },
]
