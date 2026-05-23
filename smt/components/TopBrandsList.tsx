'use client';

interface Brand {
  name: string;
  count: number;
  pct: number;
}

interface TopBrandsListProps {
  brands: Brand[];
  loading?: boolean;
}

const BRAND_COLORS = [
  '#2D6FF7',
  '#FF5C35',
  '#12B76A',
  '#9B50E8',
  '#F79009',
];

export default function TopBrandsList({ brands, loading = false }: TopBrandsListProps) {
  return (
    <div
      style={{
        backgroundColor: '#FFFFFF',
        borderRadius: '16px',
        border: '1px solid #EAECF0',
        boxShadow: '0 1px 2px rgba(16,24,40,0.04)',
        padding: '24px',
        display: 'flex',
        flexDirection: 'column',
      }}
    >
      {/* Header */}
      <div style={{ marginBottom: '20px' }}>
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
          Top Brands
        </p>
        <h3
          style={{
            fontFamily: 'var(--font-display)',
            fontWeight: 700,
            fontSize: '18px',
            color: '#101828',
          }}
        >
          By record count
        </h3>
      </div>

      {/* Brand list */}
      <div style={{ display: 'flex', flexDirection: 'column', gap: '14px', flex: 1 }}>
        {loading ? (
          Array.from({ length: 5 }).map((_, i) => (
            <div key={i}>
              <div className="skeleton" style={{ height: '14px', width: '80%', marginBottom: '8px' }} />
              <div className="skeleton" style={{ height: '6px', width: '100%' }} />
            </div>
          ))
        ) : brands.length === 0 ? (
          <p style={{ fontFamily: 'var(--font-body)', fontSize: '14px', color: '#98A2B3', textAlign: 'center', paddingTop: '24px' }}>
            No data yet
          </p>
        ) : (
          brands.map((brand, i) => {
            const color = BRAND_COLORS[i % BRAND_COLORS.length];
            return (
              <div key={brand.name}>
                <div
                  style={{
                    display: 'flex',
                    alignItems: 'center',
                    justifyContent: 'space-between',
                    marginBottom: '6px',
                  }}
                >
                  <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
                    <span
                      style={{
                        width: '8px',
                        height: '8px',
                        borderRadius: '50%',
                        backgroundColor: color,
                        flexShrink: 0,
                      }}
                    />
                    <span
                      style={{
                        fontFamily: 'var(--font-body)',
                        fontWeight: 500,
                        fontSize: '13px',
                        color: '#101828',
                      }}
                    >
                      {brand.name}
                    </span>
                  </div>
                  <div style={{ display: 'flex', alignItems: 'center', gap: '6px' }}>
                    <span
                      style={{
                        fontFamily: 'var(--font-display)',
                        fontWeight: 700,
                        fontSize: '13px',
                        color: '#101828',
                        fontVariantNumeric: 'tabular-nums',
                      }}
                    >
                      {brand.count}
                    </span>
                    <span
                      style={{
                        fontFamily: 'var(--font-body)',
                        fontSize: '11px',
                        color: '#98A2B3',
                      }}
                    >
                      {brand.pct}%
                    </span>
                  </div>
                </div>
                {/* Bar */}
                <div
                  style={{
                    height: '6px',
                    backgroundColor: '#F2F4F7',
                    borderRadius: '9999px',
                    overflow: 'hidden',
                  }}
                >
                  <div
                    style={{
                      height: '100%',
                      width: `${brand.pct}%`,
                      backgroundColor: color,
                      borderRadius: '9999px',
                      transition: 'width 0.6s ease',
                    }}
                  />
                </div>
              </div>
            );
          })
        )}
      </div>
    </div>
  );
}
