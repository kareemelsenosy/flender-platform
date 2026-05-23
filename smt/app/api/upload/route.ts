import { NextRequest, NextResponse } from 'next/server';
import { v4 as uuidv4 } from 'uuid';
import { query, queryOne, SessionRow } from '@/lib/db';
import { putFile } from '@/lib/storage';
import { buildFilename, getFileExtension } from '@/lib/utils';

export const dynamic = 'force-dynamic';

export async function POST(request: NextRequest) {
  try {
    const formData = await request.formData();
    const session_id = formData.get('session_id') as string;
    const customer = formData.get('customer') as string;
    const brandsRaw = formData.get('brands') as string;
    const date = formData.get('date') as string;
    const type = formData.get('type') as string;
    const content_type = formData.get('content_type') as string;
    const content_source = formData.get('content_source') as string;

    if (!session_id) {
      return NextResponse.json({ error: 'session_id is required' }, { status: 400 });
    }

    let brands: string[] = [];
    try {
      brands = JSON.parse(brandsRaw || '[]');
    } catch {
      return NextResponse.json({ error: 'brands must be a JSON array' }, { status: 400 });
    }

    if (!customer || !date || !type || !content_type || !content_source || brands.length === 0) {
      return NextResponse.json({ error: 'All fields are required (at least 1 brand)' }, { status: 400 });
    }

    const session = await queryOne<SessionRow>(
      'SELECT * FROM sessions WHERE id = $1', [session_id]
    );
    if (!session) {
      return NextResponse.json({ error: 'Session not found' }, { status: 400 });
    }
    if (session.status !== 'open') {
      return NextResponse.json({ error: 'Session is closed; cannot upload' }, { status: 400 });
    }

    const files = formData.getAll('files[]') as File[];
    if (!files || files.length === 0) {
      return NextResponse.json({ error: 'At least one file is required' }, { status: 400 });
    }

    const id = uuidv4();
    const savedFilenames: string[] = [];

    for (let i = 0; i < files.length; i++) {
      const file = files[i];
      const ext = getFileExtension(file.name);
      const filename = files.length === 1
        ? buildFilename(brands, type, ext)
        : buildFilename(brands, type, ext, i + 1);

      const arrayBuffer = await file.arrayBuffer();
      await putFile(id, filename, Buffer.from(arrayBuffer), file.type || undefined);
      savedFilenames.push(filename);
    }

    const now = new Date().toISOString();

    await query(
      `INSERT INTO records
         (id, session_id, customer, brands, num_posts, type, content_type, content_source, date, files, created_at)
       VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11)`,
      [
        id,
        session_id,
        customer,
        JSON.stringify(brands),
        files.length,
        type,
        content_type,
        content_source,
        date,
        JSON.stringify(savedFilenames),
        now,
      ]
    );

    await query('INSERT INTO customers (name) VALUES ($1) ON CONFLICT DO NOTHING', [customer]);
    for (const b of brands) {
      await query('INSERT INTO brands (name) VALUES ($1) ON CONFLICT DO NOTHING', [b]);
    }

    const record = await queryOne('SELECT * FROM records WHERE id = $1', [id]);
    return NextResponse.json(record, { status: 201 });
  } catch (error) {
    console.error('Upload error:', error);
    return NextResponse.json(
      { error: 'Upload failed', details: String(error) },
      { status: 500 }
    );
  }
}
