import { NextRequest, NextResponse } from 'next/server';
import JSZip from 'jszip';
import * as XLSX from 'xlsx';
import { query, queryOne, SessionRow } from '@/lib/db';
import { getFile } from '@/lib/storage';
import { slugifySessionName } from '@/lib/utils';

export const dynamic = 'force-dynamic';

interface RecordRow {
  id: string;
  session_id: string;
  customer: string;
  brands: string;
  num_posts: number;
  type: string;
  content_type: string;
  content_source: string;
  date: string;
  files: string;
  created_at: string;
}

export async function GET(_req: NextRequest, { params }: { params: { id: string } }) {
  try {
    const session = await queryOne<SessionRow>(
      'SELECT * FROM sessions WHERE id = $1', [params.id]
    );
    if (!session) return NextResponse.json({ error: 'Session not found' }, { status: 404 });

    const records = await query<RecordRow>(
      'SELECT * FROM records WHERE session_id = $1 ORDER BY created_at DESC',
      [params.id]
    );

    const zip = new JSZip();
    const rootName = `Session_${slugifySessionName(session.name)}`;
    const root = zip.folder(rootName)!;

    // Build records.xlsx
    const sheetData = records.map((r) => {
      const fileList = (() => { try { return JSON.parse(r.files) as string[]; } catch { return []; } })();
      const brandList = (() => { try { return JSON.parse(r.brands) as string[]; } catch { return []; } })();
      return {
        Customer: r.customer,
        Brand: brandList.join(', '),
        'Number of Posts Uploaded': r.num_posts,
        Type: r.type,
        'Content Type': r.content_type,
        'Content Source': r.content_source,
        'Link to Content': fileList.join(', '),
        Date: r.date,
      };
    });

    const wb = XLSX.utils.book_new();
    const ws = XLSX.utils.json_to_sheet(sheetData);
    ws['!cols'] = [
      { wch: 18 }, { wch: 24 }, { wch: 22 }, { wch: 12 },
      { wch: 22 }, { wch: 18 }, { wch: 48 }, { wch: 14 },
    ];
    XLSX.utils.book_append_sheet(wb, ws, 'Records');
    const xlsxBuf = XLSX.write(wb, { type: 'buffer', bookType: 'xlsx' }) as Buffer;
    root.file('records.xlsx', xlsxBuf);

    // Files grouped by customer/date — pulled from object storage
    for (const r of records) {
      const files = (() => { try { return JSON.parse(r.files) as string[]; } catch { return []; } })();
      if (files.length === 0) continue;

      const customerFolder = root.folder(r.customer)!;
      const dateFolder = customerFolder.folder(r.date)!;

      for (const filename of files) {
        const data = await getFile(r.id, filename);
        if (data) {
          dateFolder.file(filename, data);
        }
      }
    }

    const zipBuffer = await zip.generateAsync({ type: 'nodebuffer' });
    return new Response(zipBuffer as unknown as BodyInit, {
      headers: {
        'Content-Type': 'application/zip',
        'Content-Disposition': `attachment; filename="${rootName}.zip"`,
      },
    });
  } catch (error) {
    console.error('Session export error:', error);
    return NextResponse.json({ error: 'Export failed', details: String(error) }, { status: 500 });
  }
}
