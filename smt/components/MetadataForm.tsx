'use client';

import { useState, useEffect, useRef } from 'react';
import { format } from 'date-fns';
import { ChevronDown, Loader2, CheckCircle, Upload, X } from 'lucide-react';

interface MetadataFormProps {
  onSubmit: (data: FormPayload) => Promise<void>;
  isLoading: boolean;
  isSuccess: boolean;
  fileCount: number;
}

interface FormPayload {
  customer: string;
  brands: string[];
  date: string;
  type: string;
  content_type: string;
  content_source: string;
}

const labelStyle: React.CSSProperties = {
  display: 'block',
  fontFamily: 'var(--font-body)',
  fontWeight: 500,
  fontSize: '12px',
  color: '#98A2B3',
  letterSpacing: '0.06em',
  textTransform: 'uppercase',
  marginBottom: '6px',
};

const inputStyle: React.CSSProperties = {
  width: '100%',
  padding: '10px 14px',
  backgroundColor: '#FFFFFF',
  border: '1px solid #D0D5DD',
  borderRadius: '10px',
  color: '#101828',
  fontFamily: 'var(--font-body)',
  fontSize: '14px',
  outline: 'none',
  boxSizing: 'border-box',
  transition: 'border-color 0.15s ease, box-shadow 0.15s ease',
};

function Combobox({
  label,
  value,
  onChange,
  options,
  placeholder,
}: {
  label: string;
  value: string;
  onChange: (v: string) => void;
  options: string[];
  placeholder: string;
}) {
  const [open, setOpen] = useState(false);
  const [inputValue, setInputValue] = useState(value);
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => { setInputValue(value); }, [value]);

  useEffect(() => {
    const handler = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false);
    };
    document.addEventListener('mousedown', handler);
    return () => document.removeEventListener('mousedown', handler);
  }, []);

  const filtered = options.filter((o) => o.toLowerCase().includes(inputValue.toLowerCase()));

  return (
    <div ref={ref} style={{ position: 'relative' }}>
      <label style={labelStyle}>{label}</label>
      <input
        type="text"
        value={inputValue}
        placeholder={placeholder}
        onFocus={() => setOpen(true)}
        onChange={(e) => { setInputValue(e.target.value); onChange(e.target.value); setOpen(true); }}
        style={inputStyle}
      />
      {open && filtered.length > 0 && (
        <div style={{
          position: 'absolute', top: 'calc(100% + 4px)', left: 0, right: 0,
          backgroundColor: '#FFFFFF', border: '1px solid #EAECF0', borderRadius: '10px',
          boxShadow: '0 8px 32px rgba(16,24,40,0.12)', maxHeight: '180px',
          overflowY: 'auto', zIndex: 100,
        }}>
          {filtered.map((opt) => (
            <div key={opt}
              onMouseDown={(e) => { e.preventDefault(); setInputValue(opt); onChange(opt); setOpen(false); }}
              style={{
                padding: '9px 14px', fontFamily: 'var(--font-body)', fontSize: '14px',
                color: opt === value ? '#2D6FF7' : '#101828',
                backgroundColor: opt === value ? '#EFF4FF' : 'transparent',
                cursor: 'pointer', transition: 'background-color 0.1s ease',
              }}
              onMouseEnter={(e) => { (e.currentTarget as HTMLDivElement).style.backgroundColor = '#F9FAFB'; }}
              onMouseLeave={(e) => { (e.currentTarget as HTMLDivElement).style.backgroundColor = opt === value ? '#EFF4FF' : 'transparent'; }}>
              {opt}
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

function MultiTagCombobox({
  label,
  values,
  onChange,
  options,
  placeholder,
}: {
  label: string;
  values: string[];
  onChange: (v: string[]) => void;
  options: string[];
  placeholder: string;
}) {
  const [open, setOpen] = useState(false);
  const [text, setText] = useState('');
  const ref = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    const handler = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false);
    };
    document.addEventListener('mousedown', handler);
    return () => document.removeEventListener('mousedown', handler);
  }, []);

  const addTag = (raw: string) => {
    const v = raw.trim();
    if (!v) return;
    if (values.includes(v)) { setText(''); return; }
    onChange([...values, v]);
    setText('');
  };

  const removeTag = (t: string) => onChange(values.filter((x) => x !== t));

  const filtered = options.filter(
    (o) => o.toLowerCase().includes(text.toLowerCase()) && !values.includes(o)
  );

  const handleKey = (e: React.KeyboardEvent<HTMLInputElement>) => {
    if (e.key === 'Enter' || e.key === ',') {
      e.preventDefault();
      addTag(text);
    } else if (e.key === 'Backspace' && !text && values.length > 0) {
      onChange(values.slice(0, -1));
    }
  };

  return (
    <div ref={ref} style={{ position: 'relative' }}>
      <label style={labelStyle}>{label}</label>
      <div
        onClick={() => inputRef.current?.focus()}
        style={{
          width: '100%',
          padding: '6px 8px',
          backgroundColor: '#FFFFFF',
          border: '1px solid #D0D5DD',
          borderRadius: '10px',
          display: 'flex',
          flexWrap: 'wrap',
          gap: '6px',
          alignItems: 'center',
          minHeight: '42px',
          cursor: 'text',
          boxSizing: 'border-box',
        }}
      >
        {values.map((tag) => (
          <span key={tag} style={{
            display: 'inline-flex', alignItems: 'center', gap: '6px',
            padding: '4px 8px 4px 10px',
            background: 'linear-gradient(135deg, #EFF4FF 0%, #DBE7FF 100%)',
            color: '#2D6FF7',
            borderRadius: '9999px',
            fontFamily: 'var(--font-body)',
            fontSize: '12px',
            fontWeight: 600,
          }}>
            {tag}
            <button type="button" onClick={(e) => { e.stopPropagation(); removeTag(tag); }}
              style={{
                background: 'none', border: 'none', padding: 0, display: 'flex',
                cursor: 'pointer', color: '#2D6FF7',
              }}>
              <X size={12} />
            </button>
          </span>
        ))}
        <input
          ref={inputRef}
          type="text"
          value={text}
          placeholder={values.length === 0 ? placeholder : ''}
          onFocus={() => setOpen(true)}
          onChange={(e) => { setText(e.target.value); setOpen(true); }}
          onKeyDown={handleKey}
          style={{
            flex: 1, minWidth: '120px', border: 'none', outline: 'none',
            padding: '4px 6px', fontFamily: 'var(--font-body)', fontSize: '14px',
            color: '#101828', backgroundColor: 'transparent',
          }}
        />
      </div>
      {open && filtered.length > 0 && (
        <div style={{
          position: 'absolute', top: 'calc(100% + 4px)', left: 0, right: 0,
          backgroundColor: '#FFFFFF', border: '1px solid #EAECF0', borderRadius: '10px',
          boxShadow: '0 8px 32px rgba(16,24,40,0.12)', maxHeight: '180px',
          overflowY: 'auto', zIndex: 100,
        }}>
          {filtered.map((opt) => (
            <div key={opt}
              onMouseDown={(e) => { e.preventDefault(); addTag(opt); }}
              style={{
                padding: '9px 14px', fontFamily: 'var(--font-body)', fontSize: '14px',
                color: '#101828', cursor: 'pointer',
              }}
              onMouseEnter={(e) => { (e.currentTarget as HTMLDivElement).style.backgroundColor = '#F9FAFB'; }}
              onMouseLeave={(e) => { (e.currentTarget as HTMLDivElement).style.backgroundColor = 'transparent'; }}>
              {opt}
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

function SelectField({
  label,
  value,
  onChange,
  options,
}: {
  label: string;
  value: string;
  onChange: (v: string) => void;
  options: string[];
}) {
  return (
    <div style={{ position: 'relative' }}>
      <label style={labelStyle}>{label}</label>
      <div style={{ position: 'relative' }}>
        <select
          value={value}
          onChange={(e) => onChange(e.target.value)}
          style={{
            ...inputStyle, paddingRight: '38px', appearance: 'none', cursor: 'pointer',
            color: value ? '#101828' : '#98A2B3',
          }}
        >
          <option value="" disabled>Select…</option>
          {options.map((opt) => (<option key={opt} value={opt}>{opt}</option>))}
        </select>
        <ChevronDown size={15} color="#98A2B3"
          style={{ position: 'absolute', right: '12px', top: '50%', transform: 'translateY(-50%)', pointerEvents: 'none' }} />
      </div>
    </div>
  );
}

export default function MetadataForm({
  onSubmit,
  isLoading,
  isSuccess,
  fileCount,
}: MetadataFormProps) {
  const [customers, setCustomers] = useState<string[]>([]);
  const [brandOptions, setBrandOptions] = useState<string[]>([]);
  const [customer, setCustomer] = useState('');
  const [brands, setBrands] = useState<string[]>([]);
  const [date, setDate] = useState(format(new Date(), 'yyyy-MM-dd'));
  const [type, setType] = useState('');
  const [contentType, setContentType] = useState('');
  const [contentSource, setContentSource] = useState('');

  useEffect(() => {
    fetch('/api/customers').then((r) => r.json()).then(setCustomers).catch(console.error);
    fetch('/api/brands').then((r) => r.json()).then(setBrandOptions).catch(console.error);
  }, []);

  useEffect(() => {
    if (isSuccess) {
      setCustomer('');
      setBrands([]);
      setDate(format(new Date(), 'yyyy-MM-dd'));
      setType('');
      setContentType('');
      setContentSource('');
    }
  }, [isSuccess]);

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    const [year, month, day] = date.split('-');
    onSubmit({
      customer,
      brands,
      date: `${day}-${month}-${year}`,
      type,
      content_type: contentType,
      content_source: contentSource,
    });
  };

  const isValid =
    !!customer && brands.length > 0 && !!date &&
    !!type && !!contentType && !!contentSource && fileCount > 0;

  return (
    <form onSubmit={handleSubmit} style={{ display: 'flex', flexDirection: 'column', height: '100%', gap: '0' }}>
      <div style={{ paddingBottom: '16px', borderBottom: '1px solid #EAECF0', marginBottom: '18px' }}>
        <h2 style={{ fontFamily: 'var(--font-display)', fontWeight: 700, fontSize: '16px', color: '#101828', marginBottom: '2px' }}>
          Record Metadata
        </h2>
        <p style={{ fontFamily: 'var(--font-body)', fontSize: '13px', color: fileCount > 0 ? '#12B76A' : '#98A2B3' }}>
          {fileCount > 0
            ? `${fileCount} file${fileCount !== 1 ? 's' : ''} ready to upload`
            : 'Add files to upload first'}
        </p>
      </div>

      <div style={{ display: 'flex', flexDirection: 'column', gap: '14px', flex: 1 }}>
        <Combobox label="Customer" value={customer} onChange={setCustomer} options={customers} placeholder="e.g. PopUp, Shift…" />
        <MultiTagCombobox label="Brands" values={brands} onChange={setBrands} options={brandOptions}
          placeholder="Type a brand and press Enter…" />

        <div>
          <label style={labelStyle}>Date</label>
          <input type="date" value={date} onChange={(e) => setDate(e.target.value)}
            style={{ ...inputStyle, fontFamily: 'monospace', fontSize: '13px', colorScheme: 'light' }} />
        </div>

        <SelectField label="Type" value={type} onChange={setType} options={['Stories', 'Reels', 'Posts']} />
        <SelectField label="Content Type" value={contentType} onChange={setContentType}
          options={['Product IMG', 'Campaign IMG/VID', 'Store IMG (w/ brand)', 'Sales', 'Other (?)']} />
        <SelectField label="Content Source" value={contentSource} onChange={setContentSource}
          options={['Brand', 'Customer', 'Others (?)']} />
      </div>

      <button
        type="submit"
        disabled={!isValid || isLoading}
        style={{
          marginTop: '20px', width: '100%', padding: '13px',
          background: isValid && !isLoading
            ? 'linear-gradient(135deg, #2D6FF7 0%, #4F8AFF 100%)'
            : '#F2F4F7',
          border: 'none', borderRadius: '10px',
          color: isValid && !isLoading ? '#FFFFFF' : '#98A2B3',
          fontFamily: 'var(--font-display)', fontWeight: 700, fontSize: '14px',
          cursor: isValid && !isLoading ? 'pointer' : 'not-allowed',
          display: 'flex', alignItems: 'center', justifyContent: 'center', gap: '8px',
          transition: 'opacity 0.2s ease, box-shadow 0.2s ease',
          boxShadow: isValid && !isLoading ? '0 4px 14px rgba(45,111,247,0.35)' : 'none',
        }}
        onMouseEnter={(e) => { if (isValid && !isLoading) (e.currentTarget as HTMLButtonElement).style.opacity = '0.9'; }}
        onMouseLeave={(e) => { (e.currentTarget as HTMLButtonElement).style.opacity = '1'; }}
      >
        {isLoading ? (
          <><Loader2 size={15} style={{ animation: 'spin 1s linear infinite' }} /> Uploading…</>
        ) : isSuccess ? (
          <><CheckCircle size={15} /> Saved Successfully</>
        ) : (
          <><Upload size={15} /> Upload &amp; Save</>
        )}
      </button>
    </form>
  );
}
