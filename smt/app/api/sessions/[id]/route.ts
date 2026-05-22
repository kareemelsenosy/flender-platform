import { NextRequest, NextResponse } from 'next/server';
import { query, queryOne, SessionRow } from '@/lib/db';
import { deletePrefix } from '@/lib/storage';

export const dynamic = 'force-dynamic';

export async function GET(_req: NextRequest, { params }: { params: { id: string } }) {
  try {
    const session = await queryOne<SessionRow>('SELECT * FROM sessions WHERE id = $1', [params.id]);
    if (!session) return NextResponse.json({ error: 'Session not found' }, { status: 404 });

    const records = await query(
      'SELECT * FROM records WHERE session_id = $1 ORDER BY created_at DESC',
      [params.id]
    );

    return NextResponse.json({ ...session, records });
  } catch (error) {
    console.error('Session get error:', error);
    return NextResponse.json({ error: 'Failed' }, { status: 500 });
  }
}

export async function PATCH(_req: NextRequest, { params }: { params: { id: string } }) {
  try {
    const session = await queryOne<SessionRow>('SELECT * FROM sessions WHERE id = $1', [params.id]);
    if (!session) return NextResponse.json({ error: 'Session not found' }, { status: 404 });
    if (session.status === 'closed') {
      return NextResponse.json({ error: 'Session already closed' }, { status: 400 });
    }
    const now = new Date().toISOString();
    await query(
      "UPDATE sessions SET status = 'closed', closed_at = $1 WHERE id = $2",
      [now, params.id]
    );
    const updated = await queryOne<SessionRow>('SELECT * FROM sessions WHERE id = $1', [params.id]);
    return NextResponse.json(updated);
  } catch (error) {
    console.error('Session close error:', error);
    return NextResponse.json({ error: 'Failed' }, { status: 500 });
  }
}

export async function DELETE(_req: NextRequest, { params }: { params: { id: string } }) {
  try {
    const session = await queryOne<SessionRow>('SELECT * FROM sessions WHERE id = $1', [params.id]);
    if (!session) return NextResponse.json({ error: 'Session not found' }, { status: 404 });

    // Remove the stored files for every record in this session
    const records = await query<{ id: string }>(
      'SELECT id FROM records WHERE session_id = $1',
      [params.id]
    );
    for (const r of records) {
      await deletePrefix(r.id);
    }

    // FK ON DELETE CASCADE removes the records rows
    await query('DELETE FROM sessions WHERE id = $1', [params.id]);
    return NextResponse.json({ success: true });
  } catch (error) {
    console.error('Session delete error:', error);
    return NextResponse.json({ error: 'Failed' }, { status: 500 });
  }
}
