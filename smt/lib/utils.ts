import { format, parse } from 'date-fns';

/**
 * Trigger a file download from a URL by simulating an anchor click.
 * This is more reliable than window.open() which gets popup-blocked
 * and doesn't always respect Content-Disposition headers.
 */
export function triggerDownload(url: string, filename?: string): void {
  if (typeof document === 'undefined') return;
  const a = document.createElement('a');
  a.href = url;
  if (filename) a.download = filename;
  a.style.display = 'none';
  document.body.appendChild(a);
  a.click();
  // Small delay before removing so the browser registers the click
  setTimeout(() => {
    if (a.parentNode) a.parentNode.removeChild(a);
  }, 100);
}

export function formatDateForDisplay(dateStr: string): string {
  return dateStr;
}

export function formatDateForInput(dateStr: string): string {
  try {
    const parsed = parse(dateStr, 'dd-MM-yyyy', new Date());
    return format(parsed, 'yyyy-MM-dd');
  } catch {
    return dateStr;
  }
}

export function inputDateToDisplay(inputDate: string): string {
  try {
    const parsed = parse(inputDate, 'yyyy-MM-dd', new Date());
    return format(parsed, 'dd-MM-yyyy');
  } catch {
    return inputDate;
  }
}

export function getTodayForInput(): string {
  return format(new Date(), 'yyyy-MM-dd');
}

export function getTodayDisplay(): string {
  return format(new Date(), 'dd-MM-yyyy');
}

export function getFileExtension(filename: string): string {
  const parts = filename.split('.');
  return parts.length > 1 ? parts[parts.length - 1].toLowerCase() : 'bin';
}

export function sanitizeBrand(name: string): string {
  return name.replace(/\s+/g, '').replace(/[^a-zA-Z0-9\-]/g, '');
}

export function sanitizeContentType(contentType: string): string {
  // Specific cases first
  const map: Record<string, string> = {
    'Product IMG': 'ProductIMG',
    'Campaign IMG/VID': 'CampaignIMGVID',
    'Store IMG (w/ brand)': 'StoreIMGwBrand',
    'Sales': 'Sales',
    'Other (?)': 'Other',
  };
  if (map[contentType] !== undefined) return map[contentType];
  // Generic fallback
  return contentType
    .replace(/\(w\/\s*([^)]*)\)/gi, (_m, inner) => 'w' + String(inner).replace(/\s+/g, ''))
    .replace(/\(\?\)/g, '')
    .replace(/[/\\]/g, '')
    .replace(/[()?]/g, '')
    .replace(/\s+/g, '')
    .replace(/[^a-zA-Z0-9\-]/g, '');
}

export function slugifySessionName(name: string): string {
  return name
    .trim()
    .replace(/\s+/g, '_')
    .replace(/[^a-zA-Z0-9\-_]/g, '');
}

export function sanitizePostType(postType: string): string {
  // PostType is Stories | Reels | Posts (already clean), but strip spaces/special chars just in case
  return postType.replace(/\s+/g, '').replace(/[^a-zA-Z0-9\-]/g, '');
}

export function buildFilename(
  brands: string[],
  postType: string,
  ext: string,
  index?: number
): string {
  const brandPart = brands.map(sanitizeBrand).filter(Boolean).join('_');
  const ptPart = sanitizePostType(postType);
  const base = `${brandPart}_${ptPart}`;
  if (index !== undefined) {
    return `${base}_${index}.${ext}`;
  }
  return `${base}.${ext}`;
}
