# PostgreSQL

Relational database I use for most side projects.

## Notes
- Use `jsonb` for semi-structured data; index with GIN when querying keys.
- Connection pooling via PgBouncer when many short-lived connections.
- Prefer `timestamptz` over `timestamp`.
