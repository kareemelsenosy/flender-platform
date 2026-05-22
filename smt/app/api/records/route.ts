import { NextRequest, NextResponse } from 'next/server';

export const dynamic = 'force-dynamic';
import { getDb, getOpenSession } from '@/lib/db';
import fs from 'fs';
import path from 'path';

const UPLOADS_DIR = path.join(process.cwd(), 'uploads');

export async function GET(request: NextRequest) {
  try {
    const db = getDb();
    const { searchParams } = new URL(request.url);

    const customer = searchParams.get('customer') || undefined;
    const brand = searchParams.get('brand') || undefined;
    const startDate = searchParams.get('startDate') || undefined;
    const endDate = searchParams.get('endDate') || undefined;
    const sessionId = searchParams.get('session_id') || undefined;
    const open = searchParams.get('open');

    const conditions: string[] = [];
    const values: string[] = [];

    if (customer) {
      conditions.push('LOWER(customer) LIKE LOWER(?)');
      values.push(`%${customer}%`);
    }
    if (brand) {
      // brands is a JSON array string — substring match is fine for filtering
      conditions.push('LOWER(brands) LIKE LOWER(?)');
      values.push(`%${brand}%`);
    }
    if (startDate) {
      conditions.push('date >= ?');
      values.push(startDate);
    }
    if (endDate) {
      conditions.push('date <= ?');
      values.push(endDate);
    }
    if (sessionId) {
      conditions.push('session_id = ?');
      values.push(sessionId);
    } else if (open === 'true') {
      const openSession = getOpenSession();
      if (!openSession) return NextResponse.json([]);
      conditions.push('session_id = ?');
      values.push(openSession.id);
    }

    const whereClause = conditions.length > 0 ? `WHERE ${conditions.join(' AND ')}` : '';
    const records = db
      .prepare(`SELECT * FROM records ${whereClause} ORDER BY created_at DESC`)
      .all(...values);

    return NextResponse.json(records);
  } catch (error) {
    console.error('Records fetch error:', error);
    return NextResponse.json({ error: 'Failed to fetch records' }, { status: 500 });
  }
}

export async function DELETE(request: NextRequest) {
  try {
    const { searchParams } = new URL(request.url);
    const id = searchParams.get('id');

    if (!id) {
      return NextResponse.json({ error: 'Record ID required' }, { status: 400 });
    }

    const db = getDb();
    const record = db.prepare('SELECT * FROM records WHERE id = ?').get(id) as {
      id: string;
      files: string;
    } | undefined;

    if (!record) {
      return NextResponse.json({ error: 'Record not found' }, { status: 404 });
    }

    const recordDir = path.join(UPLOADS_DIR, id);
    if (fs.existsSync(recordDir)) {
      fs.rmSync(recordDir, { recursive: true, force: true });
    }

    db.prepare('DELETE FROM records WHERE id = ?').run(id);

    return NextResponse.json({ success: true });
  } catch (error) {
    console.error('Delete error:', error);
    return NextResponse.json({ error: 'Failed to delete record' }, { status: 500 });
  }
}
