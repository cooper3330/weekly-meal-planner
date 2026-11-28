CREATE TABLE recipes (
    id              INTEGER PRIMARY KEY,
    user_id         INTEGER NOT NULL REFERENCES users(id),
    slate_option_id INTEGER NOT NULL UNIQUE REFERENCES slate_options(id),
    data            TEXT    NOT NULL,
    generated_at    TEXT    NOT NULL
);
