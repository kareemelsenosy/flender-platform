'use client';

import { useState, useEffect, useCallback } from 'react';
import { useParams } from 'next/navigation';
import Link from 'next/link';
import { ArrowLeft, Download, Layers, Loader2 } from 'lucide-react';
import RecordsTable from '@/components/RecordsTable';
import { triggerDownload } from '@/lib/utils';
import { apiFetch } from '@/lib/api';
import { useConfirm } from '@/components/ConfirmDialog';

interface SessionDetail {
  id: string;
  name: string;
  status: 'open' | 'closed';
  created_at: string;
  closed_at: string | null;
}

export default function SessionDetailPage() {
  const confirm = useConfirm();
  const params = useParams<{ id: string }>();
  const id = params?.id;
  const [session, setSession] = useState<SessionDetail | null>(null);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  const load = useCallback(() => {
    if (!id) return;
    setLoading(true);
    apiFetch(`/api/sessions/${id}`)
      .then((r) => r.json())
      .then(setSession)
      .catch(() => {})
      .finally(() => setLoading(false));
  }, [id]);

  useEffect(() => { load(); }, [load]);

  const closeAndExport = async () => {
    if (!session) return;
    const ok = await confirm({
      title: `Close session "${session.name}"?`,
      message: 'Closing will finalize the session and start the export download.',
      confirmLabel: 'Close & export',
    });
    if (!ok) return;
    setBusy(true); setErr(null);
    try {
      const res = await apiFetch(`/api/sessions/${session.id}`, { method: 'PATCH' });
      if (!res.ok) {
        const e = await res.json();
        throw new Error(e.error || 'Failed');
      }
      triggerDownload(`/api/sessions/${session.id}/export`);
      load();
    } catch (e) {
      setErr(e instanceof Error ? e.message : 'Failed');
    } finally {
      setBusy(false);
    }
  };

  if (loading) {
    return <div style={{ padding: 60, textAlign: 'center', fontFamily: 'var(--font-body)', color: '#98A2B3' }}>Loading…</div>;
  }
  if (!session) {
    return (
      <div style={{ padding: 60, textAlign: 'center', fontFamily: 'var(--font-body)', color: '#98A2B3' }}>
        Session not found.
        <div style={{ marginTop: 12 }}>
          <Link href="/sessions" style={{ color: '#2D6FF7' }}>← Back to sessions</Link>
        </div>
      </div>
    );
  }

  const readOnly = session.status === 'closed';

  return (
    <div style={{ padding: '28px 32px 40px' }}>
      <Link href="/sessions" style={{
        display: 'inline-flex', alignItems: 'center', gap: 6,
        fontFamily: 'var(--font-body)', fontSize: 13, color: '#475467',
        textDecoration: 'none', marginBottom: 14,
      }}>
        <ArrowLeft size={13} /> All sessions
      </Link>

      <div style={{
        backgroundColor: '#FFFFFF', borderRadius: '16px',
        border: session.status === 'open' ? '2px solid #2D6FF7' : '1px solid #EAECF0',
        boxShadow: session.status === 'open'
          ? '0 0 0 4px rgba(45,111,247,0.10), 0 6px 18px rgba(45,111,247,0.12)'
          : '0 1px 2px rgba(16,24,40,0.04)',
        padding: '24px 28px', marginBottom: 20,
        display: 'flex', alignItems: 'center', gap: 20, flexWrap: 'wrap',
      }}>
        <div style={{
          width: 56, height: 56, borderRadius: 14,
          background: session.status === 'open'
            ? 'linear-gradient(135deg, #2D6FF7 0%, #4F8AFF 100%)'
            : 'linear-gradient(135deg, #EFF4FF 0%, #DBE7FF 100%)',
          display: 'flex', alignItems: 'center', justifyContent: 'center',
          flexShrink: 0,
        }}>
          <Layers size={26} color={session.status === 'open' ? '#FFFFFF' : '#2D6FF7'} />
        </div>
        <div style={{ flex: 1, minWidth: 240 }}>
          <div style={{
            display: 'inline-flex', alignItems: 'center', gap: 5,
            padding: '3px 10px',
            borderRadius: 9999,
            fontFamily: 'var(--font-body)', fontWeight: 600, fontSize: 11,
            background: session.status === 'open' ? '#ECFDF3' : '#F2F4F7',
            color: session.status === 'open' ? '#12B76A' : '#475467',
            marginBottom: 6,
          }}>
            <span style={{
              width: 6, height: 6, borderRadius: '50%',
              background: session.status === 'open' ? '#12B76A' : '#98A2B3',
            }} />
            {session.status.toUpperCase()}
          </div>
          <h1 style={{ fontFamily: 'var(--font-display)', fontWeight: 800, fontSize: '22px', color: '#101828', marginBottom: 4 }}>
            {session.name}
          </h1>
          <div style={{ fontFamily: 'var(--font-body)', fontSize: 13, color: '#475467' }}>
            Started {new Date(session.created_at).toLocaleString()}
            {session.closed_at && <> · Closed {new Date(session.closed_at).toLocaleString()}</>}
          </div>
        </div>
        <div style={{ display: 'flex', gap: 8 }}>
          <button onClick={() => triggerDownload(`/api/sessions/${session.id}/export`)}
            style={{
              padding: '12px 20px',
              background: 'linear-gradient(135deg, #2D6FF7 0%, #4F8AFF 100%)',
              color: '#FFFFFF', border: 'none', borderRadius: 12,
              fontFamily: 'var(--font-display)', fontWeight: 700, fontSize: 14,
              cursor: 'pointer', display: 'inline-flex', alignItems: 'center', gap: 8,
              boxShadow: '0 4px 14px rgba(45,111,247,0.35)',
            }}>
            <Download size={15} /> Download Export ZIP
          </button>
          {session.status === 'open' && (
            <button onClick={closeAndExport} disabled={busy}
              style={{
                padding: '12px 16px',
                background: '#FFFFFF', color: '#2D6FF7',
                border: '1px solid #B2CCFF', borderRadius: 12,
                fontFamily: 'var(--font-display)', fontWeight: 700, fontSize: 14,
                cursor: busy ? 'not-allowed' : 'pointer',
                display: 'inline-flex', alignItems: 'center', gap: 6,
                opacity: busy ? 0.6 : 1,
              }}>
              {busy ? <Loader2 size={14} style={{ animation: 'spin 1s linear infinite' }} /> : null}
              Close session
            </button>
          )}
        </div>
      </div>

      {err && (
        <div style={{
          marginBottom: 16, padding: '10px 14px', borderRadius: 10,
          backgroundColor: '#FEF3F2', border: '1px solid #FDA29B',
          fontFamily: 'var(--font-body)', fontSize: 13, color: '#B42318',
        }}>{err}</div>
      )}

      <RecordsTable sessionId={session.id} readOnly={readOnly} />
    </div>
  );
}
