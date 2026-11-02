CREATE TABLE slates (
    id              INTEGER PRIMARY KEY,
    user_id         INTEGER NOT NULL REFERENCES users(id),
    week_start      TEXT    NOT NULL,
    created_at      TEXT    NOT NULL,
    profile_version INTEGER NOT NULL,
    UNIQUE (user_id, week_start)
);

CREATE TABLE slate_options (
    id          INTEGER PRIMARY KEY,
    slate_id    INTEGER NOT NULL REFERENCES slates(id),
    user_id     INTEGER NOT NULL REFERENCES users(id),
    meal_type   TEXT    NOT NULL CHECK (meal_type IN ('lunch', 'dinner')),
    position    INTEGER NOT NULL CHECK (position >= 1),
    servings    INTEGER NOT NULL CHECK (servings >= 1),
    data        TEXT    NOT NULL,
    created_at  TEXT    NOT NULL,
    replaced_at TEXT,
    replaced_by INTEGER REFERENCES slate_options(id)
);

CREATE UNIQUE INDEX slate_options_active_slot
    ON slate_options (slate_id, meal_type, position) WHERE replaced_at IS NULL;
