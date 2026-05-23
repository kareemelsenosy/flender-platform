import { NextResponse } from 'next/server';
import { getOpenSession } from '@/lib/db';

export const dynamic = 'force-dynamic';

export async function GET() {
  try {
    const open = await getOpenSession();
    return NextResponse.json(open);
  } catch (error) {
    console.error('Current session error:', error);
    return NextResponse.json({ error: 'Failed' }, { status: 500 });
  }
}
