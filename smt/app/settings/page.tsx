'use client';

import { useEffect, useState } from 'react';
import { Database, HardDrive, Save, FileText } from 'lucide-react';
import { apiFetch } from '@/lib/api';

const cardStyle: React.CSSProperties = {
  backgroundColor: '#FFFFFF',
  borderRadius: '16px',
  border: '1px solid #EAECF0',
  boxShadow: '0 1px 2px rgba(16,24,40,0.04)',
  padding: '28px 32px',
  marginBottom: '24px',
};

const cardTitleStyle: React.CSSProperties = {
  fontFamily: 'var(--font-display)',
  fontWeight: 700,
  fontSize: '16px',
  color: '#101828',
  marginBottom: '20px',
};

const labelStyle: React.CSSProperties = {
  fontFamily: 'var(--font-body)',
  fontSize: '11px',
  fontWeight: 600,
  color: '#98A2B3',
  letterSpacing: '0.06em',
  textTransform: 'uppercase',
  marginBottom: '6px',
};

const valueStyle: React.CSSProperties = {
  fontFamily: 'var(--font-body)',
  fontSize: '14px',
  color: '#101828',
  fontWeight: 500,
};

const chipStyle: React.CSSProperties = {
  display: 'inline-block',
  backgroundColor: '#F2F4F7',
  color: '#344054',
  padding: '4px 10px',
  borderRadius: 9999,
  fontFamily: 'var(--font-body)',
  fontSize: 12,
  fontWeight: 500,
};

export default function SettingsPage() {
  const [customers, setCustomers] = useState<string[]>([]);
  const [brands, setBrands] = useState<string[]>([]);
  const [loadingLists, setLoadingLists] = useState(true);

  useEffect(() => {
    Promise.all([
      apiFetch('/api/customers').then((r) => r.json()).catch(() => []),
      apiFetch('/api/brands').then((r) => r.json()).catch(() => []),
    ])
      .then(([c, b]) => {
        setCustomers(Array.isArray(c) ? c : []);
        setBrands(Array.isArray(b) ? b : []);
      })
      .finally(() => setLoadingLists(false));
  }, []);

  return (
    <div style={{ padding: '28px 32px 40px' }}>
      <div style={{ marginBottom: '24px' }}>
        <h1
          style={{
            fontFamily: 'var(--font-display)',
            fontWeight: 800,
            fontSize: '24px',
            color: '#101828',
            marginBottom: '4px',
          }}
        >
          Settings
        </h1>
        <p
          style={{
            fontFamily: 'var(--font-body)',
            fontSize: '14px',
            color: '#667085',
          }}
        >
          Application info, saved entities, and data management.
        </p>
      </div>

      {/* Application Info */}
      <div style={cardStyle}>
        <h2 style={cardTitleStyle}>Application</h2>
        <dl
          style={{
            display: 'grid',
            gridTemplateColumns: 'repeat(3, 1fr)',
            gap: '20px',
            margin: 0,
          }}
        >
          <div>
            <dt style={labelStyle}>App version</dt>
            <dd style={{ ...valueStyle, margin: 0 }}>v1.0.0</dd>
          </div>
          <div>
            <dt style={labelStyle}>Database location</dt>
            <dd
              style={{
                ...valueStyle,
                margin: 0,
                fontFamily: 'monospace',
                fontSize: '13px',
                color: '#475467',
              }}
            >
              data/tracker.db
            </dd>
          </div>
          <div>
            <dt style={labelStyle}>Uploads location</dt>
            <dd
              style={{
                ...valueStyle,
                margin: 0,
                fontFamily: 'monospace',
                fontSize: '13px',
                color: '#475467',
              }}
            >
              uploads/
            </dd>
          </div>
        </dl>
      </div>

      {/* Customers & Brands */}
      <div style={cardStyle}>
        <h2 style={cardTitleStyle}>Saved Customers &amp; Brands</h2>
        <div
          style={{
            display: 'grid',
            gridTemplateColumns: '1fr 1fr',
            gap: '32px',
          }}
        >
          <div>
            <div
              style={{
                display: 'flex',
                alignItems: 'center',
                justifyContent: 'space-between',
                marginBottom: '12px',
              }}
            >
              <h3
                style={{
                  fontFamily: 'var(--font-display)',
                  fontWeight: 700,
                  fontSize: '14px',
                  color: '#101828',
                }}
              >
                Customers
              </h3>
              <span
                style={{
                  fontFamily: 'var(--font-body)',
                  fontSize: 12,
                  color: '#98A2B3',
                  fontWeight: 600,
                }}
              >
                {loadingLists ? '—' : `${customers.length} customer${customers.length !== 1 ? 's' : ''}`}
              </span>
            </div>
            <div style={{ display: 'flex', flexWrap: 'wrap', gap: '6px' }}>
              {loadingLists ? (
                <span
                  style={{
                    fontFamily: 'var(--font-body)',
                    fontSize: 13,
                    color: '#98A2B3',
                  }}
                >
                  Loading…
                </span>
              ) : customers.length === 0 ? (
                <span
                  style={{
                    fontFamily: 'var(--font-body)',
                    fontSize: 13,
                    color: '#98A2B3',
                  }}
                >
                  No customers yet.
                </span>
              ) : (
                customers.map((c) => (
                  <span key={c} style={chipStyle}>
                    {c}
                  </span>
                ))
              )}
            </div>
          </div>

          <div>
            <div
              style={{
                display: 'flex',
                alignItems: 'center',
                justifyContent: 'space-between',
                marginBottom: '12px',
              }}
            >
              <h3
                style={{
                  fontFamily: 'var(--font-display)',
                  fontWeight: 700,
                  fontSize: '14px',
                  color: '#101828',
                }}
              >
                Brands
              </h3>
              <span
                style={{
                  fontFamily: 'var(--font-body)',
                  fontSize: 12,
                  color: '#98A2B3',
                  fontWeight: 600,
                }}
              >
                {loadingLists ? '—' : `${brands.length} brand${brands.length !== 1 ? 's' : ''}`}
              </span>
            </div>
            <div style={{ display: 'flex', flexWrap: 'wrap', gap: '6px' }}>
              {loadingLists ? (
                <span
                  style={{
                    fontFamily: 'var(--font-body)',
                    fontSize: 13,
                    color: '#98A2B3',
                  }}
                >
                  Loading…
                </span>
              ) : brands.length === 0 ? (
                <span
                  style={{
                    fontFamily: 'var(--font-body)',
                    fontSize: 13,
                    color: '#98A2B3',
                  }}
                >
                  No brands yet.
                </span>
              ) : (
                brands.map((b) => (
                  <span key={b} style={chipStyle}>
                    {b}
                  </span>
                ))
              )}
            </div>
          </div>
        </div>

        <p
          style={{
            marginTop: 20,
            fontFamily: 'var(--font-body)',
            fontSize: 12.5,
            color: '#98A2B3',
            borderTop: '1px solid #F2F4F7',
            paddingTop: 14,
          }}
        >
          Customers and brands are added automatically when you use them in uploads.
        </p>
      </div>

      {/* Data Management */}
      <div style={cardStyle}>
        <h2 style={cardTitleStyle}>Data Management</h2>
        <div style={{ display: 'flex', flexDirection: 'column', gap: '16px' }}>
          <div
            style={{
              display: 'flex',
              gap: '16px',
              padding: '16px',
              border: '1px solid #EAECF0',
              borderRadius: '12px',
              backgroundColor: '#F9FAFB',
            }}
          >
            <div
              style={{
                width: 40,
                height: 40,
                borderRadius: 10,
                background: 'linear-gradient(135deg, #EFF4FF 0%, #DBE7FF 100%)',
                display: 'flex',
                alignItems: 'center',
                justifyContent: 'center',
                flexShrink: 0,
              }}
            >
              <Save size={18} color="#2D6FF7" />
            </div>
            <div style={{ flex: 1 }}>
              <h3
                style={{
                  fontFamily: 'var(--font-display)',
                  fontWeight: 700,
                  fontSize: '14px',
                  color: '#101828',
                  marginBottom: 4,
                }}
              >
                Backup
              </h3>
              <p
                style={{
                  fontFamily: 'var(--font-body)',
                  fontSize: 13,
                  color: '#475467',
                  lineHeight: 1.5,
                }}
              >
                Copy <code style={{ fontFamily: 'monospace', backgroundColor: '#F2F4F7', padding: '1px 6px', borderRadius: 4, fontSize: 12 }}>data/</code>{' '}
                and <code style={{ fontFamily: 'monospace', backgroundColor: '#F2F4F7', padding: '1px 6px', borderRadius: 4, fontSize: 12 }}>uploads/</code> folders to back up everything. Restore by copying them back.
              </p>
            </div>
          </div>

          <div
            style={{
              display: 'flex',
              gap: '16px',
              padding: '16px',
              border: '1px solid #EAECF0',
              borderRadius: '12px',
              backgroundColor: '#F9FAFB',
            }}
          >
            <div
              style={{
                width: 40,
                height: 40,
                borderRadius: 10,
                background: 'linear-gradient(135deg, #FFF4ED 0%, #FFE0CC 100%)',
                display: 'flex',
                alignItems: 'center',
                justifyContent: 'center',
                flexShrink: 0,
              }}
            >
              <HardDrive size={18} color="#FF5C35" />
            </div>
            <div style={{ flex: 1 }}>
              <h3
                style={{
                  fontFamily: 'var(--font-display)',
                  fontWeight: 700,
                  fontSize: '14px',
                  color: '#101828',
                  marginBottom: 4,
                }}
              >
                Reset
              </h3>
              <p
                style={{
                  fontFamily: 'var(--font-body)',
                  fontSize: 13,
                  color: '#475467',
                  lineHeight: 1.5,
                }}
              >
                To wipe all data, quit the app and delete those two folders manually.
              </p>
            </div>
          </div>

          <div
            style={{
              display: 'flex',
              gap: '16px',
              padding: '16px',
              border: '1px solid #EAECF0',
              borderRadius: '12px',
              backgroundColor: '#F9FAFB',
            }}
          >
            <div
              style={{
                width: 40,
                height: 40,
                borderRadius: 10,
                background: 'linear-gradient(135deg, #ECFDF3 0%, #D1FADF 100%)',
                display: 'flex',
                alignItems: 'center',
                justifyContent: 'center',
                flexShrink: 0,
              }}
            >
              <Database size={18} color="#12B76A" />
            </div>
            <div style={{ flex: 1 }}>
              <h3
                style={{
                  fontFamily: 'var(--font-display)',
                  fontWeight: 700,
                  fontSize: '14px',
                  color: '#101828',
                  marginBottom: 4,
                }}
              >
                Storage
              </h3>
              <p
                style={{
                  fontFamily: 'var(--font-body)',
                  fontSize: 13,
                  color: '#475467',
                  lineHeight: 1.5,
                }}
              >
                All records live in a local SQLite database. Uploaded files are stored on disk in the uploads folder.
              </p>
            </div>
          </div>
        </div>
      </div>

      {/* About */}
      <div
        style={{
          ...cardStyle,
          padding: '22px 28px',
          display: 'flex',
          alignItems: 'center',
          gap: '20px',
          flexWrap: 'wrap',
        }}
      >
        <div style={{ flex: 1, minWidth: 240 }}>
          <h3
            style={{
              fontFamily: 'var(--font-display)',
              fontWeight: 700,
              fontSize: '15px',
              color: '#101828',
              marginBottom: 4,
            }}
          >
            Social Media Content Tracker v1.0.0
          </h3>
          <p
            style={{
              fontFamily: 'var(--font-body)',
              fontSize: 13,
              color: '#667085',
            }}
          >
            Built for FLENDER GROUP — Internal Use Only
          </p>
        </div>
        <span
          style={{
            padding: '10px 16px',
            border: '1px solid #D0D5DD',
            borderRadius: '10px',
            fontFamily: 'var(--font-body)',
            fontWeight: 600,
            fontSize: 13,
            color: '#344054',
            backgroundColor: '#FFFFFF',
            display: 'inline-flex',
            alignItems: 'center',
            gap: 8,
            cursor: 'default',
          }}
        >
          <FileText size={14} />
          View User Guide → docs/user-guide.pdf
        </span>
      </div>
    </div>
  );
}
