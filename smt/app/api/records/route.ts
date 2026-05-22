import { NextRequest, NextResponse } from 'next/server';
import { query, queryOne, getOpenSession } from '@/lib/db';
import { deletePrefix } from '@/lib/storage';

export const dynamic = 'force-dynamic';

export async function GET(request: NextRequest) {
  try {
    const { searchParams } = new URL(request.url);

    const customer = searchParams.get('customer') || undefined;
    const brand = searchParams.get('brand') || undefined;
    const startDate = searchParams.get('startDate') || undefined;
    const endDate = searchParams.get('endDate') || undefined;
    const sessionId = searchParams.get('session_id') || undefined;
    const open = searchParams.get('open');

    const conditions: string[] = [];
    const values: unknown[] = [];
    const ph = () => `$${values.length}`; // call AFTER pushing the value

    if (customer) {
      values.push(`%${customer}%`);
      conditions.push(`LOWER(customer) LIKE LOWER(${ph()})`);
    }
    if (brand) {
      // brands is a JSON array string — substring match is fine for filtering
      values.push(`%${brand}%`);
      conditions.push(`LOWER(brands) LIKE LOWER(${ph()})`);
    }
    if (startDate) {
      values.push(startDate);
      conditions.push(`date >= ${ph()}`);
    }
    if (endDate) {
      values.push(endDate);
      conditions.push(`date <= ${ph()}`);
    }
    if (sessionId) {
      values.push(sessionId);
      conditions.push(`session_id = ${ph()}`);
    } else if (open === 'true') {
      const openSession = await getOpenSession();
      if (!openSession) return NextResponse.json([]);
      values.push(openSession.id);
      conditions.push(`session_id = ${ph()}`);
    }

    const whereClause = conditions.length > 0 ? `WHERE ${conditions.join(' AND ')}` : '';
    const records = await query(
      `SELECT * FROM records ${whereClause} ORDER BY created_at DESC`,
      values
    );

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

    const record = await queryOne<{ id: string; files: string }>(
      'SELECT * FROM records WHERE id = $1',
      [id]
    );

    if (!record) {
      return NextResponse.json({ error: 'Record not found' }, { status: 404 });
    }

    // Remove the record's stored files, then the row
    await deletePrefix(id);
    await query('DELETE FROM records WHERE id = $1', [id]);

    return NextResponse.json({ success: true });
  } catch (error) {
    console.error('Delete error:', error);
    return NextResponse.json({ error: 'Failed to delete record' }, { status: 500 });
  }
}
