import { Pool, PoolClient } from 'pg';

/**
 * PostgreSQL data layer for the Social Media Tracker.
 *
 * Connection is configured via the DATABASE_URL environment variable, e.g.
 *   postgresql://user:password@host:5432/smt
 * The schema is created on first query (idempotent — safe to run repeatedly).
 */

let pool: Pool | null = null;
let schemaReady: Promise<void> | null = null;

export function getPool(): Pool {
  if (!pool) {
    const connectionString = process.env.DATABASE_URL;
    if (!connectionString) {
      throw new Error('DATABASE_URL is not set');
    }
    pool = new Pool({
      connectionString,
      max: 10,
      idleTimeoutMillis: 30_000,
      connectionTimeoutMillis: 10_000,
    });
  }
  return pool;
}

const SEED_BRANDS = [
  'CarharttWIP', 'Edwin', 'Arte', 'Gramicci', 'Jason Markk',
  'Karl Kani', 'TwoJeys', 'HUF', 'AndWander', 'Thisisneverthat',
];

const SEED_CUSTOMERS = [
  'PopUp', 'Shift', 'SVN', 'Urbnlot', 'Vitruta', 'Concept Star',
  'Poison Drop', 'Brux', 'Solelab', 'Brandshop', 'Hoss KZ', 'RKN',
  'Capsul', 'Conceptstar', 'Shelflife', 'TAF', 'Wunder', 'Vegnonveg',
  'Personage', 'Bunka', 'Reserved',
];

async function initSchema(): Promise<void> {
  const p = getPool();

  await p.query(`
    CREATE TABLE IF NOT EXISTS sessions (
      id          TEXT PRIMARY KEY,
      name        TEXT NOT NULL,
      status      TEXT NOT NULL DEFAULT 'open',
      created_at  TEXT NOT NULL,
      closed_at   TEXT
    );

    CREATE TABLE IF NOT EXISTS records (
      id              TEXT PRIMARY KEY,
      session_id      TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
      customer        TEXT NOT NULL,
      brands          TEXT NOT NULL,
      num_posts       INTEGER NOT NULL,
      type            TEXT NOT NULL,
      content_type    TEXT NOT NULL,
      content_source  TEXT NOT NULL,
      date            TEXT NOT NULL,
      files           TEXT NOT NULL,
      created_at      TEXT NOT NULL
    );

    CREATE TABLE IF NOT EXISTS customers (name TEXT PRIMARY KEY);
    CREATE TABLE IF NOT EXISTS brands (name TEXT PRIMARY KEY);

    CREATE INDEX IF NOT EXISTS idx_records_session ON records(session_id);
    CREATE INDEX IF NOT EXISTS idx_records_date    ON records(date);
  `);

  // Seed lookup tables (idempotent)
  for (const brand of SEED_BRANDS) {
    await p.query('INSERT INTO brands (name) VALUES ($1) ON CONFLICT DO NOTHING', [brand]);
  }
  for (const customer of SEED_CUSTOMERS) {
    await p.query('INSERT INTO customers (name) VALUES ($1) ON CONFLICT DO NOTHING', [customer]);
  }
}

/** Ensure the schema exists. Memoised — the work runs at most once per process. */
export function ensureSchema(): Promise<void> {
  if (!schemaReady) {
    schemaReady = initSchema().catch((err) => {
      // Reset so a later request can retry if the first attempt failed.
      schemaReady = null;
      throw err;
    });
  }
  return schemaReady;
}

/** Run a query and return the rows. Ensures the schema exists first. */
export async function query<T = Record<string, unknown>>(
  text: string,
  params: unknown[] = []
): Promise<T[]> {
  await ensureSchema();
  const res = await getPool().query(text, params);
  return res.rows as T[];
}

/** Run a query and return the first row (or null). */
export async function queryOne<T = Record<string, unknown>>(
  text: string,
  params: unknown[] = []
): Promise<T | null> {
  const rows = await query<T>(text, params);
  return rows[0] ?? null;
}

/** Run several statements inside a transaction. */
export async function withTransaction<T>(
  fn: (client: PoolClient) => Promise<T>
): Promise<T> {
  await ensureSchema();
  const client = await getPool().connect();
  try {
    await client.query('BEGIN');
    const result = await fn(client);
    await client.query('COMMIT');
    return result;
  } catch (err) {
    await client.query('ROLLBACK');
    throw err;
  } finally {
    client.release();
  }
}

export interface SessionRow {
  id: string;
  name: string;
  status: 'open' | 'closed';
  created_at: string;
  closed_at: string | null;
}

export async function getOpenSession(): Promise<SessionRow | null> {
  return queryOne<SessionRow>(
    "SELECT * FROM sessions WHERE status = 'open' ORDER BY created_at DESC LIMIT 1"
  );
}

export async function assertNoOpenSession(): Promise<void> {
  const open = await getOpenSession();
  if (open) {
    throw new Error(`There is already an open session: "${open.name}". Close it first.`);
  }
}
