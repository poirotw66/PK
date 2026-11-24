declare namespace Cloudflare {
  interface Env {
    DB?: D1Database;
    ADMIN_AUTH_MODE?: string;
    TEMP_ADMIN_PASSWORD_HASH?: string;
    TEMP_ADMIN_EXPIRES_AT?: string;
    DRAFT_CUTOVER_SOURCE_REVISION?: string;
    DRAFT_CUTOVER_SHA256?: string;
    PRIMARY_ADMIN_EMAIL?: string;
    ADDITIONAL_ADMIN_EMAIL?: string;
    ADDITIONAL_ADMIN_EMAILS?: string;
    SYNC_RETRY_TOKEN_HASH?: string;
    APP_ORIGIN?: string;
    READER_ORIGIN?: string;
    CLOUD_PUBLISH_SECRET?: string;
    PUBLISH_SOURCE_ID?: string;
    BUCKET?: R2Bucket;
  }
}
