'use client';

import { useState, useEffect } from 'react';
import RecordsTable, { type Filters } from '@/components/RecordsTable';
import { ChevronDown } from 'lucide-react';

interface SessionStub {
  id: string;
  name: string;
  status: 'open' | 'closed';
}

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

export default function RecordsPage() {
  const [, setFilters] = useState<Filters>({
    customer: '', brand: '', startDate: '', endDate: '',
  });
  const [sessions, setSessions] = useState<SessionStub[]>([]);
  const [selectedSession, setSelectedSession] = useState<string>('');

  useEffect(() => {
    fetch('/api/sessions').then((r) => r.json()).then(setSessions).catch(() => {});
  }, []);

  return (
    <div style={{ minHeight: '100vh', display: 'flex', flexDirection: 'column' }}>
      <div style={{ flex: 1, padding: '28px 32px 40px' }}>
        {/* Session filter */}
        <div style={{
          backgroundColor: '#FFFFFF', border: '1px solid #EAECF0',
          borderRadius: '16px', padding: '16px 20px',
          boxShadow: '0 1px 2px rgba(16,24,40,0.04)',
          marginBottom: '16px',
          display: 'flex', alignItems: 'end', gap: 16, flexWrap: 'wrap',
        }}>
          <div style={{ minWidth: 240 }}>
            <label style={labelStyle}>Session</label>
            <div style={{ position: 'relative' }}>
              <select
                value={selectedSession}
                onChange={(e) => setSelectedSession(e.target.value)}
                style={{
                  width: '100%', padding: '9px 36px 9px 12px',
                  backgroundColor: '#FFFFFF', border: '1px solid #EAECF0',
                  borderRadius: '10px', fontFamily: 'var(--font-body)',
                  fontSize: '13px', color: '#101828', appearance: 'none',
                  cursor: 'pointer', outline: 'none',
                }}
              >
                <option value="">All sessions</option>
                {sessions.map((s) => (
                  <option key={s.id} value={s.id}>
                    {s.name} — {s.status}
                  </option>
                ))}
              </select>
              <ChevronDown size={14} color="#98A2B3"
                style={{ position: 'absolute', right: 12, top: '50%', transform: 'translateY(-50%)', pointerEvents: 'none' }} />
            </div>
          </div>
        </div>

        <RecordsTable
          onFiltersChange={setFilters}
          sessionId={selectedSession || undefined}
        />
      </div>
    </div>
  );
}
