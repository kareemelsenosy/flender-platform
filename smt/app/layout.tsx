import type { Metadata } from 'next';
import './globals.css';
import Sidebar from '@/components/Sidebar';
import TopBar from '@/components/TopBar';

export const metadata: Metadata = {
  title: 'SMT — Social Media Tracker',
  description: 'Social media content monitoring tool for brand distribution tracking',
};

export default function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <html lang="en">
      <body>
        <div style={{ display: 'flex', minHeight: '100vh' }}>
          <Sidebar />
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
