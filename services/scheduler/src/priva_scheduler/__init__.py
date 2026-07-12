"""Priva Cloud scheduler (Phase 4a — design docs/scheduler-implementation-design.md).

A leaderless fleet of clocks: every replica arms the SAME job set from the
dataplane (30s re-list, D6) and fires locally; the Postgres ``job_fire`` claim
(D5) makes exactly one replica own each fire. The winner never executes the
job — it wakes the account's pod (AgentTenant CR patch) and POSTs an admission
frame; the pod runs the job and writes its own outcome (D13). The scheduler
holds no state outside the DB — kill/roll replicas freely.
"""
