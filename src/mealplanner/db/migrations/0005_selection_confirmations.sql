CREATE TABLE selection_confirmations (
    id           INTEGER PRIMARY KEY,
    selection_id INTEGER NOT NULL REFERENCES selections(id),
    user_id      INTEGER NOT NULL REFERENCES users(id),
    confirmed_at TEXT    NOT NULL,
    reopened_at  TEXT
);

CREATE TABLE confirmation_options (
    confirmation_id INTEGER NOT NULL REFERENCES selection_confirmations(id),
    user_id         INTEGER NOT NULL REFERENCES users(id),
    slate_option_id INTEGER NOT NULL REFERENCES slate_options(id),
    outcome         TEXT    NOT NULL CHECK (outcome IN ('picked', 'skipped')),
    PRIMARY KEY (confirmation_id, slate_option_id)
);
