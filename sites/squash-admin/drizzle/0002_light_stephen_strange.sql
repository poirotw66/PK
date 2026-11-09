CREATE TABLE `match_scores` (
	`match_id` text PRIMARY KEY NOT NULL,
	`version` integer NOT NULL,
	`payload` text NOT NULL,
	`updated_at` text NOT NULL,
	`actor` text NOT NULL
);
