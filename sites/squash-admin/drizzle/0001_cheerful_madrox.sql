CREATE TABLE `public_outbox` (
	`id` text PRIMARY KEY NOT NULL,
	`sequence` integer NOT NULL,
	`payload` text NOT NULL,
	`actor` text NOT NULL,
	`action` text NOT NULL,
	`created` integer NOT NULL,
	`delivered` integer DEFAULT 0 NOT NULL
);
--> statement-breakpoint
CREATE UNIQUE INDEX `public_outbox_sequence_unique` ON `public_outbox` (`sequence`);--> statement-breakpoint
CREATE TABLE `public_state` (
	`id` integer PRIMARY KEY NOT NULL,
	`sequence` integer NOT NULL,
	`dispatch_id` text NOT NULL,
	`schedule_revision` text NOT NULL,
	`payload` text NOT NULL,
	`published_at` text NOT NULL,
	`live_updated_at` text,
	`ack_sequence` integer DEFAULT 0 NOT NULL,
	`ack_version` text,
	`synced_at` text,
	`last_error` text
);
