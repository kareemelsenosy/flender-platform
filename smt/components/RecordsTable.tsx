'use client';

import { useState, useEffect, useCallback } from 'react';
import {
  Trash2, ChevronUp, ChevronDown, ChevronsUpDown, Search,
} from 'lucide-react';
import { apiFetch } from '@/lib/api';

// ── Types ────────────────────────────────────────────────────────────────────

interface RecordItem {
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

function parseBrandsField(raw: string): string[] {
  try { return JSON.parse(raw) as string[]; } catch { return []; }
}

export interface Filters {
  customer: string;
  brand: string;
  startDate: string;
  endDate: string;
}

interface RecordsTableProps {
  onFiltersChange?: (filters: Filters) => void;
  sessionId?: string;
  readOnly?: boolean;
}

// ── Palette helpers ──────────────────────────────────────────────────────────

const PALETTES = [
  { bg: '#EFF4FF', text: '#2D6FF7' },
  { bg: '#FFF4ED', text: '#FF5C35' },
  { bg: '#ECFDF3', text: '#12B76A' },
  { bg: '#FDF4FF', text: '#9B50E8' },
  { bg: '#FFFAEB', text: '#F79009' },
];

function palette(name: string) {
  const h = name.split('').reduce((a, c) => a + c.charCodeAt(0), 0);
  return PALETTES[h % PALETTES.length];
}

function initials(name: string) {
  return name.split(' ').slice(0, 2).map((w) => w[0]).join('').toUpperCase();
}

// ── Pill definitions ─────────────────────────────────────────────────────────

const TYPE_COLORS: Record<string, { bg: string; text: string }> = {
  Stories: { bg: '#EFF4FF', text: '#2D6FF7' },
  Reels:   { bg: '#FDF4FF', text: '#9B50E8' },
  Posts:   { bg: '#ECFDF3', text: '#12B76A' },
};

const CONTENT_TYPE_COLORS: Record<string, { bg: string; text: string }> = {
  'Product IMG':          { bg: '#EFF4FF', text: '#2D6FF7' },
  'Campaign IMG/VID':     { bg: '#FDF4FF', text: '#9B50E8' },
  'Store IMG (w/ brand)': { bg: '#ECFDF3', text: '#12B76A' },
  'Sales':                { bg: '#FFFAEB', text: '#F79009' },
  'Other (?)':            { bg: '#F2F4F7', text: '#475467' },
};

const SOURCE_COLORS: Record<string, { bg: string; text: string }> = {
  Brand:         { bg: '#EFF4FF', text: '#2D6FF7' },
  Customer:      { bg: '#ECFDF3', text: '#12B76A' },
  'Others (?)':  { bg: '#F2F4F7', text: '#475467' },
};

function Pill({ value, map }: { value: string; map: Record<string, { bg: string; text: string }> }) {
  const c = map[value] ?? { bg: '#F2F4F7', text: '#475467' };
  return (
    <span style={{
      display: 'inline-block',
      padding: '3px 10px',
      backgroundColor: c.bg,
      color: c.text,
      borderRadius: '9999px',
      fontFamily: 'var(--font-body)',
      fontWeight: 600,
      fontSize: '11px',
      whiteSpace: 'nowrap',
    }}>
      {value}
    </span>
  );
}

// ── Constants ────────────────────────────────────────────────────────────────

const PAGE_SIZE = 20;
type SortField = keyof RecordItem;
type SortDir = 'asc' | 'desc';

// ── Styles ───────────────────────────────────────────────────────────────────

const thStyle: React.CSSProperties = {
  padding: '10px 16px',
  fontFamily: 'var(--font-body)',
  fontWeight: 600,
  fontSize: '11px',
  color: '#98A2B3',
  letterSpacing: '0.06em',
  textTransform: 'uppercase',
  textAlign: 'left',
  whiteSpace: 'nowrap',
  cursor: 'pointer',
  userSelect: 'none',
  borderBottom: '1px solid #EAECF0',
};

const tdStyle: React.CSSProperties = {
  padding: '12px 16px',
  fontFamily: 'var(--font-body)',
  fontSize: '13px',
  color: '#101828',
  borderBottom: '1px solid #F2F4F7',
  verticalAlign: 'middle',
};

const filterInputStyle: React.CSSProperties = {
  width: '100%',
  padding: '9px 12px 9px 34px',
  backgroundColor: '#FFFFFF',
  border: '1px solid #EAECF0',
  borderRadius: '10px',
  fontFamily: 'var(--font-body)',
  fontSize: '13px',
  color: '#101828',
  outline: 'none',
  boxSizing: 'border-box',
};

const dateInputStyle: React.CSSProperties = {
  width: '100%',
  padding: '9px 12px',
  backgroundColor: '#FFFFFF',
  border: '1px solid #EAECF0',
  borderRadius: '10px',
  fontFamily: 'monospace',
  fontSize: '12px',
  color: '#101828',
  outline: 'none',
  boxSizing: 'border-box',
  colorScheme: 'light',
};

const labelStyle: React.CSSProperties = {
  display: 'block',
  fontFamily: 'var(--font-body)',
  fontWeight: 600,
  fontSize: '11px',
  color: '#98A2B3',
  letterSpacing: '0.06em',
  textTransform: 'uppercase',
  marginBottom: '6px',
};

// ── Component ────────────────────────────────────────────────────────────────

export default function RecordsTable({ onFiltersChange, sessionId, readOnly }: RecordsTableProps) {
  const [records,   setRecords]   = useState<RecordItem[]>([]);
  const [loading,   setLoading]   = useState(true);
  const [filters,   setFilters]   = useState<Filters>({ customer: '', brand: '', startDate: '', endDate: '' });
  const [sortField, setSortField] = useState<SortField>('created_at');
  const [sortDir,   setSortDir]   = useState<SortDir>('desc');
  const [page,      setPage]      = useState(1);
  const [deleting,  setDeleting]  = useState<string | null>(null);

  const fetchRecords = useCallback(async () => {
    setLoading(true);
    try {
      const params = new URLSearchParams();
      if (filters.customer)  params.set('customer',  filters.customer);
      if (filters.brand)     params.set('brand',     filters.brand);
      if (filters.startDate) params.set('startDate', filters.startDate);
      if (filters.endDate)   params.set('endDate',   filters.endDate);
      if (sessionId)         params.set('session_id', sessionId);
      const res  = await apiFetch(`/api/records?${params}`);
      const data = await res.json();
      setRecords(data);
    } catch (err) {
      console.error(err);
    } finally {
      setLoading(false);
    }
  }, [filters, sessionId]);

  useEffect(() => { fetchRecords(); }, [fetchRecords]);
  useEffect(() => { onFiltersChange?.(filters); }, [filters, onFiltersChange]);

  const handleSort = (field: SortField) => {
    if (sortField === field) setSortDir((d) => (d === 'asc' ? 'desc' : 'asc'));
    else { setSortField(field); setSortDir('asc'); }
    setPage(1);
  };

  const handleDelete = async (id: string) => {
    if (!confirm('Delete this record and all associated files? This cannot be undone.')) return;
    setDeleting(id);
    try {
      await apiFetch(`/api/records?id=${id}`, { method: 'DELETE' });
      setRecords((prev) => prev.filter((r) => r.id !== id));
    } catch (err) { console.error(err); }
    finally { setDeleting(null); }
  };

  const sorted = [...records].sort((a, b) => {
    const cmp = String(a[sortField]).localeCompare(String(b[sortField]));
    return sortDir === 'asc' ? cmp : -cmp;
  });

  const totalPages = Math.ceil(sorted.length / PAGE_SIZE);
  const paginated  = sorted.slice((page - 1) * PAGE_SIZE, page * PAGE_SIZE);

  function SortIcon({ field }: { field: SortField }) {
    if (sortField !== field) return <ChevronsUpDown size={12} color="#D0D5DD" />;
    return sortDir === 'asc'
      ? <ChevronUp   size={12} color="#2D6FF7" />
      : <ChevronDown size={12} color="#2D6FF7" />;
  }

  const setFilter = (key: keyof Filters) => (val: string) => {
    setFilters((f) => ({ ...f, [key]: val }));
    setPage(1);
  };

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: '16px' }}>

      {/* Filter card */}
      <div style={{
        backgroundColor: '#FFFFFF',
        border: '1px solid #EAECF0',
        borderRadius: '16px',
        padding: '20px 24px',
        boxShadow: '0 1px 2px rgba(16,24,40,0.04)',
        display: 'grid',
        gridTemplateColumns: '1fr 1fr 1fr 1fr',
        gap: '16px',
        alignItems: 'end',
      }}>
        {[
          { key: 'customer' as const, label: 'Customer', isSearch: true },
          { key: 'brand'    as const, label: 'Brand',    isSearch: true },
        ].map(({ key, label }) => (
          <div key={key}>
            <label style={labelStyle}>{label}</label>
            <div style={{ position: 'relative' }}>
              <Search size={13} color="#98A2B3" style={{ position: 'absolute', left: '11px', top: '50%', transform: 'translateY(-50%)' }} />
              <input
                type="text"
                value={filters[key]}
                onChange={(e) => setFilter(key)(e.target.value)}
                placeholder="Filter…"
                style={filterInputStyle}
              />
            </div>
          </div>
        ))}
        <div>
          <label style={labelStyle}>From Date</label>
          <input type="text" value={filters.startDate} onChange={(e) => setFilter('startDate')(e.target.value)} placeholder="01-01-2026" style={dateInputStyle} />
        </div>
        <div>
          <label style={labelStyle}>To Date</label>
          <input type="text" value={filters.endDate} onChange={(e) => setFilter('endDate')(e.target.value)} placeholder="31-12-2026" style={dateInputStyle} />
        </div>
      </div>

      {/* Count row */}
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
        <span style={{ fontFamily: 'var(--font-body)', fontSize: '13px', color: '#475467' }}>
          <span style={{ fontWeight: 700, color: '#101828' }}>{records.length}</span> record{records.length !== 1 ? 's' : ''} found
        </span>
        {totalPages > 1 && (
          <span style={{ fontFamily: 'var(--font-body)', fontSize: '12px', color: '#98A2B3' }}>
            Page {page} / {totalPages}
          </span>
        )}
      </div>

      {/* Table card */}
      <div style={{
        backgroundColor: '#FFFFFF',
        border: '1px solid #EAECF0',
        borderRadius: '16px',
        boxShadow: '0 1px 2px rgba(16,24,40,0.04)',
        overflow: 'hidden',
      }}>
        {loading ? (
          <div style={{ padding: '48px', textAlign: 'center', fontFamily: 'var(--font-body)', fontSize: '14px', color: '#98A2B3' }}>Loading…</div>
        ) : records.length === 0 ? (
          <div style={{ padding: '64px', textAlign: 'center' }}>
            <p style={{ fontFamily: 'var(--font-display)', fontWeight: 700, fontSize: '16px', color: '#475467' }}>No records found</p>
            <p style={{ fontFamily: 'var(--font-body)', fontSize: '13px', color: '#98A2B3', marginTop: '6px' }}>Try adjusting your filters or upload some content</p>
          </div>
        ) : (
          <div style={{ overflowX: 'auto' }}>
            <table style={{ width: '100%', borderCollapse: 'collapse', minWidth: '960px' }}>
              <thead>
                <tr style={{ backgroundColor: '#F9FAFB' }}>
                  {([
                    { key: 'customer',       label: 'Customer' },
                    { key: 'brands',         label: 'Brands' },
                    { key: 'num_posts',      label: 'Posts' },
                    { key: 'type',           label: 'Type' },
                    { key: 'content_type',   label: 'Content Type' },
                    { key: 'content_source', label: 'Source' },
                    { key: 'files',          label: 'Files' },
                    { key: 'date',           label: 'Date' },
                  ] as { key: SortField; label: string }[]).map(({ key, label }) => (
                    <th key={key} style={thStyle} onClick={() => handleSort(key)}>
                      <div style={{ display: 'flex', alignItems: 'center', gap: '4px' }}>
                        {label} <SortIcon field={key} />
                      </div>
                    </th>
                  ))}
                  <th style={{ ...thStyle, cursor: 'default' }}>Actions</th>
                </tr>
              </thead>
              <tbody>
                {paginated.map((record) => {
                  const files = JSON.parse(record.files) as string[];
                  const brandList = parseBrandsField(record.brands);
                  const cp = palette(record.customer);
                  return (
                    <tr key={record.id}>
                      <td style={tdStyle}>
                        <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
                          <div style={{ width: '28px', height: '28px', borderRadius: '50%', backgroundColor: cp.bg, color: cp.text, display: 'flex', alignItems: 'center', justifyContent: 'center', fontSize: '10px', fontWeight: 700, flexShrink: 0 }}>{initials(record.customer)}</div>
                          <span style={{ fontWeight: 500 }}>{record.customer}</span>
                        </div>
                      </td>
                      <td style={tdStyle}>
                        <div style={{ display: 'flex', flexWrap: 'wrap', gap: '4px', maxWidth: '200px' }}>
                          {brandList.slice(0, 3).map((b) => (
                            <span key={b} style={{
                              display: 'inline-block', padding: '2px 9px',
                              backgroundColor: '#EFF4FF', color: '#2D6FF7',
                              borderRadius: 9999, fontSize: 11, fontWeight: 600,
                              fontFamily: 'var(--font-body)', whiteSpace: 'nowrap',
                            }}>{b}</span>
                          ))}
                          {brandList.length > 3 && (
                            <span style={{ fontSize: 11, color: '#98A2B3', fontFamily: 'var(--font-body)', alignSelf: 'center' }}>
                              +{brandList.length - 3} more
                            </span>
                          )}
                        </div>
                      </td>
                      <td style={{ ...tdStyle, textAlign: 'center', fontFamily: 'var(--font-display)', fontWeight: 700, color: '#2D6FF7' }}>{record.num_posts}</td>
                      <td style={tdStyle}><Pill value={record.type} map={TYPE_COLORS} /></td>
                      <td style={tdStyle}><Pill value={record.content_type} map={CONTENT_TYPE_COLORS} /></td>
                      <td style={tdStyle}><Pill value={record.content_source} map={SOURCE_COLORS} /></td>
                      <td style={tdStyle}>
                        <div style={{ display: 'flex', flexWrap: 'wrap', gap: '4px', maxWidth: '180px' }}>
                          {files.slice(0, 2).map((f, i) => (
                            <span key={i} style={{ display: 'inline-block', padding: '2px 7px', backgroundColor: '#F2F4F7', border: '1px solid #EAECF0', borderRadius: '6px', fontFamily: 'monospace', fontSize: '9px', color: '#475467', maxWidth: '160px', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                              {(f.split('/').pop() ?? f)}
                            </span>
                          ))}
                          {files.length > 2 && <span style={{ fontSize: '10px', color: '#98A2B3', fontFamily: 'var(--font-body)' }}>+{files.length - 2}</span>}
                        </div>
                      </td>
                      <td style={{ ...tdStyle, fontFamily: 'monospace', fontSize: '12px', color: '#98A2B3' }}>{record.date}</td>
                      <td style={tdStyle}>
                        <div style={{ display: 'flex', gap: '6px' }}>
                          <button onClick={() => handleDelete(record.id)} disabled={readOnly || deleting === record.id} title={readOnly ? 'Read-only (session closed)' : 'Delete record'}
                            style={{ width: '32px', height: '32px', borderRadius: '8px', border: '1px solid #EAECF0', backgroundColor: '#F9FAFB', display: 'flex', alignItems: 'center', justifyContent: 'center', cursor: deleting === record.id ? 'not-allowed' : 'pointer', opacity: deleting === record.id ? 0.5 : 1, transition: 'all 0.15s ease' }}
                            onMouseEnter={(e) => { if (deleting !== record.id) { (e.currentTarget as HTMLButtonElement).style.backgroundColor = '#FEF3F2'; (e.currentTarget as HTMLButtonElement).style.borderColor = '#FDA29B'; }}}
                            onMouseLeave={(e) => { (e.currentTarget as HTMLButtonElement).style.backgroundColor = '#F9FAFB'; (e.currentTarget as HTMLButtonElement).style.borderColor = '#EAECF0'; }}>
                            <Trash2 size={13} color={deleting === record.id ? '#98A2B3' : '#F04438'} />
                          </button>
                        </div>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
      </div>

      {/* Pagination */}
      {totalPages > 1 && (
        <div style={{ display: 'flex', gap: '4px', justifyContent: 'center', alignItems: 'center' }}>
          <button onClick={() => setPage((p) => Math.max(1, p - 1))} disabled={page === 1}
            style={{ padding: '7px 14px', borderRadius: '9999px', border: '1px solid #EAECF0', backgroundColor: '#FFFFFF', color: page === 1 ? '#D0D5DD' : '#475467', fontFamily: 'var(--font-body)', fontWeight: 500, fontSize: '13px', cursor: page === 1 ? 'not-allowed' : 'pointer' }}>
            ← Prev
          </button>
          {Array.from({ length: Math.min(5, totalPages) }, (_, i) => {
            const start = Math.max(1, page - 2);
            const pn = start + i;
            if (pn > totalPages) return null;
            return (
              <button key={pn} onClick={() => setPage(pn)}
                style={{ width: '36px', height: '36px', borderRadius: '9999px', border: '1px solid', borderColor: pn === page ? '#2D6FF7' : '#EAECF0', backgroundColor: pn === page ? '#2D6FF7' : '#FFFFFF', color: pn === page ? '#FFFFFF' : '#475467', fontFamily: 'var(--font-body)', fontWeight: pn === page ? 700 : 400, fontSize: '13px', cursor: 'pointer' }}>
                {pn}
              </button>
            );
          })}
          <button onClick={() => setPage((p) => Math.min(totalPages, p + 1))} disabled={page === totalPages}
            style={{ padding: '7px 14px', borderRadius: '9999px', border: '1px solid #EAECF0', backgroundColor: '#FFFFFF', color: page === totalPages ? '#D0D5DD' : '#475467', fontFamily: 'var(--font-body)', fontWeight: 500, fontSize: '13px', cursor: page === totalPages ? 'not-allowed' : 'pointer' }}>
            Next →
          </button>
        </div>
      )}
    </div>
  );
}
