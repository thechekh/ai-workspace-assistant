# Service Catalog

Core backend services, their owners, and how they talk to each other.
Synchronous calls are REST over HTTP behind the api-gateway; asynchronous
communication uses SQS queues with the transactional outbox pattern.

## api-gateway

The single public entry point. Terminates TLS, authenticates requests by
validating JWTs against auth-service keys, applies rate limits, and routes to
internal services. Owned by the platform team.

## auth-service

Handles user authentication and identity. An OIDC provider backed by
Keycloak; issues short-lived JWT access tokens and rotating refresh tokens.
Supports SSO via Google Workspace and enforces MFA for admin roles. All other
services validate tokens locally using the JWKS endpoint
`/auth/.well-known/jwks.json`. Owned by the identity team.

## billing-service

Responsible for invoice generation and subscription management. A nightly job
renders PDF invoices for all subscriptions due that day, stores them in S3,
and publishes an `invoice.created` event to the events queue. Charges are
executed through payment-adapter. Exposes REST endpoints for invoice history
and upcoming charges. Owned by the billing team.

## payment-adapter

An anti-corruption layer between our domain and external payment providers
(Stripe today, PayPal planned). billing-service asks payment-adapter to
execute a charge; the adapter translates the request into provider-specific
API calls, using an idempotency key per charge so retries never double-charge
a customer. Provider webhooks (payment succeeded, payment failed, chargeback)
arrive on `/webhooks/stripe`, are verified by signature, normalized into
domain events, and published to the events queue. Owned by the billing team.

## notification-service

Sends email and Slack notifications. It is an SQS consumer: it reads the
`notifications` queue, renders templates, and dispatches through SES and the
Slack API. Retries use the queue's redrive policy with a dead-letter queue
after five attempts. Owned by the platform team.

## search-indexer

Keeps OpenSearch in sync. Another SQS integration: it consumes domain events
(`invoice.created`, `customer.updated`, and friends) from the `search-events`
queue and updates the search indexes. Reindexing from scratch runs as a batch
job on the `jobs` node group. Owned by the platform team.

## Event flow

Producers write events to their own outbox table in the same transaction as
the state change; a relay process publishes them to SQS. Consumers are
idempotent — every event carries an `event_id`, and handlers deduplicate on
it. Queues of record: `events` (fanned out via SNS), `notifications`, and
`search-events`.
