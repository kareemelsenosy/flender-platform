'use client';

import { useState, useEffect, useCallback, useMemo } from 'react';
import Link from 'next/link';
import { Layers, Download, Trash2, Eye, Plus, Loader2 } from 'lucide-react';
import { triggerDownload } from '@/lib/utils';
import { apiFetch } from '@/lib/api';
import { useConfirm } from '@/components/ConfirmDialog';

interface Session {
  id: string;
  name: string;
  status: 'open' | 'closed';
  created_at: string;
  closed_at: string | null;
  record_count: number;
  file_count: number;
}

type Filter = 'all' | 'open' | 'closed';

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

export default function SessionsPage() {
  const confirm = useConfirm();
  const [sessions, setSessions] = useState<Session[]>([]);
  const [loading, setLoading] = useState(true);
  const [filter, setFilter] = useState<Filter>('all');
  const [newName, setNewName] = useState('');
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  const load = useCallback(() => {
    setLoading(true);
    apiFetch('/api/sessions').then((r) => r.json()).then(setSessions)
      .catch(() => {})
      .finally(() => setLoading(false));
  }, []);

  useEffect(() => { load(); }, [load]);

  const hasOpen = sessions.some((s) => s.status === 'open');

  const startSession = async () => {
    if (!newName.trim()) return;
    setBusy(true); setErr(null);
    try {
      const res = await apiFetch('/api/sessions', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ name: newName.trim() }),
      });
      if (!res.ok) {
        const e = await res.json();
        throw new Error(e.error || 'Failed');
      }
      setNewName('');
      load();
    } catch (e) {
      setErr(e instanceof Error ? e.message : 'Failed');
    } finally {
      setBusy(false);
    }
  };

  const closeSession = async (s: Session) => {
    const ok = await confirm({
      title: `Close session "${s.name}"?`,
      message: 'Closing will finalize the session and trigger the export download.',
      confirmLabel: 'Close & export',
    });
    if (!ok) return;
    setBusy(true); setErr(null);
    try {
      const res = await apiFetch(`/api/sessions/${s.id}`, { method: 'PATCH' });
      if (!res.ok) {
        const e = await res.json();
        throw new Error(e.error || 'Failed');
      }
      triggerDownload(`/api/sessions/${s.id}/export`);
      load();
    } catch (e) {
      setErr(e instanceof Error ? e.message : 'Failed');
    } finally {
      setBusy(false);
    }
  };

  const deleteSession = async (s: Session) => {
    const ok = await confirm({
      title: `Delete session "${s.name}"?`,
      message: 'The session and all associated files will be permanently removed. This cannot be undone.',
      danger: true,
      confirmLabel: 'Delete session',
    });
    if (!ok) return;
    setBusy(true); setErr(null);
    try {
      const res = await apiFetch(`/api/sessions/${s.id}`, { method: 'DELETE' });
      if (!res.ok) {
        const e = await res.json();
        throw new Error(e.error || 'Failed');
      }
      load();
    } catch (e) {
      setErr(e instanceof Error ? e.message : 'Failed');
    } finally {
      setBusy(false);
    }
  };

  const filtered = useMemo(() => {
    let list = sessions;
    if (filter === 'open') list = list.filter((s) => s.status === 'open');
    if (filter === 'closed') list = list.filter((s) => s.status === 'closed');
    return list;
  }, [sessions, filter]);

  return (
    <div style={{ padding: '28px 32px 40px' }}>
      {/* Header + new session */}
      <div style={{
        backgroundColor: '#FFFFFF', borderRadius: '16px',
        border: '1px solid #EAECF0', boxShadow: '0 1px 2px rgba(16,24,40,0.04)',
        padding: '20px 24px', marginBottom: '20px',
        display: 'flex', alignItems: 'center', gap: 20, flexWrap: 'wrap',
      }}>
        <div style={{ flex: 1, minWidth: 220 }}>
          <h1 style={{ fontFamily: 'var(--font-display)', fontWeight: 800, fontSize: '20px', color: '#101828' }}>
            Sessions
          </h1>
          <p style={{ fontFamily: 'var(--font-body)', fontSize: '13px', color: '#98A2B3' }}>
            One open session at a time. Close it to download the export.
          </p>
        </div>

        {!hasOpen && (
          <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
            <input
              type="text" value={newName} onChange={(e) => setNewName(e.target.value)}
              placeholder="New session name…"
              onKeyDown={(e) => { if (e.key === 'Enter') startSession(); }}
              style={{
                padding: '10px 14px', border: '1px solid #D0D5DD',
                borderRadius: '10px', fontFamily: 'var(--font-body)',
                fontSize: '13px', minWidth: '240px', outline: 'none',
              }}
            />
            <button onClick={startSession} disabled={busy || !newName.trim()}
              style={{
                padding: '10px 18px',
                background: newName.trim() && !busy
                  ? 'linear-gradient(135deg, #2D6FF7 0%, #4F8AFF 100%)'
                  : '#F2F4F7',
                color: newName.trim() && !busy ? '#FFFFFF' : '#98A2B3',
                border: 'none', borderRadius: '10px',
                fontFamily: 'var(--font-display)', fontWeight: 700, fontSize: '13px',
                cursor: newName.trim() && !busy ? 'pointer' : 'not-allowed',
                display: 'inline-flex', alignItems: 'center', gap: 6,
                boxShadow: newName.trim() && !busy ? '0 4px 14px rgba(45,111,247,0.35)' : 'none',
              }}>
              <Plus size={14} /> Start
            </button>
          </div>
        )}
      </div>

      {/* Filter tabs */}
      <div style={{ display: 'flex', gap: 8, marginBottom: 16 }}>
        {(['all', 'open', 'closed'] as Filter[]).map((f) => (
          <button key={f} onClick={() => setFilter(f)}
            style={{
              padding: '8px 16px', borderRadius: 9999,
              border: '1px solid', borderColor: filter === f ? '#2D6FF7' : '#EAECF0',
              background: filter === f ? '#2D6FF7' : '#FFFFFF',
              color: filter === f ? '#FFFFFF' : '#475467',
              fontFamily: 'var(--font-body)', fontSize: 13,
              fontWeight: filter === f ? 700 : 500, cursor: 'pointer',
              textTransform: 'capitalize',
            }}>{f}</button>
        ))}
      </div>

      {err && (
        <div style={{
          marginBottom: 16, padding: '10px 14px', borderRadius: 10,
          backgroundColor: '#FEF3F2', border: '1px solid #FDA29B',
          fontFamily: 'var(--font-body)', fontSize: 13, color: '#B42318',
        }}>{err}</div>
      )}

      {/* Sessions list */}
      {loading ? (
        <div style={{ padding: '60px', textAlign: 'center', fontFamily: 'var(--font-body)', color: '#98A2B3' }}>Loading…</div>
      ) : filtered.length === 0 ? (
        <div style={{
          padding: '60px 30px', textAlign: 'center',
          background: '#FFFFFF', borderRadius: '16px', border: '1px solid #EAECF0',
        }}>
          <Layers size={32} color="#98A2B3" style={{ margin: '0 auto 12px', display: 'block' }} />
          <p style={{ fontFamily: 'var(--font-display)', fontWeight: 700, fontSize: '16px', color: '#475467' }}>
            No sessions {filter !== 'all' && `(${filter})`} yet
          </p>
          <p style={{ fontFamily: 'var(--font-body)', fontSize: '13px', color: '#98A2B3', marginTop: 4 }}>
            Start a session to begin uploading content.
          </p>
        </div>
      ) : (
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(340px, 1fr))', gap: 16 }}>
          {filtered.map((s) => (
            <div key={s.id} style={{
              backgroundColor: '#FFFFFF',
              border: s.status === 'open' ? '2px solid #2D6FF7' : '1px solid #EAECF0',
              borderRadius: '16px',
              boxShadow: s.status === 'open'
                ? '0 0 0 4px rgba(45,111,247,0.10), 0 6px 18px rgba(45,111,247,0.12)'
                : '0 1px 2px rgba(16,24,40,0.04)',
              padding: '20px',
              display: 'flex', flexDirection: 'column', gap: 12,
            }}>
              <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 8 }}>
                <h3 style={{
                  fontFamily: 'var(--font-display)', fontWeight: 800,
                  fontSize: '17px', color: '#101828', flex: 1,
                  overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap',
                }}>{s.name}</h3>
                <span style={{
                  display: 'inline-flex', alignItems: 'center', gap: 5,
                  padding: '3px 10px',
                  borderRadius: 9999,
                  fontFamily: 'var(--font-body)', fontWeight: 600, fontSize: 11,
                  background: s.status === 'open' ? '#ECFDF3' : '#F2F4F7',
                  color: s.status === 'open' ? '#12B76A' : '#475467',
                }}>
                  <span style={{
                    width: 6, height: 6, borderRadius: '50%',
                    background: s.status === 'open' ? '#12B76A' : '#98A2B3',
                  }} />
                  {s.status.toUpperCase()}
                </span>
              </div>

              <div style={{ fontFamily: 'var(--font-body)', fontSize: 12, color: '#475467', lineHeight: 1.6 }}>
                <div>Started: <span style={{ color: '#101828' }}>{new Date(s.created_at).toLocaleString()}</span></div>
                {s.closed_at && <div>Closed: <span style={{ color: '#101828' }}>{new Date(s.closed_at).toLocaleString()}</span></div>}
              </div>

              <div style={{ display: 'flex', gap: 14, fontFamily: 'var(--font-body)', fontSize: 12, color: '#475467' }}>
                <span><strong style={{ color: '#101828', fontFamily: 'var(--font-display)' }}>{s.record_count}</strong> records</span>
                <span><strong style={{ color: '#101828', fontFamily: 'var(--font-display)' }}>{s.file_count}</strong> files</span>
              </div>

              <div style={{ display: 'flex', gap: 6, marginTop: 'auto', paddingTop: 8 }}>
                <Link href={`/sessions/${s.id}`} style={{
                  flex: 1, display: 'inline-flex', alignItems: 'center', justifyContent: 'center', gap: 5,
                  padding: '8px 12px', borderRadius: 10,
                  border: '1px solid #EAECF0', background: '#F9FAFB',
                  fontFamily: 'var(--font-body)', fontWeight: 600, fontSize: 12,
                  color: '#475467', textDecoration: 'none',
                }}>
                  <Eye size={12} /> Open
                </Link>
                <button onClick={() => triggerDownload(`/api/sessions/${s.id}/export`)}
                  title="Download export"
                  style={{
                    padding: '8px 12px', borderRadius: 10,
                    border: '1px solid #B2CCFF', background: '#EFF4FF',
                    color: '#2D6FF7', cursor: 'pointer',
                    display: 'inline-flex', alignItems: 'center', gap: 5,
                    fontFamily: 'var(--font-body)', fontWeight: 600, fontSize: 12,
                  }}>
                  <Download size={12} />
                </button>
                {s.status === 'open' && (
                  <button onClick={() => closeSession(s)} disabled={busy}
                    title="Close and export"
                    style={{
                      padding: '8px 12px', borderRadius: 10, border: 'none',
                      background: 'linear-gradient(135deg, #2D6FF7 0%, #4F8AFF 100%)',
                      color: '#FFFFFF', cursor: busy ? 'not-allowed' : 'pointer',
                      fontFamily: 'var(--font-body)', fontWeight: 700, fontSize: 12,
                      display: 'inline-flex', alignItems: 'center', gap: 5,
                      boxShadow: '0 2px 8px rgba(45,111,247,0.3)',
                      opacity: busy ? 0.6 : 1,
                    }}>
                    {busy ? <Loader2 size={12} style={{ animation: 'spin 1s linear infinite' }} /> : 'Close'}
                  </button>
                )}
                <button onClick={() => deleteSession(s)} disabled={busy}
                  title="Delete session"
                  style={{
                    padding: '8px 10px', borderRadius: 10,
                    border: '1px solid #EAECF0', background: '#F9FAFB',
                    color: '#F04438', cursor: busy ? 'not-allowed' : 'pointer',
                    display: 'inline-flex', alignItems: 'center',
                  }}>
                  <Trash2 size={12} />
                </button>
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
