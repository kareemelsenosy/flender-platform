import Database from 'better-sqlite3';
import path from 'path';
import fs from 'fs';

const DATA_DIR = path.join(process.cwd(), 'data');
const DB_PATH = path.join(DATA_DIR, 'tracker.db');

if (!fs.existsSync(DATA_DIR)) {
  fs.mkdirSync(DATA_DIR, { recursive: true });
}

let db: Database.Database;

function getDb(): Database.Database {
  if (!db) {
    try {
      db = new Database(DB_PATH);
      db.pragma('journal_mode = WAL');
      db.pragma('foreign_keys = ON');
      initializeSchema();
    } catch (error) {
      console.error('Failed to initialize database:', error);
      throw error;
    }
  }
  return db;
}

function initializeSchema() {
  const database = db;

  database.exec(`
    CREATE TABLE IF NOT EXISTS sessions (
      id TEXT PRIMARY KEY,
      name TEXT NOT NULL,
      status TEXT NOT NULL DEFAULT 'open',
      created_at TEXT NOT NULL,
      closed_at TEXT
    );

    CREATE TABLE IF NOT EXISTS records (
      id TEXT PRIMARY KEY,
      session_id TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
      customer TEXT NOT NULL,
      brands TEXT NOT NULL,
      num_posts INTEGER NOT NULL,
      type TEXT NOT NULL,
      content_type TEXT NOT NULL,
      content_source TEXT NOT NULL,
      date TEXT NOT NULL,
      files TEXT NOT NULL,
      created_at TEXT NOT NULL
    );

    CREATE TABLE IF NOT EXISTS customers (name TEXT PRIMARY KEY);
    CREATE TABLE IF NOT EXISTS brands (name TEXT PRIMARY KEY);

    CREATE INDEX IF NOT EXISTS idx_records_session ON records(session_id);
    CREATE INDEX IF NOT EXISTS idx_records_date ON records(date);
  `);

  const seedBrands = [
    'CarharttWIP', 'Edwin', 'Arte', 'Gramicci', 'Jason Markk',
    'Karl Kani', 'TwoJeys', 'HUF', 'AndWander', 'Thisisneverthat'
  ];
  const insertBrand = database.prepare('INSERT OR IGNORE INTO brands (name) VALUES (?)');
  for (const brand of seedBrands) insertBrand.run(brand);

  const seedCustomers = [
    'PopUp', 'Shift', 'SVN', 'Urbnlot', 'Vitruta', 'Concept Star',
    'Poison Drop', 'Brux', 'Solelab', 'Brandshop', 'Hoss KZ', 'RKN',
    'Capsul', 'Conceptstar', 'Shelflife', 'TAF', 'Wunder', 'Vegnonveg',
    'Personage', 'Bunka', 'Reserved'
  ];
  const insertCustomer = database.prepare('INSERT OR IGNORE INTO customers (name) VALUES (?)');
  for (const customer of seedCustomers) insertCustomer.run(customer);
}

export interface SessionRow {
  id: string;
  name: string;
  status: 'open' | 'closed';
  created_at: string;
  closed_at: string | null;
}

export function getOpenSession(): SessionRow | null {
  const database = getDb();
  const row = database
    .prepare("SELECT * FROM sessions WHERE status = 'open' ORDER BY created_at DESC LIMIT 1")
    .get() as SessionRow | undefined;
  return row ?? null;
}

export function assertNoOpenSession() {
  const open = getOpenSession();
  if (open) {
    throw new Error(`There is already an open session: "${open.name}". Close it first.`);
  }
}

export { getDb };
export type { Database };
