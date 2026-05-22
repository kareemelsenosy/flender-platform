'use client';

import {
  ResponsiveContainer,
  LineChart,
  Line,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
  Area,
  AreaChart,
} from 'recharts';

interface DataPoint {
  date: string;
  count: number;
}

interface UploadChartProps {
  data: DataPoint[];
  loading?: boolean;
}

function CustomTooltip({ active, payload, label }: any) {
  if (!active || !payload || !payload.length) return null;
  return (
    <div
      style={{
        backgroundColor: '#FFFFFF',
        border: '1px solid #EAECF0',
        borderRadius: '10px',
        padding: '10px 14px',
        boxShadow: '0 8px 24px rgba(16,24,40,0.1)',
      }}
    >
      <p
        style={{
          fontFamily: 'var(--font-body)',
          fontSize: '12px',
          color: '#98A2B3',
          marginBottom: '4px',
        }}
      >
        {label}
      </p>
      <p
        style={{
          fontFamily: 'var(--font-display)',
          fontWeight: 700,
          fontSize: '18px',
          color: '#2D6FF7',
        }}
      >
        {payload[0].value}
        <span
          style={{
            fontFamily: 'var(--font-body)',
            fontWeight: 400,
            fontSize: '12px',
            color: '#98A2B3',
            marginLeft: '4px',
          }}
        >
          uploads
        </span>
      </p>
    </div>
  );
}

export default function UploadChart({ data, loading = false }: UploadChartProps) {
  const displayData = data.length > 0 ? data : [];

  // Show every 5th label to avoid crowding
  const tickFormatter = (_: string, index: number) =>
    index % 5 === 0 ? displayData[index]?.date ?? '' : '';

  return (
    <div
      style={{
        backgroundColor: '#FFFFFF',
        borderRadius: '16px',
        border: '1px solid #EAECF0',
        boxShadow: '0 1px 2px rgba(16,24,40,0.04)',
        padding: '24px',
      }}
    >
      {/* Header */}
      <div style={{ display: 'flex', alignItems: 'flex-start', justifyContent: 'space-between', marginBottom: '24px' }}>
        <div>
          <p
            style={{
              fontFamily: 'var(--font-body)',
              fontWeight: 600,
              fontSize: '11px',
              color: '#98A2B3',
              letterSpacing: '0.08em',
              textTransform: 'uppercase',
              marginBottom: '4px',
            }}
          >
            Upload Activity
          </p>
          <h3
            style={{
              fontFamily: 'var(--font-display)',
              fontWeight: 700,
              fontSize: '18px',
              color: '#101828',
            }}
          >
            Uploads per day
          </h3>
        </div>
        <span
          style={{
            fontFamily: 'var(--font-body)',
            fontSize: '12px',
            color: '#98A2B3',
            backgroundColor: '#F7F8FA',
            padding: '4px 10px',
            borderRadius: '9999px',
            border: '1px solid #EAECF0',
          }}
        >
          Last 30 days
        </span>
      </div>

      {loading ? (
        <div
          className="skeleton"
          style={{ height: '200px', borderRadius: '10px' }}
        />
      ) : displayData.length === 0 ? (
        <div
          style={{
            height: '200px',
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'center',
            color: '#98A2B3',
            fontSize: '14px',
            fontFamily: 'var(--font-body)',
          }}
        >
          No upload data yet
        </div>
      ) : (
        <ResponsiveContainer width="100%" height={200}>
          <AreaChart data={displayData} margin={{ top: 4, right: 4, left: -20, bottom: 0 }}>
            <defs>
              <linearGradient id="uploadGrad" x1="0" y1="0" x2="0" y2="1">
                <stop offset="0%" stopColor="#2D6FF7" stopOpacity={0.15} />
                <stop offset="100%" stopColor="#2D6FF7" stopOpacity={0} />
              </linearGradient>
            </defs>
            <CartesianGrid strokeDasharray="3 3" stroke="#F2F4F7" vertical={false} />
            <XAxis
              dataKey="date"
              tick={{ fontFamily: 'var(--font-body)', fontSize: 11, fill: '#98A2B3' }}
              tickLine={false}
              axisLine={false}
              tickFormatter={tickFormatter}
              interval={0}
            />
            <YAxis
              tick={{ fontFamily: 'var(--font-body)', fontSize: 11, fill: '#98A2B3' }}
              tickLine={false}
              axisLine={false}
              allowDecimals={false}
            />
            <Tooltip content={<CustomTooltip />} cursor={{ stroke: '#EAECF0', strokeWidth: 1 }} />
            <Area
              type="monotone"
              dataKey="count"
              stroke="#2D6FF7"
              strokeWidth={2}
              fill="url(#uploadGrad)"
              dot={false}
              activeDot={{ r: 4, fill: '#2D6FF7', stroke: '#FFFFFF', strokeWidth: 2 }}
            />
          </AreaChart>
        </ResponsiveContainer>
      )}
    </div>
  );
}
