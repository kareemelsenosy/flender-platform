import { NextResponse } from 'next/server';

export const dynamic = 'force-dynamic';
import { getDb } from '@/lib/db';

export async function GET() {
  try {
    const db = getDb();
    const customers = db.prepare('SELECT name FROM customers ORDER BY name ASC').all() as { name: string }[];
    return NextResponse.json(customers.map((c) => c.name));
  } catch (error) {
    console.error('Customers fetch error:', error);
    return NextResponse.json({ error: 'Failed to fetch customers' }, { status: 500 });
  }
}
