'use client';

import { Film, Image as ImageIcon } from 'lucide-react';

interface FilePreviewProps {
  filenames: string[];
  recordId?: string;
}

function isVideoFile(filename: string): boolean {
  const ext = filename.split('.').pop()?.toLowerCase() ?? '';
  return ['mp4', 'mov', 'avi', 'mkv', 'webm', 'wmv', 'm4v'].includes(ext);
}

export default function FilePreview({ filenames, recordId }: FilePreviewProps) {
  if (!filenames || filenames.length === 0) return null;

  return (
    <div style={{ display: 'grid', gridTemplateColumns: 'repeat(3, 1fr)', gap: '6px' }}>
      {filenames.map((filename, index) => {
        const isVideo = isVideoFile(filename);
        const displayName = filename.split('/').pop() ?? filename;

        return (
          <div
            key={index}
            style={{
              backgroundColor: '#F9FAFB',
              border: '1px solid #EAECF0',
              borderRadius: '8px',
              aspectRatio: '1',
              display: 'flex',
              flexDirection: 'column',
              alignItems: 'center',
              justifyContent: 'center',
              gap: '4px',
              padding: '8px',
              position: 'relative',
              overflow: 'hidden',
            }}
          >
            {isVideo ? (
              <>
                <Film size={18} color="#FF5C35" strokeWidth={1.5} />
                <span
                  style={{
                    fontFamily: 'monospace',
                    fontSize: '8px',
                    color: '#98A2B3',
                    textAlign: 'center',
                    wordBreak: 'break-all',
                    lineHeight: 1.3,
                    maxWidth: '100%',
                  }}
                >
                  {displayName.length > 18 ? displayName.substring(0, 15) + '…' : displayName}
                </span>
              </>
            ) : recordId ? (
              <img
                src={`/api/file?id=${recordId}&filename=${encodeURIComponent(displayName)}`}
                alt={displayName}
                style={{
                  width: '100%',
                  height: '100%',
                  objectFit: 'cover',
                  position: 'absolute',
                  inset: 0,
                  borderRadius: '8px',
                }}
                onError={(e) => {
                  (e.target as HTMLImageElement).style.display = 'none';
                }}
              />
            ) : (
              <>
                <ImageIcon size={18} color="#D0D5DD" strokeWidth={1} />
                <span
                  style={{
                    fontFamily: 'monospace',
                    fontSize: '8px',
                    color: '#98A2B3',
                    textAlign: 'center',
                    wordBreak: 'break-all',
                    lineHeight: 1.3,
                    maxWidth: '100%',
                  }}
                >
                  {displayName.length > 18 ? displayName.substring(0, 15) + '…' : displayName}
                </span>
              </>
            )}
          </div>
        );
      })}
    </div>
  );
}
