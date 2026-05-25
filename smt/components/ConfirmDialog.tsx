'use client';

/**
 * In-app confirmation + alert dialogs.
 *
 * Wrap the app in <ConfirmProvider>, then call useConfirm() in any client
 * component to get an imperative API that returns a Promise:
 *
 *   const confirm = useConfirm();
 *   const ok = await confirm({ title: 'Delete?', message: '...', danger: true });
 *   if (!ok) return;
 *
 * Or use it as a toast/alert (no Cancel button):
 *
 *   await confirm({ title: 'Heads up', message: '...', alertOnly: true });
 *
 * Replaces window.confirm() / window.alert() which look like generic
 * browser chrome and break the visual identity of the app.
 */

import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useRef,
  useState,
  type ReactNode,
} from 'react';
import { AlertTriangle, Info } from 'lucide-react';

export interface ConfirmOptions {
  title: string;
  message?: string;
  confirmLabel?: string;
  cancelLabel?: string;
  /** Style the confirm button red (destructive action). */
  danger?: boolean;
  /** Hide the Cancel button — behaves like alert(). */
  alertOnly?: boolean;
}

type Resolver = (value: boolean) => void;

const ConfirmContext = createContext<((opts: ConfirmOptions) => Promise<boolean>) | null>(null);

export function useConfirm() {
  const ctx = useContext(ConfirmContext);
  if (!ctx) {
    throw new Error('useConfirm() must be used inside <ConfirmProvider>');
  }
  return ctx;
}

export function ConfirmProvider({ children }: { children: ReactNode }) {
  const [opts, setOpts] = useState<ConfirmOptions | null>(null);
  const resolverRef = useRef<Resolver | null>(null);

  const confirm = useCallback((options: ConfirmOptions) => {
    return new Promise<boolean>((resolve) => {
      resolverRef.current = resolve;
      setOpts(options);
    });
  }, []);

  const close = useCallback((value: boolean) => {
    const r = resolverRef.current;
    resolverRef.current = null;
    setOpts(null);
    if (r) r(value);
  }, []);

  // Escape cancels, Enter confirms.
  useEffect(() => {
    if (!opts) return;
    const handler = (e: KeyboardEvent) => {
      if (e.key === 'Escape') close(false);
      else if (e.key === 'Enter') close(true);
    };
    window.addEventListener('keydown', handler);
    return () => window.removeEventListener('keydown', handler);
  }, [opts, close]);

  return (
    <ConfirmContext.Provider value={confirm}>
      {children}
      {opts && <ConfirmDialog opts={opts} onClose={close} />}
    </ConfirmContext.Provider>
  );
}

function ConfirmDialog({
  opts,
  onClose,
}: {
  opts: ConfirmOptions;
  onClose: (value: boolean) => void;
}) {
  const Icon = opts.danger ? AlertTriangle : Info;
  const iconColor = opts.danger ? '#F04438' : '#2D6FF7';
  const iconBg = opts.danger
    ? 'linear-gradient(135deg, #FEE4E2 0%, #FECDCA 100%)'
    : 'linear-gradient(135deg, #EFF4FF 0%, #DBE7FF 100%)';
  const confirmBg = opts.danger
    ? 'linear-gradient(135deg, #F04438 0%, #D92D20 100%)'
    : 'linear-gradient(135deg, #2D6FF7 0%, #4F8AFF 100%)';
  const confirmShadow = opts.danger
    ? '0 4px 14px rgba(240,68,56,0.35)'
    : '0 4px 14px rgba(45,111,247,0.35)';

  return (
    <div
      role="dialog"
      aria-modal="true"
      aria-labelledby="confirm-dialog-title"
      onClick={(e) => {
        // Backdrop click cancels (alertOnly closes too).
        if (e.target === e.currentTarget) onClose(false);
      }}
      style={{
        position: 'fixed',
        inset: 0,
        backgroundColor: 'rgba(16,24,40,0.55)',
        backdropFilter: 'blur(2px)',
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'center',
        zIndex: 1000,
        padding: '16px',
        animation: 'smtConfirmFadeIn 0.15s ease',
      }}
    >
      <style>{`
        @keyframes smtConfirmFadeIn {
          from { opacity: 0; }
          to   { opacity: 1; }
        }
        @keyframes smtConfirmPopIn {
          from { opacity: 0; transform: translateY(8px) scale(0.98); }
          to   { opacity: 1; transform: translateY(0) scale(1); }
        }
      `}</style>
      <div
        onClick={(e) => e.stopPropagation()}
        style={{
          backgroundColor: '#FFFFFF',
          borderRadius: '16px',
          padding: '28px 28px 22px',
          width: '100%',
          maxWidth: '440px',
          boxShadow: '0 24px 60px rgba(16,24,40,0.25)',
          animation: 'smtConfirmPopIn 0.18s ease',
        }}
      >
        <div
          style={{
            display: 'flex',
            alignItems: 'flex-start',
            gap: '16px',
            marginBottom: '20px',
          }}
        >
          <div
            style={{
              flexShrink: 0,
              width: 44,
              height: 44,
              borderRadius: 12,
              background: iconBg,
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'center',
            }}
          >
            <Icon size={20} color={iconColor} />
          </div>
          <div style={{ flex: 1, minWidth: 0 }}>
            <h3
              id="confirm-dialog-title"
              style={{
                fontFamily: 'var(--font-display)',
                fontWeight: 700,
                fontSize: '16px',
                color: '#101828',
                margin: '4px 0 6px',
              }}
            >
              {opts.title}
            </h3>
            {opts.message && (
              <p
                style={{
                  fontFamily: 'var(--font-body)',
                  fontSize: '13.5px',
                  color: '#475467',
                  lineHeight: 1.55,
                  margin: 0,
                  whiteSpace: 'pre-wrap',
                }}
              >
                {opts.message}
              </p>
            )}
          </div>
        </div>

        <div style={{ display: 'flex', gap: '10px', justifyContent: 'flex-end' }}>
          {!opts.alertOnly && (
            <button
              type="button"
              onClick={() => onClose(false)}
              style={{
                padding: '10px 18px',
                borderRadius: '10px',
                border: '1px solid #D0D5DD',
                backgroundColor: '#FFFFFF',
                color: '#344054',
                fontFamily: 'var(--font-body)',
                fontWeight: 600,
                fontSize: '13.5px',
                cursor: 'pointer',
                transition: 'background-color 0.15s ease, border-color 0.15s ease',
              }}
              onMouseEnter={(e) => {
                (e.currentTarget as HTMLButtonElement).style.backgroundColor = '#F9FAFB';
              }}
              onMouseLeave={(e) => {
                (e.currentTarget as HTMLButtonElement).style.backgroundColor = '#FFFFFF';
              }}
            >
              {opts.cancelLabel || 'Cancel'}
            </button>
          )}
          <button
            type="button"
            autoFocus
            onClick={() => onClose(true)}
            style={{
              padding: '10px 20px',
              borderRadius: '10px',
              border: 'none',
              background: confirmBg,
              color: '#FFFFFF',
              fontFamily: 'var(--font-display)',
              fontWeight: 700,
              fontSize: '13.5px',
              cursor: 'pointer',
              boxShadow: confirmShadow,
              transition: 'opacity 0.15s ease',
            }}
            onMouseEnter={(e) => {
              (e.currentTarget as HTMLButtonElement).style.opacity = '0.92';
            }}
            onMouseLeave={(e) => {
              (e.currentTarget as HTMLButtonElement).style.opacity = '1';
            }}
          >
            {opts.confirmLabel || (opts.alertOnly ? 'OK' : opts.danger ? 'Delete' : 'Confirm')}
          </button>
        </div>
      </div>
    </div>
  );
}
