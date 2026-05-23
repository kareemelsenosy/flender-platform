'use client';

import { useState, useCallback, useEffect } from 'react';
import Link from 'next/link';
import UploadZone from '@/components/UploadZone';
import MetadataForm from '@/components/MetadataForm';
import { CheckCircle, AlertTriangle, X, Layers, LayoutDashboard } from 'lucide-react';
import { apiFetch } from '@/lib/api';

interface FormPayload {
  customer: string;
  brands: string[];
  date: string;
  type: string;
  content_type: string;
  content_source: string;
}

interface SessionInfo {
  id: string;
  name: string;
  status: 'open' | 'closed';
  created_at: string;
}

export default function UploadPage() {
  const [files, setFiles] = useState<File[]>([]);
  const [isLoading, setIsLoading] = useState(false);
  const [isSuccess, setIsSuccess] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [session, setSession] = useState<SessionInfo | null>(null);
  const [sessionLoading, setSessionLoading] = useState(true);

  const refreshSession = useCallback(() => {
    setSessionLoading(true);
    apiFetch('/api/sessions/current')
      .then((r) => r.json())
      .then((data) => setSession(data || null))
      .catch(() => setSession(null))
      .finally(() => setSessionLoading(false));
  }, []);

  useEffect(() => { refreshSession(); }, [refreshSession]);

  const handleSubmit = useCallback(
    async (formData: FormPayload) => {
      if (!session) {
        setError('No active session');
        return;
      }
      if (files.length === 0) {
        setError('Please add at least one file');
        return;
      }

      setIsLoading(true);
      setError(null);
      setIsSuccess(false);

      try {
        const data = new FormData();
        data.append('session_id', session.id);
        data.append('customer', formData.customer);
        data.append('brands', JSON.stringify(formData.brands));
        data.append('date', formData.date);
        data.append('type', formData.type);
        data.append('content_type', formData.content_type);
        data.append('content_source', formData.content_source);

        for (const file of files) {
          data.append('files[]', file);
        }

        const res = await apiFetch('/api/upload', { method: 'POST', body: data });
        if (!res.ok) {
          const err = await res.json();
          throw new Error(err.error || 'Upload failed');
        }

        setIsSuccess(true);
        setFiles([]);
        setTimeout(() => setIsSuccess(false), 4000);
      } catch (err) {
        setError(err instanceof Error ? err.message : 'Upload failed');
      } finally {
        setIsLoading(false);
      }
    },
    [files, session]
  );

  if (sessionLoading) {
    return (
      <div style={{ padding: '64px', textAlign: 'center', fontFamily: 'var(--font-body)', color: '#98A2B3' }}>
        Loading…
      </div>
    );
  }

  if (!session) {
    return (
      <div style={{ padding: '60px 32px', display: 'flex', justifyContent: 'center' }}>
        <div style={{
          maxWidth: '480px', width: '100%', backgroundColor: '#FFFFFF',
          borderRadius: '16px', border: '1px solid #EAECF0',
          boxShadow: '0 1px 2px rgba(16,24,40,0.04)',
          padding: '32px', textAlign: 'center',
        }}>
          <div style={{
            width: 52, height: 52, borderRadius: 12, margin: '0 auto 16px',
            background: 'linear-gradient(135deg, #EFF4FF 0%, #DBE7FF 100%)',
            display: 'flex', alignItems: 'center', justifyContent: 'center',
          }}>
            <Layers size={24} color="#2D6FF7" />
          </div>
          <h2 style={{ fontFamily: 'var(--font-display)', fontWeight: 700, fontSize: '20px', color: '#101828', marginBottom: '6px' }}>
            No active session
          </h2>
          <p style={{ fontFamily: 'var(--font-body)', fontSize: '14px', color: '#475467', marginBottom: '20px' }}>
            Please start a session on the dashboard before uploading.
          </p>
          <Link href="/" style={{
            display: 'inline-flex', alignItems: 'center', gap: '8px',
            padding: '11px 20px', background: 'linear-gradient(135deg, #2D6FF7 0%, #4F8AFF 100%)',
            color: '#FFFFFF', borderRadius: '10px', textDecoration: 'none',
            fontFamily: 'var(--font-display)', fontWeight: 700, fontSize: '14px',
            boxShadow: '0 4px 14px rgba(45,111,247,0.35)',
          }}>
            <LayoutDashboard size={15} /> Go to Dashboard
          </Link>
        </div>
      </div>
    );
  }

  return (
    <div style={{ padding: '28px 32px 40px', maxWidth: '1300px' }}>
      {/* Session badge */}
      <div style={{
        marginBottom: '20px', padding: '12px 18px',
        backgroundColor: '#EFF4FF', border: '1px solid #B2CCFF',
        borderRadius: '12px', display: 'flex', alignItems: 'center', gap: '10px',
      }}>
        <Layers size={16} color="#2D6FF7" />
        <span style={{ fontFamily: 'var(--font-body)', fontSize: '13px', color: '#1849A9', fontWeight: 600, flex: 1 }}>
          Adding to session: <span style={{ color: '#101828' }}>{session.name}</span>
        </span>
        <Link href="/" style={{ fontFamily: 'var(--font-body)', fontSize: '13px', fontWeight: 600, color: '#2D6FF7', textDecoration: 'none' }}>
          Close Session →
        </Link>
      </div>

      {isSuccess && (
        <div className="animate-slide-up" style={{
          marginBottom: '20px', padding: '14px 18px',
          backgroundColor: '#ECFDF3', border: '1px solid #6CE9A6',
          borderRadius: '12px', display: 'flex', alignItems: 'center', gap: '10px',
        }}>
          <CheckCircle size={16} color="#12B76A" />
          <span style={{ fontFamily: 'var(--font-body)', fontSize: '14px', color: '#027A48', fontWeight: 500, flex: 1 }}>
            Record saved successfully — files renamed and stored.
          </span>
        </div>
      )}

      {error && (
        <div className="animate-slide-up" style={{
          marginBottom: '20px', padding: '14px 18px',
          backgroundColor: '#FEF3F2', border: '1px solid #FDA29B',
          borderRadius: '12px', display: 'flex', alignItems: 'center', gap: '10px',
        }}>
          <AlertTriangle size={16} color="#F04438" />
          <span style={{ fontFamily: 'var(--font-body)', fontSize: '14px', color: '#B42318', fontWeight: 500, flex: 1 }}>
            {error}
          </span>
          <button onClick={() => setError(null)}
            style={{ background: 'none', border: 'none', cursor: 'pointer', padding: '2px', display: 'flex', alignItems: 'center' }}>
            <X size={14} color="#B42318" />
          </button>
        </div>
      )}

      <div style={{ display: 'grid', gridTemplateColumns: '60% 1fr', gap: '20px' }}>
        <div style={{
          backgroundColor: '#FFFFFF', borderRadius: '16px', border: '1px solid #EAECF0',
          boxShadow: '0 1px 2px rgba(16,24,40,0.04)', padding: '24px',
          display: 'flex', flexDirection: 'column',
        }}>
          <div style={{ marginBottom: '16px', paddingBottom: '16px', borderBottom: '1px solid #EAECF0' }}>
            <h2 style={{ fontFamily: 'var(--font-display)', fontWeight: 700, fontSize: '16px', color: '#101828', marginBottom: '2px' }}>
              Drop Zone
            </h2>
            <p style={{ fontFamily: 'var(--font-body)', fontSize: '13px', color: '#98A2B3' }}>
              Drag and drop screenshots or screen recordings
            </p>
          </div>
          <div style={{ flex: 1 }}>
            <UploadZone files={files} onFilesChange={setFiles} />
          </div>
        </div>

        <div style={{
          backgroundColor: '#FFFFFF', borderRadius: '16px', border: '1px solid #EAECF0',
          boxShadow: '0 1px 2px rgba(16,24,40,0.04)', padding: '24px',
        }}>
          <MetadataForm
            onSubmit={handleSubmit}
            isLoading={isLoading}
            isSuccess={isSuccess}
            fileCount={files.length}
          />
        </div>
      </div>
    </div>
  );
}
