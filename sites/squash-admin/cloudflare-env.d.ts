declare namespace Cloudflare {
  interface Env {
    DB?: D1Database;
    ADMIN_PEPPER?: string;
    APP_ORIGIN?: string;
    READER_ORIGIN?: string;
    CLOUD_PUBLISH_SECRET?: string;
    PUBLISH_SOURCE_ID?: string;
    BUCKET?: R2Bucket;
  }
}
