# protos/

The data-plane gRPC contracts (data-spine §1.7). Generated stubs are exported
from `libs/common` (`priva_common`) so every service shares one client.

Planned services: Accounts, Identities, Sessions, ChannelBinding, ScheduledJobs,
JobRuns, Audit, Config. Defined in Phase 1 alongside the data-spine service.
