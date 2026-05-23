'use client';

import { useState } from 'react';
import { usePathname } from 'next/navigation';
import { Bell } from 'lucide-react';

const PAGE_INFO: Record<string, { title: string; subtitle?: string; crumbs?: string[] }> = {
  '/': { title: 'Hello, Admin', subtitle: "Here's what's been tracked today" },
  '/upload': { title: 'Upload Content', subtitle: 'Upload screenshots and videos from partner accounts', crumbs: ['Upload'] },
  '/sessions': { title: 'Sessions', subtitle: 'One open session at a time. Close it to download the export.', crumbs: ['Sessions'] },
  '/records': { title: 'Records', subtitle: 'Browse, filter, and export all uploaded content records', crumbs: ['Records'] },
  '/settings': { title: 'Settings', subtitle: 'Configure your tracker and manage data', crumbs: ['Settings'] },
  '/help': { title: 'Help', subtitle: 'Guides, workflows, and troubleshooting', crumbs: ['Help'] },
};

export default function TopBar() {
  const pathname = usePathname();
  const [bellHovered, setBellHovered] = useState(false);

  const info = PAGE_INFO[pathname] ?? { title: 'Dashboard' };

  return (
    <header
      style={{
        padding: '18px 32px',
        backgroundColor: '#FFFFFF',
        borderBottom: '1px solid #EAECF0',
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'space-between',
        position: 'sticky',
        top: 0,
        zIndex: 30,
      }}
    >
      {/* Left: title + breadcrumbs */}
      <div>
        {info.crumbs && info.crumbs.length > 0 && (
          <div
            style={{
              display: 'flex',
              alignItems: 'center',
              gap: '6px',
              marginBottom: '2px',
            }}
          >
            <span style={{ fontSize: '12px', color: '#98A2B3', fontFamily: 'var(--font-body)' }}>
              Home
            </span>
            <span style={{ fontSize: '12px', color: '#D0D5DD' }}>/</span>
            {info.crumbs.map((c, i) => (
              <span
                key={i}
                style={{
                  fontSize: '12px',
                  color: '#475467',
                  fontWeight: 500,
                  fontFamily: 'var(--font-body)',
                }}
              >
                {c}
              </span>
            ))}
          </div>
        )}
        <h1
          style={{
            fontFamily: 'var(--font-display)',
            fontWeight: 700,
            fontSize: '24px',
            color: '#101828',
            lineHeight: 1.25,
            letterSpacing: '-0.02em',
          }}
        >
          {info.title}
        </h1>
        {info.subtitle && (
          <p
            style={{
              fontFamily: 'var(--font-body)',
              fontSize: '13px',
              color: '#98A2B3',
              marginTop: '2px',
            }}
          >
            {info.subtitle}
          </p>
        )}
      </div>

      {/* Right: bell + avatar */}
      <div style={{ display: 'flex', alignItems: 'center', gap: '10px' }}>
        <button
          onMouseEnter={() => setBellHovered(true)}
          onMouseLeave={() => setBellHovered(false)}
          style={{
            width: '38px',
            height: '38px',
            borderRadius: '10px',
            border: '1px solid #EAECF0',
            backgroundColor: bellHovered ? '#F7F8FA' : '#FFFFFF',
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'center',
            cursor: 'pointer',
            position: 'relative',
            transition: 'background-color 0.15s ease',
          }}
        >
          <Bell size={17} color="#475467" />
          <span
            style={{
              position: 'absolute',
              top: '8px',
              right: '8px',
              width: '7px',
              height: '7px',
              backgroundColor: '#FF5C35',
              borderRadius: '50%',
              border: '1.5px solid #FFFFFF',
            }}
          />
        </button>

        <div
          style={{
            width: '36px',
            height: '36px',
            borderRadius: '50%',
            background: 'linear-gradient(135deg, #2D6FF7 0%, #4F8AFF 100%)',
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'center',
            color: '#FFFFFF',
            fontSize: '13px',
            fontWeight: 700,
            fontFamily: 'var(--font-body)',
            cursor: 'pointer',
            boxShadow: '0 2px 6px rgba(45,111,247,0.3)',
          }}
        >
          A
        </div>
      </div>
    </header>
  );
}
