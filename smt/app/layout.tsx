import type { Metadata } from 'next';
import { headers } from 'next/headers';
import './globals.css';
import Sidebar from '@/components/Sidebar';
import TopBar from '@/components/TopBar';

export const metadata: Metadata = {
  title: 'SMT — Social Media Tracker',
  description: 'Social media content monitoring tool for brand distribution tracking',
};

// SMT lives behind the Order Sheet's session auth. The /smt proxy injects
// the active user's email + username on every request; we read them here
// (which opts the layout into dynamic rendering) and hand them to Sidebar.
export const dynamic = 'force-dynamic';

export default function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  const h = headers();
  const userEmail = h.get('x-smt-user-email') || '';
  const userName = h.get('x-smt-user-name') || '';

  return (
    <html lang="en">
      <body>
        <div style={{ display: 'flex', minHeight: '100vh' }}>
          <Sidebar userEmail={userEmail} userName={userName} />
          <main
            style={{
              marginLeft: '240px',
              flex: 1,
              minHeight: '100vh',
              backgroundColor: 'var(--color-bg)',
              display: 'flex',
              flexDirection: 'column',
            }}
          >
            <TopBar />
            <div style={{ flex: 1 }}>
              {children}
            </div>
          </main>
        </div>
      </body>
    </html>
  );
}
