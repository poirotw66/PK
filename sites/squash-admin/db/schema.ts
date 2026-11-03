import { sqliteTable, text, integer } from "drizzle-orm/sqlite-core";
export const administrator = sqliteTable("administrator", {
    id: integer("id").primaryKey(),
    owner: text("owner").notNull(),
    username: text("username"),
    passwordHash: text("password_hash"),
});
export const sessions = sqliteTable("sessions", {
    id: text("id").primaryKey(),
    owner: text("owner").notNull(),
    csrf: text("csrf").notNull(),
    created: integer("created").notNull(),
    touched: integer("touched").notNull(),
});
export const loginAttempts = sqliteTable("login_attempts", {
    owner: text("owner").primaryKey(),
    window: integer("window").notNull(),
    count: integer("count").notNull(),
});
export const tournament = sqliteTable("tournament", {
    id: integer("id").primaryKey(),
    revision: text("revision").notNull(),
    payload: text("payload").notNull(),
});
export const revisions = sqliteTable("revisions", {
    id: text("id").primaryKey(),
    parent: text("parent"),
    actor: text("actor").notNull(),
    reason: text("reason").notNull(),
    created: integer("created").notNull(),
    payload: text("payload").notNull(),
    changes: text("changes").notNull(),
});
export const previews = sqliteTable("previews", {
    id: text("id").primaryKey(),
    session: text("session").notNull(),
    base: text("base").notNull(),
    payload: text("payload").notNull(),
    changes: text("changes").notNull(),
    reason: text("reason").notNull(),
    created: integer("created").notNull(),
});

export const publicState = sqliteTable("public_state", {
  id: integer("id").primaryKey(),
  sequence: integer("sequence").notNull(),
  dispatchId: text("dispatch_id").notNull(),
  scheduleRevision: text("schedule_revision").notNull(),
  payload: text("payload").notNull(),
  publishedAt: text("published_at").notNull(),
  liveUpdatedAt: text("live_updated_at"),
  ackSequence: integer("ack_sequence").notNull().default(0),
  ackVersion: text("ack_version"),
  syncedAt: text("synced_at"),
  lastError: text("last_error"),
});
export const publicOutbox = sqliteTable("public_outbox", {
  id: text("id").primaryKey(),
  sequence: integer("sequence").notNull().unique(),
  payload: text("payload").notNull(),
  actor: text("actor").notNull(),
  action: text("action").notNull(),
  created: integer("created").notNull(),
  delivered: integer("delivered").notNull().default(0),
});
