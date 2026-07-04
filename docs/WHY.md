# Why Chronos exists

Every choice below was made against a concrete alternative, and I'll defend
each the way I would in review: name the failure the alternative introduces,
then name the cost we accepted instead.

**Why PostgreSQL over a Redis queue.** The moment jobs live in Redis and
their meaning lives in Postgres, you have two systems that must agree and no
transaction that spans them. `LPUSH` after commit can be lost; `LPUSH` before
commit creates jobs that reference rows that don't exist. Fixing that
properly means building an outbox and a relay — more machinery than the
scheduler itself. In Postgres, enqueue, state transitions, and the attempt
audit trail commit atomically, and `FOR UPDATE SKIP LOCKED` gives
contention-free exactly-once *claiming* for free. The cost we accepted is a
throughput ceiling in the low thousands of claims per second and polling
latency we mask with a wake channel. Our jobs take seconds, not
microseconds; we are nowhere near that ceiling, and the moment we are,
the migration path (sharding, partitioning, then broker+outbox) is written
down. You don't buy Kafka's problems before you have Kafka's traffic.

**Why at-least-once over exactly-once.** Because exactly-once *execution* is
not a design option — a worker can always die after sending the email and
before committing `succeeded`, and no protocol removes that window; it can
only move it. Systems that claim exactly-once either mean "within our own
transactional domain" or they mean at-most-once, which silently loses work —
strictly worse for every workload we run. We chose the honest contract:
duplicates are possible, rare, and bounded to one window, and we hand every
handler the `job_id` as its deduplication key. Enqueue, meanwhile, *is*
exactly-once, because there the side effect and the ack share a database.

**Why leases and heartbeats.** Failure detection has to come from somewhere,
and the alternatives are worse: OS-level liveness (doesn't survive network
partitions), a lock service (a new stateful dependency doing what a
timestamp column does), or nothing (jobs stuck in `running` forever). A
lease is a promise with an expiry; heartbeats renew it; a dead worker stops
promising and the reaper collects. The 3:1 lease-to-heartbeat ratio means
one GC pause or dropped packet never triggers a false reclaim — three
consecutive silences is evidence, one is noise. And the lease clock is
Postgres's `now()`, never a worker's, because two containers disagreeing
about the time must never be able to disagree about ownership.

**Why Redis is not on the correctness path.** Because it doesn't need to be,
and anything not on the correctness path can't take correctness down with
it. Redis carries a wake signal — "a job may be ready, claim now instead of
in two seconds" — published only after commit, and a stats cache. Every
Redis call converts failure into a logged fallback: publishers drop, workers
poll. Redis down costs us two seconds of latency and nothing else. That's a
designed property, and the readiness probe encodes it: Postgres gates,
Redis informs.

**Why the reaper uses advisory locks.** The reaper must run somewhere, and a
dedicated process is a single point of failure with a leader-election
problem attached. Instead every worker hosts the sweep and
`pg_try_advisory_xact_lock` admits one per tick. Failover is implicit — any
survivor wins the next tick, five seconds later. The transaction-scoped
variant is the point: a reaper that crashes mid-sweep releases the lock *by
crashing*. No sessions to expire, no locks to leak, no new infrastructure —
the coordination problem is solved by the database we already trust.

**Why graceful shutdown refunds attempts.** Retry budget exists to measure
one thing: evidence that the *job* is bad. A lease expiry is such evidence —
the job may have killed its worker, and a poison pill must converge to the
DLQ rather than tour the fleet. A deploy is not evidence of anything except
that we shipped code. If drains consumed attempts, every release would push
long-running jobs toward the dead letter queue in proportion to our deploy
frequency — reliability degraded by the act of improving the system. So a
drained job goes back to `pending` immediately, attempt refunded, and the
audit trail records `aborted`, not `lost`, so an operator can tell an
operational interruption from a death at a glance.

The common thread: one source of truth, one recovery mechanism, one clock —
and every optimization (Redis, graceful drain) allowed to fail *into* the
guarantee, never past it.
