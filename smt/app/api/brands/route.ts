import { NextResponse } from 'next/server';
import { query } from '@/lib/db';

export const dynamic = 'force-dynamic';

export async function GET() {
  try {
    const brands = await query<{ name: string }>(
      'SELECT name FROM brands ORDER BY name ASC'
    );
    return NextResponse.json(brands.map((b) => b.name));
  } catch (error) {
    console.error('Brands fetch error:', error);
    return NextResponse.json({ error: 'Failed to fetch brands' }, { status: 500 });
  }
}
