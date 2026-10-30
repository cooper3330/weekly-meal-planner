CREATE TABLE users (
    id INTEGER PRIMARY KEY,
    tailscale_login TEXT UNIQUE,
    display_name TEXT,
    created_at TEXT NOT NULL
);

INSERT INTO users (id, created_at) VALUES (1, strftime('%Y-%m-%dT%H:%M:%SZ', 'now'));
