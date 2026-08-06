# Incident Response

## Severity levels

| Level | Definition                          | Examples                       | Response                                     |
| ----- | ----------------------------------- | ------------------------------ | -------------------------------------------- |
| SEV1  | Customer-facing outage or data loss | API down, payments failing     | Page on-call immediately, commander assigned |
| SEV2  | Degraded service, workaround exists | Elevated errors, delayed jobs  | On-call engaged within 30 minutes            |
| SEV3  | Minor impact, no customer harm      | Flaky alert, single-tenant bug | Ticket, handled in normal priority           |

## On-call

Each team runs a weekly on-call rotation in PagerDuty. The on-call engineer
acknowledges pages within five minutes for SEV1. Escalation after 15
unacknowledged minutes goes to the team lead, then the engineering manager.

## During a SEV1 incident

1. Acknowledge the page and open an incident channel `#inc-<date>-<slug>`.
2. Assign roles: incident commander, operations lead, communications lead.
3. Update the status page within 15 minutes, then hourly.
4. Mitigate first (rollback, feature flag off, scale up); root-cause later.
5. Keep a timeline in the channel — it becomes the postmortem input.

## Postmortems

Every SEV1 and SEV2 gets a blameless postmortem written within five business
days. The template covers timeline, impact, root cause, detection gaps, and
action items with owners and due dates. Action items are tracked in Jira and
reviewed in the monthly reliability meeting.
