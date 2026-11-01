CREATE TABLE profile_versions (
    id INTEGER PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES users(id),
    version INTEGER NOT NULL CHECK (version >= 1),
    created_at TEXT NOT NULL,
    data TEXT NOT NULL,
    UNIQUE (user_id, version)
);
