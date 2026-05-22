'use client';

import { useState } from 'react';
import Link from 'next/link';
import { usePathname } from 'next/navigation';
import {
  LayoutDashboard, Upload, Table2, Settings, HelpCircle,
  Radio, ChevronRight, Search, Layers,
  type LucideIcon,
} from 'lucide-react';

const mainNavItems = [
  { href: '/', label: 'Dashboard', icon: LayoutDashboard },
  { href: '/upload', label: 'Upload', icon: Upload },
  { href: '/sessions', label: 'Sessions', icon: Layers },
  { href: '/records', label: 'Records', icon: Table2 },
];

const otherNavItems = [
  { href: '/settings', label: 'Settings', icon: Settings },
  { href: '/help', label: 'Help', icon: HelpCircle },
];

function NavItem({
  href,
  label,
  icon: Icon,
  isActive,
}: {
  href: string;
  label: string;
  icon: LucideIcon;
  isActive: boolean;
}) {
  const [hovered, setHovered] = useState(false);

  return (
    <Link href={href} style={{ textDecoration: 'none', display: 'block', marginBottom: '2px' }}>
      <div
        onMouseEnter={() => setHovered(true)}
        onMouseLeave={() => setHovered(false)}
        style={{
          display: 'flex',
          alignItems: 'center',
          gap: '10px',
          padding: '9px 12px',
          borderRadius: '9999px',
          background: isActive
            ? 'linear-gradient(135deg, #2D6FF7 0%, #4F8AFF 100%)'
            : hovered
            ? '#F2F4F7'
            : 'transparent',
          boxShadow: isActive ? '0 2px 8px rgba(45,111,247,0.25)' : 'none',
          transition: 'all 0.18s ease',
          cursor: 'pointer',
        }}
      >
        <Icon
          size={16}
          color={isActive ? '#FFFFFF' : '#475467'}
          strokeWidth={isActive ? 2 : 1.75}
        />
        <span
          style={{
            fontFamily: 'var(--font-body)',
            fontWeight: isActive ? 600 : 500,
            fontSize: '14px',
            color: isActive ? '#FFFFFF' : '#475467',
            flex: 1,
          }}
        >
          {label}
        </span>
        {isActive && (
          <ChevronRight size={14} color="rgba(255,255,255,0.65)" />
        )}
      </div>
    </Link>
  );
}

export default function Sidebar() {
  const pathname = usePathname();

  return (
    <aside
      style={{
        width: '240px',
        minWidth: '240px',
        height: '100vh',
        position: 'fixed',
        left: 0,
        top: 0,
        backgroundColor: '#FFFFFF',
        borderRight: '1px solid #EAECF0',
        display: 'flex',
        flexDirection: 'column',
        zIndex: 50,
      }}
    >
      {/* Logo */}
      <div style={{ padding: '22px 20px 16px', borderBottom: '1px solid #EAECF0' }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: '10px', marginBottom: '3px' }}>
          <div
            style={{
              width: '36px',
              height: '36px',
              background: 'linear-gradient(135deg, #2D6FF7 0%, #4F8AFF 100%)',
              borderRadius: '10px',
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'center',
              boxShadow: '0 2px 8px rgba(45,111,247,0.3)',
              flexShrink: 0,
            }}
          >
            <Radio size={16} color="#FFFFFF" strokeWidth={2.5} />
          </div>
          <span
            style={{
              fontFamily: 'var(--font-display)',
              fontWeight: 800,
              fontSize: '20px',
              color: '#101828',
              letterSpacing: '-0.02em',
            }}
          >
            SMT
          </span>
        </div>
        <p
          style={{
            fontFamily: 'var(--font-body)',
            fontWeight: 500,
            fontSize: '11px',
            color: '#98A2B3',
            paddingLeft: '46px',
          }}
        >
          Social Media Tracker
        </p>
      </div>

      {/* Search */}
      <div style={{ padding: '14px 14px 6px' }}>
        <div
          style={{
            display: 'flex',
            alignItems: 'center',
            gap: '8px',
            padding: '8px 12px',
            backgroundColor: '#F7F8FA',
            border: '1px solid #EAECF0',
            borderRadius: '10px',
            cursor: 'text',
          }}
        >
          <Search size={14} color="#98A2B3" />
          <span
            style={{
              fontFamily: 'var(--font-body)',
              fontSize: '13px',
              color: '#98A2B3',
              flex: 1,
            }}
          >
            Search...
          </span>
          <span
            style={{
              fontFamily: 'var(--font-body)',
              fontSize: '10px',
              color: '#98A2B3',
              backgroundColor: '#EAECF0',
              padding: '2px 5px',
              borderRadius: '4px',
              letterSpacing: '0.02em',
            }}
          >
            ⌘K
          </span>
        </div>
      </div>

      {/* Nav */}
      <nav style={{ flex: 1, padding: '8px 12px', overflowY: 'auto' }}>
        {/* GENERAL */}
        <p
          style={{
            fontFamily: 'var(--font-body)',
            fontWeight: 600,
            fontSize: '11px',
            color: '#98A2B3',
            letterSpacing: '0.08em',
            textTransform: 'uppercase',
            padding: '8px 12px 6px',
          }}
        >
          General
        </p>
        {mainNavItems.map(({ href, label, icon }) => (
          <NavItem
            key={href}
            href={href}
            label={label}
            icon={icon}
            isActive={pathname === href}
          />
        ))}

        {/* OTHERS */}
        <p
          style={{
            fontFamily: 'var(--font-body)',
            fontWeight: 600,
            fontSize: '11px',
            color: '#98A2B3',
            letterSpacing: '0.08em',
            textTransform: 'uppercase',
            padding: '16px 12px 6px',
          }}
        >
          Others
        </p>
        {otherNavItems.map(({ href, label, icon }) => (
          <NavItem
            key={href}
            href={href}
            label={label}
            icon={icon}
            isActive={pathname === href}
          />
        ))}
      </nav>

      {/* Profile card */}
      <div style={{ padding: '12px 14px', borderTop: '1px solid #EAECF0' }}>
        <div
          style={{
            display: 'flex',
            alignItems: 'center',
            gap: '10px',
            padding: '10px 12px',
            backgroundColor: '#F7F8FA',
            borderRadius: '12px',
          }}
        >
          <div
            style={{
              width: '32px',
              height: '32px',
              borderRadius: '50%',
              background: 'linear-gradient(135deg, #2D6FF7 0%, #4F8AFF 100%)',
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'center',
              color: '#FFFFFF',
              fontSize: '13px',
              fontWeight: 700,
              fontFamily: 'var(--font-body)',
              flexShrink: 0,
            }}
          >
            A
          </div>
          <div style={{ minWidth: 0 }}>
            <p
              style={{
                fontFamily: 'var(--font-body)',
                fontWeight: 600,
                fontSize: '13px',
                color: '#101828',
                margin: 0,
              }}
            >
              Admin
            </p>
            <p
              style={{
                fontFamily: 'var(--font-body)',
                fontSize: '11px',
                color: '#98A2B3',
                margin: 0,
                overflow: 'hidden',
                textOverflow: 'ellipsis',
                whiteSpace: 'nowrap',
              }}
            >
              admin@flender.com
            </p>
          </div>
        </div>
      </div>
    </aside>
  );
}
