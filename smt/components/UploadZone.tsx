'use client';

import { useCallback, useState } from 'react';
import { useDropzone } from 'react-dropzone';
import { Upload, Film, Image as ImageIcon, X } from 'lucide-react';

interface UploadZoneProps {
  files: File[];
  onFilesChange: (files: File[]) => void;
}

export default function UploadZone({ files, onFilesChange }: UploadZoneProps) {
  const [previews, setPreviews] = useState<Record<string, string>>({});

  const onDrop = useCallback(
    (acceptedFiles: File[]) => {
      const newFiles = [...files, ...acceptedFiles];
      onFilesChange(newFiles);
      acceptedFiles.forEach((file) => {
        if (file.type.startsWith('image/')) {
          const url = URL.createObjectURL(file);
          setPreviews((prev) => ({ ...prev, [file.name + file.size]: url }));
        }
      });
    },
    [files, onFilesChange]
  );

  const { getRootProps, getInputProps, isDragActive } = useDropzone({
    onDrop,
    accept: { 'image/*': [], 'video/*': [] },
    multiple: true,
  });

  const removeFile = (index: number) => {
    const file = files[index];
    const key = file.name + file.size;
    if (previews[key]) {
      URL.revokeObjectURL(previews[key]);
      setPreviews((prev) => {
        const next = { ...prev };
        delete next[key];
        return next;
      });
    }
    onFilesChange(files.filter((_, i) => i !== index));
  };

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: '16px', height: '100%' }}>
      {/* Drop area */}
      <div
        {...getRootProps()}
        style={{
          flex: files.length === 0 ? 1 : '0 0 auto',
          minHeight: files.length === 0 ? '280px' : '130px',
          border: isDragActive
            ? '2px solid #2D6FF7'
            : '2px dashed #D0D5DD',
          backgroundColor: isDragActive ? '#EFF4FF' : '#FAFAFA',
          borderRadius: '12px',
          cursor: 'pointer',
          display: 'flex',
          flexDirection: 'column',
          alignItems: 'center',
          justifyContent: 'center',
          gap: '12px',
          transition: 'all 0.18s ease',
        }}
      >
        <input {...getInputProps()} />

        <div
          style={{
            width: '52px',
            height: '52px',
            borderRadius: '12px',
            backgroundColor: isDragActive ? '#2D6FF7' : '#F2F4F7',
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'center',
            transition: 'all 0.18s ease',
          }}
        >
          <Upload
            size={22}
            color={isDragActive ? '#FFFFFF' : '#98A2B3'}
            strokeWidth={1.75}
          />
        </div>

        {isDragActive ? (
          <div style={{ textAlign: 'center' }}>
            <p
              style={{
                fontFamily: 'var(--font-display)',
                fontWeight: 700,
                fontSize: '16px',
                color: '#2D6FF7',
              }}
            >
              Drop to add files
            </p>
          </div>
        ) : (
          <div style={{ textAlign: 'center' }}>
            <p
              style={{
                fontFamily: 'var(--font-body)',
                fontWeight: 500,
                fontSize: '14px',
                color: '#475467',
              }}
            >
              <span style={{ color: '#2D6FF7', fontWeight: 600 }}>Click to upload</span>{' '}
              or drag & drop
            </p>
            <p
              style={{
                fontFamily: 'var(--font-body)',
                fontSize: '12px',
                color: '#98A2B3',
                marginTop: '4px',
              }}
            >
              Images and videos accepted
            </p>
          </div>
        )}
      </div>

      {/* File grid */}
      {files.length > 0 && (
        <div>
          <div
            style={{
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'space-between',
              marginBottom: '10px',
            }}
          >
            <span
              style={{
                fontFamily: 'var(--font-body)',
                fontWeight: 600,
                fontSize: '12px',
                color: '#475467',
              }}
            >
              {files.length} file{files.length !== 1 ? 's' : ''} queued
            </span>
            <button
              onClick={() => {
                Object.values(previews).forEach(URL.revokeObjectURL);
                setPreviews({});
                onFilesChange([]);
              }}
              style={{
                fontFamily: 'var(--font-body)',
                fontSize: '12px',
                fontWeight: 500,
                color: '#F04438',
                background: 'none',
                border: '1px solid #FEE4E2',
                borderRadius: '6px',
                padding: '3px 10px',
                cursor: 'pointer',
              }}
            >
              Clear all
            </button>
          </div>

          <div
            style={{
              display: 'grid',
              gridTemplateColumns: 'repeat(3, 1fr)',
              gap: '10px',
            }}
          >
            {files.map((file, index) => {
              const key = file.name + file.size;
              const isImage = file.type.startsWith('image/');
              const preview = previews[key];

              return (
                <div
                  key={key}
                  style={{
                    position: 'relative',
                    backgroundColor: '#F9FAFB',
                    border: '1px solid #EAECF0',
                    borderRadius: '10px',
                    aspectRatio: '1',
                    overflow: 'hidden',
                  }}
                >
                  {isImage && preview ? (
                    <img
                      src={preview}
                      alt={file.name}
                      style={{ width: '100%', height: '100%', objectFit: 'cover' }}
                    />
                  ) : isImage ? (
                    <div
                      style={{
                        width: '100%',
                        height: '100%',
                        display: 'flex',
                        alignItems: 'center',
                        justifyContent: 'center',
                      }}
                    >
                      <ImageIcon size={24} color="#D0D5DD" strokeWidth={1} />
                    </div>
                  ) : (
                    <div
                      style={{
                        width: '100%',
                        height: '100%',
                        display: 'flex',
                        flexDirection: 'column',
                        alignItems: 'center',
                        justifyContent: 'center',
                        gap: '6px',
                        padding: '8px',
                      }}
                    >
                      <Film size={22} color="#FF5C35" strokeWidth={1.5} />
                      <span
                        style={{
                          fontFamily: 'monospace',
                          fontSize: '9px',
                          color: '#98A2B3',
                          textAlign: 'center',
                          wordBreak: 'break-all',
                          lineHeight: 1.3,
                        }}
                      >
                        {file.name.length > 20 ? file.name.substring(0, 17) + '…' : file.name}
                      </span>
                    </div>
                  )}

                  {/* Filename overlay */}
                  {isImage && preview && (
                    <div
                      style={{
                        position: 'absolute',
                        bottom: 0,
                        left: 0,
                        right: 0,
                        background: 'linear-gradient(to top, rgba(16,24,40,0.6), transparent)',
                        padding: '12px 8px 6px',
                      }}
                    >
                      <p
                        style={{
                          fontFamily: 'monospace',
                          fontSize: '9px',
                          color: '#FFFFFF',
                          overflow: 'hidden',
                          textOverflow: 'ellipsis',
                          whiteSpace: 'nowrap',
                        }}
                      >
                        {file.name}
                      </p>
                    </div>
                  )}

                  {/* Remove button */}
                  <button
                    onClick={(e) => { e.stopPropagation(); removeFile(index); }}
                    style={{
                      position: 'absolute',
                      top: '6px',
                      right: '6px',
                      width: '22px',
                      height: '22px',
                      backgroundColor: 'rgba(255,255,255,0.9)',
                      border: '1px solid #EAECF0',
                      borderRadius: '50%',
                      display: 'flex',
                      alignItems: 'center',
                      justifyContent: 'center',
                      cursor: 'pointer',
                      backdropFilter: 'blur(4px)',
                    }}
                  >
                    <X size={10} color="#475467" />
                  </button>
                </div>
              );
            })}
          </div>
        </div>
      )}
    </div>
  );
}
