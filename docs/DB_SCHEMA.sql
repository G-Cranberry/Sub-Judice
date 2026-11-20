-- BriefPrism schema. Works on SQLite and PostgreSQL (plain types only).
-- Use TEXT ids like C001/u1 so mock data and DB match 1:1.

CREATE TABLE users (
  id TEXT PRIMARY KEY,
  name TEXT NOT NULL,
  role TEXT NOT NULL CHECK (role IN ('filer','court_staff','student','lawyer','judge','notary')),
  bar_or_roll_no TEXT
);

CREATE TABLE cases (
  id TEXT PRIMARY KEY,
  case_number TEXT UNIQUE NOT NULL,
  title TEXT NOT NULL,
  case_type TEXT NOT NULL,
  summary TEXT NOT NULL,
  claim_amount INTEGER,
  deadline TEXT,
  filed_by TEXT REFERENCES users(id),
  claimant_name TEXT,
  respondent_name TEXT,
  filed_at TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'filed',
  route TEXT,                      -- PHYSICAL_COURT | AI_REVIEW | NULL until triaged
  urgency_score INTEGER, urgency_level TEXT,
  complexity_score INTEGER, complexity_level TEXT,
  triage_confidence REAL,
  hard_flags TEXT,                 -- JSON array as text
  triage_reasons TEXT,             -- JSON array as text
  override_by TEXT REFERENCES users(id),
  override_from TEXT, override_to TEXT, override_reason TEXT, override_at TEXT
);

CREATE TABLE evidence (
  id TEXT PRIMARY KEY,
  case_id TEXT NOT NULL REFERENCES cases(id),
  description TEXT,
  file_name TEXT, file_path TEXT,
  ocr_text TEXT,
  handler_alias TEXT NOT NULL,     -- public alias, e.g. IO-0417
  officer_ref_private TEXT,        -- real officer identity. NEVER returned by the API.
  attestation_status TEXT NOT NULL DEFAULT 'pending',
  attested_by TEXT REFERENCES users(id), attestation_note TEXT, attested_at TEXT,
  created_at TEXT NOT NULL
);

-- Append-only: backend must only INSERT into this table.
CREATE TABLE custody_log (
  id TEXT PRIMARY KEY,
  evidence_id TEXT NOT NULL REFERENCES evidence(id),
  action TEXT NOT NULL,            -- collected | transferred | sealed | examined | attested
  from_alias TEXT, to_alias TEXT, note TEXT,
  at TEXT NOT NULL
);

CREATE TABLE proposals (
  id TEXT PRIMARY KEY,
  case_id TEXT NOT NULL REFERENCES cases(id),
  proposed_resolution TEXT NOT NULL,
  reasoning TEXT,                  -- JSON array as text
  suggested_relief TEXT,
  confidence REAL,
  status TEXT NOT NULL DEFAULT 'pending_review',
  judge_id TEXT REFERENCES users(id), judge_decision TEXT, judge_note TEXT, signed_at TEXT,
  created_at TEXT NOT NULL
);

CREATE TABLE opinions (
  id TEXT PRIMARY KEY,
  case_id TEXT NOT NULL REFERENCES cases(id),
  author_id TEXT NOT NULL REFERENCES users(id),
  text TEXT NOT NULL,
  created_at TEXT NOT NULL
);

CREATE TABLE comments (
  id TEXT PRIMARY KEY,
  opinion_id TEXT NOT NULL REFERENCES opinions(id),
  author_id TEXT NOT NULL REFERENCES users(id),
  text TEXT NOT NULL,
  created_at TEXT NOT NULL
);

CREATE TABLE hearings (
  id TEXT PRIMARY KEY,
  case_id TEXT NOT NULL REFERENCES cases(id),
  scheduled_at TEXT NOT NULL,
  hearing_type TEXT NOT NULL,
  room TEXT
);

CREATE TABLE documents (
  id TEXT PRIMARY KEY,
  case_id TEXT NOT NULL REFERENCES cases(id),
  doc_type TEXT NOT NULL,          -- notice | summons | order_draft
  content_text TEXT NOT NULL,
  created_at TEXT NOT NULL
);
