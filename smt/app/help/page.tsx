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

const bodyTextStyle: React.CSSProperties = {
  fontFamily: 'var(--font-body)',
  fontSize: 13.5,
  color: '#475467',
  lineHeight: 1.6,
};

const codeBlockStyle: React.CSSProperties = {
  backgroundColor: '#F9FAFB',
  border: '1px solid #EAECF0',
  borderRadius: 10,
  padding: 16,
  fontFamily: 'monospace',
  fontSize: 12.5,
  color: '#475467',
  whiteSpace: 'pre',
  overflowX: 'auto',
  margin: 0,
};

const exampleBoxStyle: React.CSSProperties = {
  backgroundColor: '#F9FAFB',
  border: '1px solid #EAECF0',
  borderRadius: 8,
  padding: '10px 12px',
  fontFamily: 'monospace',
  fontSize: 12.5,
  color: '#101828',
};

const COMMON_TASKS: { title: string; desc: string }[] = [
  { title: 'Start a new session', desc: 'Dashboard → type a name → Start' },
  { title: 'Upload content', desc: 'Upload page (requires open session)' },
  { title: 'Tag multiple brands', desc: 'Type a brand and press Enter to add as a chip' },
  { title: 'Re-download an export', desc: 'Sessions page → Download on any past session' },
  { title: 'Monthly report', desc: 'Dashboard → Monthly Summary Report card → pick month → Export' },
  { title: 'Search records', desc: 'Records page → filter by customer/brand/date/session' },
  { title: 'Delete a record', desc: 'Records page → trash icon on the row' },
  { title: 'Backup data', desc: 'Copy data/ and uploads/ folders to safe place' },
];

const TROUBLESHOOTING: { problem: string; fix: string }[] = [
  { problem: "Can't upload — 'No active session'", fix: 'Start a session on the Dashboard first' },
  { problem: "Brand chip won't add", fix: 'Press Enter after typing the brand name' },
  { problem: "ZIP didn't download", fix: 'Allow popups for localhost in your browser' },
  { problem: 'Customer suggestion missing', fix: "First time you've used it — it'll appear next time" },
  { problem: 'Need to recover a deleted record', fix: 'Deletion is permanent. Restore from your data backup.' },
];

export default function HelpPage() {
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
          Help &amp; User Guide
        </h1>
        <p
          style={{
            fontFamily: 'var(--font-body)',
            fontSize: '14px',
            color: '#667085',
          }}
        >
          Daily workflow, common tasks, and answers to common questions.
        </p>
      </div>

      {/* Quick Start */}
      <div style={cardStyle}>
        <h2 style={cardTitleStyle}>Quick Start</h2>
        <ol
          style={{
            margin: 0,
            padding: 0,
            listStyle: 'none',
            display: 'flex',
            flexDirection: 'column',
            gap: 12,
          }}
        >
          {[
            'Start a session from the Dashboard',
            'Upload screenshots, tagging Customer, Brands, Date, Type, Content Type, Source',
            'When done, click "Close & Export Session"',
            'The ZIP downloads with folders per partner and dates inside',
          ].map((step, i) => (
            <li
              key={i}
              style={{
                display: 'flex',
                gap: 14,
                alignItems: 'flex-start',
                fontFamily: 'var(--font-body)',
                fontSize: 14,
                color: '#101828',
              }}
            >
              <span
                style={{
                  width: 26,
                  height: 26,
                  borderRadius: '50%',
                  background: 'linear-gradient(135deg, #2D6FF7 0%, #4F8AFF 100%)',
                  color: '#FFFFFF',
                  display: 'flex',
                  alignItems: 'center',
                  justifyContent: 'center',
                  fontFamily: 'var(--font-display)',
                  fontWeight: 700,
                  fontSize: 12,
                  flexShrink: 0,
                  boxShadow: '0 2px 6px rgba(45,111,247,0.30)',
                }}
              >
                {i + 1}
              </span>
              <span style={{ paddingTop: 3, lineHeight: 1.5 }}>{step}</span>
            </li>
          ))}
        </ol>
      </div>

      {/* Common Tasks */}
      <div style={cardStyle}>
        <h2 style={cardTitleStyle}>Common Tasks</h2>
        <div
          style={{
            display: 'grid',
            gridTemplateColumns: '1fr 1fr',
            gap: '14px',
          }}
        >
          {COMMON_TASKS.map((t) => (
            <div
              key={t.title}
              style={{
                padding: '14px 16px',
                border: '1px solid #EAECF0',
                borderRadius: 12,
                backgroundColor: '#F9FAFB',
              }}
            >
              <div
                style={{
                  fontFamily: 'var(--font-display)',
                  fontWeight: 700,
                  fontSize: 13.5,
                  color: '#101828',
                  marginBottom: 4,
                }}
              >
                {t.title}
              </div>
              <div
                style={{
                  fontFamily: 'var(--font-body)',
                  fontSize: 12.5,
                  color: '#667085',
                  lineHeight: 1.5,
                }}
              >
                {t.desc}
              </div>
            </div>
          ))}
        </div>
      </div>

      {/* File Naming */}
      <div style={cardStyle}>
        <h2 style={cardTitleStyle}>File Naming Rules</h2>
        <p style={{ ...bodyTextStyle, marginBottom: 16 }}>
          Files are renamed using the pattern{' '}
          <code
            style={{
              fontFamily: 'monospace',
              backgroundColor: '#F2F4F7',
              padding: '2px 6px',
              borderRadius: 4,
              fontSize: 12.5,
              color: '#101828',
            }}
          >
            Brand_PostType.ext
          </code>
          . When multiple brands are tagged, they are joined with underscores:{' '}
          <code
            style={{
              fontFamily: 'monospace',
              backgroundColor: '#F2F4F7',
              padding: '2px 6px',
              borderRadius: 4,
              fontSize: 12.5,
              color: '#101828',
            }}
          >
            Brand1_Brand2_PostType.ext
          </code>
          . When multiple files are uploaded in one record, they get numbered:{' '}
          <code
            style={{
              fontFamily: 'monospace',
              backgroundColor: '#F2F4F7',
              padding: '2px 6px',
              borderRadius: 4,
              fontSize: 12.5,
              color: '#101828',
            }}
          >
            Brand_PostType_1.ext
          </code>
          ,{' '}
          <code
            style={{
              fontFamily: 'monospace',
              backgroundColor: '#F2F4F7',
              padding: '2px 6px',
              borderRadius: 4,
              fontSize: 12.5,
              color: '#101828',
            }}
          >
            Brand_PostType_2.ext
          </code>
          .
        </p>
        <div
          style={{
            display: 'grid',
            gridTemplateColumns: 'repeat(3, 1fr)',
            gap: 10,
          }}
        >
          <div style={exampleBoxStyle}>CarharttWIP_Stories.png</div>
          <div style={exampleBoxStyle}>CarharttWIP_Edwin_Reels.mp4</div>
          <div style={exampleBoxStyle}>Gramicci_Market_Twojeys_Stories.png</div>
        </div>
      </div>

      {/* Export Structure */}
      <div style={cardStyle}>
        <h2 style={cardTitleStyle}>Export Structure</h2>
        <pre style={codeBlockStyle}>{`Session_{Name}/
  records.xlsx
  {Customer}/
    {DD-MM-YYYY}/
      {Brand_PostType}.ext`}</pre>
      </div>

      {/* Troubleshooting */}
      <div style={cardStyle}>
        <h2 style={cardTitleStyle}>Troubleshooting</h2>
        <div style={{ overflowX: 'auto' }}>
          <table
            style={{
              width: '100%',
              borderCollapse: 'collapse',
              minWidth: 500,
            }}
          >
            <thead>
              <tr style={{ backgroundColor: '#F9FAFB' }}>
                <th
                  style={{
                    padding: '10px 16px',
                    fontFamily: 'var(--font-body)',
                    fontWeight: 600,
                    fontSize: 11,
                    color: '#98A2B3',
                    letterSpacing: '0.06em',
                    textTransform: 'uppercase',
                    textAlign: 'left',
                    borderBottom: '1px solid #EAECF0',
                    width: '45%',
                  }}
                >
                  Problem
                </th>
                <th
                  style={{
                    padding: '10px 16px',
                    fontFamily: 'var(--font-body)',
                    fontWeight: 600,
                    fontSize: 11,
                    color: '#98A2B3',
                    letterSpacing: '0.06em',
                    textTransform: 'uppercase',
                    textAlign: 'left',
                    borderBottom: '1px solid #EAECF0',
                  }}
                >
                  Fix
                </th>
              </tr>
            </thead>
            <tbody>
              {TROUBLESHOOTING.map((row) => (
                <tr key={row.problem}>
                  <td
                    style={{
                      padding: '12px 16px',
                      fontFamily: 'var(--font-body)',
                      fontSize: 13,
                      color: '#101828',
                      fontWeight: 500,
                      borderBottom: '1px solid #F2F4F7',
                      verticalAlign: 'top',
                    }}
                  >
                    {row.problem}
                  </td>
                  <td
                    style={{
                      padding: '12px 16px',
                      fontFamily: 'var(--font-body)',
                      fontSize: 13,
                      color: '#475467',
                      borderBottom: '1px solid #F2F4F7',
                      verticalAlign: 'top',
                      lineHeight: 1.5,
                    }}
                  >
                    {row.fix}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  );
}
