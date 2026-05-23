import { NextResponse } from 'next/server';
import { query } from '@/lib/db';

export const dynamic = 'force-dynamic';

export async function GET() {
  try {
    const customers = await query<{ name: string }>(
      'SELECT name FROM customers ORDER BY name ASC'
    );
    return NextResponse.json(customers.map((c) => c.name));
  } catch (error) {
    console.error('Customers fetch error:', error);
    return NextResponse.json({ error: 'Failed to fetch customers' }, { status: 500 });
  }
}
