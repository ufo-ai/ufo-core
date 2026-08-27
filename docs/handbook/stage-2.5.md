# Core Billing, Ledger, Seats, and Balance Migrations  `stage-2.5`

This stage is shared behind-the-scenes support for billing. It is made of database migrations, which are small upgrade scripts that change how stored records are shaped while keeping existing data. Together they prepare the system to measure usage, charge for it, explain it later, and manage workspace payment rules.

The ledger usage migrations widen the billing notebook. They let ledger rows record new kinds of usage, such as data sent out, sandbox tokens, images, and videos, and they split token counts into clearer parts like input, output, cache reads, and cache writes.

The ledger metadata and export migrations add the “receipt details.” They store pricing snapshots, connect charges to workspaces, track which ledger records were exported, note BYOK exports, add faster lookup by workspace, record exact debits, and freeze per-turn billing data so history stays stable.

The seat migrations reshape how workspace membership is counted, moving from seat limits and bundled seats to a simpler model where old seat-tracking fields are removed.

The balance migrations add spend caps, prepaid workspace balances, top-up records, automatic refill settings, and verification dates for overdraft decisions.

## Sub-stages

- [Ledger Usage Dimensions and Token Accounting Migrations](stage-2.5.1.md) `stage-2.5.1` — 6 files
- [Ledger Metadata, Exports, and Billing Record Migrations](stage-2.5.2.md) `stage-2.5.2` — 7 files
- [Seat and Workspace Membership Model Migrations](stage-2.5.3.md) `stage-2.5.3` — 4 files
- [Workspace Balance, Spend Cap, and Top-Up Migrations](stage-2.5.4.md) `stage-2.5.4` — 4 files
