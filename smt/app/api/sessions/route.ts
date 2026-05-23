import { NextRequest, NextResponse } from 'next/server';
import { v4 as uuidv4 } from 'uuid';
import { query, queryOne, getOpenSession, SessionRow } from '@/lib/db';

export const dynamic = 'force-dynamic';

async function withStats(session: SessionRow) {
  const records = await query<{ id: string; files: string }>(
    'SELECT id, files FROM records WHERE session_id = $1',
    [session.id]
  );
  const file_count = records.reduce((sum, r) => {
    try { return sum + (JSON.parse(r.files) as string[]).length; } catch { return sum; }
  }, 0);
  return { ...session, record_count: records.length, file_count };
}

export async function GET() {
  try {
    const rows = await query<SessionRow>(
      'SELECT * FROM sessions ORDER BY created_at DESC'
    );
    const withStatsRows = await Promise.all(rows.map(withStats));
    return NextResponse.json(withStatsRows);
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

    const existing = await getOpenSession();
    if (existing) {
      return NextResponse.json(
        { error: `An open session already exists: "${existing.name}". Close it before starting a new one.` },
        { status: 409 }
      );
    }

    const id = uuidv4();
    const now = new Date().toISOString();
    await query(
      "INSERT INTO sessions (id, name, status, created_at, closed_at) VALUES ($1, $2, 'open', $3, NULL)",
      [id, name, now]
    );

    const session = await queryOne<SessionRow>('SELECT * FROM sessions WHERE id = $1', [id]);
    return NextResponse.json(await withStats(session!), { status: 201 });
  } catch (error) {
    console.error('Session create error:', error);
    return NextResponse.json({ error: 'Failed to create session', details: String(error) }, { status: 500 });
  }
}
