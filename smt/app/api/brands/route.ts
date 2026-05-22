import { NextResponse } from 'next/server';

export const dynamic = 'force-dynamic';
import { getDb } from '@/lib/db';

export async function GET() {
  try {
    const db = getDb();
    const brands = db.prepare('SELECT name FROM brands ORDER BY name ASC').all() as { name: string }[];
    return NextResponse.json(brands.map((b) => b.name));
  } catch (error) {
    console.error('Brands fetch error:', error);
    return NextResponse.json({ error: 'Failed to fetch brands' }, { status: 500 });
  }
}
