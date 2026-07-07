-- DepCover schema for Butterbase (Postgres). Column names match the JSON row keys
-- the live adapter writes (model_dump(mode="json")). jsonb for nested/tuple fields.

CREATE TABLE IF NOT EXISTS repos (
  id            text PRIMARY KEY,
  url           text NOT NULL,
  owner         text NOT NULL,
  registered_at timestamptz NOT NULL
);

CREATE TABLE IF NOT EXISTS underwriting_reports (
  repo_id             text PRIMARY KEY,
  id                  text NOT NULL,
  target_package      text NOT NULL,
  failing_tests       jsonb NOT NULL,
  affected_file_count integer NOT NULL,
  centrality          jsonb NOT NULL,
  graph_layout        jsonb NOT NULL,
  warnings            jsonb NOT NULL,
  created_at          timestamptz NOT NULL
);

CREATE TABLE IF NOT EXISTS incidents (
  id              text PRIMARY KEY,
  repo_id         text NOT NULL,
  trigger_type    text NOT NULL,
  chosen_strategy text,
  status          text NOT NULL,
  created_at      timestamptz NOT NULL,
  updated_at      timestamptz NOT NULL
);

CREATE TABLE IF NOT EXISTS transplants (
  id            text PRIMARY KEY,
  incident_id   text NOT NULL,
  surgery_plan  jsonb NOT NULL,
  diff          jsonb NOT NULL,
  evidence      jsonb NOT NULL,
  consensus     jsonb NOT NULL
);

CREATE TABLE IF NOT EXISTS judge_verdicts (
  transplant_id text NOT NULL,
  judge_name    text NOT NULL,
  verdict       text NOT NULL,
  rationale     text NOT NULL,
  PRIMARY KEY (transplant_id, judge_name)
);

CREATE TABLE IF NOT EXISTS reviews (
  transplant_id text NOT NULL,
  user_id       text NOT NULL,
  decision      text NOT NULL,
  per_file      jsonb NOT NULL,
  reason        text,
  PRIMARY KEY (transplant_id, user_id)
);

CREATE TABLE IF NOT EXISTS recipes (
  library_pair   text PRIMARY KEY,
  id             text NOT NULL,
  wrapper_pattern text NOT NULL,
  known_gaps     jsonb NOT NULL,
  confirmed_fix  text NOT NULL
);
