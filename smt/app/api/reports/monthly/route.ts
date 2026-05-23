import { NextRequest, NextResponse } from 'next/server';
import * as XLSX from 'xlsx';
import { query } from '@/lib/db';

export const dynamic = 'force-dynamic';

interface RecordRow {
  id: string;
  customer: string;
  brands: string;          // JSON array
  num_posts: number;
  type: string;
  content_type: string;
  content_source: string;
  date: string;            // DD-MM-YYYY
  files: string;           // JSON array
}

function parseArr(raw: string): string[] {
  try { return JSON.parse(raw) as string[]; } catch { return []; }
}

const MONTH_NAMES = [
  'January', 'February', 'March', 'April', 'May', 'June',
  'July', 'August', 'September', 'October', 'November', 'December',
];

export async function GET(req: NextRequest) {
  try {
    const url = new URL(req.url);
    const now = new Date();
    const month = parseInt(url.searchParams.get('month') || String(now.getMonth() + 1), 10);
    const year  = parseInt(url.searchParams.get('year')  || String(now.getFullYear()), 10);

    if (isNaN(month) || month < 1 || month > 12 || isNaN(year)) {
      return NextResponse.json({ error: 'Invalid month/year' }, { status: 400 });
    }

    const mm = String(month).padStart(2, '0');
    const yyyy = String(year);

    // dates stored as DD-MM-YYYY
    const records = await query<RecordRow>(
      `SELECT * FROM records WHERE substr(date, 4, 2) = $1 AND substr(date, 7, 4) = $2 ORDER BY customer ASC, date ASC`,
      [mm, yyyy]
    );

    // ── Aggregate by Customer ────────────────────────────────────────────────
    interface CustomerAgg {
      customer: string;
      totalPosts: number;
      records: number;
      brands: Set<string>;
      uniqueDates: Set<string>;
      typeCounts: Record<string, number>;
    }
    const byCustomer = new Map<string, CustomerAgg>();
    for (const r of records) {
      const cust = r.customer;
      if (!byCustomer.has(cust)) {
        byCustomer.set(cust, {
          customer: cust,
          totalPosts: 0,
          records: 0,
          brands: new Set(),
          uniqueDates: new Set(),
          typeCounts: { Stories: 0, Reels: 0, Posts: 0 },
        });
      }
      const agg = byCustomer.get(cust)!;
      agg.totalPosts += r.num_posts || 0;
      agg.records   += 1;
      agg.uniqueDates.add(r.date);
      parseArr(r.brands).forEach((b) => agg.brands.add(b));
      if (agg.typeCounts[r.type] !== undefined) {
        agg.typeCounts[r.type] += r.num_posts || 0;
      } else {
        agg.typeCounts[r.type] = r.num_posts || 0;
      }
    }

    const customerRows = Array.from(byCustomer.values())
      .sort((a, b) => b.totalPosts - a.totalPosts)
      .map((c) => ({
        'Business Partner': c.customer,
        'Brands Posted':    Array.from(c.brands).sort().join(', '),
        '# of Brands':      c.brands.size,
        'Total Posts':      c.totalPosts,
        'Stories':          c.typeCounts['Stories'] || 0,
        'Reels':            c.typeCounts['Reels']   || 0,
        'Posts':            c.typeCounts['Posts']   || 0,
        'Upload Sessions':  c.records,
        'Active Days':      c.uniqueDates.size,
      }));

    // ── Aggregate by Brand ───────────────────────────────────────────────────
    interface BrandAgg {
      brand: string;
      totalPosts: number;
      customers: Set<string>;
      typeCounts: Record<string, number>;
    }
    const byBrand = new Map<string, BrandAgg>();
    for (const r of records) {
      const brands = parseArr(r.brands);
      for (const b of brands) {
        if (!byBrand.has(b)) {
          byBrand.set(b, {
            brand: b,
            totalPosts: 0,
            customers: new Set(),
            typeCounts: { Stories: 0, Reels: 0, Posts: 0 },
          });
        }
        const agg = byBrand.get(b)!;
        agg.totalPosts += r.num_posts || 0;
        agg.customers.add(r.customer);
        if (agg.typeCounts[r.type] !== undefined) {
          agg.typeCounts[r.type] += r.num_posts || 0;
        } else {
          agg.typeCounts[r.type] = r.num_posts || 0;
        }
      }
    }

    const brandRows = Array.from(byBrand.values())
      .sort((a, b) => b.totalPosts - a.totalPosts)
      .map((b) => ({
        'Brand':           b.brand,
        'Total Posts':     b.totalPosts,
        '# of Partners':   b.customers.size,
        'Partners':        Array.from(b.customers).sort().join(', '),
        'Stories':         b.typeCounts['Stories'] || 0,
        'Reels':           b.typeCounts['Reels']   || 0,
        'Posts':           b.typeCounts['Posts']   || 0,
      }));

    // ── Overview sheet ───────────────────────────────────────────────────────
    const totalPosts = records.reduce((s, r) => s + (r.num_posts || 0), 0);
    const overview = [
      { Metric: 'Report Period',          Value: `${MONTH_NAMES[month - 1]} ${year}` },
      { Metric: 'Total Records (rows)',   Value: records.length },
      { Metric: 'Total Posts Tracked',    Value: totalPosts },
      { Metric: 'Active Business Partners', Value: byCustomer.size },
      { Metric: 'Active Brands',          Value: byBrand.size },
    ];

    // ── Build workbook ───────────────────────────────────────────────────────
    const wb = XLSX.utils.book_new();

    const wsOverview = XLSX.utils.json_to_sheet(overview);
    wsOverview['!cols'] = [{ wch: 28 }, { wch: 40 }];
    XLSX.utils.book_append_sheet(wb, wsOverview, 'Overview');

    const wsCust = XLSX.utils.json_to_sheet(customerRows);
    wsCust['!cols'] = [
      { wch: 22 }, { wch: 50 }, { wch: 12 }, { wch: 12 },
      { wch: 10 }, { wch: 10 }, { wch: 10 }, { wch: 16 }, { wch: 14 },
    ];
    XLSX.utils.book_append_sheet(wb, wsCust, 'By Business Partner');

    const wsBrand = XLSX.utils.json_to_sheet(brandRows);
    wsBrand['!cols'] = [
      { wch: 22 }, { wch: 14 }, { wch: 14 }, { wch: 50 },
      { wch: 10 }, { wch: 10 }, { wch: 10 },
    ];
    XLSX.utils.book_append_sheet(wb, wsBrand, 'By Brand');

    // ── Detail sheet (every record this month, same columns as session export)
    const detailRows = records.map((r) => ({
      Customer:                    r.customer,
      Brand:                       parseArr(r.brands).join(', '),
      'Number of Posts Uploaded':  r.num_posts,
      Type:                        r.type,
      'Content Type':              r.content_type,
      'Content Source':            r.content_source,
      'Link to Content':           parseArr(r.files).join(', '),
      Date:                        r.date,
    }));
    const wsDetail = XLSX.utils.json_to_sheet(detailRows);
    wsDetail['!cols'] = [
      { wch: 18 }, { wch: 24 }, { wch: 22 }, { wch: 12 },
      { wch: 22 }, { wch: 18 }, { wch: 48 }, { wch: 14 },
    ];
    XLSX.utils.book_append_sheet(wb, wsDetail, 'All Records');

    const buf = XLSX.write(wb, { type: 'buffer', bookType: 'xlsx' }) as Buffer;
    const filename = `MonthlyReport_${MONTH_NAMES[month - 1]}_${year}.xlsx`;

    return new Response(buf as unknown as BodyInit, {
      headers: {
        'Content-Type': 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
        'Content-Disposition': `attachment; filename="${filename}"`,
      },
    });
  } catch (error) {
    console.error('Monthly report error:', error);
    return NextResponse.json({ error: 'Report failed', details: String(error) }, { status: 500 });
  }
}
