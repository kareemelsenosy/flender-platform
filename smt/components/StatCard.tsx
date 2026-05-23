'use client';

interface SparklineProps {
  data: number[];
  color?: string;
  positive?: boolean;
}

function Sparkline({ data, positive = true }: SparklineProps) {
  if (!data || data.length < 2) return null;
  const width = 80;
  const height = 32;
  const pad = 2;
  const min = Math.min(...data);
  const max = Math.max(...data);
  const range = max - min || 1;

  const pts = data.map((v, i) => {
    const x = pad + (i / (data.length - 1)) * (width - pad * 2);
    const y = height - pad - ((v - min) / range) * (height - pad * 2);
    return [x, y] as [number, number];
  });

  // Build smooth path
  const pathD = pts.reduce((acc, [x, y], i) => {
    if (i === 0) return `M ${x} ${y}`;
    const [px, py] = pts[i - 1];
    const cpx = (px + x) / 2;
    return `${acc} C ${cpx} ${py}, ${cpx} ${y}, ${x} ${y}`;
  }, '');

  // Fill path going back along bottom
  const fillD =
    `${pathD} L ${pts[pts.length - 1][0]} ${height - pad} L ${pts[0][0]} ${height - pad} Z`;

  const color = positive ? '#12B76A' : '#F04438';
  const fillId = `spark-fill-${Math.random().toString(36).slice(2)}`;

  return (
    <svg width={width} height={height} viewBox={`0 0 ${width} ${height}`} style={{ overflow: 'visible' }}>
      <defs>
        <linearGradient id={fillId} x1="0" y1="0" x2="0" y2="1">
          <stop offset="0%" stopColor={color} stopOpacity="0.18" />
          <stop offset="100%" stopColor={color} stopOpacity="0" />
        </linearGradient>
      </defs>
      <path d={fillD} fill={`url(#${fillId})`} />
      <path d={pathD} fill="none" stroke={color} strokeWidth={1.75} strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  );
}

interface StatCardProps {
  label: string;
  value: string;
  change: string;
  changePositive?: boolean;
  sparkData?: number[];
  loading?: boolean;
  isBrand?: boolean;
}

export default function StatCard({
  label,
  value,
  change,
  changePositive = true,
  sparkData = [],
  loading = false,
  isBrand = false,
}: StatCardProps) {
  const changeColor = changePositive ? '#12B76A' : '#F04438';
  const changeBg = changePositive ? '#ECFDF3' : '#FEF3F2';
  const arrow = changePositive ? '↑' : '↓';

  return (
    <div
      style={{
        backgroundColor: '#FFFFFF',
        borderRadius: '16px',
        border: '1px solid #EAECF0',
        boxShadow: '0 1px 2px rgba(16,24,40,0.04), 0 1px 3px rgba(16,24,40,0.04)',
        padding: '20px 24px',
        display: 'flex',
        flexDirection: 'column',
        gap: '12px',
        transition: 'box-shadow 0.2s ease, transform 0.2s ease',
      }}
      onMouseEnter={(e) => {
        (e.currentTarget as HTMLDivElement).style.boxShadow =
          '0 4px 16px rgba(16,24,40,0.08), 0 2px 6px rgba(16,24,40,0.04)';
        (e.currentTarget as HTMLDivElement).style.transform = 'translateY(-2px)';
      }}
      onMouseLeave={(e) => {
        (e.currentTarget as HTMLDivElement).style.boxShadow =
          '0 1px 2px rgba(16,24,40,0.04), 0 1px 3px rgba(16,24,40,0.04)';
        (e.currentTarget as HTMLDivElement).style.transform = 'translateY(0)';
      }}
    >
      {/* Label */}
      <p
        style={{
          fontFamily: 'var(--font-body)',
          fontWeight: 600,
          fontSize: '11px',
          color: '#98A2B3',
          letterSpacing: '0.08em',
          textTransform: 'uppercase',
        }}
      >
        {label}
      </p>

      {/* Value + sparkline row */}
      <div style={{ display: 'flex', alignItems: 'flex-end', justifyContent: 'space-between' }}>
        {loading ? (
          <div className="skeleton" style={{ width: '100px', height: '32px' }} />
        ) : (
          <p
            style={{
              fontFamily: 'var(--font-display)',
              fontWeight: 700,
              fontSize: isBrand ? '22px' : '30px',
              color: '#101828',
              lineHeight: 1,
              fontVariantNumeric: 'tabular-nums',
              letterSpacing: '-0.02em',
              maxWidth: isBrand ? '120px' : undefined,
              overflow: 'hidden',
              textOverflow: 'ellipsis',
              whiteSpace: 'nowrap',
            }}
          >
            {value}
          </p>
        )}
        {sparkData.length >= 2 && !loading && (
          <Sparkline data={sparkData} positive={changePositive} />
        )}
      </div>

      {/* Change badge */}
      {loading ? (
        <div className="skeleton" style={{ width: '60px', height: '20px' }} />
      ) : (
        <div
          style={{
            display: 'inline-flex',
            alignItems: 'center',
            gap: '4px',
            padding: '3px 8px',
            backgroundColor: isBrand ? '#EFF4FF' : changeBg,
            borderRadius: '9999px',
            alignSelf: 'flex-start',
          }}
        >
          {!isBrand && (
            <span
              style={{
                fontSize: '11px',
                fontWeight: 700,
                color: changeColor,
              }}
            >
              {arrow}
            </span>
          )}
          <span
            style={{
              fontFamily: 'var(--font-body)',
              fontWeight: 600,
              fontSize: '12px',
              color: isBrand ? '#2D6FF7' : changeColor,
            }}
          >
            {change}
          </span>
        </div>
      )}
    </div>
  );
}
