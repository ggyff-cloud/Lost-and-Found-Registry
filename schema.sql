-- Lost & Found Claim Registry - schema
-- The claim invariant is enforced here, not in the browser:
--   claims has UNIQUE(item_id), so an item can carry at most one claim row.

PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS users (
    id   TEXT PRIMARY KEY,
    role TEXT NOT NULL CHECK (role IN ('reporter', 'claimant', 'coordinator'))
);

CREATE TABLE IF NOT EXISTS items (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    name        TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    place       TEXT NOT NULL,
    occurred_at TEXT NOT NULL,
    status      TEXT NOT NULL DEFAULT 'Unclaimed'
                CHECK (status IN ('Unclaimed', 'Claimed', 'Returned')),
    reporter_id TEXT NOT NULL REFERENCES users(id),
    created_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS claims (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    item_id     INTEGER NOT NULL REFERENCES items(id),
    claimant_id TEXT NOT NULL REFERENCES users(id),
    created_at  TEXT NOT NULL,
    UNIQUE (item_id)
);

CREATE INDEX IF NOT EXISTS idx_items_status ON items(status);
