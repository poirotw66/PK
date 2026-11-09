ALTER TABLE `match_scores` ADD `synced_version` integer DEFAULT 0 NOT NULL;--> statement-breakpoint
ALTER TABLE `match_scores` ADD `sync_error` text;