import { NextRequest, NextResponse } from 'next/server';
import fs from 'fs';
import path from 'path';
import { getDb, SessionRow } from '@/lib/db';

export const dynamic = 'force-dynamic';

const UPLOADS_DIR = path.join(process.cwd(), 'uploads');

export async function GET(_req: NextRequest, { params }: { params: { id: string } }) {
  try {
    const db = getDb();
    const session = db.prepare('SELECT * FROM sessions WHERE id = ?').get(params.id) as SessionRow | undefined;
    if (!session) return NextResponse.json({ error: 'Session not found' }, { status: 404 });

    const records = db
      .prepare('SELECT * FROM records WHERE session_id = ? ORDER BY created_at DESC')
      .all(params.id);

    return NextResponse.json({ ...session, records });
  } catch (error) {
    console.error('Session get error:', error);
    return NextResponse.json({ error: 'Failed' }, { status: 500 });
  }
}

export async function PATCH(_req: NextRequest, { params }: { params: { id: string } }) {
  try {
    const db = getDb();
    const session = db.prepare('SELECT * FROM sessions WHERE id = ?').get(params.id) as SessionRow | undefined;
    if (!session) return NextResponse.json({ error: 'Session not found' }, { status: 404 });
    if (session.status === 'closed') {
      return NextResponse.json({ error: 'Session already closed' }, { status: 400 });
    }
    const now = new Date().toISOString();
    db.prepare("UPDATE sessions SET status = 'closed', closed_at = ? WHERE id = ?").run(now, params.id);
    const updated = db.prepare('SELECT * FROM sessions WHERE id = ?').get(params.id);
    return NextResponse.json(updated);
  } catch (error) {
    console.error('Session close error:', error);
    return NextResponse.json({ error: 'Failed' }, { status: 500 });
  }
}

export async function DELETE(_req: NextRequest, { params }: { params: { id: string } }) {
  try {
    const db = getDb();
    const session = db.prepare('SELECT * FROM sessions WHERE id = ?').get(params.id) as SessionRow | undefined;
    if (!session) return NextResponse.json({ error: 'Session not found' }, { status: 404 });

    // Find record ids to delete their upload dirs
    const records = db.prepare('SELECT id FROM records WHERE session_id = ?').all(params.id) as { id: string }[];
    for (const r of records) {
      const dir = path.join(UPLOADS_DIR, r.id);
      if (fs.existsSync(dir)) fs.rmSync(dir, { recursive: true, force: true });
    }

    db.prepare('DELETE FROM sessions WHERE id = ?').run(params.id);
    return NextResponse.json({ success: true });
  } catch (error) {
    console.error('Session delete error:', error);
    return NextResponse.json({ error: 'Failed' }, { status: 500 });
  }
}
