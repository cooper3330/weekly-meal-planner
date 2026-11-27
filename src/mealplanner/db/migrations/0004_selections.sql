CREATE TABLE selections (
    id           INTEGER PRIMARY KEY,
    user_id      INTEGER NOT NULL REFERENCES users(id),
    slate_id     INTEGER NOT NULL UNIQUE REFERENCES slates(id),
    lunch_limit  INTEGER NOT NULL CHECK (lunch_limit  BETWEEN 0 AND 14),
    dinner_limit INTEGER NOT NULL CHECK (dinner_limit BETWEEN 0 AND 14),
    updated_at   TEXT    NOT NULL
);

CREATE TABLE selection_picks (
    selection_id    INTEGER NOT NULL REFERENCES selections(id),
    user_id         INTEGER NOT NULL REFERENCES users(id),
    slate_option_id INTEGER NOT NULL REFERENCES slate_options(id),
    picked_at       TEXT    NOT NULL,
    PRIMARY KEY (selection_id, slate_option_id)
);
