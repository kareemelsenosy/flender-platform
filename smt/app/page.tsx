'use client';

import { useState, useEffect, useMemo, useCallback } from 'react';
import Link from 'next/link';
import { Layers, Download, Plus, Clock, FileText, Loader2, FileSpreadsheet, Calendar } from 'lucide-react';
import StatCard from '@/components/StatCard';
import UploadChart from '@/components/UploadChart';
import TopBrandsList from '@/components/TopBrandsList';
import { triggerDownload } from '@/lib/utils';

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

interface SessionInfo {
  id: string;
  name: string;
  status: 'open' | 'closed';
  created_at: string;
  closed_at: string | null;
}

const AVATAR_PALETTES = [
  { bg: '#EFF4FF', text: '#2D6FF7' },
  { bg: '#FFF4ED', text: '#FF5C35' },
  { bg: '#ECFDF3', text: '#12B76A' },
  { bg: '#FDF4FF', text: '#9B50E8' },
  { bg: '#FFFAEB', text: '#F79009' },
];

function avatarPalette(name: string) {
  const hash = name.split('').reduce((a, c) => a + c.charCodeAt(0), 0);
  return AVATAR_PALETTES[hash % AVATAR_PALETTES.length];
}

function initials(name: string) {
  return name.split(' ').slice(0, 2).map((w) => w[0]).join('').toUpperCase();
}

const TYPE_COLORS: Record<string, { bg: string; text: string }> = {
  Stories: { bg: '#EFF4FF', text: '#2D6FF7' },
  Reels:   { bg: '#FDF4FF', text: '#9B50E8' },
  Posts:   { bg: '#ECFDF3', text: '#12B76A' },
};

function TypePill({ value }: { value: string }) {
  const c = TYPE_COLORS[value] ?? { bg: '#F2F4F7', text: '#475467' };
  return (
    <span style={{
      display: 'inline-block', padding: '3px 10px',
      backgroundColor: c.bg, color: c.text, borderRadius: '9999px',
      fontFamily: 'var(--font-body)', fontWeight: 600, fontSize: '11px',
    }}>{value}</span>
  );
}

const thStyle: React.CSSProperties = {
  padding: '10px 16px', fontFamily: 'var(--font-body)', fontWeight: 600,
  fontSize: '11px', color: '#98A2B3', letterSpacing: '0.06em',
  textTransform: 'uppercase', textAlign: 'left', whiteSpace: 'nowrap',
  borderBottom: '1px solid #EAECF0',
};

const tdStyle: React.CSSProperties = {
  padding: '12px 16px', fontFamily: 'var(--font-body)', fontSize: '13px',
  color: '#101828', borderBottom: '1px solid #F2F4F7', verticalAlign: 'middle',
};

function parseBrands(raw: string): string[] {
  try { return JSON.parse(raw) as string[]; } catch { return []; }
}

export default function DashboardPage() {
  const [records, setRecords] = useState<RecordItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [session, setSession] = useState<SessionInfo | null>(null);
  const [sessionLoading, setSessionLoading] = useState(true);
  const [newName, setNewName] = useState('');
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  // Monthly report controls (default = this month)
  const _now = new Date();
  const [reportMonth, setReportMonth] = useState<number>(_now.getMonth() + 1);
  const [reportYear, setReportYear]   = useState<number>(_now.getFullYear());
  const [reportBusy, setReportBusy]   = useState(false);

  const downloadMonthlyReport = () => {
    setReportBusy(true);
    try {
      triggerDownload(`/api/reports/monthly?month=${reportMonth}&year=${reportYear}`);
    } finally {
      // small delay so the button visibly flashes
      setTimeout(() => setReportBusy(false), 800);
    }
  };

  const loadSession = useCallback(() => {
    setSessionLoading(true);
    fetch('/api/sessions/current')
      .then((r) => r.json())
      .then((d) => setSession(d || null))
      .catch(() => setSession(null))
      .finally(() => setSessionLoading(false));
  }, []);

  const loadRecords = useCallback(() => {
    setLoading(true);
    fetch('/api/records')
      .then((r) => r.json())
      .then((data) => setRecords(data))
      .catch(() => {})
      .finally(() => setLoading(false));
  }, []);

  useEffect(() => { loadSession(); loadRecords(); }, [loadSession, loadRecords]);

  const startSession = async () => {
    if (!newName.trim()) return;
    setBusy(true); setErr(null);
    try {
      const res = await fetch('/api/sessions', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ name: newName.trim() }),
      });
      if (!res.ok) {
        const e = await res.json();
        throw new Error(e.error || 'Failed');
      }
      setNewName('');
      loadSession();
    } catch (e) {
      setErr(e instanceof Error ? e.message : 'Failed');
    } finally {
      setBusy(false);
    }
  };

  const closeAndExport = async () => {
    if (!session) return;
    if (!confirm(`Close session "${session.name}" and download the export?`)) return;
    setBusy(true); setErr(null);
    try {
      const res = await fetch(`/api/sessions/${session.id}`, { method: 'PATCH' });
      if (!res.ok) {
        const e = await res.json();
        throw new Error(e.error || 'Failed to close');
      }
      // Trigger export download
      triggerDownload(`/api/sessions/${session.id}/export`);
      loadSession();
      loadRecords();
    } catch (e) {
      setErr(e instanceof Error ? e.message : 'Failed');
    } finally {
      setBusy(false);
    }
  };

  // Filter records to active session if any
  const scopedRecords = useMemo(() => {
    if (session) return records.filter((r) => r.session_id === session.id);
    return records;
  }, [records, session]);

  const stats = useMemo(() => {
    const src = scopedRecords;
    const now = new Date();
    const mm = String(now.getMonth() + 1).padStart(2, '0');
    const yyyy = String(now.getFullYear());

    const thisMonth = src.filter((r) => {
      const parts = r.date.split('-');
      return parts.length === 3 && parts[1] === mm && parts[2] === yyyy;
    });

    const activeCustomers = new Set(thisMonth.map((r) => r.customer)).size;
    const totalPosts = thisMonth.reduce((s, r) => s + (r.num_posts || 0), 0);

    const brandCounts: Record<string, number> = {};
    src.forEach((r) => {
      parseBrands(r.brands).forEach((b) => {
        brandCounts[b] = (brandCounts[b] || 0) + 1;
      });
    });
    const topBrandEntry = Object.entries(brandCounts).sort((a, b) => b[1] - a[1])[0];

    const activity = Array.from({ length: 30 }, (_, i) => {
      const d = new Date();
      d.setDate(d.getDate() - (29 - i));
      const dd = String(d.getDate()).padStart(2, '0');
      const dMm = String(d.getMonth() + 1).padStart(2, '0');
      const yy = String(d.getFullYear());
      const dayStr = `${dd}-${dMm}-${yy}`;
      return { date: `${dMm}/${dd}`, count: src.filter((r) => r.date === dayStr).length };
    });

    const totalMax = topBrandEntry ? topBrandEntry[1] : 1;
    const topBrands = Object.entries(brandCounts)
      .sort((a, b) => b[1] - a[1])
      .slice(0, 5)
      .map(([name, count]) => ({ name, count, pct: Math.round((count / totalMax) * 100) }));

    return { activeCustomers, totalPosts, topBrandEntry, activity, topBrands };
  }, [scopedRecords]);

  const sessionFileCount = useMemo(() => {
    return scopedRecords.reduce((sum, r) => {
      try { return sum + (JSON.parse(r.files) as string[]).length; } catch { return sum; }
    }, 0);
  }, [scopedRecords]);

  const recentRecords = scopedRecords.slice(0, 8);

  return (
    <div style={{ padding: '28px 32px 40px' }}>
      {/* Active session hero */}
      <div style={{ marginBottom: '24px' }}>
        {sessionLoading ? (
          <div style={{
            backgroundColor: '#FFFFFF', borderRadius: '16px',
            border: '1px solid #EAECF0', padding: '24px',
            fontFamily: 'var(--font-body)', color: '#98A2B3',
          }}>Loading session…</div>
        ) : session ? (
          <div style={{
            position: 'relative',
            background: 'linear-gradient(135deg, #FFFFFF 0%, #F5F9FF 100%)',
            borderRadius: '16px',
            border: '1px solid #B2CCFF',
            padding: '24px 28px',
            boxShadow: '0 4px 18px rgba(45,111,247,0.10)',
            display: 'flex', alignItems: 'center', gap: '24px', flexWrap: 'wrap',
          }}>
            <div style={{
              width: 56, height: 56, borderRadius: 14,
              background: 'linear-gradient(135deg, #2D6FF7 0%, #4F8AFF 100%)',
              display: 'flex', alignItems: 'center', justifyContent: 'center',
              boxShadow: '0 4px 14px rgba(45,111,247,0.4)',
              flexShrink: 0,
            }}>
              <Layers size={26} color="#FFFFFF" strokeWidth={2.25} />
            </div>
            <div style={{ flex: 1, minWidth: 240 }}>
              <div style={{
                display: 'inline-flex', alignItems: 'center', gap: 6,
                fontFamily: 'var(--font-body)', fontWeight: 600,
                fontSize: '11px', color: '#12B76A',
                background: '#ECFDF3', padding: '3px 9px', borderRadius: 9999,
                marginBottom: 6,
              }}>
                <span style={{ width: 6, height: 6, borderRadius: '50%', background: '#12B76A', display: 'inline-block' }} />
                ACTIVE SESSION
              </div>
              <h2 style={{
                fontFamily: 'var(--font-display)', fontWeight: 800,
                fontSize: '22px', color: '#101828', marginBottom: '4px',
              }}>
                {session.name}
              </h2>
              <div style={{ display: 'flex', alignItems: 'center', gap: 18, flexWrap: 'wrap', fontFamily: 'var(--font-body)', fontSize: '13px', color: '#475467' }}>
                <span style={{ display: 'inline-flex', alignItems: 'center', gap: 6 }}>
                  <Clock size={13} /> Started {new Date(session.created_at).toLocaleString()}
                </span>
                <span style={{ display: 'inline-flex', alignItems: 'center', gap: 6 }}>
                  <FileText size={13} /> {scopedRecords.length} record{scopedRecords.length !== 1 ? 's' : ''} · {sessionFileCount} file{sessionFileCount !== 1 ? 's' : ''}
                </span>
              </div>
            </div>
            <button
              onClick={closeAndExport}
              disabled={busy}
              style={{
                padding: '13px 22px',
                background: 'linear-gradient(135deg, #2D6FF7 0%, #4F8AFF 100%)',
                color: '#FFFFFF', border: 'none', borderRadius: '12px',
                fontFamily: 'var(--font-display)', fontWeight: 700, fontSize: '14px',
                cursor: busy ? 'not-allowed' : 'pointer',
                display: 'inline-flex', alignItems: 'center', gap: 8,
                boxShadow: '0 4px 14px rgba(45,111,247,0.35)',
                opacity: busy ? 0.6 : 1,
              }}
            >
              {busy ? <Loader2 size={15} style={{ animation: 'spin 1s linear infinite' }} /> : <Download size={15} />}
              Close &amp; Export Session
            </button>
          </div>
        ) : (
          <div style={{
            backgroundColor: '#FFFFFF', borderRadius: '16px',
            border: '1px solid #EAECF0', padding: '24px 28px',
            display: 'flex', alignItems: 'center', gap: '20px', flexWrap: 'wrap',
          }}>
            <div style={{
              width: 52, height: 52, borderRadius: 12,
              background: 'linear-gradient(135deg, #EFF4FF 0%, #DBE7FF 100%)',
              display: 'flex', alignItems: 'center', justifyContent: 'center',
              flexShrink: 0,
            }}>
              <Layers size={24} color="#2D6FF7" />
            </div>
            <div style={{ flex: 1, minWidth: 220 }}>
              <h2 style={{ fontFamily: 'var(--font-display)', fontWeight: 700, fontSize: '18px', color: '#101828' }}>
                No active session
              </h2>
              <p style={{ fontFamily: 'var(--font-body)', fontSize: '13px', color: '#98A2B3' }}>
                Start a session to begin uploading content.
              </p>
            </div>
            <input
              type="text" value={newName} onChange={(e) => setNewName(e.target.value)}
              placeholder="e.g. Week 20, Daily Check 18-May…"
              onKeyDown={(e) => { if (e.key === 'Enter') startSession(); }}
              style={{
                padding: '11px 14px', border: '1px solid #D0D5DD',
                borderRadius: '10px', fontFamily: 'var(--font-body)',
                fontSize: '14px', minWidth: '240px', outline: 'none',
              }}
            />
            <button
              onClick={startSession}
              disabled={busy || !newName.trim()}
              style={{
                padding: '11px 20px',
                background: newName.trim() && !busy
                  ? 'linear-gradient(135deg, #2D6FF7 0%, #4F8AFF 100%)'
                  : '#F2F4F7',
                color: newName.trim() && !busy ? '#FFFFFF' : '#98A2B3',
                border: 'none', borderRadius: '10px',
                fontFamily: 'var(--font-display)', fontWeight: 700, fontSize: '14px',
                cursor: newName.trim() && !busy ? 'pointer' : 'not-allowed',
                display: 'inline-flex', alignItems: 'center', gap: 8,
                boxShadow: newName.trim() && !busy ? '0 4px 14px rgba(45,111,247,0.35)' : 'none',
              }}
            >
              <Plus size={15} /> Start New Session
            </button>
          </div>
        )}
        {err && (
          <div style={{
            marginTop: 12, padding: '10px 14px', borderRadius: 10,
            backgroundColor: '#FEF3F2', border: '1px solid #FDA29B',
            fontFamily: 'var(--font-body)', fontSize: 13, color: '#B42318',
          }}>{err}</div>
        )}
      </div>

      <div style={{
        display: 'grid', gridTemplateColumns: 'repeat(4, 1fr)',
        gap: '20px', marginBottom: '24px',
      }}>
        <StatCard
          label={session ? 'Session Records' : 'Total Records'}
          value={loading ? '—' : scopedRecords.length.toLocaleString()}
          change="+12%" changePositive
          sparkData={[3, 5, 4, 7, 6, 8, 10, 9, 12]} loading={loading} />
        <StatCard
          label="Active Customers"
          value={loading ? '—' : String(stats.activeCustomers)}
          change="+4%" changePositive
          sparkData={[2, 4, 3, 5, 4, 6, 5, 7, 8]} loading={loading} />
        <StatCard
          label="Posts This Month"
          value={loading ? '—' : stats.totalPosts.toLocaleString()}
          change="+18%" changePositive
          sparkData={[10, 20, 15, 25, 30, 22, 35, 28, 40]} loading={loading} />
        <StatCard
          label="Most Active Brand"
          value={loading ? '—' : (stats.topBrandEntry?.[0] ?? '—')}
          change={`${stats.topBrandEntry?.[1] ?? 0} records`} changePositive
          sparkData={[5, 8, 6, 9, 7, 10, 8, 12, 11]} loading={loading} isBrand />
      </div>

      <div style={{ display: 'grid', gridTemplateColumns: '65% 1fr', gap: '20px', marginBottom: '24px' }}>
        <UploadChart data={stats.activity} loading={loading} />
        <TopBrandsList brands={stats.topBrands} loading={loading} />
      </div>

      {/* Monthly Summary Report */}
      <div style={{
        backgroundColor: '#FFFFFF', borderRadius: '16px',
        border: '1px solid #EAECF0', boxShadow: '0 1px 2px rgba(16,24,40,0.04)',
        padding: '22px 28px', marginBottom: '24px',
        display: 'flex', alignItems: 'center', gap: '20px', flexWrap: 'wrap',
      }}>
        <div style={{
          width: 48, height: 48, borderRadius: 12,
          background: 'linear-gradient(135deg, #FFF4ED 0%, #FFE0CC 100%)',
          display: 'flex', alignItems: 'center', justifyContent: 'center',
          flexShrink: 0,
        }}>
          <FileSpreadsheet size={22} color="#FF5C35" />
        </div>
        <div style={{ flex: 1, minWidth: 220 }}>
          <h2 style={{ fontFamily: 'var(--font-display)', fontWeight: 700, fontSize: '17px', color: '#101828', marginBottom: 3 }}>
            Monthly Summary Report
          </h2>
          <p style={{ fontFamily: 'var(--font-body)', fontSize: '13px', color: '#98A2B3' }}>
            Export an Excel with active partners, brands posted, and total posts per partner.
          </p>
        </div>
        <div style={{ display: 'inline-flex', alignItems: 'center', gap: 8 }}>
          <Calendar size={14} color="#98A2B3" />
          <select
            value={reportMonth}
            onChange={(e) => setReportMonth(parseInt(e.target.value, 10))}
            style={{
              padding: '9px 12px', border: '1px solid #D0D5DD', borderRadius: 10,
              fontFamily: 'var(--font-body)', fontSize: 13, color: '#101828',
              backgroundColor: '#FFFFFF', outline: 'none', cursor: 'pointer',
            }}
          >
            {['January','February','March','April','May','June','July','August','September','October','November','December'].map((m, i) => (
              <option key={m} value={i + 1}>{m}</option>
            ))}
          </select>
          <select
            value={reportYear}
            onChange={(e) => setReportYear(parseInt(e.target.value, 10))}
            style={{
              padding: '9px 12px', border: '1px solid #D0D5DD', borderRadius: 10,
              fontFamily: 'var(--font-body)', fontSize: 13, color: '#101828',
              backgroundColor: '#FFFFFF', outline: 'none', cursor: 'pointer',
            }}
          >
            {Array.from({ length: 6 }, (_, i) => _now.getFullYear() - 2 + i).map((y) => (
              <option key={y} value={y}>{y}</option>
            ))}
          </select>
        </div>
        <button
          onClick={downloadMonthlyReport}
          disabled={reportBusy}
          style={{
            padding: '11px 20px',
            background: 'linear-gradient(135deg, #FF5C35 0%, #FF8059 100%)',
            color: '#FFFFFF', border: 'none', borderRadius: 10,
            fontFamily: 'var(--font-display)', fontWeight: 700, fontSize: 13,
            cursor: reportBusy ? 'not-allowed' : 'pointer',
            display: 'inline-flex', alignItems: 'center', gap: 8,
            boxShadow: '0 4px 14px rgba(255,92,53,0.30)',
            opacity: reportBusy ? 0.7 : 1,
          }}
        >
          {reportBusy ? <Loader2 size={14} style={{ animation: 'spin 1s linear infinite' }} /> : <Download size={14} />}
          Export Report
        </button>
      </div>

      <div style={{
        backgroundColor: '#FFFFFF', borderRadius: '16px',
        border: '1px solid #EAECF0', boxShadow: '0 1px 2px rgba(16,24,40,0.04)', overflow: 'hidden',
      }}>
        <div style={{
          padding: '18px 24px', borderBottom: '1px solid #EAECF0',
          display: 'flex', alignItems: 'center', justifyContent: 'space-between',
        }}>
          <h2 style={{ fontFamily: 'var(--font-display)', fontWeight: 700, fontSize: '16px', color: '#101828' }}>
            {session ? `Recent Uploads — ${session.name}` : 'Recent Uploads'}
          </h2>
          <Link href="/records" style={{ fontFamily: 'var(--font-body)', fontWeight: 600, fontSize: '13px', color: '#2D6FF7', textDecoration: 'none' }}>
            View all →
          </Link>
        </div>

        {loading ? (
          <div style={{ padding: '48px', textAlign: 'center', fontFamily: 'var(--font-body)', fontSize: '14px', color: '#98A2B3' }}>Loading…</div>
        ) : recentRecords.length === 0 ? (
          <div style={{ padding: '48px', textAlign: 'center', fontFamily: 'var(--font-body)', fontSize: '14px', color: '#98A2B3' }}>
            No uploads yet — go to{' '}
            <Link href="/upload" style={{ color: '#2D6FF7' }}>Upload</Link> to get started.
          </div>
        ) : (
          <div style={{ overflowX: 'auto' }}>
            <table style={{ width: '100%', borderCollapse: 'collapse', minWidth: '700px' }}>
              <thead>
                <tr style={{ backgroundColor: '#F9FAFB' }}>
                  <th style={thStyle}>Customer</th>
                  <th style={thStyle}>Brands</th>
                  <th style={thStyle}>Type</th>
                  <th style={thStyle}>Content Type</th>
                  <th style={{ ...thStyle, textAlign: 'center' }}>Posts</th>
                  <th style={thStyle}>Date</th>
                </tr>
              </thead>
              <tbody>
                {recentRecords.map((r) => {
                  const custPalette = avatarPalette(r.customer);
                  const brandList = parseBrands(r.brands);
                  return (
                    <tr key={r.id}>
                      <td style={tdStyle}>
                        <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
                          <div style={{ width: '28px', height: '28px', borderRadius: '50%', backgroundColor: custPalette.bg, color: custPalette.text, display: 'flex', alignItems: 'center', justifyContent: 'center', fontSize: '10px', fontWeight: 700, flexShrink: 0 }}>
                            {initials(r.customer)}
                          </div>
                          <span style={{ fontWeight: 500 }}>{r.customer}</span>
                        </div>
                      </td>
                      <td style={tdStyle}>
                        <div style={{ display: 'flex', flexWrap: 'wrap', gap: '4px' }}>
                          {brandList.slice(0, 3).map((b) => (
                            <span key={b} style={{
                              display: 'inline-block', padding: '2px 9px',
                              backgroundColor: '#EFF4FF', color: '#2D6FF7',
                              borderRadius: 9999, fontSize: 11, fontWeight: 600,
                              fontFamily: 'var(--font-body)',
                            }}>{b}</span>
                          ))}
                          {brandList.length > 3 && (
                            <span style={{ fontSize: 11, color: '#98A2B3', fontFamily: 'var(--font-body)' }}>
                              +{brandList.length - 3} more
                            </span>
                          )}
                        </div>
                      </td>
                      <td style={tdStyle}><TypePill value={r.type} /></td>
                      <td style={{ ...tdStyle, color: '#475467', fontSize: '12px' }}>{r.content_type}</td>
                      <td style={{ ...tdStyle, textAlign: 'center', fontWeight: 700, fontFamily: 'var(--font-display)' }}>{r.num_posts}</td>
                      <td style={{ ...tdStyle, color: '#98A2B3', fontSize: '12px', fontFamily: 'monospace' }}>{r.date}</td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </div>
  );
}
