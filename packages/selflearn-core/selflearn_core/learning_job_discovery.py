"""Bounded, domain-neutral discovery metadata. Discovery grants no ownership."""
import datetime as dt

from . import learning_jobs as jobs

MAX_LIMIT = 16
KEYS = {"version", "authority_id", "namespace", "operation", "code_id", "policy_object_id", "observed_utc", "job_ids"}


def scope(authority_id, namespace, operation, code_id=None, policy_object_id=None, limit=MAX_LIMIT):
    authority_id = jobs.digest(authority_id)
    namespace = jobs.namespace(namespace)
    operation = jobs.name(operation)
    if code_id is not None:
        jobs.digest(code_id)
    if policy_object_id is not None:
        jobs.digest(policy_object_id)
    jobs.need(type(limit) is int and 1 <= limit <= MAX_LIMIT, "invalid_job_discovery_limit")
    return authority_id, namespace, operation, code_id, policy_object_id, limit


def validate(value, *, authority_id, namespace, operation, code_id=None, policy_object_id=None, limit=MAX_LIMIT):
    authority_id, namespace, operation, code_id, policy_object_id, limit = scope(
        authority_id, namespace, operation, code_id, policy_object_id, limit)
    value = jobs.snapshot(value)
    jobs.need(type(value) is dict and set(value) == KEYS, "invalid_job_discovery")
    jobs.need(type(value["version"]) is int and value["version"] == 1
        and value["authority_id"] == authority_id and jobs.canonical(value["namespace"]) == jobs.canonical(namespace)
        and value["operation"] == operation and value["code_id"] == code_id
        and value["policy_object_id"] == policy_object_id, "job_discovery_scope_mismatch")
    jobs.clock(value["observed_utc"])
    ids = value["job_ids"]
    jobs.need(type(ids) is list and len(ids) <= limit and len(set(ids)) == len(ids), "invalid_job_discovery_ids")
    for job_id in ids:
        jobs.digest(job_id)
    return value


def clock_microseconds(value):
    """Exact timestamp comparison for SQLite, avoiding julianday rounding."""
    if value is None:
        return None
    stamp = jobs.clock(value) - dt.datetime(1970, 1, 1, tzinfo=dt.timezone.utc)
    return (stamp.days * 86400 + stamp.seconds) * 1_000_000 + stamp.microseconds
