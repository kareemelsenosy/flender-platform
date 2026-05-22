import {
  S3Client,
  PutObjectCommand,
  GetObjectCommand,
  ListObjectsV2Command,
  DeleteObjectsCommand,
  CreateBucketCommand,
  HeadBucketCommand,
} from '@aws-sdk/client-s3';

/**
 * Object storage for uploaded screenshots/videos.
 *
 * Works with any S3-compatible service. Configured via env vars:
 *   S3_ENDPOINT           e.g. http://minio:9000  (omit for AWS S3)
 *   S3_REGION             default us-east-1
 *   S3_BUCKET             default smt-uploads
 *   S3_ACCESS_KEY_ID
 *   S3_SECRET_ACCESS_KEY
 *   S3_FORCE_PATH_STYLE   "true" for MinIO / most self-hosted setups
 *
 * Object keys follow the pattern  {recordId}/{filename}
 */

let client: S3Client | null = null;
let bucketReady: Promise<void> | null = null;

function bucket(): string {
  return process.env.S3_BUCKET || 'smt-uploads';
}

function getClient(): S3Client {
  if (!client) {
    const endpoint = process.env.S3_ENDPOINT || undefined;
    client = new S3Client({
      region: process.env.S3_REGION || 'us-east-1',
      endpoint,
      forcePathStyle: process.env.S3_FORCE_PATH_STYLE === 'true' || Boolean(endpoint),
      credentials: {
        accessKeyId: process.env.S3_ACCESS_KEY_ID || '',
        secretAccessKey: process.env.S3_SECRET_ACCESS_KEY || '',
      },
    });
  }
  return client;
}

/** Ensure the bucket exists (idempotent, memoised). */
function ensureBucket(): Promise<void> {
  if (!bucketReady) {
    bucketReady = (async () => {
      const c = getClient();
      try {
        await c.send(new HeadBucketCommand({ Bucket: bucket() }));
      } catch {
        try {
          await c.send(new CreateBucketCommand({ Bucket: bucket() }));
        } catch (err: unknown) {
          // Tolerate races / "already owned by you"
          const name = (err as { name?: string })?.name || '';
          if (!/BucketAlreadyOwnedByYou|BucketAlreadyExists/.test(name)) {
            bucketReady = null;
            throw err;
          }
        }
      }
    })();
  }
  return bucketReady;
}

async function streamToBuffer(stream: unknown): Promise<Buffer> {
  // Node.js readable stream
  const chunks: Buffer[] = [];
  for await (const chunk of stream as AsyncIterable<Buffer>) {
    chunks.push(Buffer.isBuffer(chunk) ? chunk : Buffer.from(chunk));
  }
  return Buffer.concat(chunks);
}

/** Build the object key for a file belonging to a record. */
export function fileKey(recordId: string, filename: string): string {
  return `${recordId}/${filename}`;
}

/** Upload a file. */
export async function putFile(
  recordId: string,
  filename: string,
  body: Buffer,
  contentType?: string
): Promise<void> {
  await ensureBucket();
  await getClient().send(new PutObjectCommand({
    Bucket: bucket(),
    Key: fileKey(recordId, filename),
    Body: body,
    ContentType: contentType || 'application/octet-stream',
  }));
}

/** Download a file. Returns null if it does not exist. */
export async function getFile(recordId: string, filename: string): Promise<Buffer | null> {
  await ensureBucket();
  try {
    const res = await getClient().send(new GetObjectCommand({
      Bucket: bucket(),
      Key: fileKey(recordId, filename),
    }));
    if (!res.Body) return null;
    return await streamToBuffer(res.Body);
  } catch (err: unknown) {
    const name = (err as { name?: string })?.name || '';
    if (/NoSuchKey|NotFound/.test(name)) return null;
    throw err;
  }
}

/** Delete every object under a record's prefix (i.e. all of a record's files). */
export async function deletePrefix(recordId: string): Promise<void> {
  await ensureBucket();
  const c = getClient();
  const prefix = `${recordId}/`;

  let continuationToken: string | undefined;
  do {
    const listed = await c.send(new ListObjectsV2Command({
      Bucket: bucket(),
      Prefix: prefix,
      ContinuationToken: continuationToken,
    }));
    const objects = (listed.Contents || []).map((o) => ({ Key: o.Key! }));
    if (objects.length > 0) {
      await c.send(new DeleteObjectsCommand({
        Bucket: bucket(),
        Delete: { Objects: objects },
      }));
    }
    continuationToken = listed.IsTruncated ? listed.NextContinuationToken : undefined;
  } while (continuationToken);
}
