import { NextRequest, NextResponse } from 'next/server';
import { v4 as uuidv4 } from 'uuid';
import { getDb, getOpenSession, SessionRow } from '@/lib/db';

export const dynamic = 'force-dynamic';

interface RecordRow {
  id: string;
  session_id: string;
  files: string;
}

function withStats(session: SessionRow) {
  const db = getDb();
  const records = db
    .prepare('SELECT id, files FROM records WHERE session_id = ?')
    .all(session.id) as { id: string; files: string }[];
  const file_count = records.reduce((sum, r) => {
    try { return sum + (JSON.parse(r.files) as string[]).length; } catch { return sum; }
  }, 0);
  return { ...session, record_count: records.length, file_count };
}

export async function GET() {
  try {
    const db = getDb();
    const rows = db
      .prepare('SELECT * FROM sessions ORDER BY created_at DESC')
      .all() as SessionRow[];
    return NextResponse.json(rows.map(withStats));
  } catch (error) {
    console.error('Sessions list error:', error);
    return NextResponse.json({ error: 'Failed to list sessions' }, { status: 500 });
  }
}

export async function POST(request: NextRequest) {
  try {
    const body = await request.json();
    const name = (body?.name ?? '').toString().trim();
    if (!name) {
      return NextResponse.json({ error: 'Session name is required' }, { status: 400 });
    }

    const existing = getOpenSession();
    if (existing) {
      return NextResponse.json(
        { error: `An open session already exists: "${existing.name}". Close it before starting a new one.` },
        { status: 409 }
      );
    }

    const id = uuidv4();
    const now = new Date().toISOString();
    const db = getDb();
    db.prepare(
      "INSERT INTO sessions (id, name, status, created_at, closed_at) VALUES (?, ?, 'open', ?, NULL)"
    ).run(id, name, now);

    const session = db.prepare('SELECT * FROM sessions WHERE id = ?').get(id) as SessionRow;
    return NextResponse.json(withStats(session), { status: 201 });
  } catch (error) {
    console.error('Session create error:', error);
    return NextResponse.json({ error: 'Failed to create session', details: String(error) }, { status: 500 });
  }
}
